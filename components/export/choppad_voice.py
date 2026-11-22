"""voice: the pad's filter and ADSR envelope, rendered into audio (W3b), and the same settings as SFZ opcodes.

Hydrogen kits and the chops zip are plain WAV files, so the pad filter and envelope are BAKED into the rendered WAVs
(the Hydrogen instrument has its own ADSR and filter fields, but they are a different model: baking is the one that sounds
like the pad does in ChopPad). SFZ can say all of it in opcodes, so there nothing is baked.

Same maths as the browser engine (components/ui/engine.js): an RBJ biquad (low-pass, high-pass, band-pass) with linear
`q`, attack ramp, decay down to `sustain`, and a linear fade over the last `release` seconds of the slice.
"""
import math

import numpy as np

DEFAULT_ATTACK, DEFAULT_RELEASE = 0.0025, 0.003
FILTER_TYPES = ("off", "lp", "hp", "bp")
SFZ_FILTER = {"lp": "lpf_2p", "hp": "hpf_2p", "bp": "bpf_2p"}


def params_of(pad):
    """Pad sound dict -> clean voice params with the engine's defaults filled in."""
    p = pad or {}
    f = p.get("filter") if isinstance(p.get("filter"), dict) else {}
    ftype = f.get("type") if f.get("type") in FILTER_TYPES else "off"
    num = lambda v, d: float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else d  # noqa: E731
    return {
        "attack": max(0.0, num(p.get("attack"), DEFAULT_ATTACK)),
        "decay": max(0.0, num(p.get("decay"), 0.0)),
        "sustain": min(1.0, max(0.0, num(p.get("sustain"), 1.0))),
        "release": max(0.0, num(p.get("release"), DEFAULT_RELEASE)),
        "filter": {"type": ftype, "cutoff": min(20000.0, max(20.0, num(f.get("cutoff"), 8000.0))),
                   "q": min(24.0, max(0.1, num(f.get("q"), 0.7)))},
    }


def has_filter(v):
    return v["filter"]["type"] != "off"


def has_shaped_envelope(v):
    """True when the envelope is more than the default 2.5 ms in / 3 ms out declick."""
    return v["decay"] > 0 and v["sustain"] < 1 or v["sustain"] < 1 or v["attack"] != DEFAULT_ATTACK or v["release"] != DEFAULT_RELEASE


def is_neutral(pad):
    """True when baking would change nothing (the exporters then keep their plain declicked slice)."""
    v = params_of(pad)
    return not has_filter(v) and not has_shaped_envelope(v)


def biquad_coeffs(kind, cutoff, q, sr):
    """RBJ cookbook (b, a) for lp / hp / bp, linear q. The cutoff is held below 0.45 x the sample rate."""
    f0 = min(cutoff, 0.45 * sr)
    w0 = 2 * math.pi * f0 / sr
    cosw, sinw = math.cos(w0), math.sin(w0)
    alpha = sinw / (2 * q)
    if kind == "lp":
        b = [(1 - cosw) / 2, 1 - cosw, (1 - cosw) / 2]
    elif kind == "hp":
        b = [(1 + cosw) / 2, -(1 + cosw), (1 + cosw) / 2]
    elif kind == "bp":
        b = [alpha, 0.0, -alpha]
    else:
        raise ValueError(f"unknown filter type {kind!r}")
    a = [1 + alpha, -2 * cosw, 1 - alpha]
    return [x / a[0] for x in b], [1.0, a[1] / a[0], a[2] / a[0]]


def apply_filter(x, sr, f):
    """x: (frames, channels) float array. Returns the filtered copy."""
    if f["type"] == "off":
        return x
    from scipy.signal import lfilter
    b, a = biquad_coeffs(f["type"], f["cutoff"], f["q"], sr)
    return lfilter(b, a, x, axis=0).astype(np.float32)


def envelope(n, sr, v):
    """Gain curve of n frames: attack ramp, decay to sustain, then a linear fade to 0 over the last `release` seconds."""
    dur = n / sr
    t = np.arange(n, dtype=np.float64) / sr
    a = min(v["attack"], dur / 2)
    sus, dec = v["sustain"], v["decay"]

    def base(tt):
        out = np.ones_like(tt)
        if sus < 1:
            after = np.full_like(tt, sus) if dec <= 0 else np.where(tt < a + dec, 1 + (sus - 1) * (tt - a) / dec, sus)
            out = np.where(tt >= a, after, out)
        if a > 0:
            out = np.where(tt < a, tt / a, out)
        return out

    env = base(t)
    r = min(v["release"], dur)
    tr = dur - r
    if r > 0:
        at_tr = float(base(np.array([max(tr, a)]))[0])
        fade = t >= tr
        env = np.where(fade, at_tr * np.clip((dur - t) / r, 0, 1), env)
    return env.astype(np.float32)


def bake(x, sr, pad):
    """Filter then envelope of one slice. x: (frames, channels) float32 in -1..1. Returns a float32 array of the same shape."""
    v = params_of(pad)
    y = apply_filter(x, sr, v["filter"])
    return (y * envelope(len(y), sr, v)[:, None]).astype(np.float32)


def sfz_opcodes(pad):
    """The pad's filter and envelope as SFZ opcodes (string, with a leading space) or '' when it is the plain default."""
    v = params_of(pad)
    ops = []
    if has_filter(v):
        f = v["filter"]
        res = min(40.0, max(0.0, 20 * math.log10(f["q"] / math.sqrt(0.5))))
        ops += [f"fil_type={SFZ_FILTER[f['type']]}", f"cutoff={f['cutoff']:.1f}", f"resonance={res:.1f}"]
    if has_shaped_envelope(v):
        ops += [f"ampeg_attack={v['attack']:.4f}", f"ampeg_decay={v['decay']:.4f}", f"ampeg_sustain={v['sustain'] * 100:.1f}",
                f"ampeg_release={v['release']:.4f}"]
    return (" " + " ".join(ops)) if ops else ""
