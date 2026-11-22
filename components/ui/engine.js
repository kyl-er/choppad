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

  // Scheduled hits move the video when they actually sound.
  function followAt(when, start, pauseAfter) {
    const delay = when - ctx.currentTime;
    if (!video) return;
    if (delay > 0.02) schedule(() => followVideo(start, pauseAfter), delay * 1000);
    else followVideo(start, pauseAfter);
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
  // Per-pad alternate sound source (W3): a time-stretched rendering of the slice ({whole: false}: the buffer IS the slice)
  // or another song's audio ({whole: true}: play from the slice start like the main buffer). key -> {buffer, whole, gain}
  const padBuffers = new Map();
  const reversedCache = new WeakMap();
  const reversedOf = (buf) => {
    if (buf === buffer) return reversedBuffer();
    let r = reversedCache.get(buf);
    if (!r) {
      r = ctx.createBuffer(buf.numberOfChannels, buf.length, buf.sampleRate);
      for (let ch = 0; ch < buf.numberOfChannels; ch++) {
        const src = buf.getChannelData(ch), dst = r.getChannelData(ch), n = src.length;
        for (let i = 0; i < n; i++) dst[i] = src[n - 1 - i];
      }
      reversedCache.set(buf, r);
    }
    return r;
  };
  function sliceFor(bank, pad) {
    const base = resolveChop(_chops, bank, pad);
    const o = overrides.get(`${bank}:${pad}`);
    if (!o) return base;
    const out = { ...(base ?? { index: -1, bar: 0, beat: 0 }), start: o.start, end: o.end, custom: true };
    if (o.song) out.song = o.song;
    if (o.stretch) out.stretch = o.stretch;
    return out;
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

  /** Play `pad` now, or at AudioContext time `when` (sequencer); `bank` defaults to the current bank. */
  function trigger(pad, velocity = 1, when = null, bank = _bank) {
    const chop = sliceFor(bank, pad);
    if (!chop) return;
    const P = padParams[pad];
    const mode = P.mode ?? _mode;
    const now = when ?? ctx.currentTime;
    if (_choke) chokeAll(now);

    const alt = padBuffers.get(`${bank}:${pad}`);   // stretched slice or another song's audio, else the main song
    const srcBuf = alt ? alt.buffer : buffer;
    const sliceBuf = alt && !alt.whole;             // the buffer is exactly the slice: play it from 0
    const rate = Math.pow(2, P.pitch / 12);
    const level = (alt ? alt.gain : normGain) * Math.pow(10, P.gainDb / 20) * (_fullLevel ? 1 : Math.pow(velocity, 1.4));
    const source = ctx.createBufferSource();
    source.buffer = P.reverse ? reversedOf(srcBuf) : srcBuf;
    source.playbackRate.value = rate;
    const gainNode = ctx.createGain();
    source.connect(gainNode);
    gainNode.connect(output ?? ctx.destination);

    const dur = sliceBuf ? srcBuf.duration : chop.end - chop.start;   // in source time
    const realDur = dur / rate;              // in wall-clock time
    const offset = sliceBuf ? 0 : (P.reverse ? srcBuf.duration - chop.end : chop.start);
    const follow = !(alt && alt.whole);      // the video is the open song's: it does not follow another song's pad
    const attack = Math.min(P.attack, realDur / 2);
    gainNode.gain.setValueAtTime(0, now);
    gainNode.gain.linearRampToValueAtTime(level, now + attack);

    if (mode === "oneshot") {
      gainNode.gain.setValueAtTime(level, now + Math.max(attack, realDur - P.release));
      gainNode.gain.linearRampToValueAtTime(1e-6, now + realDur);
      source.start(now, offset, dur);
      if (follow) followAt(now, chop.start, realDur);
    } else {
      source.start(now, offset); // gate: until release; playthrough: until choked
      if (follow) followAt(now, chop.start, null);
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
    /** Put a custom slice {start, end, song?, stretch?} on (bank, pad); null restores the grid chop.
     *  A slice without `stretch` / `song` drops that pad's alternate buffer. */
    setOverride(bank, pad, slice) {
      const key = `${bank}:${pad}`;
      if (slice) {
        const o = { start: slice.start, end: slice.end };
        if (slice.song) o.song = slice.song;
        if (slice.stretch) o.stretch = slice.stretch;
        overrides.set(key, o);
        const alt = padBuffers.get(key);
        if (alt && ((alt.whole && !slice.song) || (!alt.whole && !slice.stretch))) padBuffers.delete(key);
      } else { overrides.delete(key); padBuffers.delete(key); }
    },
    /** Play (bank, pad) from `buf` instead of the song: `whole: false` = the buffer is the slice itself (a stretched
     *  render), `whole: true` = another song's full audio (played from the slice start). null goes back to the song.
     *  `gain` = level normalisation for it (default: the song's for a slice, its own peak for a whole song). */
    setPadBuffer(bank, pad, buf, { whole = false, gain } = {}) {
      const key = `${bank}:${pad}`;
      if (!buf) { padBuffers.delete(key); return; }
      padBuffers.set(key, { buffer: buf, whole, gain: gain ?? (whole ? normalizationGain(buf) : normGain) });
    },
    getPadBuffer(bank, pad) { return padBuffers.get(`${bank}:${pad}`)?.buffer ?? null; },
    getOverrides() { return Object.fromEntries(overrides); },
    getSlice(bank, pad) { return sliceFor(bank, pad); },
    _ctx: ctx,
    get _mode() { return _mode; },
  };
}
