import os
import sys
import tempfile
import unittest

import numpy as np
import soundfile as sf

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from choppad_fx import apply_to_file, filter_setting, is_neutral  # noqa: E402


def _tone(path, sr=44100, secs=0.5):
    t = np.arange(int(sr * secs)) / sr
    x = 0.3 * (np.sin(2 * np.pi * 100 * t) + np.sin(2 * np.pi * 8000 * t))
    sf.write(path, np.stack([x, x], 1), sr, subtype="PCM_16")


def _band(x, sr, f):
    spec = np.abs(np.fft.rfft(x[:, 0]))
    freqs = np.fft.rfftfreq(len(x), 1 / sr)
    return spec[np.argmin(np.abs(freqs - f))]


class TestFx(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_neutral_detection(self):
        self.assertTrue(is_neutral({}))
        self.assertTrue(is_neutral({"size": 0.9}))
        self.assertFalse(is_neutral({"reverb": 0.2}))
        self.assertFalse(is_neutral({"tone": -0.3}))
        self.assertIsNone(filter_setting(0.01))

    def test_dark_tone_cuts_highs_and_lifts_lows(self):
        p = os.path.join(self.tmp.name, "a.wav")
        _tone(p)
        before, sr = sf.read(p, always_2d=True)
        apply_to_file(p, {"tone": -1})
        after, _ = sf.read(p, always_2d=True)
        self.assertLess(_band(after, sr, 8000), 0.5 * _band(before, sr, 8000))
        self.assertGreater(_band(after, sr, 100), _band(before, sr, 100))

    def test_reverb_adds_a_tail(self):
        p = os.path.join(self.tmp.name, "b.wav")
        _tone(p, secs=0.2)
        n0 = sf.info(p).frames
        apply_to_file(p, {"reverb": 0.5, "size": 0.6})
        self.assertGreater(sf.info(p).frames, n0)


if __name__ == "__main__":
    unittest.main()
