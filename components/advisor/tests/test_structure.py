import os, sys, tempfile, unittest
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from _common import *  # noqa: E402,F403
import json  # noqa: E402
import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402
from choppad_structure import sections  # noqa: E402

SR = 44100


def chord_song(path, plan, bpm=120, bars_per_part=8):
    """Each part = a sustained chord (list of Hz) + a kick on every beat; returns analysis dict."""
    spb = 60 / bpm
    out = []
    t = np.arange(int(bars_per_part * 4 * spb * SR)) / SR
    for chord, loud in plan:
        x = sum(np.sin(2 * np.pi * f * t) for f in chord) * (0.12 * loud)
        for k in range(bars_per_part * 4):
            i = int(k * spb * SR)
            n = min(len(x) - i, int(0.08 * SR))
            x[i:i + n] += 0.5 * np.exp(-np.arange(n) / (0.015 * SR)) * np.sin(2 * np.pi * 60 * np.arange(n) / SR)
        out.append(x)
    y = np.concatenate(out).astype(np.float32)
    sf.write(path, np.stack([y, y], axis=1), SR)
    beats = [round(k * spb, 6) for k in range(int(len(y) / SR / spb))]
    return {"schema": "choppad.analysis/2", "duration_s": len(y) / SR, "bpm": bpm, "bpm_candidates": [bpm],
            "meter": {"beats_per_bar": 4, "beat_unit": 4}, "beats": beats, "downbeats": beats[0:-1:4],
            "confidence": 1, "meter_confidence": 1, "method": "test"}


C, FS = [261.6, 329.6, 392.0], [370.0, 466.2, 554.4]  # C major vs F# major


class Structure(unittest.TestCase):
    def test_alternating_parts_found_and_grouped(self):
        d = tempfile.mkdtemp()
        a = chord_song(os.path.join(d, "audio.wav"), [(C, 0.6), (FS, 1.0), (C, 0.6), (FS, 1.0)])
        json.dump(a, open(os.path.join(d, "analysis.json"), "w"))
        doc = sections(d)
        bounds = [s["start_bar"] for s in doc["sections"]]
        self.assertEqual(bounds, [0, 8, 16, 24])
        letters = [s["letter"] for s in doc["sections"]]
        self.assertEqual(letters[0], letters[2]); self.assertEqual(letters[1], letters[3]); self.assertNotEqual(letters[0], letters[1])
        self.assertEqual(doc["sections"][1]["label"], "chorus")  # louder repeated part
        self.assertTrue(os.path.isfile(os.path.join(d, "sections.json")))


if __name__ == "__main__":
    unittest.main()
