import os, sys, tempfile, unittest
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from _common import *  # noqa: E402,F403
import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402
from choppad_key import NAMES, detect_key, detect_key_file, key_label  # noqa: E402

SR = 22050


def tone(midi, seconds):
    f = 440.0 * 2 ** ((midi - 69) / 12)
    t = np.arange(int(seconds * SR)) / SR
    return sum(a * np.sin(2 * np.pi * f * k * t) for k, a in ((1, 1.0), (2, 0.5), (3, 0.25))) * np.exp(-t / 2.0)


def progression(tonic_midi, mode, seconds=2.0, noise=0.0, seed=3):
    """I-IV-V-I (major) or i-iv-V-i (minor) triads, with a bass note, in the key `tonic_midi`."""
    third = 4 if mode == "major" else 3
    iv_third = 4 if mode == "major" else 3
    chords = [(0, third), (5, iv_third), (7, 4), (0, third)]
    out = []
    for root, th in chords:
        base = tonic_midi + root
        c = tone(base - 12, seconds) + tone(base, seconds) + tone(base + th, seconds) + tone(base + 7, seconds)
        out.append(c)
    x = np.concatenate(out * 2)
    if noise:
        x = x + noise * np.random.default_rng(seed).standard_normal(len(x))
    return (x / np.abs(x).max() * 0.5).astype(np.float32)


class Key(unittest.TestCase):
    def test_major_and_minor_in_several_keys(self):
        for tonic in (0, 2, 5, 9, 11):                  # C D F A B
            for mode in ("major", "minor"):
                k = detect_key(progression(48 + tonic, mode), SR)
                self.assertEqual((k["tonic"], k["mode"]), (NAMES[tonic], mode), f"{NAMES[tonic]} {mode}")
                self.assertGreater(k["confidence"], 0.3)
                self.assertLessEqual(k["confidence"], 1.0)

    def test_file_and_label(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "a.wav")
            sf.write(p, progression(57, "minor"), SR)    # A minor
            k = detect_key_file(p)
        self.assertEqual(k["tonic"], "A")
        self.assertEqual(k["mode"], "minor")
        self.assertEqual(key_label(k), "A min")
        self.assertEqual(key_label({"tonic": "F#", "mode": "major"}), "F# maj")
        self.assertEqual(key_label(None), "")

    def test_noise_and_silence_have_low_confidence(self):
        noise = np.random.default_rng(1).standard_normal(SR * 8).astype(np.float32) * 0.1
        self.assertLess(detect_key(noise, SR)["confidence"], 0.35)
        self.assertEqual(detect_key(np.zeros(SR * 3, dtype=np.float32), SR)["confidence"], 0.0)
        # noise on top of a clear key keeps the answer
        k = detect_key(progression(48, "major", noise=0.05), SR)
        self.assertEqual((k["tonic"], k["mode"]), ("C", "major"))


if __name__ == "__main__":
    unittest.main()
