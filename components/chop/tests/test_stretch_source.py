"""W3b: stretch_slice can cut its slice from a stem of the song instead of the full mix."""
import os, sys, shutil, tempfile, unittest
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from _common import *  # noqa: E402,F403
import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402
from choppad_stretch import StretchError, stretch_hash, stretch_slice  # noqa: E402


class StretchSource(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="choppad-stretch-src-")
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        click_track(os.path.join(self.d, "audio.wav"), 100, n_beats=8, pickup=0)
        os.makedirs(os.path.join(self.d, "stems"))
        x, sr = sf.read(os.path.join(self.d, "audio.wav"), dtype="float32", always_2d=True)
        sf.write(os.path.join(self.d, "stems", "drums.wav"), x * 0.25, sr, subtype="PCM_16")

    def test_the_stem_is_cut_and_the_hash_differs_from_the_mix(self):
        mix = stretch_slice(self.d, 0.0, 1.2, 1.5)
        stem = stretch_slice(self.d, 0.0, 1.2, 1.5, "stems/drums.wav")
        self.assertNotEqual(mix["hash"], stem["hash"])
        self.assertAlmostEqual(stem["duration"], mix["duration"], delta=0.005)
        a, _ = sf.read(mix["path"], always_2d=True)
        b, _ = sf.read(stem["path"], always_2d=True)
        self.assertAlmostEqual(np.abs(b).max() / np.abs(a).max(), 0.25, delta=0.03)

    def test_the_plain_song_keeps_its_old_hash(self):
        import hashlib
        st = os.stat(os.path.join(self.d, "audio.wav"))
        old = hashlib.sha1(f"v1:{st.st_size}:{st.st_mtime_ns}:{0.0:.5f}:{1.2:.5f}:{1.5:.6f}".encode()).hexdigest()[:16]
        self.assertEqual(stretch_hash(self.d, 0.0, 1.2, 1.5), old, "renderings made before stems existed stay valid")

    def test_bad_sources(self):
        for bad in ("../audio.wav", "stems/guitar.wav", "audio.mp3", "/etc/passwd"):
            with self.subTest(bad), self.assertRaises(StretchError):
                stretch_slice(self.d, 0.0, 1.0, 1.5, bad)
        with self.assertRaisesRegex(StretchError, "stems step"):
            stretch_slice(self.d, 0.0, 1.0, 1.5, "stems/bass.wav")


if __name__ == "__main__":
    unittest.main()
