"""Deterministic fixtures for every component (stdlib only).

python3 contracts/fixtures/make_fixtures.py   # (re)writes the *.sample.json goldens next to this file

Golden song: 120 BPM, 4/4, beats every 0.5 s starting at 0.5 s, a 2-beat pickup, 44 beats total.
=> 11 downbeats, 41 quarter-note chops, banks of 16/16/9.
"""
import json
import math
import os
import struct
import wave

HERE = os.path.dirname(os.path.abspath(__file__))
SR = 44100


def click_track(path, bpm, beats_per_bar=4, offset_s=0.5, n_beats=44, pickup=2, seconds=None, intro_noise_s=0.0):
    """Write a stereo s16 44.1k WAV with an accented click on every downbeat. Returns ground truth.

    intro_noise_s > 0 prepends that many seconds of quiet (-30 dBFS) noise before the clicks (beat times shift)."""
    import random
    ibi = 60.0 / bpm
    offset_s += intro_noise_s
    beats = [round(offset_s + k * ibi, 6) for k in range(n_beats)]
    downbeats = [b for k, b in enumerate(beats) if k >= pickup and (k - pickup) % beats_per_bar == 0]
    total = seconds or (beats[-1] + ibi)
    n = int(total * SR)
    rng = random.Random(7)
    buf = [(rng.uniform(-0.0316, 0.0316) if i < intro_noise_s * SR else 0.0) for i in range(n)]
    for b in beats:
        accent = b in downbeats
        f, amp = (1500.0, 0.9) if accent else (800.0, 0.45)
        s0 = int(b * SR)
        for i in range(int(0.03 * SR)):
            if s0 + i < n:
                buf[s0 + i] += amp * math.exp(-i / (0.006 * SR)) * math.sin(2 * math.pi * f * i / SR)
    with wave.open(path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(b"".join(struct.pack("<hh", s, s) for s in (int(max(-1, min(1, x)) * 32767) for x in buf)))
    return {"bpm": bpm, "beats_per_bar": beats_per_bar, "beats": beats, "downbeats": downbeats, "duration_s": total}


def golden():
    bpm, bpb, pickup = 120.0, 4, 2
    beats = [round(0.5 + k * 0.5, 6) for k in range(44)]
    downbeats = [b for k, b in enumerate(beats) if k >= pickup and (k - pickup) % bpb == 0]
    meta = {"schema": "choppad.meta/2", "id": "golden120", "source": "fixture:golden120", "title": "Golden 120 BPM click",
            "duration_s": 23.0, "video": "video.mp4", "audio": "audio.wav", "audio_sr": 44100, "audio_channels": 2,
            "video_info": {"codec": "h264", "height": 480, "gop": 6, "b_frames": 0, "has_audio": False}}
    analysis = {"schema": "choppad.analysis/2", "duration_s": 23.0, "bpm": bpm, "bpm_candidates": [bpm, bpm / 2, bpm * 2],
                "meter": {"beats_per_bar": bpb, "beat_unit": 4}, "beats": beats, "downbeats": downbeats,
                "confidence": 1.0, "meter_confidence": 1.0, "method": "fixture"}
    chops = []
    for k in range(pickup, len(beats) - 1):
        i = len(chops)
        chops.append({"index": i, "start": beats[k], "end": beats[k + 1], "bar": i // bpb, "beat": i % bpb})
    banks = [list(range(s, min(s + 16, len(chops)))) for s in range(0, len(chops), 16)]
    chops_doc = {"schema": "choppad.chops/2", "grid": "1/4", "bpm": bpm, "beats_per_bar": bpb, "bpm_mult": 1,
                 "beat_offset": 0, "pads_per_bank": 16, "bank_size": 16, "chops": chops, "banks": banks}
    advisor_doc = {
        "schema": "choppad.advisor/2", "model": "qwen/qwen3.8-omni-flash",
        "sections": [
            {"label": "intro", "start_bar": 0, "end_bar": 4, "start_s": 1.5, "end_s": 9.5},
            {"label": "groove", "start_bar": 4, "end_bar": 10, "start_s": 9.5, "end_s": 21.5},
        ],
        "suggested_bank": list(range(0, 32, 2)),
        "pad_labels": [f"Chop {i}" for i in range(0, 32, 2)],
        "bpm_meter_check": {"felt_bpm": 120.0, "bpm_mult_hint": 1, "meter_hint": "4/4", "notes": "Steady 120 BPM 4/4 click"},
    }
    return meta, analysis, chops_doc, advisor_doc


if __name__ == "__main__":
    for name, doc in zip(("meta", "analysis", "chops", "advisor"), golden()):
        with open(os.path.join(HERE, f"{name}.sample.json"), "w") as f:
            json.dump(doc, f, indent=1)
        print("wrote", f"{name}.sample.json")
