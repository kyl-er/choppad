import os, sys, tempfile, unittest
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from _common import *  # noqa: E402,F403

from choppad_rhythm import analyze  # noqa: E402


def frac_close(found, truth, tol=0.02):
    return sum(any(abs(f - t) <= tol for f in found) for t in truth) / len(truth)


class Rhythm(unittest.TestCase):
    tmp = tempfile.mkdtemp(prefix="choppad-rhythm-")

    def run_case(self, bpm, bpb=4, offset=0.5, meter=None, n_beats=48):
        wav = os.path.join(self.tmp, f"{bpm}-{bpb}-{offset}.wav")
        truth = click_track(wav, bpm, beats_per_bar=bpb, offset_s=offset, n_beats=n_beats)
        a = analyze(wav, meter=meter)
        validate_analysis(a, duration_s=truth["duration_s"])
        return a, truth

    def test_120_four_four(self):
        a, t = self.run_case(120)
        self.assertAlmostEqual(a["bpm"], 120, delta=1.2)
        self.assertGreaterEqual(frac_close(a["beats"], t["beats"]), 0.9)

    def test_odd_tempo_and_offset(self):
        a, t = self.run_case(87.5, offset=0.37)
        self.assertAlmostEqual(a["bpm"], 87.5, delta=0.9)
        self.assertGreaterEqual(frac_close(a["beats"], t["beats"]), 0.9)

    def test_no_double_time(self):
        a, _ = self.run_case(70)
        self.assertAlmostEqual(a["bpm"], 70, delta=1.0)

    def test_downbeat_phase_with_meter_hint(self):
        a, t = self.run_case(100, meter="4/4")
        self.assertEqual(a["meter"]["beats_per_bar"], 4)
        self.assertGreaterEqual(frac_close(a["downbeats"], t["downbeats"], tol=0.03), 0.8)

    def test_three_four_detected(self):
        a, t = self.run_case(90, bpb=3)
        self.assertEqual(a["meter"]["beats_per_bar"], 3)
        self.assertGreaterEqual(frac_close(a["downbeats"], t["downbeats"], tol=0.03), 0.8)

    def test_meter_override(self):
        a, _ = self.run_case(90, bpb=3, meter="4/4")
        self.assertEqual(a["meter"]["beats_per_bar"], 4)


if __name__ == "__main__":
    unittest.main()
