// CHOP-13 step B, engine: a kept pad plays with its own params and its own buffer, is silent (never the song) until that buffer is
// bound, and the main buffer can be swapped under a running engine (setBuffer) without touching the kept pads.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createEngine, DEFAULT_PAD } from "../engine.js";

const chops = JSON.parse(readFileSync(new URL("../../../contracts/fixtures/chops.sample.json", import.meta.url)));
class Param { constructor(v = 1) { this.value = v; } setValueAtTime() {} linearRampToValueAtTime() {} cancelScheduledValues() {} }
class Node { connect(n) { return n; } }
class Source extends Node { constructor(log) { super(); this.log = log; this.playbackRate = new Param(); }
  start(when, offset, duration) { this.log.push({ when, offset, duration, buffer: this.buffer, rate: this.playbackRate.value }); } stop() {} }
class Ctx { constructor() { this.log = []; this.currentTime = 10; this.sampleRate = 44100; this.destination = new Node(); }
  createBufferSource() { return new Source(this.log); } createGain() { const g = new Node(); g.gain = new Param(); return g; }
  createBiquadFilter() { const f = new Node(); f.frequency = new Param(); f.Q = new Param(); return f; } }
class Buf { constructor(peak = 0.891, duration = 30) { this.numberOfChannels = 1; this.sampleRate = 44100; this.duration = duration; this.length = 100;
  this.data = new Float32Array(100); this.data[3] = peak; } getChannelData() { return this.data; } }
const KEPT = { start: 0, end: 1.5, src: "/media/_mega/m1/samples/0123456789abcdef.wav", kept: true, params: { ...DEFAULT_PAD, pitch: 12 } };

test("a kept pad plays with override.params, the pad next to it with pads[pad]", () => {
  const ctx = new Ctx(), main = new Buf(), own = new Buf(0.5, 1.5);
  const eng = createEngine({ ctx, buffer: main, chops });
  eng.setPadParams(0, { pitch: -12 });                       // the shared sound of pad 0 (other banks)
  eng.setOverride(0, 0, KEPT);
  eng.setPadBuffer(0, 0, own, { whole: true, gain: 1, follow: false });
  eng.trigger(0, 1, 10, 0);                                  // the kept pad on bank 0
  eng.trigger(0, 1, 11, 1);                                  // pad 0 of bank 1: a grid chop, shared params
  assert.equal(ctx.log[0].buffer, own);
  assert.equal(ctx.log[0].rate, 2, "kept: its own pitch (+12 st)");
  assert.equal(ctx.log[1].buffer, main);
  assert.equal(ctx.log[1].rate, 0.5, "free: pads[0] (-12 st)");
  assert.equal(eng.getSlice(0, 0).kept, true);
  assert.equal(eng.getSlice(1, 0).kept, undefined);
});

test("a kept pad whose sample is not bound yet is silent, never the song", () => {
  const ctx = new Ctx(), eng = createEngine({ ctx, buffer: new Buf(), chops });
  eng.setOverride(0, 2, KEPT);
  eng.trigger(2, 1, 10, 0);
  assert.equal(ctx.log.length, 0);
  eng.setPadBuffer(0, 2, new Buf(0.4, 1.5), { whole: true, gain: 1, follow: false });
  eng.trigger(2, 1, 10, 0);
  assert.equal(ctx.log.length, 1);
});

test("setBuffer swaps the main buffer (and its level) under the engine; kept pads keep theirs", () => {
  const ctx = new Ctx(), a = new Buf(0.891), b = new Buf(0.2), own = new Buf(0.5, 1.5);
  const eng = createEngine({ ctx, buffer: a, chops });
  eng.setOverride(0, 0, KEPT);
  eng.setPadBuffer(0, 0, own, { whole: true, gain: 1, follow: false });
  eng.setBuffer(b);
  assert.equal(eng.buffer, b);
  eng.trigger(1, 1, 10, 0);
  eng.trigger(0, 1, 10, 0);
  assert.equal(ctx.log[0].buffer, b, "a free pad cuts the new song");
  assert.equal(ctx.log[1].buffer, own, "the kept pad still plays its own sample");
});

test("bank count: setBank may go past the song's own banks once kept pads need them", () => {
  const eng = createEngine({ ctx: new Ctx(), buffer: new Buf(), chops });
  const n = chops.banks.length;
  eng.setBank(n);
  assert.equal(eng.bank, 0, "clamped to the chop table by default");
  eng.setBankCount(n + 2);
  eng.setBank(n + 1);
  assert.equal(eng.bank, n + 1);
});
