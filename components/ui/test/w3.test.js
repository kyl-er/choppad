// W3 sampler core: alternate pad buffers in the engine (stretched slices, other songs' audio) and kit.load pad params.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createEngine } from "../engine.js";
import { initialState, reduce } from "../actions.js";

const chops = JSON.parse(readFileSync(new URL("../../../contracts/fixtures/chops.sample.json", import.meta.url)));

class Param { constructor() { this.value = 1; this.calls = []; }
  setValueAtTime(v, t) { this.calls.push(["set", v, t]); } linearRampToValueAtTime(v, t) { this.calls.push(["ramp", v, t]); }
  cancelScheduledValues() {} }
class Node { connect(n) { this.out = n; return n; } }
class Source extends Node { constructor(log) { super(); this.log = log; this.playbackRate = new Param(); }
  start(when, offset, duration) { this.log.push({ when, offset, duration, buffer: this.buffer, node: this }); } stop() {} }
class Ctx { constructor() { this.log = []; this.currentTime = 10; this.destination = new Node(); }
  createBufferSource() { return new Source(this.log); }
  createGain() { const g = new Node(); g.gain = new Param(); return g; } }
class Buf { constructor(peak, duration) { this.numberOfChannels = 1; this.sampleRate = 44100; this.duration = duration; this.length = 100;
  this.data = new Float32Array(100); this.data[3] = peak; } getChannelData() { return this.data; } }
class Video extends EventTarget { constructor() { super(); this.seeks = []; this.seeking = false; }
  get currentTime() { return 0; } set currentTime(v) { this.seeks.push(v); } play() { return Promise.resolve(); } pause() {} }

const make = (video = null) => { const ctx = new Ctx(); const song = new Buf(0.5, 30);
  return { ctx, song, eng: createEngine({ ctx, buffer: song, chops, video }) }; };

test("a pad with a stretched buffer plays that buffer from 0 for its own length", () => {
  const { ctx, song, eng } = make();
  const alt = new Buf(0.5, 0.7);
  eng.setOverride(0, 3, { start: 5, end: 5.5, stretch: { ratio: 1.4, url: "/media/x/stretch/0123456789abcdef.wav" } });
  eng.setPadBuffer(0, 3, alt);
  eng.trigger(3, 1);
  const [s] = ctx.log;
  assert.equal(s.buffer, alt);
  assert.equal(s.offset, 0);
  assert.equal(s.duration, 0.7);
  assert.notEqual(s.buffer, song);
  assert.equal(eng.getSlice(0, 3).stretch.ratio, 1.4);
  assert.equal(eng.getPadBuffer(0, 3), alt);
  // other pads are untouched
  eng.trigger(0, 1);
  assert.equal(ctx.log[1].buffer, song);
});

test("pitch still works on top of a stretched buffer: the voice ends at length / rate", () => {
  const { ctx, eng } = make();
  eng.setOverride(0, 0, { start: 1, end: 2, stretch: { ratio: 2, url: "u" } });
  eng.setPadBuffer(0, 0, new Buf(0.5, 2));
  eng.setPadParams(0, { pitch: 12 });
  eng.trigger(0, 1);
  const [s] = ctx.log;
  assert.equal(s.node.playbackRate.value, 2);
  const g = s.node.out.gain;
  const end = g.calls.filter(([op]) => op === "ramp").pop();
  assert.ok(Math.abs(end[2] - (ctx.currentTime + 1)) < 1e-9, "2 s buffer at double rate lasts 1 s");
});

test("another song's buffer plays from the slice start and leaves the video alone", () => {
  const video = new Video();
  const { ctx, song, eng } = make(video);
  const other = new Buf(0.25, 60);
  eng.setOverride(1, 5, { start: 20, end: 20.5, song: "other" });
  eng.setPadBuffer(1, 5, other, { whole: true });
  eng.setBank(1);
  video.seeks.length = 0;
  eng.trigger(5, 1);
  const [s] = ctx.log;
  assert.equal(s.buffer, other);
  assert.equal(s.offset, 20);
  assert.ok(Math.abs(s.duration - 0.5) < 1e-9);
  assert.deepEqual(video.seeks, [], "the video shows the open song, not the other one");
  assert.ok(song !== other);
  // level normalises on the other song's own peak (0.25 -> x3.56), not the open song's (0.5)
  const peak = s.node.out.gain.calls.find(([op, v]) => op === "ramp" && v > 0.1);
  assert.ok(Math.abs(peak[1] - 0.891 / 0.25) < 0.01);
});

test("an override without stretch or song drops the alternate buffer", () => {
  const { ctx, song, eng } = make();
  eng.setOverride(0, 2, { start: 1, end: 2, stretch: { ratio: 1, url: "u" } });
  eng.setPadBuffer(0, 2, new Buf(0.5, 1));
  eng.setOverride(0, 2, { start: 1, end: 3 });
  assert.equal(eng.getPadBuffer(0, 2), null);
  eng.trigger(2, 1);
  assert.equal(ctx.log[0].buffer, song);
  eng.setOverride(0, 2, { start: 1, end: 3, song: "o" });
  eng.setPadBuffer(0, 2, new Buf(0.5, 9), { whole: true });
  eng.setOverride(0, 2, null);
  assert.equal(eng.getPadBuffer(0, 2), null);
});

test("kit.load sets the kit pads' sound params and keeps the other pads", () => {
  const s0 = initialState({ project: { pads: [{}, { pitch: 7 }] }, chops });
  const s1 = reduce(s0, { type: "kit.load", bank: 0, song: "me", pads: [
    { song_id: "o", start: 1, end: 2, params: { gainDb: -6, reverse: true, bogus: 9 } }, null, { song_id: "o", start: 2, end: 3 }] });
  assert.equal(s1.project.pads[0].gainDb, -6);
  assert.equal(s1.project.pads[0].reverse, true);
  assert.equal(s1.project.pads[0].bogus, undefined);
  assert.equal(s1.project.pads[1].pitch, 7);
  assert.equal(s1.project.pads[2].gainDb, 0);
  assert.deepEqual(Object.keys(s1.project.overrides).sort(), ["0:0", "0:2"]);
});
