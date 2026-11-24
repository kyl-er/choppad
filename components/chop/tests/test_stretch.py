import os, sys, shutil, tempfile, unittest
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from _common import *  # noqa: E402,F403
import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402
from choppad_stretch import StretchError, ratio_for_tempo, stretch_slice  # noqa: E402
from choppad_transient import detect_onsets, select_onsets  # noqa: E402


def song_dir(bpm, beats, offset=0.2):
    d = tempfile.mkdtemp(prefix="choppad-stretch-")
    truth = click_track(os.path.join(d, "audio.wav"), bpm, n_beats=beats, pickup=0, offset_s=offset)
    return d, truth


def onsets_of(path):
    x, sr = sf.read(path, dtype="float32", always_2d=True)
    return select_onsets(detect_onsets((x.mean(axis=1), sr)), 0.6)


class Stretch(unittest.TestCase):
    def test_72_to_101_bpm_stays_in_time_for_8_bars(self):
        d, truth = song_dir(72, 32)   # 8 bars of 4/4
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        ratio = ratio_for_tempo(72, 101)
        self.assertAlmostEqual(ratio, 0.7129, places=4)
        r = stretch_slice(d, 0.0, truth["duration_s"], ratio)
        on = onsets_of(r["path"])
        ibi = 60 / 101
        self.assertEqual(len(on), 32)
        first = on[0]
        self.assertAlmostEqual(first, 0.2 * ratio, delta=0.025)  # rubberband moves the attack a little
        for k, t in enumerate(on):  # no drift: every click sits on the new 101 BPM grid
            self.assertAlmostEqual(t - first, k * ibi, delta=0.010, msg=f"click {k}")
        # the total length follows the ratio
        self.assertAlmostEqual(r["duration"], truth["duration_s"] * ratio, delta=0.005)

    def test_pitch_is_kept(self):
        d, truth = song_dir(90, 8)
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        r = stretch_slice(d, 0.2, 0.2 + 0.4, 1.6)   # the first (accented) click, 1.6x longer
        y, sr = sf.read(r["path"], dtype="float32", always_2d=True)
        seg = y[:int(0.15 * sr), 0]
        spec = np.abs(np.fft.rfft(seg * np.hanning(len(seg))))
        peak_hz = np.argmax(spec) * sr / len(seg)
        self.assertAlmostEqual(peak_hz, 1500.0, delta=80)   # the first beat is an accented 1500 Hz click; still 1500 Hz

    def test_cache_and_errors(self):
        d, truth = song_dir(100, 8)
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        a = stretch_slice(d, 0.2, 1.0, 0.8)
        mtime = os.path.getmtime(a["path"])
        b = stretch_slice(d, 0.2, 1.0, 0.8)
        self.assertEqual((a["hash"], os.path.getmtime(b["path"])), (b["hash"], mtime))
        self.assertNotEqual(stretch_slice(d, 0.2, 1.0, 0.9)["hash"], a["hash"])
        for bad in ((1.0, 0.5, 1.0), (0.0, 1.0, 9.0), (0.0, 1.0, 0.1)):
            with self.assertRaises(StretchError):
                stretch_slice(d, *bad)
        with self.assertRaises(StretchError):
            stretch_slice(d, 500.0, 501.0, 1.0)


if __name__ == "__main__":
    unittest.main()
