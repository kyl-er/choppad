"""export fx: bake the UI's master sound settings into exported samples with pedalboard (Spotify's
JUCE-based effects library; it can also host VST3/LV2 plugins). Params mirror components/ui/fx.js:
tone (tilt EQ), low/mid/high EQ dB, one-knob filter, reverb mix + size."""

import math

import numpy as np
import soundfile as sf

DEFAULT_FX = {"tone": 0.0, "low": 0.0, "mid": 0.0, "high": 0.0, "filter": 0.0, "reverb": 0.0, "size": 0.5}
TILT_DB = 9.0


def _clamp(v, a, b):
    return min(b, max(a, float(v)))


def normalize(fx):
    out = dict(DEFAULT_FX)
    for k in DEFAULT_FX:
        if isinstance((fx or {}).get(k), (int, float)):
            out[k] = float(fx[k])
    return out


def is_neutral(fx):
    """True when the settings don't change the sound (room size alone does nothing without reverb)."""
    f = normalize(fx)
    return f["reverb"] < 1e-6 and filter_setting(f["filter"]) is None and \
        all(abs(f[k]) < 1e-6 for k in ("tone", "low", "mid", "high"))


def filter_setting(v):
    """Same curve as fx.js filterSetting: -1 lowpass 20 kHz->150 Hz, +1 highpass 20 Hz->4 kHz, ~0 off."""
    x = _clamp(v, -1, 1)
    if abs(x) < 0.02:
        return None
    if x < 0:
        return "lowpass", 20000 * (150 / 20000) ** (-x)
    return "highpass", 20 * (4000 / 20) ** x


def reverb_seconds(size):
    return 0.4 + _clamp(size, 0, 1) * 5.6


def build_board(fx):
    from pedalboard import (HighShelfFilter, LadderFilter, LowShelfFilter, Pedalboard, PeakFilter, Reverb)
    f = normalize(fx)
    board = []
    flt = filter_setting(f["filter"])
    if flt:
        mode = LadderFilter.Mode.LPF12 if flt[0] == "lowpass" else LadderFilter.Mode.HPF12
        board.append(LadderFilter(mode=mode, cutoff_hz=flt[1], resonance=0.0, drive=1.0))
    t = _clamp(f["tone"], -1, 1)
    if abs(t) > 1e-6:
        board += [LowShelfFilter(cutoff_frequency_hz=700, gain_db=-t * TILT_DB),
                  HighShelfFilter(cutoff_frequency_hz=700, gain_db=t * TILT_DB)]
    if f["low"]:
        board.append(LowShelfFilter(cutoff_frequency_hz=120, gain_db=f["low"]))
    if f["mid"]:
        board.append(PeakFilter(cutoff_frequency_hz=1000, gain_db=f["mid"], q=0.8))
    if f["high"]:
        board.append(HighShelfFilter(cutoff_frequency_hz=6000, gain_db=f["high"]))
    mix = _clamp(f["reverb"], 0, 1)
    if mix > 1e-6:
        board.append(Reverb(room_size=0.3 + 0.69 * _clamp(f["size"], 0, 1), damping=0.45, width=1.0,
                            wet_level=math.sin(mix * math.pi / 2) * 0.9, dry_level=math.cos(mix * math.pi / 2)))
    return Pedalboard(board)


def apply_to_file(path, fx, *, max_tail_s=3.0):
    """Process a WAV in place. Reverb gets room for its tail, then trailing near-silence is trimmed."""
    data, sr = sf.read(path, dtype="float32", always_2d=True)
    f = normalize(fx)
    tail = min(max_tail_s, reverb_seconds(f["size"])) if f["reverb"] > 1e-6 else 0.0
    if tail:
        data = np.concatenate([data, np.zeros((int(tail * sr), data.shape[1]), dtype=np.float32)])
    out = build_board(f)(data.T, sr).T
    if tail:
        loud = np.nonzero(np.max(np.abs(out), axis=1) > 10 ** (-60 / 20))[0]
        out = out[: (loud[-1] + 1 if loud.size else len(out))]
    peak = float(np.max(np.abs(out))) if out.size else 0.0
    if peak > 0.999:  # EQ boosts can clip; keep the kit sample clean
        out *= 0.999 / peak
    sf.write(path, out, sr, subtype="PCM_16")
    return path
