import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createEngine, resolveChop } from "../engine.js";
import { cellToPad, padToCell } from "../layout.js";

const chops = JSON.parse(readFileSync(new URL("../../../contracts/fixtures/chops.sample.json", import.meta.url)));

class FakeParam { constructor() { this.value = 1; } setValueAtTime(v) { this.value = v; } setTargetAtTime(v) { this.value = v; } linearRampToValueAtTime(v) { this.value = v; } }
class FakeNode { connect(n) { this.out = n; return n; } disconnect() {} }
class FakeSource extends FakeNode {
  constructor(log) { super(); this.log = log; this.onended = null; }
  start(when, offset, duration) { this.log.push({ op: "start", when, offset, duration, node: this }); }
  stop(when) { this.stopped = true; this.log.push({ op: "stop", when, node: this }); }
}
class FakeCtx {
  constructor() { this.log = []; this.currentTime = 10; this.destination = new FakeNode(); }
  createBufferSource() { return new FakeSource(this.log); }
  createGain() { const g = new FakeNode(); g.gain = new FakeParam(); return g; }
}
class FakeVideo { constructor() { this.currentTime = 0; this.paused = true; } play() { this.paused = false; return Promise.resolve(); } pause() { this.paused = true; } }
const starts = (ctx) => ctx.log.filter((l) => l.op === "start");

test("resolveChop maps bank/pad to chop, empty pads -> null", () => {
  assert.equal(resolveChop(chops, 0, 0).index, 0);
  assert.equal(resolveChop(chops, 1, 3).index, 19);
  assert.equal(resolveChop(chops, 2, 8).index, 40);
  assert.equal(resolveChop(chops, 2, 9), null);
  assert.equal(resolveChop(chops, 9, 0), null);
});

test("oneshot trigger plays exactly the chop slice", () => {
  const ctx = new FakeCtx(); const eng = createEngine({ ctx, buffer: {}, chops });
  eng.trigger(4, 1);
  const [s] = starts(ctx);
  assert.equal(s.offset, chops.chops[4].start);
  assert.ok(Math.abs(s.duration - (chops.chops[4].end - chops.chops[4].start)) < 1e-9);
});

test("velocity scales gain", () => {
  const ctx = new FakeCtx(); const eng = createEngine({ ctx, buffer: {}, chops });
  eng.trigger(0, 0.5);
  const gain = starts(ctx)[0].node.out.gain.value;
  assert.ok(gain > 0 && gain < 1);
});

test("choke: retriggering stops the previous voice", () => {
  const ctx = new FakeCtx(); const eng = createEngine({ ctx, buffer: {}, chops, choke: true });
  eng.trigger(0, 1); eng.trigger(1, 1);
  assert.equal(starts(ctx)[0].node.stopped, true);
});

test("gate mode: release stops the voice", () => {
  const ctx = new FakeCtx(); const eng = createEngine({ ctx, buffer: {}, chops, mode: "gate", choke: false });
  eng.trigger(2, 1); eng.release(2);
  assert.equal(starts(ctx)[0].node.stopped, true);
});

test("video follows the chop", () => {
  const ctx = new FakeCtx(); const video = new FakeVideo(); const eng = createEngine({ ctx, buffer: {}, chops, video });
  eng.trigger(5, 1);
  assert.equal(video.currentTime, chops.chops[5].start); assert.equal(video.paused, false);
});

test("banks switch and clamp; empty pads are silent", () => {
  const ctx = new FakeCtx(); const eng = createEngine({ ctx, buffer: {}, chops });
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
