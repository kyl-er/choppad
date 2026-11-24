"""A kept pad sounds as loud as it did before it was kept (CHOP-13). The browser plays a chop at its song's normalising
gain; the kept sample stays the raw audio and the override remembers that gain."""
import os, sys, tempfile, unittest
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from _common import *  # noqa: E402,F403

import json, shutil, wave  # noqa: E402
import numpy as np  # noqa: E402
import mega as M  # noqa: E402
from test_mega import SR, write_song  # noqa: E402


def write_wav(path, x):
    with wave.open(path, "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(SR)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype("<i2").tobytes())


class KeptLevel(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="choppad-level-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def test_gain_is_the_engines_normalisation(self):
        for amp, want in ((0.5, 0.891 / 0.5), (0.891, 1.0), (0.1, 4.0), (0.0, 1.0)):
            path = os.path.join(self.root, f"a{amp}.wav")
            write_wav(path, amp * np.sin(np.arange(SR) * 0.05))
            self.assertAlmostEqual(M.normalization_gain(path), want, delta=0.01, msg=f"peak {amp}")

    def test_full_scale_negative_peak_does_not_wrap(self):
        path = os.path.join(self.root, "neg.wav")
        with wave.open(path, "wb") as w:
            w.setnchannels(1), w.setsampwidth(2), w.setframerate(SR)
            w.writeframes(np.array([0, -32768, 0], dtype="<i2").tobytes())
        self.assertAlmostEqual(M.normalization_gain(path), 0.891, delta=0.001)

    def test_a_kept_grid_pad_carries_its_songs_gain_and_the_sample_stays_raw(self):
        write_song(self.root, "songA", "A", 220, 16)   # sine at half scale: the engine plays it at 0.891 / 0.5
        doc = M.create(self.root, name="t", source="songA")
        chops = json.load(open(os.path.join(self.root, "songA", "chops.json")))
        r = M.render_keep(self.root, doc["id"], "songA", doc["project"], chops, 0, 3)
        self.assertAlmostEqual(r["gain"], 0.891 / 0.5, delta=0.01)
        sample = os.path.join(self.root, r["src"][len("/media/"):])
        with wave.open(sample, "rb") as w:
            x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
        self.assertAlmostEqual(np.abs(x).max() / 32768.0, 0.5, delta=0.01)   # not scaled: exports are unchanged

    def test_a_resample_keeps_gain_one(self):
        write_song(self.root, "songA", "A", 220, 16)
        doc = M.create(self.root, name="t", source="songA")
        chops = json.load(open(os.path.join(self.root, "songA", "chops.json")))
        os.makedirs(os.path.join(self.root, "songA", "resample"))
        write_wav(os.path.join(self.root, "songA", "resample", "0123456789abcdef.wav"), 0.3 * np.sin(np.arange(SR // 2) * 0.05))
        project = {**doc["project"], "overrides": {"0:1": {"start": 0, "end": 0.5, "src": "/media/songA/resample/0123456789abcdef.wav"}}}
        r = M.render_keep(self.root, doc["id"], "songA", project, chops, 0, 1)
        self.assertNotIn("gain", r)   # a resample played at gain 1 before it was kept, and still does


if __name__ == "__main__":
    unittest.main()
