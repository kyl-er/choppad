// sequencer: MPC-style pattern recorder/looper on the AudioContext clock (lookahead scheduler).
// Pattern = { bars, events: [{ beat, pad, bank, vel, pass }] } with `beat` measured from the loop start.

export const QUANT = { off: 0, "1/8": 0.5, "1/16": 0.25 };

export function quantize(beat, q, loopBeats) {
  if (!q) return beat;
  const b = Math.round(beat / q) * q;
  return b >= loopBeats - 1e-9 ? 0 : b;
}

/** Swing delays every off-grid step (odd multiples of `q`) by swing * q beats (swing 0..0.5). */
export function swungBeat(beat, q, swing) {
  if (!q || !swing) return beat;
  const step = Math.round(beat / q);
  return Math.abs(beat - step * q) < 1e-6 && step % 2 === 1 ? beat + swing * q : beat;
}

/**
 * createSequencer({ ctx, play(pad, vel, when, bank), click(when, accent), schedule? })
 *  state: bpm, beatsPerBar, bars, quant, swing, metronome, recording, playing, pattern
 *  start() stop() toggleRecord() record(pad, bank, vel) undo() clear() position() tick() load(pattern)
 */
export function createSequencer({ ctx, play, click, lookahead = 0.12, interval = 25,
  setTimer = (fn, ms) => setInterval(fn, ms), clearTimer = (h) => clearInterval(h) }) {
  const s = {
    bpm: 120, beatsPerBar: 4, bars: 2, quant: "1/16", swing: 0, metronome: true,
    recording: false, playing: false, pattern: { bars: 2, events: [] },
  };
  let startTime = 0;      // ctx time of loop beat 0 (first pass)
  let scheduledUntil = 0; // absolute beats (since start) already scheduled
  let timer = null;
  let pass = 0;

  const spb = () => 60 / s.bpm;
  const loopBeats = () => s.bars * s.beatsPerBar;
  const absBeatAt = (t) => (t - startTime) / spb();

  function scheduleRange(fromAbs, toAbs) {
    const L = loopBeats();
    const q = QUANT[s.quant];
    // metronome
    if (s.metronome) {
      for (let b = Math.ceil(fromAbs); b < toAbs; b++) {   // [from, to) is half-open, so no beat is skipped or doubled
        click(startTime + b * spb(), b % s.beatsPerBar === 0);
      }
    }
    // pattern events, possibly across a loop wrap
    const firstLoop = Math.floor(fromAbs / L);
    const lastLoop = Math.floor(toAbs / L);
    for (let loop = firstLoop; loop <= lastLoop; loop++) {
      for (const e of s.pattern.events) {
        const abs = loop * L + swungBeat(e.beat, q, s.swing);
        if (abs >= fromAbs && abs < toAbs) play(e.pad, e.vel, startTime + abs * spb(), e.bank, e);
      }
    }
  }

  function tick() {
    if (!s.playing) return;
    const horizon = absBeatAt(ctx.currentTime + lookahead);
    if (horizon > scheduledUntil) {
      scheduleRange(scheduledUntil, horizon);
      scheduledUntil = horizon;
    }
  }

  return {
    state: s,
    loopBeats,
    /** Start the loop; `at` = the context time of its first beat (default: right away, 50 ms ahead). */
    start(at) {
      if (s.playing) return;
      s.playing = true;
      s.pattern.bars = s.bars;
      startTime = at ?? ctx.currentTime + 0.05;
      scheduledUntil = 0;
      tick();
      timer = setTimer(tick, interval);
    },
    stop() {
      s.playing = false;
      s.recording = false;
      if (timer != null) clearTimer(timer);
      timer = null;
    },
    toggleRecord() {
      s.recording = !s.recording;
      if (s.recording) pass++;
      if (s.recording && !s.playing) this.start();
      return s.recording;
    },
    /** Position in the loop (beats from loop start) for the UI. */
    position() {
      if (!s.playing) return 0;
      const abs = Math.max(0, absBeatAt(ctx.currentTime));
      return abs % loopBeats();
    },
    /** Record a live hit (call when a pad is played while recording). `extra` = {pitch?, cutoff?} (16 levels). */
    record(pad, bank, vel = 1, extra = null) {
      if (!s.recording || !s.playing) return null;
      const L = loopBeats();
      const raw = ((absBeatAt(ctx.currentTime) % L) + L) % L;
      const beat = quantize(raw, QUANT[s.quant], L);
      const ev = { beat, pad, bank, vel, pass };
      if (Number.isFinite(extra?.pitch)) ev.pitch = extra.pitch;
      if (Number.isFinite(extra?.cutoff)) ev.cutoff = extra.cutoff;
      s.pattern.events.push(ev);
      // A quantized hit that lands just ahead of "now" would also be played by the scheduler: the live hit already sounded.
      return ev;
    },
    undo() {
      const last = Math.max(0, ...s.pattern.events.map((e) => e.pass));
      s.pattern.events = s.pattern.events.filter((e) => e.pass !== last);
    },
    clear() { s.pattern.events = []; },
    load(pattern) {
      s.pattern = { bars: pattern?.bars ?? s.bars, events: [...(pattern?.events ?? [])] };
      s.bars = s.pattern.bars;
      pass = Math.max(0, ...s.pattern.events.map((e) => e.pass ?? 0));
    },
    tick,
    /** Change the tempo, also while playing: the loop keeps its place (beats are what is scheduled). */
    setBpm(bpm) {
      if (!(bpm > 0) || bpm === s.bpm) return;
      if (s.playing) {
        const abs = absBeatAt(ctx.currentTime);
        s.bpm = bpm;
        startTime = ctx.currentTime - abs * spb();
      } else s.bpm = bpm;
    },
  };
}

/** Standard MIDI File (type 0, 480 ppq) for a pattern: pad p -> note 36 + p, 1/16-note length. */
export function patternToMidi(pattern, { bpm, beatsPerBar = 4 }) {
  const PPQ = 480;
  const vlq = (n) => { const out = [n & 0x7f]; while ((n >>= 7)) out.unshift((n & 0x7f) | 0x80); return out; };
  const evs = [];
  for (const e of pattern.events) {
    const t = Math.round(e.beat * PPQ);
    const vel = Math.max(1, Math.min(127, Math.round(e.vel * 127)));
    evs.push({ t, data: [0x99, 36 + e.pad, vel] }, { t: t + PPQ / 4, data: [0x89, 36 + e.pad, 0] });
  }
  evs.sort((a, b) => a.t - b.t || (a.data[0] === 0x89 ? -1 : 1));
  const usPerBeat = Math.round(60e6 / bpm);
  const track = [0, 0xff, 0x51, 3, (usPerBeat >> 16) & 255, (usPerBeat >> 8) & 255, usPerBeat & 255,
    0, 0xff, 0x58, 4, beatsPerBar, 2, 24, 8];
  let last = 0;
  for (const e of evs) { track.push(...vlq(e.t - last), ...e.data); last = e.t; }
  const end = pattern.bars * beatsPerBar * PPQ;
  track.push(...vlq(Math.max(0, end - last)), 0xff, 0x2f, 0);
  const u32 = (n) => [(n >>> 24) & 255, (n >>> 16) & 255, (n >>> 8) & 255, n & 255];
  return new Uint8Array([0x4d, 0x54, 0x68, 0x64, ...u32(6), 0, 0, 0, 1, (PPQ >> 8) & 255, PPQ & 255,
    0x4d, 0x54, 0x72, 0x6b, ...u32(track.length), ...track]);
}

/** 16-bit PCM WAV bytes from an AudioBuffer. */
export function encodeWav(buffer) {
  const ch = buffer.numberOfChannels, n = buffer.length, sr = buffer.sampleRate;
  const out = new DataView(new ArrayBuffer(44 + n * ch * 2));
  const str = (o, t) => { for (let i = 0; i < t.length; i++) out.setUint8(o + i, t.charCodeAt(i)); };
  str(0, "RIFF"); out.setUint32(4, 36 + n * ch * 2, true); str(8, "WAVEfmt "); out.setUint32(16, 16, true);
  out.setUint16(20, 1, true); out.setUint16(22, ch, true); out.setUint32(24, sr, true); out.setUint32(28, sr * ch * 2, true);
  out.setUint16(32, ch * 2, true); out.setUint16(34, 16, true); str(36, "data"); out.setUint32(40, n * ch * 2, true);
  const data = Array.from({ length: ch }, (_, c) => buffer.getChannelData(c));
  let o = 44;
  for (let i = 0; i < n; i++) for (let c = 0; c < ch; c++) {
    const v = Math.max(-1, Math.min(1, data[c][i]));
    out.setInt16(o, v < 0 ? v * 0x8000 : v * 0x7fff, true);
    o += 2;
  }
  return new Uint8Array(out.buffer);
}
