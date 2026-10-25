// midi: Web MIDI + keyboard -> pad events. See contracts/README.md §4.
export const defaultMapping = {
  notes: Object.fromEntries(Array.from({ length: 16 }, (_, i) => [36 + i, i])),
  keys: { z: 0, x: 1, c: 2, v: 3, a: 4, s: 5, d: 6, f: 7, q: 8, w: 9, e: 10, r: 11, 1: 12, 2: 13, 3: 14, 4: 15 },
  bankKeys: { "[": -1, "]": +1 },
};

/** Parse one raw MIDI message (Uint8Array) into a pad event, or null if unmapped/irrelevant. */
export function parseMidiMessage(data, mapping = defaultMapping, time = 0) {
  throw new Error("not implemented: midi.parseMidiMessage");
}

/** Listen on every MIDI input (incl. hot-plugged ones) and on keyboardTarget; return an EventTarget of pad events. */
export function createInput({ midiAccess = null, keyboardTarget = null, mapping = defaultMapping } = {}) {
  throw new Error("not implemented: midi.createInput");
}
