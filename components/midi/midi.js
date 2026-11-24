// midi: Web MIDI + keyboard -> pad events. See contracts/README.md §4 (v2.1).

// Some hosts (and the Node test runner) build events with
// `Object.assign(new Event(t), { target, timeStamp })`. Those two are prototype
// getters with no setter, so that idiom throws. Give them an instance-level
// setter that shadows the accessor. No-op wherever a setter already exists, and
// invisible to real browser events (they never assign these).
const installEventSetters = () => {
  if (typeof Event !== "function" || !Event.prototype) return;
  for (const key of ["timeStamp", "target"]) {
    const desc = Object.getOwnPropertyDescriptor(Event.prototype, key);
    if (!desc || desc.set || typeof desc.get !== "function") continue;
    try {
      Object.defineProperty(Event.prototype, key, {
        configurable: true,
        enumerable: true,
        get() {
          const own = Object.getOwnPropertyDescriptor(this, `__choppad_${key}`);
          return own ? own.value : desc.get.call(this);
        },
        set(value) {
          Object.defineProperty(this, `__choppad_${key}`, {
            value,
            configurable: true,
            enumerable: false,
            writable: true,
          });
        },
      });
    } catch {
      /* frozen prototype: leave the host alone */
    }
  }
};
installEventSetters();

export const defaultMapping = {
  notes: Object.fromEntries(Array.from({ length: 16 }, (_, i) => [36 + i, i])),
  // KeyboardEvent.code (physical keys, layout-independent), bottom row = pads 0-3
  keys: {
    KeyZ: 0, KeyX: 1, KeyC: 2, KeyV: 3, KeyA: 4, KeyS: 5, KeyD: 6, KeyF: 7,
    KeyQ: 8, KeyW: 9, KeyE: 10, KeyR: 11, Digit1: 12, Digit2: 13, Digit3: 14, Digit4: 15,
  },
  bankKeys: { BracketLeft: -1, BracketRight: +1 },
  panicKey: "Space",
};

export const STORAGE_KEY = "choppad.midiMap";

const PAD_COUNT = 16;
const FORM_TAGS = new Set(["INPUT", "TEXTAREA", "SELECT"]);

const copyMapping = (mapping) => ({
  notes: { ...mapping.notes },
  keys: { ...mapping.keys },
  bankKeys: { ...mapping.bankKeys },
  panicKey: mapping.panicKey,
});

const toNote = (note) => {
  const n = typeof note === "string" ? Number(note) : note;
  return Number.isInteger(n) && n >= 0 && n <= 127 ? n : null;
};

/** Parse one raw MIDI message (Uint8Array) into a pad event, or null if unmapped/irrelevant. */
export function parseMidiMessage(data, mapping = defaultMapping, time = 0) {
  if (!data || typeof data.length !== "number" || data.length < 3) return null;
  const status = data[0];
  const kind = status & 0xf0;
  if (kind !== 0x90 && kind !== 0x80) return null;
  const note = data[1];
  const velocity = data[2];
  const pad = Object.prototype.hasOwnProperty.call(mapping.notes, note) ? mapping.notes[note] : null;
  if (pad === null || !Number.isInteger(pad) || pad < 0 || pad >= PAD_COUNT) return null;
  if (kind === 0x90 && velocity > 0) {
    return { type: "pad-on", pad, velocity: velocity / 127, time, source: "midi" };
  }
  return { type: "pad-off", pad, time, source: "midi" };
}

/**
 * Listen on every MIDI input (incl. hot-plugged) and on keyboardTarget. Returns an EventTarget emitting
 * pad-on / pad-off / bank / panic / devices / learn-state CustomEvents, extended with:
 *   getDevices() -> [{id, name, state}], setMidiAccess(access), startLearn(), cancelLearn(), resetMap(), mapping, destroy()
 * storage: localStorage-like ({getItem, setItem, removeItem}); learned note map persists under STORAGE_KEY.
 */
export function createInput({ midiAccess = null, keyboardTarget = null, mapping = defaultMapping, storage = null } = {}) {
  const target = new EventTarget();
  let current = copyMapping(mapping);
  let learn = null;
  let destroyed = false;

  const attached = new Map(); // input port -> handler
  const savedInputs = [];
  let stateHandler = null;

  const emit = (type, detail) => {
    if (destroyed) return;
    target.dispatchEvent(new CustomEvent(type, { detail: detail.type === type ? detail : { type, ...detail } }));
  };

  // ---- persisted learned map -------------------------------------------------
  const loadPersisted = () => {
    let raw = null;
    try {
      raw = storage?.getItem(STORAGE_KEY) ?? null;
    } catch {
      return;
    }
    if (!raw) return;
    let parsed;
    try {
      parsed = JSON.parse(raw);
    } catch {
      return;
    }
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return;
    for (const [note, pad] of Object.entries(parsed)) {
      const n = toNote(note);
      if (n === null || !Number.isInteger(pad) || pad < 0 || pad >= PAD_COUNT) continue;
      current.notes[n] = pad;
    }
  };
  loadPersisted();

  const persist = () => {
    if (!storage?.setItem) return;
    const out = {};
    for (const [note, pad] of Object.entries(current.notes)) out[note] = pad;
    try {
      storage.setItem(STORAGE_KEY, JSON.stringify(out));
    } catch {
      /* storage full / unavailable: mapping stays in-memory */
    }
  };

  // ---- MIDI ------------------------------------------------------------------
  const getDevices = () =>
    midiAccess
      ? Array.from(midiAccess.inputs.values()).map((port) => ({
          id: port.id,
          name: port.name ?? port.id,
          state: port.state ?? "connected",
        }))
      : [];

  const announceDevices = () => emit("devices", { devices: getDevices() });

  const handleMessage = (event) => {
    if (destroyed) return;
    const data = event?.data ?? event;
    if (!data || typeof data.length !== "number" || data.length < 3) return;
    const status = data[0];
    const kind = status & 0xf0;
    if (kind !== 0x90 && kind !== 0x80) return;
    const note = data[1];
    const velocity = data[2];
    const isOn = kind === 0x90 && velocity > 0;
    const time = (event?.timeStamp ?? 0) / 1000;

    if (learn && isOn) {
      const pad = learn.nextPad;
      for (const [existingNote, assigned] of Object.entries(current.notes)) {
        if (assigned === pad) delete current.notes[existingNote];
      }
      current.notes[note] = pad;
      learn.nextPad = pad + 1;
      if (learn.nextPad >= PAD_COUNT) {
        learn = null;
        persist();
        emit("learn-state", { type: "learn-state", active: false, nextPad: null });
      } else {
        emit("learn-state", { type: "learn-state", active: true, nextPad: learn.nextPad });
      }
      return;
    }

    const parsed = parseMidiMessage(data, current, time);
    if (parsed) emit(parsed.type, parsed);
  };

  const attachInput = (port) => {
    if (!port || attached.has(port)) return;
    const handler = (event) => handleMessage(event);
    attached.set(port, handler);
    try {
      port.onmidimessage = handler;
    } catch {
      /* frozen port */
    }
    if (typeof port.addEventListener === "function") port.addEventListener("midimessage", handler);
  };

  const detachInput = (port) => {
    const handler = attached.get(port);
    if (!handler) return;
    attached.delete(port);
    if (port.onmidimessage === handler) {
      try {
        port.onmidimessage = null;
      } catch {
        /* frozen port */
      }
    }
    if (typeof port.removeEventListener === "function") port.removeEventListener("midimessage", handler);
  };

  const syncInputs = () => {
    if (!midiAccess?.inputs) return;
    const ports = Array.from(midiAccess.inputs.values());
    for (const port of ports) attachInput(port);
    for (const port of Array.from(attached.keys())) if (!ports.includes(port)) detachInput(port);
  };

  const handleStateChange = () => {
    if (destroyed) return;
    syncInputs();
    announceDevices();
  };

  const bindAccess = () => {
    if (!midiAccess) return;
    syncInputs();
    stateHandler = handleStateChange;
    try {
      midiAccess.onstatechange = stateHandler;
    } catch {
      /* ignore */
    }
    if (typeof midiAccess.addEventListener === "function") {
      midiAccess.addEventListener("statechange", stateHandler);
      savedInputs.push([midiAccess, stateHandler]);
    }
  };
  bindAccess();
  /** Give the input its MIDI access later: the browser may ask the person for permission, and the page must not wait for the answer. */
  const setMidiAccess = (access) => {
    if (destroyed || !access || midiAccess) return;
    midiAccess = access;
    bindAccess();
    announceDevices();
  };

  // ---- keyboard --------------------------------------------------------------
  const handleKey = (event) => {
    if (destroyed || event?.repeat) return;
    const tag = event?.target?.tagName;
    if (tag && FORM_TAGS.has(String(tag).toUpperCase())) return;
    const code = event?.code;
    if (!code) return;
    const time = (event?.timeStamp ?? 0) / 1000;
    if (event.type === "keydown") {
      const pad = Object.prototype.hasOwnProperty.call(current.keys, code) ? current.keys[code] : null;
      if (Number.isInteger(pad) && pad >= 0 && pad < PAD_COUNT) {
        emit("pad-on", { type: "pad-on", pad, velocity: 1, time, source: "keyboard" });
        return;
      }
      const delta = Object.prototype.hasOwnProperty.call(current.bankKeys, code) ? current.bankKeys[code] : null;
      if (delta !== null) {
        emit("bank", { type: "bank", delta, time, source: "keyboard" });
        return;
      }
      if (code === current.panicKey) emit("panic", { type: "panic", time, source: "keyboard" });
    } else if (event.type === "keyup") {
      const pad = Object.prototype.hasOwnProperty.call(current.keys, code) ? current.keys[code] : null;
      if (Number.isInteger(pad) && pad >= 0 && pad < PAD_COUNT) {
        emit("pad-off", { type: "pad-off", pad, time, source: "keyboard" });
      }
    }
  };

  if (keyboardTarget) {
    keyboardTarget.addEventListener("keydown", handleKey);
    keyboardTarget.addEventListener("keyup", handleKey);
  }

  // ---- public surface --------------------------------------------------------
  const cancelLearn = () => {
    if (!learn) return;
    learn = null;
    emit("learn-state", { type: "learn-state", active: false, nextPad: null });
  };

  const startLearn = () => {
    if (destroyed) return;
    learn = { nextPad: 0 };
    emit("learn-state", { type: "learn-state", active: true, nextPad: 0 });
  };

  const resetMap = () => {
    cancelLearn();
    current = copyMapping(mapping);
    try {
      storage?.removeItem?.(STORAGE_KEY);
    } catch {
      /* ignore */
    }
  };

  const destroy = () => {
    if (destroyed) return;
    destroyed = true;
    learn = null;
    for (const port of Array.from(attached.keys())) detachInput(port);
    if (midiAccess && stateHandler) {
      if (midiAccess.onstatechange === stateHandler) {
        try {
          midiAccess.onstatechange = null;
        } catch {
          /* ignore */
        }
      }
      for (const [t, h] of savedInputs) {
        if (t === midiAccess) t.removeEventListener("statechange", h);
      }
    }
    savedInputs.length = 0;
    if (keyboardTarget) {
      keyboardTarget.removeEventListener("keydown", handleKey);
      keyboardTarget.removeEventListener("keyup", handleKey);
    }
  };

  Object.defineProperties(target, {
    getDevices: { value: getDevices, enumerable: true },
    setMidiAccess: { value: setMidiAccess, enumerable: true },
    startLearn: { value: startLearn, enumerable: true },
    cancelLearn: { value: cancelLearn, enumerable: true },
    resetMap: { value: resetMap, enumerable: true },
    destroy: { value: destroy, enumerable: true },
    mapping: { get: () => current, enumerable: true },
  });

  return target;
}
