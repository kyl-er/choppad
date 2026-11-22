// W3b sampler core, second slice: project tempo, filter/ADSR, mute groups, 16 levels, resample source, song mode.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { initialState, projectBpm, reduce } from "../actions.js";
import { createSequencer } from "../sequencer.js";

const chops = JSON.parse(readFileSync(new URL("../../../contracts/fixtures/chops.sample.json", import.meta.url)));
const fresh = (project) => initialState({ project, chops });
const run = (s, ...acts) => acts.reduce(reduce, s);

// ---------------------------------------------------------------- project tempo
test("projectBpm is the song's own tempo until a project tempo is set", () => {
  let s = fresh();
  assert.equal(s.project.tempo, undefined, "old projects carry no tempo key");
  assert.equal(projectBpm(s), chops.bpm);
  s = run(s, { type: "tempo.set", bpm: 92 });
  assert.equal(projectBpm(s), 92);
  s = run(s, { type: "tempo.set", bpm: null });
  assert.equal(projectBpm(s), chops.bpm);
});

test("a saved tempo with an out-of-range bpm is dropped on load", () => {
  assert.equal(fresh({ tempo: { bpm: 3 } }).project.tempo, undefined);
  assert.deepEqual(fresh({ tempo: { bpm: 90, follow: false } }).project.tempo, { bpm: 90, follow: false });
});

test("sequencer.setBpm while playing keeps the loop position and changes the pace", () => {
  const ctx = { currentTime: 0 }; const plays = [];
  const seq = createSequencer({ ctx, play: (pad, vel, when) => plays.push({ pad, when }), click() {}, setTimer: () => 1, clearTimer() {} });
  const s = seq.state; s.bpm = 120; s.bars = 1; s.metronome = false;
  s.pattern = { bars: 1, events: [{ beat: 0, pad: 0, bank: 0, vel: 1, pass: 0 }] };
  seq.start();
  while (ctx.currentTime < 1.0) { ctx.currentTime += 0.02; seq.tick(); }   // 1 s at 120 BPM = beat 1.9
  const before = seq.position();
  seq.setBpm(60);
  assert.ok(Math.abs(seq.position() - before) < 1e-6, "same place in the loop");
  while (ctx.currentTime < 8) { ctx.currentTime += 0.02; seq.tick(); }
  const gaps = plays.slice(1).map((p, i) => p.when - plays[i].when);
  assert.ok(Math.abs(gaps[gaps.length - 1] - 4) < 1e-9, `a 4-beat loop at 60 BPM lasts 4 s, got ${gaps}`);
});
