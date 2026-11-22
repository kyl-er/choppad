// fx: master sound shaping on the WebAudio graph — tone (tilt EQ), 3-band EQ, one-knob DJ filter, reverb.
// Same params drive the live bus, the offline WAV render, and the server-side kit bake (pedalboard).
export const DEFAULT_FX = Object.freeze({
  tone: 0,      // -1 dark … +1 bright (tilt EQ, ±9 dB shelves around 700 Hz)
  low: 0, mid: 0, high: 0, // 3-band EQ, dB (-12 … +12)
  filter: 0,    // -1 lowpass … 0 off … +1 highpass
  reverb: 0,    // wet mix 0 … 1
  size: 0.5,    // 0 small room … 1 big hall
});

export const PRESETS = {
  dry: { ...DEFAULT_FX },
  warm: { ...DEFAULT_FX, tone: -0.35, low: 2, reverb: 0.18, size: 0.3 },
  room: { ...DEFAULT_FX, reverb: 0.25, size: 0.35 },
  hall: { ...DEFAULT_FX, tone: 0.1, reverb: 0.4, size: 0.85 },
  lofi: { ...DEFAULT_FX, tone: -0.5, filter: -0.35, high: -4, reverb: 0.15, size: 0.4 },
  radio: { ...DEFAULT_FX, filter: 0.45, mid: 6, low: -8, high: -6, reverb: 0 },
};

const TILT_DB = 9;
const clamp = (v, a, b) => Math.min(b, Math.max(a, v));

/** Pure: tilt EQ gains for tone -1..1 -> { lowDb, highDb } (dark boosts lows and cuts highs). */
export function tiltGains(tone) {
  const t = clamp(Number(tone) || 0, -1, 1);
  return { lowDb: -t * TILT_DB, highDb: t * TILT_DB };
}

/** Pure: one-knob filter -> { type, freq } or null when centred. Exponential sweep, musical feel. */
export function filterSetting(v) {
  const x = clamp(Number(v) || 0, -1, 1);
  if (Math.abs(x) < 0.02) return null;
  if (x < 0) return { type: "lowpass", freq: 20000 * Math.pow(150 / 20000, -x) }; // 20 kHz -> 150 Hz
  return { type: "highpass", freq: 20 * Math.pow(4000 / 20, x) };                 // 20 Hz -> 4 kHz
}

/** Pure: reverb decay time in seconds for size 0..1. */
export const reverbSeconds = (size) => 0.4 + clamp(Number(size) || 0, 0, 1) * 5.6;

/** Pure: equal-power dry/wet gains for mix 0..1. */
export function wetDry(mix) {
  const m = clamp(Number(mix) || 0, 0, 1);
  return { dry: Math.cos((m * Math.PI) / 2), wet: Math.sin((m * Math.PI) / 2) };
}

// Deterministic noise so live playback and the exported WAV get the same reverb tail.
function mulberry32(seed) {
  return () => {
    seed |= 0; seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** Stereo impulse response: decorrelated noise with an exponential decay and darker late tail. */
export function makeImpulse(ctx, size) {
  const secs = reverbSeconds(size);
  const sr = ctx.sampleRate, n = Math.ceil(secs * sr);
  const ir = ctx.createBuffer(2, n, sr);
  for (let ch = 0; ch < 2; ch++) {
    const rnd = mulberry32(1234 + ch * 777);
    const d = ir.getChannelData(ch);
    let lp = 0;
    for (let i = 0; i < n; i++) {
      const t = i / n;
      const k = 0.15 + 0.8 * t; // one-pole lowpass gets heavier over the tail (air absorption)
      lp += (1 - k) * ((rnd() * 2 - 1) - lp);
      d[i] = lp * Math.pow(1 - t, 2.2) * Math.exp(-3 * t);
    }
  }
  return ir;
}

/**
 * createFx(ctx) -> { input, output, set(partial), get() }
 * input -> filter -> tilt(low/high shelf) -> EQ(low/mid/high) -> [dry | convolver -> wet] -> limiter -> output
 */
export function createFx(ctx, initial = DEFAULT_FX) {
  const input = ctx.createGain(), output = ctx.createGain();
  const filt = ctx.createBiquadFilter();
  const tiltLo = ctx.createBiquadFilter(), tiltHi = ctx.createBiquadFilter();
  const eqLo = ctx.createBiquadFilter(), eqMid = ctx.createBiquadFilter(), eqHi = ctx.createBiquadFilter();
  tiltLo.type = "lowshelf"; tiltLo.frequency.value = 700;
  tiltHi.type = "highshelf"; tiltHi.frequency.value = 700;
  eqLo.type = "lowshelf"; eqLo.frequency.value = 120;
  eqMid.type = "peaking"; eqMid.frequency.value = 1000; eqMid.Q.value = 0.8;
  eqHi.type = "highshelf"; eqHi.frequency.value = 6000;
  filt.Q.value = 0.9;
  const dry = ctx.createGain(), wet = ctx.createGain(), conv = ctx.createConvolver();
  input.connect(filt).connect(tiltLo).connect(tiltHi).connect(eqLo).connect(eqMid).connect(eqHi);
  // safety limiter: tone/EQ boosts on bass-heavy material must never clip the output
  const limiter = ctx.createDynamicsCompressor();
  limiter.threshold.value = -1.5; limiter.knee.value = 0; limiter.ratio.value = 20;
  limiter.attack.value = 0.002; limiter.release.value = 0.12;
  eqHi.connect(dry).connect(limiter);
  eqHi.connect(conv).connect(wet).connect(limiter);
  limiter.connect(output);

  const p = { ...DEFAULT_FX };
  let irSize = null;
  const smooth = (param, v) => {
    if (typeof param.setTargetAtTime === "function" && ctx.currentTime > 0) param.setTargetAtTime(v, ctx.currentTime, 0.015);
    else param.value = v;
  };

  function apply() {
    const f = filterSetting(p.filter);
    if (f) { filt.type = f.type; smooth(filt.frequency, f.freq); }
    else { filt.type = "allpass"; filt.frequency.value = 1000; }
    const { lowDb, highDb } = tiltGains(p.tone);
    smooth(tiltLo.gain, lowDb);
    smooth(tiltHi.gain, highDb);
    smooth(eqLo.gain, p.low);
    smooth(eqMid.gain, p.mid);
    smooth(eqHi.gain, p.high);
    const g = wetDry(p.reverb);
    smooth(dry.gain, g.dry);
    smooth(wet.gain, g.wet * 0.9);
    if (p.reverb > 0 && irSize !== p.size) { conv.buffer = makeImpulse(ctx, p.size); irSize = p.size; }
  }

  function set(partial) {
    for (const k of Object.keys(DEFAULT_FX)) if (partial?.[k] !== undefined) p[k] = Number(partial[k]);
    apply();
  }
  set(initial);
  return { input, output, set, get: () => ({ ...p }) };
}
