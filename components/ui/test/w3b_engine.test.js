// W3b engine: per-pad filter, ADSR, per-hit pitch/cutoff, mute groups (a recording fake of the audio graph).
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createEngine, DEFAULT_PAD } from "../engine.js";

const chops = JSON.parse(readFileSync(new URL("../../../contracts/fixtures/chops.sample.json", import.meta.url)));

class Param { constructor(v = 1) { this.value = v; this.calls = []; }
  setValueAtTime(v, t) { this.calls.push(["set", v, t]); } linearRampToValueAtTime(v, t) { this.calls.push(["ramp", v, t]); }
  cancelScheduledValues() {} }
class Node { constructor() { this.to = []; } connect(n) { this.to.push(n); return n; } }
class Source extends Node { constructor(log) { super(); this.log = log; this.playbackRate = new Param(); this.stops = []; }
  start(when, offset, duration) { this.log.push({ when, offset, duration, node: this }); } stop(t) { this.stops.push(t); } }
class Biquad extends Node { constructor() { super(); this.frequency = new Param(0); this.Q = new Param(0); this.type = "lowpass"; } }
class Ctx { constructor() { this.log = []; this.filters = []; this.gains = []; this.currentTime = 10; this.sampleRate = 44100; this.destination = new Node(); }
  createBufferSource() { return new Source(this.log); }
  createGain() { const g = new Node(); g.gain = new Param(); this.gains.push(g); return g; }
  createBiquadFilter() { const f = new Biquad(); this.filters.push(f); return f; } }
class Buf { constructor(duration = 30) { this.numberOfChannels = 1; this.sampleRate = 44100; this.duration = duration; this.length = 100;
  this.data = new Float32Array(100); this.data[3] = 0.891; } getChannelData() { return this.data; } }
const rig = (opts = {}) => { const ctx = new Ctx(); return { ctx, eng: createEngine({ ctx, buffer: new Buf(), chops, ...opts }) }; };
const calls = (g) => g.gain.calls;
const near = (a, b, msg = "") => assert.ok(Math.abs(a - b) < 1e-6, `${msg} ${a} vs ${b}`);
const dur0 = chops.chops[0].end - chops.chops[0].start;

test("default pad: no filter node is created and the gain automation is exactly today's", () => {
  const { ctx, eng } = rig();
  eng.trigger(0, 1, 10);
  assert.equal(ctx.filters.length, 0);
  const c = calls(ctx.gains[0]);
  assert.deepEqual(c.map(([op]) => op), ["set", "ramp", "set", "ramp"]);
  near(c[1][1], 1);
  assert.ok(Math.abs(c[2][2] - (10 + dur0 - DEFAULT_PAD.release)) < 1e-9);
  assert.equal(c[2][1], c[1][1], "held at the level until the final fade");
  assert.equal(c[3][1], 1e-6);
  assert.equal(ctx.log[0].node.to[0], ctx.gains[0], "source goes straight to the gain");
});

test("filter on: one biquad per voice between source and gain, Q in dB for lp/hp and linear for bp", () => {
  const { ctx, eng } = rig();
  eng.setPadParams(0, { filter: { type: "lp", cutoff: 1500, q: 2 } });
  eng.trigger(0, 1, 10);
  eng.trigger(0, 1, 10.5);
  assert.equal(ctx.filters.length, 2, "a filter per voice");
  const f = ctx.filters[0];
  assert.equal(f.type, "lowpass");
  assert.equal(f.frequency.value, 1500);
  assert.ok(Math.abs(f.Q.value - 20 * Math.log10(2)) < 1e-9);
  assert.equal(ctx.log[0].node.to[0], f, "source -> filter");
  assert.equal(f.to[0], ctx.gains[0], "filter -> gain");
  eng.setPadParams(1, { filter: { type: "bp", cutoff: 800, q: 4 } });
  eng.trigger(1, 1, 11);
  assert.equal(ctx.filters[2].type, "bandpass");
  assert.equal(ctx.filters[2].Q.value, 4);
  eng.setPadParams(2, { filter: { type: "hp", cutoff: 90, q: 0.7 } });
  eng.trigger(2, 1, 12);
  assert.equal(ctx.filters[3].type, "highpass");
});

test("a cutoff above Nyquist is held below it", () => {
  const { ctx, eng } = rig();
  eng.setPadParams(0, { filter: { type: "lp", cutoff: 20000, q: 1 } });
  eng.trigger(0, 1, 10);
  assert.ok(ctx.filters[0].frequency.value <= 44100 * 0.45);
});

test("ADSR: attack to level, decay to level * sustain, then the release from the sustain level", () => {
  const { ctx, eng } = rig();
  eng.setPadParams(0, { attack: 0.01, decay: 0.1, sustain: 0.5, release: 0.02 });
  eng.trigger(0, 1, 10);
  const c = calls(ctx.gains[0]);
  assert.deepEqual(c.slice(0, 3).map(([op]) => op), ["set", "ramp", "ramp"]);
  near(c[1][1], 1); assert.ok(Math.abs(c[1][2] - 10.01) < 1e-9);
  near(c[2][1], 0.5); assert.ok(Math.abs(c[2][2] - 10.11) < 1e-9, "decay ends at attack + decay");
  const hold = c[3];
  assert.equal(hold[0], "set"); near(hold[1], 0.5); assert.ok(Math.abs(hold[2] - (10 + dur0 - 0.02)) < 1e-9);
  assert.deepEqual(c[4], ["ramp", 1e-6, 10 + dur0]);
});

test("ADSR: a release that starts during the decay continues from the value the decay has reached", () => {
  const { ctx, eng } = rig();
  eng.setPadParams(0, { attack: 0.001, decay: 5, sustain: 0, release: 0.01 });   // the slice is shorter than the decay
  eng.trigger(0, 1, 10);
  const c = calls(ctx.gains[0]);
  const tr = dur0 - 0.01;
  const want = 1 - (tr - 0.001) / 5;   // linear decay from 1 toward 0
  const hold = c.find(([op, , t]) => op === "set" && Math.abs(t - (10 + tr)) < 1e-9);
  assert.ok(hold && Math.abs(hold[1] - want) < 1e-6, `release starts at ${hold?.[1]}, decay has reached ${want}`);
  assert.ok(c.every(([, , t], i) => i === 0 || t >= c[i - 1][2] - 1e-12), "events stay in time order");
});

test("decay 0 with a sustain below 1 drops to the sustain level right after the attack", () => {
  const { ctx, eng } = rig();
  eng.setPadParams(0, { sustain: 0.25, attack: 0.002 });
  eng.trigger(0, 1, 10);
  const c = calls(ctx.gains[0])[2];
  assert.equal(c[0], "set"); near(c[1], 0.25); assert.ok(Math.abs(c[2] - 10.002) < 1e-9);
});

test("a hit's own pitch and cutoff (16 levels) add to the pad's pitch and switch a lowpass on", () => {
  const { ctx, eng } = rig();
  eng.setPadParams(0, { pitch: 2 });
  eng.trigger(0, 1, 10, 0, { pitch: 10 });
  assert.ok(Math.abs(ctx.log[0].node.playbackRate.value - 2) < 1e-9, "12 semitones up");
  assert.equal(ctx.filters.length, 0);
  eng.trigger(0, 1, 10.2, 0, { cutoff: 700 });
  assert.equal(ctx.filters[0].type, "lowpass");
  assert.equal(ctx.filters[0].frequency.value, 700);
});
