import { test } from "node:test";
import assert from "node:assert/strict";
import { STORAGE_KEY, createInput, defaultMapping, parseMidiMessage } from "../midi.js";

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

class FakeInput extends EventTarget { constructor(id, name = id) { super(); this.id = id; this.name = name; this.state = "connected"; this.onmidimessage = null; } }
class FakeAccess extends EventTarget {
  constructor(inputs) { super(); this.inputs = new Map(inputs.map((i) => [i.id, i])); this.onstatechange = null; }
}
class FakeStorage { constructor() { this.m = new Map(); } getItem(k) { return this.m.has(k) ? this.m.get(k) : null; } setItem(k, v) { this.m.set(k, String(v)); } removeItem(k) { this.m.delete(k); } }
const send = (input, ...b) => {
  const ev = Object.assign(new Event("midimessage"), { data: msg(...b), timeStamp: 5 });
  input.onmidimessage ? input.onmidimessage(ev) : input.dispatchEvent(ev);
};
const plug = (access, input) => {
  access.inputs.set(input.id, input);
  const ev = Object.assign(new Event("statechange"), { port: Object.assign(input, { type: "input", state: "connected" }) });
  access.onstatechange ? access.onstatechange(ev) : access.dispatchEvent(ev);
};
const TYPES = ["pad-on", "pad-off", "bank", "panic", "devices", "learn-state"];
const collect = (target) => { const got = []; for (const t of TYPES) target.addEventListener(t, (e) => got.push(e.detail)); return got; };

test("createInput forwards MIDI from existing inputs", () => {
  const a = new FakeInput("a"); const got = collect(createInput({ midiAccess: new FakeAccess([a]) }));
  send(a, 0x90, 37, 64); send(a, 0x80, 37, 0);
  assert.deepEqual(got.filter((e) => e.type.startsWith("pad")).map((e) => [e.type, e.pad]), [["pad-on", 1], ["pad-off", 1]]);
});

test("hot-plugged input is picked up and announced in devices", () => {
  const access = new FakeAccess([]); const input = createInput({ midiAccess: access }); const got = collect(input);
  plug(access, new FakeInput("b", "Pixel 11 Pro XL MIDI 1"));
  send(access.inputs.get("b"), 0x90, 36, 100);
  assert.equal(got.filter((e) => e.type === "pad-on").length, 1);
  assert.ok(got.some((e) => e.type === "devices" && e.devices.some((d) => d.name === "Pixel 11 Pro XL MIDI 1")));
  assert.deepEqual(input.getDevices().map((d) => d.id), ["b"]);
});

const key = (t, type, code, extra = {}) => t.dispatchEvent(Object.assign(new Event(type), { code, key: "?", repeat: false, target: null, ...extra }));

test("keyboard uses KeyboardEvent.code: KeyZ -> pad 0, repeats ignored, keyup -> pad-off", () => {
  const kb = new EventTarget(); const got = collect(createInput({ keyboardTarget: kb }));
  key(kb, "keydown", "KeyZ"); key(kb, "keydown", "KeyZ", { repeat: true }); key(kb, "keyup", "KeyZ");
  assert.deepEqual(got.map((e) => [e.type, e.pad, e.source]), [["pad-on", 0, "keyboard"], ["pad-off", 0, "keyboard"]]);
});

test("keys typed into form fields are ignored", () => {
  const kb = new EventTarget(); const got = collect(createInput({ keyboardTarget: kb }));
  key(kb, "keydown", "KeyZ", { target: { tagName: "INPUT" } });
  key(kb, "keydown", "KeyX", { target: { tagName: "TEXTAREA" } });
  assert.equal(got.length, 0);
});

test("bank and panic keys", () => {
  const kb = new EventTarget(); const got = collect(createInput({ keyboardTarget: kb }));
  key(kb, "keydown", "BracketRight"); key(kb, "keydown", "BracketLeft"); key(kb, "keydown", "Space");
  assert.deepEqual(got.map((e) => [e.type, e.delta]), [["bank", 1], ["bank", -1], ["panic", undefined]]);
});

test("custom mapping is honoured", () => {
  const m = { ...defaultMapping, notes: { 60: 5 } };
  assert.equal(parseMidiMessage(msg(0x90, 60, 90), m).pad, 5);
  assert.equal(parseMidiMessage(msg(0x90, 36, 90), m), null);
});

test("MIDI learn: next 16 note-ons map to pads 0..15, persisted; no pad events while learning", () => {
  const a = new FakeInput("a"); const storage = new FakeStorage();
  const input = createInput({ midiAccess: new FakeAccess([a]), storage }); const got = collect(input);
  input.startLearn();
  const notes = [60, 62, 64, 65, 67, 69, 71, 72, 74, 76, 77, 79, 81, 83, 84, 86];
  for (const n of notes) { send(a, 0x99, n, 100); send(a, 0x89, n, 0); }
  assert.equal(got.filter((e) => e.type === "pad-on").length, 0);
  const states = got.filter((e) => e.type === "learn-state");
  assert.equal(states[0].active, true); assert.equal(states[0].nextPad, 0);
  assert.equal(states.at(-1).active, false);
  const saved = JSON.parse(storage.getItem(STORAGE_KEY));
  assert.equal(saved["60"], 0); assert.equal(saved["86"], 15);
  send(a, 0x90, 64, 100);
  assert.equal(got.filter((e) => e.type === "pad-on").at(-1).pad, 2);
});

test("learned map is loaded from storage; resetMap restores defaults", () => {
  const a = new FakeInput("a"); const storage = new FakeStorage();
  storage.setItem(STORAGE_KEY, JSON.stringify({ 70: 3 }));
  const input = createInput({ midiAccess: new FakeAccess([a]), storage }); const got = collect(input);
  send(a, 0x90, 70, 100); assert.equal(got.at(-1).pad, 3);
  input.resetMap();
  assert.equal(storage.getItem(STORAGE_KEY), null);
  send(a, 0x90, 36, 100); assert.equal(got.at(-1).pad, 0);
});

test("destroy stops all events", () => {
  const a = new FakeInput("a"); const kb = new EventTarget();
  const input = createInput({ midiAccess: new FakeAccess([a]), keyboardTarget: kb }); const got = collect(input);
  input.destroy(); send(a, 0x90, 36, 100); key(kb, "keydown", "KeyZ");
  assert.equal(got.length, 0);
});
