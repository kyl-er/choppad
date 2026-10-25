import { test } from "node:test";
import assert from "node:assert/strict";
import { createInput, defaultMapping, parseMidiMessage } from "../midi.js";

const msg = (...b) => Uint8Array.from(b);

test("note-on 36 vel 127 -> pad 0 full velocity", () => {
  const e = parseMidiMessage(msg(0x90, 36, 127));
  assert.equal(e.type, "pad-on"); assert.equal(e.pad, 0); assert.equal(e.velocity, 1); assert.equal(e.source, "midi");
});
test("any channel is accepted", () => assert.equal(parseMidiMessage(msg(0x99, 51, 64)).pad, 15));
test("note-on velocity 0 is pad-off", () => assert.equal(parseMidiMessage(msg(0x90, 40, 0)).type, "pad-off"));
test("note-off is pad-off", () => assert.equal(parseMidiMessage(msg(0x80, 40, 10)).type, "pad-off"));
test("unmapped note and non-note messages -> null", () => {
  assert.equal(parseMidiMessage(msg(0x90, 20, 100)), null);
  assert.equal(parseMidiMessage(msg(0xb0, 7, 100)), null);
  assert.equal(parseMidiMessage(msg(0xf8)), null); // clock
});

class FakeInput extends EventTarget { constructor(id) { super(); this.id = id; this.onmidimessage = null; } }
class FakeAccess extends EventTarget {
  constructor(inputs) { super(); this.inputs = new Map(inputs.map((i) => [i.id, i])); this.onstatechange = null; }
}
const send = (input, ...b) => {
  const ev = Object.assign(new Event("midimessage"), { data: msg(...b), timeStamp: 5 });
  input.onmidimessage ? input.onmidimessage(ev) : input.dispatchEvent(ev);
};
const collect = (target) => { const got = []; for (const t of ["pad-on", "pad-off", "bank"]) target.addEventListener(t, (e) => got.push(e.detail)); return got; };

test("createInput forwards MIDI from existing inputs", () => {
  const a = new FakeInput("a"); const got = collect(createInput({ midiAccess: new FakeAccess([a]) }));
  send(a, 0x90, 37, 64); send(a, 0x80, 37, 0);
  assert.deepEqual(got.map((e) => [e.type, e.pad]), [["pad-on", 1], ["pad-off", 1]]);
});

test("hot-plugged input is picked up", () => {
  const access = new FakeAccess([]); const got = collect(createInput({ midiAccess: access }));
  const b = new FakeInput("b"); access.inputs.set("b", b);
  const ev = Object.assign(new Event("statechange"), { port: Object.assign(b, { type: "input", state: "connected" }) });
  access.onstatechange ? access.onstatechange(ev) : access.dispatchEvent(ev);
  send(b, 0x90, 36, 100);
  assert.equal(got.length, 1);
});

const key = (t, type, k, repeat = false) => t.dispatchEvent(Object.assign(new Event(type), { key: k, repeat }));

test("keyboard fallback: z -> pad 0, repeats ignored, keyup -> pad-off", () => {
  const kb = new EventTarget(); const got = collect(createInput({ keyboardTarget: kb }));
  key(kb, "keydown", "z"); key(kb, "keydown", "z", true); key(kb, "keyup", "z");
  assert.deepEqual(got.map((e) => [e.type, e.pad, e.source]), [["pad-on", 0, "keyboard"], ["pad-off", 0, "keyboard"]]);
});

test("bank keys emit bank events", () => {
  const kb = new EventTarget(); const got = collect(createInput({ keyboardTarget: kb }));
  key(kb, "keydown", "]"); key(kb, "keydown", "[");
  assert.deepEqual(got.map((e) => [e.type, e.delta]), [["bank", 1], ["bank", -1]]);
});

test("custom mapping (MIDI learn result) is honoured", () => {
  const m = { ...defaultMapping, notes: { 60: 5 } };
  assert.equal(parseMidiMessage(msg(0x90, 60, 90), m).pad, 5);
  assert.equal(parseMidiMessage(msg(0x90, 36, 90), m), null);
});
