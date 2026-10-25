"""Executable ChopPad contracts v2 (stdlib only). Each validator raises ContractError on the first violation.

Usage: python3 contracts/validate.py <meta|analysis|chops> <file.json> [--media-dir DIR] [--duration S]
"""
import json
import os
import statistics
import sys


class ContractError(ValueError):
    pass


METERS = (2, 3, 4)
GRID_STEPS = {"1/4": 1, "1/8": 2, "1/16": 4}
BPM_MULTS = (0.5, 1, 2)


def _req(cond, msg):
    if not cond:
        raise ContractError(msg)


def _num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _increasing(xs, name):
    _req(all(b > a for a, b in zip(xs, xs[1:])), f"{name} must be strictly increasing")


def bank_size(beats_per_bar, grid):
    """Largest whole number of bars that fits on 16 pads (16 if one bar is longer than 16 steps)."""
    spb = beats_per_bar * GRID_STEPS[grid]
    return (16 // spb) * spb if spb <= 16 else 16


def validate_meta(m, media_dir=None):
    _req(m.get("schema") == "choppad.meta/2", "schema must be choppad.meta/2")
    for k in ("id", "source", "title", "video", "audio"):
        _req(isinstance(m.get(k), str) and m[k], f"meta.{k} must be a non-empty string")
    _req(_num(m.get("duration_s")) and m["duration_s"] > 0, "duration_s must be > 0")
    _req(m.get("audio_sr") == 44100, "audio_sr must be 44100")
    _req(m.get("audio_channels") == 2, "audio_channels must be 2")
    v = m.get("video_info") or {}
    _req(v.get("codec") == "h264", "video_info.codec must be h264")
    _req(isinstance(v.get("height"), int) and 0 < v["height"] <= 480, "video_info.height must be 1..480")
    _req(isinstance(v.get("gop"), int) and 1 <= v["gop"] <= 6, "video_info.gop (max keyframe distance in frames) must be 1..6")
    _req(v.get("b_frames") == 0, "video_info.b_frames must be 0")
    _req(v.get("has_audio") is False, "video_info.has_audio must be false (audio lives only in audio.wav)")
    if media_dir:
        for k in ("video", "audio"):
            _req(os.path.isfile(os.path.join(media_dir, m[k])), f"{k} file missing: {m[k]}")


def validate_analysis(a, duration_s=None):
    _req(a.get("schema") == "choppad.analysis/2", "schema must be choppad.analysis/2")
    bpm, beats, downs = a.get("bpm"), a.get("beats"), a.get("downbeats")
    _req(_num(a.get("duration_s")) and a["duration_s"] > 0, "duration_s must be > 0")
    if duration_s is not None:
        _req(abs(a["duration_s"] - duration_s) <= 0.2, "duration_s must match meta.duration_s")
    _req(_num(bpm) and 30 <= bpm <= 300, "bpm must be in [30,300]")
    meter = a.get("meter") or {}
    bpb = meter.get("beats_per_bar")
    _req(bpb in METERS, "meter.beats_per_bar must be one of 2,3,4")
    _req(meter.get("beat_unit") == 4, "meter.beat_unit must be 4")
    _req(isinstance(beats, list) and len(beats) >= bpb + 1, "need at least beats_per_bar + 1 beats")
    _increasing(beats, "beats")
    _req(beats[0] >= 0 and beats[-1] <= a["duration_s"], "beats must lie within [0, duration_s]")
    ibi = statistics.median(b - x for x, b in zip(beats, beats[1:]))
    _req(abs(60 / ibi - bpm) / bpm <= 0.005, f"bpm {bpm} must be derived from the beats (median gives {60/ibi:.2f})")
    cands = a.get("bpm_candidates")
    _req(isinstance(cands, list) and cands and all(_num(x) for x in cands), "bpm_candidates must be a non-empty number list")
    _req(any(abs(x - bpm) < 0.01 for x in cands), "bpm_candidates must include bpm")
    _req(isinstance(downs, list) and downs, "downbeats must be a non-empty list")
    idx = {round(b, 6): i for i, b in enumerate(beats)}
    pos = []
    for d in downs:
        _req(round(d, 6) in idx, f"downbeat {d} is not in beats")
        pos.append(idx[round(d, 6)])
    _req(pos[-1] < len(beats) - 1, "last downbeat must not be the final beat")
    _req(all(b - x == bpb for x, b in zip(pos, pos[1:])), "downbeats must be exactly beats_per_bar beats apart")
    for k in ("confidence", "meter_confidence"):
        _req(_num(a.get(k)) and 0 <= a[k] <= 1, f"{k} must be in [0,1]")
    _req(isinstance(a.get("method"), str) and a["method"], "method must be a non-empty string")


def validate_chops(c):
    _req(c.get("schema") == "choppad.chops/2", "schema must be choppad.chops/2")
    _req(c.get("grid") in GRID_STEPS, "grid must be 1/4, 1/8 or 1/16")
    _req(c.get("pads_per_bank") == 16, "pads_per_bank must be 16")
    bpb = c.get("beats_per_bar")
    _req(bpb in METERS, "beats_per_bar must be one of 2,3,4")
    _req(c.get("bpm_mult") in BPM_MULTS, "bpm_mult must be 0.5, 1 or 2")
    _req(isinstance(c.get("beat_offset"), int) and -3 <= c["beat_offset"] <= 3, "beat_offset must be an int in [-3,3]")
    _req(_num(c.get("bpm")) and 30 <= c["bpm"] <= 300, "bpm must be in [30,300]")
    bs = bank_size(bpb, c["grid"])
    _req(c.get("bank_size") == bs, f"bank_size must be {bs} for {bpb} beats/bar at {c['grid']}")
    steps_per_bar = bpb * GRID_STEPS[c["grid"]]
    chops = c.get("chops")
    _req(isinstance(chops, list) and chops, "chops must be a non-empty list")
    for i, ch in enumerate(chops):
        _req(ch.get("index") == i, f"chops[{i}].index must be {i}")
        _req(ch["start"] < ch["end"], f"chops[{i}] start must be < end")
        _req(0 <= ch["beat"] < steps_per_bar, f"chops[{i}].beat out of range")
        _req(ch["bar"] == i // steps_per_bar and ch["beat"] == i % steps_per_bar, f"chops[{i}] bar/beat must follow its index")
        if i:
            _req(abs(chops[i - 1]["end"] - ch["start"]) < 1e-6, f"chops[{i}] not contiguous with previous")
    banks = c.get("banks")
    _req(isinstance(banks, list) and banks, "banks must be a non-empty list")
    flat = [p for b in banks for p in b]
    _req(flat == list(range(len(chops))), "banks must cover every chop index exactly once, in order")
    for n, b in enumerate(banks):
        _req(1 <= len(b) <= bs, f"bank {n} must hold 1..{bs} chops")
        _req(n == len(banks) - 1 or len(b) == bs, f"only the last bank may be short (bank {n})")
        _req(chops[b[0]]["beat"] == 0, f"bank {n} must start on a downbeat")


VALIDATORS = {"meta": validate_meta, "analysis": validate_analysis, "chops": validate_chops}

if __name__ == "__main__":
    kind, path = sys.argv[1], sys.argv[2]
    data = json.load(open(path))
    kw = {}
    if "--media-dir" in sys.argv:
        kw["media_dir"] = sys.argv[sys.argv.index("--media-dir") + 1]
    if "--duration" in sys.argv:
        kw["duration_s"] = float(sys.argv[sys.argv.index("--duration") + 1])
    try:
        VALIDATORS[kind](data, **kw)
    except ContractError as e:
        sys.exit(f"INVALID {kind}: {e}")
    print(f"OK {kind}")
