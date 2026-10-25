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
export function createEngine({ ctx, buffer, chops, video = null, mode = "oneshot", choke = true, fullLevel = true }) {
  let _bank = 0;
  let _chops = chops;
  let _mode = mode;
  let _choke = choke;
  let _fullLevel = fullLevel;
  const normGain = normalizationGain(buffer);
  const activeVoices = [];
  let pendingTarget = null;

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
    gainNode.connect(ctx.destination);

    gainNode.gain.setValueAtTime(0, now);
    gainNode.gain.linearRampToValueAtTime(level, now + FADE_IN);

    const dur = chop.end - chop.start;

    if (_mode === "oneshot") {
      gainNode.gain.setValueAtTime(level, now + FADE_IN);
      gainNode.gain.linearRampToValueAtTime(1e-6, now + dur);
      source.start(now, chop.start, dur);
      if (video && !video.seeking) {
        video.currentTime = chop.start;
        video.play().catch(() => {});
      } else if (video && video.seeking) {
        pendingTarget = chop.start;
      }
    } else if (_mode === "gate") {
      source.start(now, chop.start);
      if (video && !video.seeking) {
        video.currentTime = chop.start;
        video.play().catch(() => {});
      } else if (video && video.seeking) {
        pendingTarget = chop.start;
      }
    } else if (_mode === "playthrough") {
      source.start(now, chop.start);
      if (video && !video.seeking) {
        video.currentTime = chop.start;
        video.play().catch(() => {});
      } else if (video && video.seeking) {
        pendingTarget = chop.start;
      }
    }

    activeVoices.push({ source, gain: gainNode, pad });
  }

  function release(pad) {
    if (_mode === "playthrough") return;
    const now = ctx.currentTime;
    for (let i = activeVoices.length - 1; i >= 0; i--) {
      const v = activeVoices[i];
      if (v.pad === pad) {
        v.gain.gain.cancelScheduledValues(now);
        v.gain.gain.setValueAtTime(v.gain.gain.value, now);
        v.gain.gain.linearRampToValueAtTime(1e-6, now + FADE_OUT);
        v.source.stop(now + FADE_OUT);
        activeVoices.splice(i, 1);
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
