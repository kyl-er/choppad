import os, sys, tempfile, unittest
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from _common import *  # noqa: E402,F403

from choppad_chop import chop  # noqa: E402


def three_four():
    beats = [round(0.2 + k * (60 / 90), 6) for k in range(40)]
    return {"schema": "choppad.analysis/1", "bpm": 90.0, "meter": {"beats_per_bar": 3, "beat_unit": 4},
            "beats": beats, "downbeats": beats[1::3], "confidence": 0.9, "method": "test"}


class Chop(unittest.TestCase):
    def test_matches_golden(self):
        self.assertEqual(chop(load_fixture("analysis")), load_fixture("chops"))

    def test_eighth_grid(self):
        c = chop(load_fixture("analysis"), grid="1/8")
        validate_chops(c)
        self.assertEqual(len(c["chops"]), 82)
        self.assertAlmostEqual(c["chops"][0]["end"] - c["chops"][0]["start"], 0.25, places=6)

    def test_three_four_banks_on_bar_edges(self):
        c = chop(three_four())
        validate_chops(c)
        self.assertEqual(len(c["banks"][0]), 15)  # 5 bars of 3; a 16th pad would cross a bar line

    def test_pickup_dropped(self):
        c = chop(load_fixture("analysis"))
        self.assertEqual(c["chops"][0]["start"], load_fixture("analysis")["downbeats"][0])

    def test_rejects_invalid_analysis(self):
        bad = load_fixture("analysis"); bad["bpm"] = 10
        with self.assertRaises(ContractError):
            chop(bad)


if __name__ == "__main__":
    unittest.main()
