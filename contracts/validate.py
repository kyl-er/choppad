"""Executable ChopPad contracts (stdlib only). Each validator raises ContractError on the first violation.

Usage: python3 contracts/validate.py <meta|analysis|chops> <file.json> [--media-dir DIR] [--duration S]
"""
import json
import os
import statistics
import sys


class ContractError(ValueError):
    pass


def _req(cond, msg):
    if not cond:
        raise ContractError(msg)


def _increasing(xs, name):
    _req(all(b > a for a, b in zip(xs, xs[1:])), f"{name} must be strictly increasing")


def validate_meta(m, media_dir=None):
    _req(m.get("schema") == "choppad.meta/1", "schema must be choppad.meta/1")
    for k in ("id", "source", "title", "video", "audio"):
        _req(isinstance(m.get(k), str) and m[k], f"meta.{k} must be a non-empty string")
    _req(isinstance(m.get("duration_s"), (int, float)) and m["duration_s"] > 0, "duration_s must be > 0")
    _req(m.get("audio_sr") == 44100, "audio_sr must be 44100")
    _req(m.get("audio_channels") == 2, "audio_channels must be 2")
    if media_dir:
        for k in ("video", "audio"):
            _req(os.path.isfile(os.path.join(media_dir, m[k])), f"{k} file missing: {m[k]}")


def validate_analysis(a, duration_s=None):
    _req(a.get("schema") == "choppad.analysis/1", "schema must be choppad.analysis/1")
    bpm, beats, downs = a.get("bpm"), a.get("beats"), a.get("downbeats")
    _req(isinstance(bpm, (int, float)) and 30 <= bpm <= 300, "bpm must be in [30,300]")
    meter = a.get("meter") or {}
    bpb = meter.get("beats_per_bar")
    _req(bpb in (2, 3, 4, 6, 7), "meter.beats_per_bar must be one of 2,3,4,6,7")
    _req(meter.get("beat_unit") in (4, 8), "meter.beat_unit must be 4 or 8")
    _req(isinstance(beats, list) and len(beats) >= 2, "need at least 2 beats")
    _increasing(beats, "beats")
    _req(beats[0] >= 0, "beats must be >= 0")
    if duration_s is not None:
        _req(beats[-1] <= duration_s, "beats must be <= duration_s")
    ibi = statistics.median(b - a for a, b in zip(beats, beats[1:]))
    _req(abs(60 / ibi - bpm) / bpm <= 0.02, f"bpm {bpm} disagrees with median beat interval ({60/ibi:.2f})")
    _req(isinstance(downs, list) and downs, "downbeats must be a non-empty list")
    idx = {round(b, 6): i for i, b in enumerate(beats)}
    pos = []
    for d in downs:
        _req(round(d, 6) in idx, f"downbeat {d} is not in beats")
        pos.append(idx[round(d, 6)])
    _req(all(b - a == bpb for a, b in zip(pos, pos[1:])), "downbeats must be exactly beats_per_bar beats apart")
    c = a.get("confidence")
    _req(isinstance(c, (int, float)) and 0 <= c <= 1, "confidence must be in [0,1]")
    _req(isinstance(a.get("method"), str) and a["method"], "method must be a non-empty string")


GRID_STEPS = {"1/4": 1, "1/8": 2, "1/16": 4}


def validate_chops(c):
    _req(c.get("schema") == "choppad.chops/1", "schema must be choppad.chops/1")
    _req(c.get("grid") in GRID_STEPS, "grid must be 1/4, 1/8 or 1/16")
    _req(c.get("pads_per_bank") == 16, "pads_per_bank must be 16")
    bpb = c.get("beats_per_bar")
    _req(bpb in (2, 3, 4, 6, 7), "beats_per_bar invalid")
    steps_per_bar = bpb * GRID_STEPS[c["grid"]]
    chops = c.get("chops")
    _req(isinstance(chops, list) and chops, "chops must be a non-empty list")
    for i, ch in enumerate(chops):
        _req(ch.get("index") == i, f"chops[{i}].index must be {i}")
        _req(ch["start"] < ch["end"], f"chops[{i}] start must be < end")
        _req(0 <= ch["beat"] < steps_per_bar, f"chops[{i}].beat out of range")
        if i:
            _req(abs(chops[i - 1]["end"] - ch["start"]) < 1e-6, f"chops[{i}] not contiguous with previous")
    _req(chops[0]["beat"] == 0, "first chop must start on a downbeat")
    banks = c.get("banks")
    _req(isinstance(banks, list) and banks, "banks must be a non-empty list")
    flat = [p for b in banks for p in b]
    _req(flat == list(range(len(chops))), "banks must cover every chop index exactly once, in order")
    for n, b in enumerate(banks):
        _req(1 <= len(b) <= 16, f"bank {n} must hold 1..16 chops")
        _req(chops[b[0]]["beat"] == 0, f"bank {n} must start on a downbeat")
        if n < len(banks) - 1:
            _req(len(b) == 16 or chops[banks[n + 1][0]]["beat"] == 0, f"bank {n} short but not at a bar edge")


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
