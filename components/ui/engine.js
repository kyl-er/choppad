// ui/engine: pad -> chop playback (WebAudio, sample-accurate) + muted video visual follow. See contracts/README.md §5 (v2.1).
export const FADE_IN = 0.0025;   // s
export const FADE_OUT = 0.003;   // s
export const TARGET_PEAK = 0.891; // -1 dBFS
export const MAX_NORM_GAIN = 4;
export const DEFAULT_PAD = Object.freeze({ gainDb: 0, pitch: 0, attack: 0.0025, release: 0.003, reverse: false, mode: null });

/** Pure: chop for (bank, pad) or null when the pad is empty. = chops.chops[chops.banks[bank]?.[pad]] ?? null */
export function resolveChop(chops, bank, pad) {
  const bankList = chops.banks[bank];
  if (!bankList || !(pad in bankList)) return null;
  const idx = bankList[pad];
  return chops.chops[idx] ?? null;
}

/** Pure: gain that brings the buffer's peak to TARGET_PEAK, capped at MAX_NORM_GAIN. */
export function normalizationGain(buffer) {
  let peak = 0;
  for (let c = 0; c < buffer.numberOfChannels; c++) {
    const data = buffer.getChannelData(c);
    for (let i = 0; i < data.length; i++) {
      const abs = Math.abs(data[i]);
      if (abs > peak) peak = abs;
    }
  }
  if (peak === 0) return 1;
  const gain = TARGET_PEAK / peak;
  return Math.min(MAX_NORM_GAIN, gain);
}

/**
 * createEngine({ ctx, buffer, chops, video?, mode: "oneshot"|"gate"|"playthrough", choke: true, fullLevel: true })
 *  -> { trigger(pad, velocity), release(pad), setBank(n), bank, setChops(chops), setMode(m), setChoke(b),
 *       setFullLevel(b), stopAll() }
 * Sets video.muted = true. Video seeks are coalesced while video.seeking (latest target wins on "seeked").
 */
export function createEngine({ ctx, buffer, chops, video = null, mode = "oneshot", choke = true, fullLevel = true, output = null,
  schedule = (fn, ms) => { const t = setTimeout(fn, ms); return () => clearTimeout(t); } }) {
  let _bank = 0;
  let _chops = chops;
  let _mode = mode;
  let _choke = choke;
  let _fullLevel = fullLevel;
  const normGain = normalizationGain(buffer);
  const activeVoices = [];
  let pendingTarget = null;
  let cancelPause = null;

  // Video is visual follow only: seek (coalesced while seeking), play, and optionally pause after `pauseAfter` s.
  function followVideo(start, pauseAfter) {
    if (!video) return;
    if (cancelPause) { cancelPause(); cancelPause = null; }
    if (video.seeking) pendingTarget = start;
    else video.currentTime = start;
    video.play()?.catch?.(() => {});
    if (pauseAfter != null) cancelPause = schedule(() => { cancelPause = null; video.pause(); }, pauseAfter * 1000);
  }

  if (video) {
    video.muted = true;
    video.addEventListener("seeked", () => {
      if (pendingTarget !== null) {
        video.currentTime = pendingTarget;
        pendingTarget = null;
      }
    });
  }

  // Per-pad sound (MPC "program"): gain, pitch (semitones, resample), envelope, reverse, mode override.
  const padParams = Array.from({ length: 16 }, () => ({ ...DEFAULT_PAD }));
  // Per-pad custom slices (any part of the song on any pad): key `${bank}:${pad}` -> {start, end}
  const overrides = new Map();
  let revBuffer = null;
  function reversedBuffer() {
    if (!revBuffer) {
      revBuffer = ctx.createBuffer(buffer.numberOfChannels, buffer.length, buffer.sampleRate);
      for (let ch = 0; ch < buffer.numberOfChannels; ch++) {
        const src = buffer.getChannelData(ch), dst = revBuffer.getChannelData(ch), n = src.length;
        for (let i = 0; i < n; i++) dst[i] = src[n - 1 - i];
      }
    }
    return revBuffer;
  }
  function sliceFor(bank, pad) {
    const base = resolveChop(_chops, bank, pad);
    const o = overrides.get(`${bank}:${pad}`);
    if (!o) return base;
    return { ...(base ?? { index: -1, bar: 0, beat: 0 }), start: o.start, end: o.end, custom: true };
  }

  function chokeAll(now) {
    for (const v of activeVoices) {
      v.gain.gain.cancelScheduledValues(now);
      v.gain.gain.setValueAtTime(v.gain.gain.value, now);
      v.gain.gain.linearRampToValueAtTime(1e-6, now + FADE_OUT);
      v.source.stop(now + FADE_OUT);
    }
    activeVoices.length = 0;
  }

  function trigger(pad, velocity = 1) {
    const chop = sliceFor(_bank, pad);
    if (!chop) return;
    const P = padParams[pad];
    const mode = P.mode ?? _mode;
    const now = ctx.currentTime;
    if (_choke) chokeAll(now);

    const rate = Math.pow(2, P.pitch / 12);
    const level = normGain * Math.pow(10, P.gainDb / 20) * (_fullLevel ? 1 : Math.pow(velocity, 1.4));
    const source = ctx.createBufferSource();
    source.buffer = P.reverse ? reversedBuffer() : buffer;
    source.playbackRate.value = rate;
    const gainNode = ctx.createGain();
    source.connect(gainNode);
    gainNode.connect(output ?? ctx.destination);

    const dur = chop.end - chop.start;       // in song time
    const realDur = dur / rate;              // in wall-clock time
    const offset = P.reverse ? buffer.duration - chop.end : chop.start;
    const attack = Math.min(P.attack, realDur / 2);
    gainNode.gain.setValueAtTime(0, now);
    gainNode.gain.linearRampToValueAtTime(level, now + attack);

    if (mode === "oneshot") {
      gainNode.gain.setValueAtTime(level, now + Math.max(attack, realDur - P.release));
      gainNode.gain.linearRampToValueAtTime(1e-6, now + realDur);
      source.start(now, offset, dur);
      followVideo(chop.start, realDur);
    } else {
      source.start(now, offset); // gate: until release; playthrough: until choked
      followVideo(chop.start, null);
    }

    const voice = { source, gain: gainNode, pad, mode, release: P.release };
    activeVoices.push(voice);
    source.onended = () => { const i = activeVoices.indexOf(voice); if (i >= 0) activeVoices.splice(i, 1); };
  }

  function release(pad) {
    const now = ctx.currentTime;
    for (let i = activeVoices.length - 1; i >= 0; i--) {
      const v = activeVoices[i];
      if (v.pad !== pad || v.mode !== "gate") continue; // oneshot plays to the end; playthrough until choked
      v.gain.gain.cancelScheduledValues(now);
      v.gain.gain.setValueAtTime(v.gain.gain.value, now);
      v.gain.gain.linearRampToValueAtTime(1e-6, now + v.release);
      v.source.stop(now + v.release);
      activeVoices.splice(i, 1);
      if (video) video.pause();
    }
  }

  function setBank(n) {
    if (n < 0) n = 0;
    if (n >= _chops.banks.length) n = _bank; // clamp stays at current
    _bank = n;
  }

  function setChops(c) {
    _chops = c;
    _bank = 0;
  }

  function setMode(m) { _mode = m; }
  function setChoke(b) { _choke = b; }
  function setFullLevel(b) { _fullLevel = b; }

  function stopAll() {
    if (cancelPause) { cancelPause(); cancelPause = null; } // a pending "pause video at chop end" must not fire later
    const now = ctx.currentTime;
    for (const v of activeVoices) {
      v.gain.gain.cancelScheduledValues(now);
      v.gain.gain.setValueAtTime(v.gain.gain.value, now);
      v.gain.gain.linearRampToValueAtTime(1e-6, now + FADE_OUT);
      v.source.stop(now + FADE_OUT);
    }
    activeVoices.length = 0;
  }

  return {
    trigger, release, setBank,
    get bank() { return _bank; },
    setChops, setMode, setChoke, setFullLevel, stopAll,
    /** Merge sound params into a pad: {gainDb, pitch, attack, release, reverse, mode|null}. */
    setPadParams(pad, partial) { Object.assign(padParams[pad], partial); },
    getPadParams(pad) { return { ...padParams[pad] }; },
    /** Put a custom slice {start, end} on (bank, pad); null restores the grid chop. */
    setOverride(bank, pad, slice) { if (slice) overrides.set(`${bank}:${pad}`, { start: slice.start, end: slice.end }); else overrides.delete(`${bank}:${pad}`); },
    getOverrides() { return Object.fromEntries(overrides); },
    getSlice(bank, pad) { return sliceFor(bank, pad); },
    _ctx: ctx,
    get _mode() { return _mode; },
  };
}
