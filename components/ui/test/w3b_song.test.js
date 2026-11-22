// W3b song mode: the timeline of a chain, the sequencer playing it bar-accurate without a gap, MIDI of the whole chain.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createSequencer, patternToMidi } from "../sequencer.js";
import { initialState, reduce, songTimeline, timelineEvents } from "../actions.js";

const chops = JSON.parse(readFileSync(new URL("../../../contracts/fixtures/chops.sample.json", import.meta.url)));
const run = (s, ...acts) => acts.reduce(reduce, s);
const hit = (beat, pad, extra = {}) => ({ beat, pad, bank: 0, vel: 1, pass: 1, ...extra });

// pattern A (1 bar): kick on 1; pattern B (2 bars): snare on bar 2 beat 1. Chain A x2, B x1, A x1 = 5 bars
function songState() {
  let s = initialState({ chops, project: { pattern: { bars: 1, events: [hit(0, 0)] } } });
  s = run(s, { type: "song.addPattern", bars: 2 }, { type: "pattern.addEvent", event: hit(4, 5) }, { type: "song.selectPattern", id: "p1" },
    { type: "song.setChain", chain: [{ pattern: "p1", repeats: 2 }, { pattern: "p2", repeats: 1 }, { pattern: "p1", repeats: 1 }] });
  return s;
}

test("the timeline lists every repeat of every chain entry, in beats", () => {
  const s = songState();
  assert.equal(songTimeline(s.project, 4).chain, false, "pattern mode: just the edited pattern");
  const tl = songTimeline(s.project, 4, { force: true });
  assert.equal(tl.chain, true);
  assert.equal(tl.total, 5 * 4);
  assert.deepEqual(tl.segments.map((g) => [g.id, g.start, g.len]), [["p1", 0, 4], ["p1", 4, 4], ["p2", 8, 8], ["p1", 16, 4]]);
  assert.deepEqual(timelineEvents(tl).map((e) => [e.pad, e.beat]), [[0, 0], [0, 4], [5, 12], [0, 16]]);
  const song = run(s, { type: "song.setMode", mode: "song" });
  assert.equal(songTimeline(song.project, 4).chain, true);
  assert.equal(songTimeline(run(song, { type: "song.setChain", chain: [] }).project, 4).chain, false, "an empty chain plays the edited pattern");
});

test("hits past the end of a pattern do not leak into the next one", () => {
  let s = songState();
  s = run(s, { type: "song.selectPattern", id: "p1" });
  const p = { ...s.project, song: { ...s.project.song, patterns: s.project.song.patterns.map((x) => (x.id === "p1" ? { ...x, events: [hit(0, 0), hit(9, 3)] } : x)) } };
  assert.deepEqual(timelineEvents(songTimeline(p, 4, { force: true })).filter((e) => e.pad === 3), []);
});

test("the sequencer plays the chain on the beat clock: each pattern starts exactly on its bar and the chain loops with no gap", () => {
  const s = songState();
  const ctx = { currentTime: 0 }; const plays = [];
  const seq = createSequencer({ ctx, play: (pad, vel, when) => plays.push({ pad, when }), click() {}, setTimer: () => 1, clearTimer() {} });
  seq.state.bpm = 120; seq.state.metronome = false; seq.state.bars = 1;
  seq.setSong(songTimeline(run(s, { type: "song.setMode", mode: "song" }).project, 4));
  assert.equal(seq.loopBeats(), 20);
  seq.start();
  while (ctx.currentTime < 25) { ctx.currentTime += 0.02; seq.tick(); }
  const t0 = 0.05, beat = 0.5;
  const at = (n) => plays.filter((p) => Math.abs(p.when - (t0 + n * beat)) < 1e-9).map((p) => p.pad);
  assert.deepEqual(at(0), [0]); assert.deepEqual(at(4), [0]); assert.deepEqual(at(12), [5]); assert.deepEqual(at(16), [0]);
  assert.deepEqual(at(20), [0], "the chain starts over with no gap");
  assert.deepEqual(at(24), [0]); assert.deepEqual(at(32), [5], "second pass: pattern B's snare");
  const whens = plays.map((p) => (p.when - t0) / beat);
  assert.ok(whens.every((b, i) => i === 0 || b > whens[i - 1]), "in order, none twice");
});

test("locate says which chain entry and where in its pattern the clock is", () => {
  const ctx = { currentTime: 0 };
  const seq = createSequencer({ ctx, play() {}, click() {}, setTimer: () => 1, clearTimer() {} });
  seq.state.bpm = 120; seq.state.metronome = false;
  seq.setSong(songTimeline(songState().project, 4, { force: true }));
  seq.start();
  ctx.currentTime = 0.05 + 10 * 0.5;   // beat 10: second bar of pattern B
  const w = seq.locate();
  assert.equal(w.id, "p2"); assert.equal(w.index, 2); assert.ok(Math.abs(w.inPattern - 2) < 1e-9);
});

test("recording is off while a chain plays", () => {
  const ctx = { currentTime: 0 };
  const seq = createSequencer({ ctx, play() {}, click() {}, setTimer: () => 1, clearTimer() {} });
  seq.setSong(songTimeline(songState().project, 4, { force: true }));
  seq.toggleRecord();
  assert.equal(seq.record(1, 0, 1), null);
});

test("MIDI of the whole chain: hits at their place in the arrangement, with their velocity", () => {
  const tl = songTimeline(songState().project, 4, { force: true });
  const m = patternToMidi({ bars: tl.total / 4, events: timelineEvents(tl) }, { bpm: 100 });
  const ons = []; let t = 0;
  for (let i = 22; i < m.length - 3;) {   // walk the track: delta-time, status, note, velocity
    let d = 0; while (m[i] & 0x80) d = (d << 7) | (m[i++] & 0x7f); d = (d << 7) | m[i++];
    t += d;
    if (m[i] === 0x99) { ons.push([m[i + 1] - 36, t / 480]); i += 3; } else if (m[i] === 0x89) i += 3;
    else if (m[i] === 0xff) i += 3 + m[i + 2]; else break;   // meta event: tempo, time signature, end of track
  }
  assert.deepEqual(ons, [[0, 0], [0, 4], [5, 12], [0, 16]]);
});
