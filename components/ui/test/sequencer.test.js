import { test } from "node:test";
import assert from "node:assert/strict";
import { QUANT, createSequencer, encodeWav, patternToMidi, quantize, swungBeat } from "../sequencer.js";

const rig = () => {
  const ctx = { currentTime: 0 }; const plays = []; const clicks = [];
  const seq = createSequencer({ ctx, play: (pad, vel, when, bank) => plays.push({ pad, when, bank }),
    click: (when, accent) => clicks.push({ when, accent }), setTimer: () => 1, clearTimer: () => {} });
  return { ctx, seq, plays, clicks };
};
const advance = (r, to, step = 0.02) => { while (r.ctx.currentTime < to) { r.ctx.currentTime += step; r.seq.tick(); } };

test("quantize snaps to the grid and wraps the loop end to 0", () => {
  assert.equal(quantize(1.13, QUANT["1/16"], 8), 1.25);
  assert.equal(quantize(1.13, QUANT["1/8"], 8), 1.0);
  assert.equal(quantize(7.95, QUANT["1/8"], 8), 0);
  assert.equal(quantize(1.13, QUANT.off, 8), 1.13);
});

test("swing delays only off-grid steps", () => {
  assert.equal(swungBeat(1.0, 0.5, 0.2), 1.0);
  assert.equal(swungBeat(1.5, 0.5, 0.2), 1.6);
});

test("metronome clicks every beat, accent on the downbeat", () => {
  const r = rig(); r.seq.state.bpm = 120; r.seq.start(); advance(r, 2.1);
  const beats = r.clicks.filter((c) => c.when < 2.05);
  assert.equal(beats.length, 4);
  assert.deepEqual(beats.map((c) => c.accent), [true, false, false, false]);
  assert.ok(Math.abs(beats[1].when - beats[0].when - 0.5) < 1e-9);
});

test("recorded hits replay quantized on the next loop pass, with their bank", () => {
  const r = rig(); const s = r.seq.state; s.bpm = 120; s.bars = 1; s.metronome = false;
  r.seq.toggleRecord();                       // starts transport at t=0.05
  advance(r, 0.05 + 0.5 * 1.04);               // ~beat 1.04
  r.seq.record(3, 7, 1);
  assert.equal(s.pattern.events[0].beat, 1);   // 1/16 quantized
  advance(r, 0.05 + 4 * 0.5 + 0.5 + 0.2);      // into the second pass
  const hit = r.plays.find((p) => p.pad === 3);
  assert.ok(hit, "replayed");
  assert.equal(hit.bank, 7);
  assert.ok(Math.abs(hit.when - (0.05 + (4 + 1) * 0.5)) < 1e-9, `at ${hit.when}`);
});

test("undo removes the last recording pass only; clear empties", () => {
  const r = rig(); const s = r.seq.state; s.metronome = false;
  r.seq.toggleRecord(); advance(r, 0.3); r.seq.record(0, 0); r.seq.toggleRecord();
  r.seq.toggleRecord(); advance(r, 0.6); r.seq.record(1, 0); r.seq.record(2, 0);
  r.seq.undo();
  assert.deepEqual(s.pattern.events.map((e) => e.pad), [0]);
  r.seq.clear(); assert.equal(s.pattern.events.length, 0);
});

test("stop halts scheduling", () => {
  const r = rig(); r.seq.start(); advance(r, 0.5); r.seq.stop(); const n = r.clicks.length; advance(r, 3);
  assert.equal(r.clicks.length, n);
});

test("patternToMidi writes a valid SMF with one note-on per event", () => {
  const bytes = patternToMidi({ bars: 1, events: [0, 1, 2, 3].map((p) => ({ beat: p, pad: p, vel: 1 })) }, { bpm: 101 });
  assert.equal(String.fromCharCode(...bytes.slice(0, 4)), "MThd");
  assert.equal(String.fromCharCode(...bytes.slice(14, 18)), "MTrk");
  let ons = 0;
  for (let i = 0; i < bytes.length - 2; i++) if (bytes[i] === 0x99 && bytes[i + 1] >= 36 && bytes[i + 1] <= 51 && bytes[i + 2] > 0) ons++;
  assert.equal(ons, 4);
});

test("encodeWav writes a RIFF header and 16-bit samples", () => {
  const b = { numberOfChannels: 2, length: 4, sampleRate: 44100, getChannelData: () => new Float32Array([0, 0.5, -0.5, 1]) };
  const w = encodeWav(b);
  assert.equal(String.fromCharCode(...w.slice(0, 4)), "RIFF");
  assert.equal(w.length, 44 + 4 * 2 * 2);
});
