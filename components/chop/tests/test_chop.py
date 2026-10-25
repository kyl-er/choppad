import os, sys, tempfile, unittest
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from _common import *  # noqa: E402,F403
from choppad_chop import chop  # noqa: E402
import time  # noqa: E402


def analysis_3_4(n=40):
    beats = [round(0.2 + k * (60 / 90), 6) for k in range(n)]
    return {"schema": "choppad.analysis/2", "duration_s": beats[-1] + 1, "bpm": 90.0, "bpm_candidates": [90.0],
            "meter": {"beats_per_bar": 3, "beat_unit": 4}, "beats": beats, "downbeats": beats[1:-1:3],
            "confidence": 0.9, "meter_confidence": 0.8, "method": "test"}


def long_analysis(n=600):
    beats = [round(0.5 + k * 0.5, 6) for k in range(n)]
    return {"schema": "choppad.analysis/2", "duration_s": beats[-1] + 1, "bpm": 120.0, "bpm_candidates": [120.0],
            "meter": {"beats_per_bar": 4, "beat_unit": 4}, "beats": beats, "downbeats": beats[0:-1:4],
            "confidence": 1, "meter_confidence": 1, "method": "test"}


class Chop(unittest.TestCase):
    def setUp(self):
        self.a = load_fixture("analysis")

    def test_matches_golden(self):
        self.assertEqual(chop(self.a), load_fixture("chops"))

    def test_eighth_grid(self):
        c = chop(self.a, grid="1/8")
        validate_chops(c)
        self.assertEqual(len(c["chops"]), 82)
        self.assertAlmostEqual(c["chops"][0]["end"] - c["chops"][0]["start"], 0.25, places=6)

    def test_three_four_banks_on_bar_edges(self):
        c = chop(analysis_3_4())
        validate_chops(c)
        self.assertEqual(c["bank_size"], 15)

    def test_three_four_sixteenth_bank_size(self):
        c = chop(analysis_3_4(), grid="1/16")
        validate_chops(c)
        self.assertEqual(c["bank_size"], 12)

    def test_pickup_dropped(self):
        self.assertEqual(chop(self.a)["chops"][0]["start"], self.a["downbeats"][0])

    def test_beat_offset_moves_bar_one(self):
        later, earlier = chop(self.a, beat_offset=1), chop(self.a, beat_offset=-1)
        validate_chops(later); validate_chops(earlier)
        self.assertEqual(later["chops"][0]["start"], self.a["beats"][3])
        self.assertEqual(earlier["chops"][0]["start"], self.a["beats"][1])
        self.assertEqual((later["beat_offset"], earlier["beat_offset"]), (1, -1))

    def test_double_time(self):
        c = chop(self.a, bpm_mult=2)
        validate_chops(c)
        self.assertAlmostEqual(c["bpm"], 240, places=3)
        self.assertAlmostEqual(c["chops"][0]["end"] - c["chops"][0]["start"], 0.25, places=6)
        self.assertEqual(c["chops"][0]["start"], self.a["downbeats"][0])

    def test_half_time(self):
        c = chop(self.a, bpm_mult=0.5)
        validate_chops(c)
        self.assertAlmostEqual(c["bpm"], 60, places=3)
        self.assertAlmostEqual(c["chops"][0]["end"] - c["chops"][0]["start"], 1.0, places=6)

    def test_meter_override(self):
        c = chop(self.a, meter="3/4")
        validate_chops(c)
        self.assertEqual((c["beats_per_bar"], c["bank_size"]), (3, 15))
        self.assertEqual(c["chops"][0]["start"], self.a["downbeats"][0])

    def test_regrid_is_fast(self):
        a = long_analysis()
        t = time.perf_counter()
        for kw in ({}, {"grid": "1/16"}, {"bpm_mult": 2}, {"beat_offset": 2}):
            validate_chops(chop(a, **kw))
        self.assertLess((time.perf_counter() - t) / 4, 0.05)

    def test_rejects_invalid_analysis(self):
        bad = load_fixture("analysis"); bad["bpm"] = 10
        with self.assertRaises(ContractError):
            chop(bad)


if __name__ == "__main__":
    unittest.main()
