// midi: Web MIDI + keyboard -> pad events. See contracts/README.md §4 (v2).
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

/** Parse one raw MIDI message (Uint8Array) into a pad event, or null if unmapped/irrelevant. */
export function parseMidiMessage(data, mapping = defaultMapping, time = 0) {
  throw new Error("not implemented: midi.parseMidiMessage");
}

/**
 * Listen on every MIDI input (incl. hot-plugged) and on keyboardTarget. Returns an EventTarget emitting
 * pad-on / pad-off / bank / panic / devices / learn-state CustomEvents, extended with:
 *   getDevices() -> [{id, name, state}], startLearn(), cancelLearn(), resetMap(), mapping, destroy()
 * storage: localStorage-like ({getItem, setItem, removeItem}); learned note map persists under STORAGE_KEY.
 */
export function createInput({ midiAccess = null, keyboardTarget = null, mapping = defaultMapping, storage = null } = {}) {
  throw new Error("not implemented: midi.createInput");
}
