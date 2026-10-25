import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { FADE_IN, FADE_OUT, createEngine, normalizationGain, resolveChop } from "../engine.js";
import { cellToPad, padToCell } from "../layout.js";

const chops = JSON.parse(readFileSync(new URL("../../../contracts/fixtures/chops.sample.json", import.meta.url)));

class FakeParam {
  constructor() { this.value = 1; this.calls = []; }
  setValueAtTime(v, t) { this.calls.push(["set", v, t]); this.value = v; }
  linearRampToValueAtTime(v, t) { this.calls.push(["ramp", v, t]); this.value = v; }
  setTargetAtTime(v, t) { this.calls.push(["target", v, t]); this.value = v; }
  exponentialRampToValueAtTime(v, t) { this.calls.push(["exp", v, t]); this.value = v; }
  cancelScheduledValues(t) { this.calls.push(["cancel", t]); }
  cancelAndHoldAtTime(t) { this.calls.push(["hold", t]); }
}
class FakeNode { connect(n) { this.out = n; return n; } disconnect() {} }
class FakeSource extends FakeNode {
  constructor(log) { super(); this.log = log; this.onended = null; this.playbackRate = new FakeParam(); }
  start(when, offset, duration) { this.log.push({ op: "start", when, offset, duration, node: this }); }
  stop(when) { this.stopped = true; this.stopAt = when; this.log.push({ op: "stop", when, node: this }); }
}
class FakeCtx {
  constructor() { this.log = []; this.currentTime = 10; this.destination = new FakeNode(); this.state = "running"; }
  createBufferSource() { return new FakeSource(this.log); }
  createGain() { const g = new FakeNode(); g.gain = new FakeParam(); return g; }
}
class FakeBuffer {
  constructor(peak = 0.5) { this.numberOfChannels = 2; this.sampleRate = 44100; this.length = 1000; this.duration = 30;
    this.data = [new Float32Array(1000), new Float32Array(1000)]; this.data[1][500] = -peak; }
  getChannelData(c) { return this.data[c]; }
}
class FakeVideo extends EventTarget {
  constructor() { super(); this._t = 0; this.seeks = []; this.paused = true; this.muted = false; this.seeking = false; }
  get currentTime() { return this._t; } set currentTime(v) { this._t = v; this.seeks.push(v); }
  play() { this.paused = false; return Promise.resolve(); } pause() { this.paused = true; }
}
const starts = (ctx) => ctx.log.filter((l) => l.op === "start");
const gainOf = (s) => s.node.out.gain;
const engine = (o = {}) => { const ctx = new FakeCtx(); return { ctx, eng: createEngine({ ctx, buffer: new FakeBuffer(), chops, ...o }) }; };

test("resolveChop maps bank/pad to chop, empty pads -> null", () => {
  assert.equal(resolveChop(chops, 0, 0).index, 0);
  assert.equal(resolveChop(chops, 1, 3).index, 19);
  assert.equal(resolveChop(chops, 2, 8).index, 40);
  assert.equal(resolveChop(chops, 2, 9), null);
  assert.equal(resolveChop(chops, 9, 0), null);
});

test("normalizationGain targets -1 dBFS and caps at 4x", () => {
  assert.ok(Math.abs(normalizationGain(new FakeBuffer(0.25)) - 0.891 / 0.25) < 0.01);
  assert.equal(normalizationGain(new FakeBuffer(0.01)), 4);
});

test("oneshot trigger plays exactly the chop slice", () => {
  const { ctx, eng } = engine(); eng.trigger(4, 1);
  const [s] = starts(ctx);
  assert.equal(s.offset, chops.chops[4].start);
  assert.ok(Math.abs(s.duration - (chops.chops[4].end - chops.chops[4].start)) < 1e-9);
});

test("every voice fades in over FADE_IN and out over FADE_OUT before the chop end", () => {
  const { ctx, eng } = engine(); eng.trigger(0, 1);
  const g = gainOf(starts(ctx)[0]); const now = ctx.currentTime; const dur = chops.chops[0].end - chops.chops[0].start;
  assert.ok(g.calls.some(([op, v, t]) => op === "set" && v === 0 && Math.abs(t - now) < 1e-6), "starts silent");
  assert.ok(g.calls.some(([op, v, t]) => op === "ramp" && v > 0 && Math.abs(t - (now + FADE_IN)) < 1e-6), "fade in");
  assert.ok(g.calls.some(([op, v, t]) => op === "ramp" && v <= 1e-3 && Math.abs(t - (now + dur)) < 1e-6), "fade out ends at chop end");
});

test("Full Level (default) ignores velocity; with it off, velocity scales gain", () => {
  const peakOf = (s) => Math.max(...gainOf(s).calls.filter((c) => c[0] === "ramp").map((c) => c[1]));
  const a = engine(); a.eng.trigger(0, 0.2); a.eng.trigger(1, 1);
  const [s1, s2] = starts(a.ctx); assert.ok(Math.abs(peakOf(s1) - peakOf(s2)) < 1e-9);
  const b = engine({ fullLevel: false }); b.eng.trigger(0, 0.2); b.eng.trigger(1, 1);
  const [t1, t2] = starts(b.ctx); assert.ok(peakOf(t1) < peakOf(t2));
});

test("choke: retrigger fades the previous voice and stops it after FADE_OUT", () => {
  const { ctx, eng } = engine(); eng.trigger(0, 1); eng.trigger(1, 1);
  const prev = starts(ctx)[0].node;
  assert.equal(prev.stopped, true);
  assert.ok(prev.stopAt >= ctx.currentTime + FADE_OUT - 1e-6);
  assert.ok(prev.out.gain.calls.some(([op, v]) => op === "ramp" && v <= 1e-3));
});

test("gate mode: release stops the voice", () => {
  const { ctx, eng } = engine({ mode: "gate", choke: false }); eng.trigger(2, 1); eng.release(2);
  assert.equal(starts(ctx)[0].node.stopped, true);
});

test("video is muted and follows the chop", () => {
  const video = new FakeVideo(); const { eng } = engine({ video });
  assert.equal(video.muted, true);
  eng.trigger(5, 1);
  assert.equal(video.currentTime, chops.chops[5].start); assert.equal(video.paused, false);
});

test("seeks are coalesced while the video is seeking; latest target wins", () => {
  const video = new FakeVideo(); const { eng } = engine({ video });
  eng.trigger(0, 1); video.seeking = true;
  for (const p of [1, 2, 3, 4, 5]) eng.trigger(p, 1);
  assert.equal(video.seeks.length, 1);
  video.seeking = false; video.dispatchEvent(new Event("seeked"));
  assert.equal(video.seeks.length, 2); assert.equal(video.currentTime, chops.chops[5].start);
});

test("setChops hot-swaps the grid", () => {
  const { ctx, eng } = engine();
  const swapped = structuredClone(chops); swapped.chops[0].start = 0.123; swapped.chops[0].end = 0.5;
  eng.setChops(swapped); eng.trigger(0, 1);
  assert.equal(starts(ctx)[0].offset, 0.123);
});

test("stopAll stops every active voice", () => {
  const { ctx, eng } = engine({ choke: false }); eng.trigger(0, 1); eng.trigger(1, 1); eng.stopAll();
  assert.ok(starts(ctx).every((s) => s.node.stopped));
});

test("banks switch and clamp; empty pads are silent", () => {
  const { ctx, eng } = engine();
  eng.setBank(2); assert.equal(eng.bank, 2);
  eng.setBank(99); assert.equal(eng.bank, 2);
  eng.setBank(-1); assert.equal(eng.bank, 0);
  eng.setBank(2); eng.trigger(15, 1); assert.equal(starts(ctx).length, 0);
});

test("layout: pad 0 bottom-left, pad 15 top-right, round-trips", () => {
  assert.deepEqual(padToCell(0), { row: 3, col: 0 });
  assert.deepEqual(padToCell(15), { row: 0, col: 3 });
  for (let p = 0; p < 16; p++) { const { row, col } = padToCell(p); assert.equal(cellToPad(row, col), p); }
});
