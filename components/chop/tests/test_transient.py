import os, sys, tempfile, unittest
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from _common import *  # noqa: E402,F403
import numpy as np  # noqa: E402
from choppad_transient import chop_transients, detect_onsets, select_onsets  # noqa: E402

SR = 44100


def drum_loop(hits, seconds, seed=1):
    """hits: [(time_s, kind, amp)], kind in kick|snare|hat. Returns mono float32."""
    rng = np.random.default_rng(seed)
    y = np.zeros(int(seconds * SR), dtype=np.float32)
    for t, kind, amp in hits:
        n = int((0.25 if kind != "hat" else 0.06) * SR)
        k = np.arange(n) / SR
        if kind == "kick":
            f = 50 + 110 * np.exp(-k / 0.02)
            s = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-k / 0.09)
        elif kind == "snare":
            s = rng.uniform(-1, 1, n) * np.exp(-k / 0.05) + 0.5 * np.sin(2 * np.pi * 190 * k) * np.exp(-k / 0.04)
        else:
            s = rng.uniform(-1, 1, n) * np.exp(-k / 0.012)
        s = s * np.minimum(1.0, (n - np.arange(n)) / (0.02 * SR))  # taper: no click at the cut
        a = int(t * SR)
        m = min(n, len(y) - a)
        if m > 0:
            y[a:a + m] += (amp * s[:m]).astype(np.float32)
    return y


def pattern(bpm=100, bars=4):
    ibi = 60 / bpm
    hits = []
    for b in range(bars * 4):
        t = 0.5 + b * ibi
        hits.append((t, "kick" if b % 2 == 0 else "snare", 0.8))
        hits.append((t + ibi / 2, "hat", 0.6))
    return hits


def analysis_for(bpm=100, bars=4):
    ibi = 60 / bpm
    beats = [round(0.5 + k * ibi, 6) for k in range(bars * 4 + 1)]
    return {"schema": "choppad.analysis/2", "duration_s": beats[-1] + 1.0, "bpm": float(bpm), "bpm_candidates": [float(bpm)],
            "meter": {"beats_per_bar": 4, "beat_unit": 4}, "beats": beats, "downbeats": beats[0:-1:4],
            "confidence": 1, "meter_confidence": 1, "method": "test"}


class Transient(unittest.TestCase):
    def test_drum_onsets_within_10ms(self):
        hits = pattern()
        a = analysis_for()
        y = drum_loop(hits, a["duration_s"])
        truth = sorted(t for t, _, _ in hits)
        ch = chop_transients((y, SR), a, sensitivity=0.6)
        starts = [c["start"] for c in ch["chops"]]
        for t in truth:
            near = min(starts, key=lambda s: abs(s - t))
            self.assertLess(abs(near - t), 0.010, f"hit at {t:.3f} -> nearest slice start {near:.3f}")
        self.assertLessEqual(len(starts), len(truth) + 2, "far too many slices for the drum pattern")

    def test_click_track_from_file(self):
        a = load_fixture("analysis")
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "c.wav")
            truth = click_track(p, 120, offset_s=0.5, n_beats=44, pickup=2)
            ch = chop_transients(p, a, sensitivity=0.5)
        starts = [c["start"] for c in ch["chops"]]
        self.assertEqual(len(starts), len(truth["beats"]))
        for t, s in zip(truth["beats"], starts):
            self.assertLess(abs(t - s), 0.010)

    def test_contract_shape(self):
        a = analysis_for()
        y = drum_loop(pattern(), a["duration_s"])
        ch = chop_transients((y, SR), a, sensitivity=0.6)
        self.assertEqual((ch["mode"], ch["schema"], ch["bank_size"]), ("transient", "choppad.chops/2", 16))
        self.assertAlmostEqual(ch["sensitivity"], 0.6)
        cs = ch["chops"]
        for i, c in enumerate(cs):
            self.assertEqual(c["index"], i)
            self.assertLess(c["start"], c["end"])
            if i:
                self.assertEqual(cs[i - 1]["end"], c["start"])
        self.assertEqual([p for b in ch["banks"] for p in b], list(range(len(cs))))
        self.assertTrue(all(len(b) == 16 for b in ch["banks"][:-1]))
        # the first kick sits on bar 1 beat 1; the snare after it on beat 2 (1/16 steps: 4)
        self.assertEqual((cs[0]["bar"], cs[0]["beat"]), (0, 0))
        snare = min(cs, key=lambda c: abs(c["start"] - (0.5 + 0.6)))
        self.assertEqual((snare["bar"], snare["beat"]), (0, 4))

    def test_sensitivity_monotonic(self):
        a = analysis_for()
        weak = [(0.5 + 0.6 * k + 0.2, "hat", 0.05 + 0.05 * (k % 3)) for k in range(14)]
        y = drum_loop(pattern() + weak, a["duration_s"])
        table = detect_onsets((y, SR))
        counts = [len(select_onsets(table, s / 10)) for s in range(0, 11)]
        self.assertEqual(counts, sorted(counts))
        self.assertGreater(counts[-1], counts[0])
        self.assertGreaterEqual(counts[0], 1)

    def test_min_len_and_max_slices(self):
        table = {"times": [1.0, 1.02, 1.5, 2.0, 2.04], "strengths": [1.0, 0.9, 0.8, 0.7, 0.9]}
        self.assertEqual(select_onsets(table, 1.0, 0.06), [1.0, 1.5, 2.04])
        self.assertEqual(len(select_onsets(table, 1.0, 0.06, max_slices=2)), 2)
        with self.assertRaises(ValueError):
            select_onsets(table, 2.0)

    def test_onset_list_input_and_silence(self):
        a = analysis_for()
        ch = chop_transients([0.5, 1.1, 1.7], a, sensitivity=0.5)
        self.assertEqual([c["start"] for c in ch["chops"]], [0.5, 1.1, 1.7])
        self.assertEqual(ch["chops"][-1]["end"], a["duration_s"])
        with self.assertRaises(ValueError):
            chop_transients((np.zeros(SR, dtype=np.float32), SR), a)


if __name__ == "__main__":
    unittest.main()
