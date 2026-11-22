// W3b 16 levels: the level table, recorded events with pitch/cutoff, and what the sequencer hands the engine.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createSequencer, patternToMidi } from "../sequencer.js";
import { SIXTEEN_MODES, initialState, reduce, sixteenLevel } from "../actions.js";

const chops = JSON.parse(readFileSync(new URL("../../../contracts/fixtures/chops.sample.json", import.meta.url)));

test("the 16 levels: velocity rises, pitch is -7..+8 semitones, cutoff rises in equal ratios", () => {
  assert.deepEqual(SIXTEEN_MODES, ["velocity", "pitch", "filter"]);
  const vel = Array.from({ length: 16 }, (_, i) => sixteenLevel("velocity", i).vel);
  assert.equal(vel[0], 1 / 16); assert.equal(vel[15], 1);
  assert.ok(vel.every((v, i) => i === 0 || v > vel[i - 1]));
  const pitch = Array.from({ length: 16 }, (_, i) => sixteenLevel("pitch", i).pitch);
  assert.equal(pitch[0], -7); assert.equal(pitch[15], 8); assert.equal(pitch[7], 0);
  const cut = Array.from({ length: 16 }, (_, i) => sixteenLevel("filter", i).cutoff);
  assert.equal(cut[0], 150); assert.equal(cut[15], 16000);
  const r = cut.slice(1).map((c, i) => c / cut[i]);
  assert.ok(Math.max(...r) / Math.min(...r) < 1.02, "equal ratios between steps");
  assert.equal(sixteenLevel("pitch", 16), null);
  assert.equal(sixteenLevel("nope", 3), null);
});

test("the sequencer records a hit with its pitch / cutoff and plays it back with them", () => {
  const ctx = { currentTime: 0 }; const plays = [];
  const seq = createSequencer({ ctx, play: (pad, vel, when, bank, ev) => plays.push({ pad, vel, bank, pitch: ev.pitch, cutoff: ev.cutoff }),
    click() {}, setTimer: () => 1, clearTimer() {} });
  const s = seq.state; s.bpm = 120; s.bars = 1; s.metronome = false;
  seq.toggleRecord();
  ctx.currentTime = 0.55; seq.tick();
  const a = seq.record(4, 1, 0.8, { pitch: 3 });
  const b = seq.record(4, 1, 1, { cutoff: 900 });
  const c = seq.record(4, 1, 0.5);
  assert.equal(a.pitch, 3); assert.equal(a.cutoff, undefined);
  assert.equal(b.cutoff, 900); assert.equal(b.pitch, undefined);
  assert.equal(c.pitch, undefined);
  while (ctx.currentTime < 2.6) { ctx.currentTime += 0.02; seq.tick(); }
  const again = plays.filter((p) => p.pad === 4);
  assert.ok(again.some((p) => p.pitch === 3 && p.vel === 0.8));
  assert.ok(again.some((p) => p.cutoff === 900));
});

test("MIDI export writes the velocity of recorded 16-level hits", () => {
  const events = [0.25, 0.5, 0.75, 1].map((vel, i) => ({ beat: i, pad: 3, bank: 0, vel, pass: 1 }));
  const m = patternToMidi({ bars: 1, events }, { bpm: 100 });
  const vels = [];
  for (let i = 0; i < m.length - 2; i++) if (m[i] === 0x99 && m[i + 1] === 39) vels.push(m[i + 2]);
  assert.deepEqual(vels, [32, 64, 95, 127]);
});

test("old patterns without pitch or cutoff are untouched by the new event fields", () => {
  let s = initialState({ chops, project: { pattern: { bars: 1, events: [{ beat: 0, pad: 1, bank: 0, vel: 1, pass: 1 }] } } });
  s = reduce(s, { type: "pattern.toggleStep", pad: 1, step: 4 });
  s = reduce(s, { type: "pattern.undo" });
  assert.deepEqual(s.project.pattern.events.map((e) => Object.keys(e).sort().join()), ["bank,beat,pad,pass,vel"]);
});
