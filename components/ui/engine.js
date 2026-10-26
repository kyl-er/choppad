// ui/engine: pad -> chop playback (WebAudio, sample-accurate) + muted video visual follow. See contracts/README.md §5 (v2.1).
export const FADE_IN = 0.0025;   // s
export const FADE_OUT = 0.003;   // s
export const TARGET_PEAK = 0.891; // -1 dBFS
export const MAX_NORM_GAIN = 4;

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

  function trigger(pad, velocity = 1) {
    const chop = resolveChop(_chops, _bank, pad);
    if (!chop) return;

    const now = ctx.currentTime;

    if (_choke) {
      for (const v of activeVoices) {
        v.gain.gain.cancelScheduledValues(now);
        v.gain.gain.setValueAtTime(v.gain.gain.value, now);
        v.gain.gain.linearRampToValueAtTime(1e-6, now + FADE_OUT);
        v.source.stop(now + FADE_OUT);
      }
      activeVoices.length = 0;
    }

    const level = normGain * (_fullLevel ? 1 : Math.pow(velocity, 1.4));

    const source = ctx.createBufferSource();
    source.buffer = buffer;
    const gainNode = ctx.createGain();
    source.connect(gainNode);
    gainNode.connect(output ?? ctx.destination);

    gainNode.gain.setValueAtTime(0, now);
    gainNode.gain.linearRampToValueAtTime(level, now + FADE_IN);

    const dur = chop.end - chop.start;

    if (_mode === "oneshot") {
      gainNode.gain.setValueAtTime(level, now + FADE_IN);
      gainNode.gain.linearRampToValueAtTime(1e-6, now + dur);
      source.start(now, chop.start, dur);
      followVideo(chop.start, dur);
    } else {
      source.start(now, chop.start); // gate: until release; playthrough: until choked
      followVideo(chop.start, null);
    }

    const voice = { source, gain: gainNode, pad };
    activeVoices.push(voice);
    source.onended = () => { const i = activeVoices.indexOf(voice); if (i >= 0) activeVoices.splice(i, 1); };
  }

  function release(pad) {
    if (_mode !== "gate") return; // oneshot plays to the chop end; playthrough until choked
    const now = ctx.currentTime;
    for (let i = activeVoices.length - 1; i >= 0; i--) {
      const v = activeVoices[i];
      if (v.pad === pad) {
        v.gain.gain.cancelScheduledValues(now);
        v.gain.gain.setValueAtTime(v.gain.gain.value, now);
        v.gain.gain.linearRampToValueAtTime(1e-6, now + FADE_OUT);
        v.source.stop(now + FADE_OUT);
        activeVoices.splice(i, 1);
        if (video) video.pause();
      }
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
    _ctx: ctx,
    get _mode() { return _mode; },
  };
}
