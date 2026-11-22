"""Opt-in tests against the real DAWs (CHOPPAD_DAW=1): h2cli renders a generated song, a headless Hydrogen takes OSC, and
ardour9-export renders a generated session. Default test runs skip all of this and never start a DAW.

Every test also checks that the owner's own config files (~/.hydrogen, ~/.config/ardour9) are not touched.
"""
import glob, hashlib, os, shutil, sys, tempfile, time, unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from support import make_media  # noqa: E402
import daw_project  # noqa: E402
import hydrogen as H  # noqa: E402
import ardour as A  # noqa: E402

ON = os.environ.get("CHOPPAD_DAW") == "1"
EVENTS = [(0.0, 0), (1.0, 1), (2.5, 5), (3.25, 3)]      # (beat, pad) at 120 BPM -> 0, 0.5, 1.25, 1.625 s
EXPECTED_S = [b * 0.5 for b, _ in EVENTS]


def digest(paths):
    out = {}
    for root in paths:
        for f in glob.glob(os.path.join(os.path.expanduser(root), "**"), recursive=True):
            if os.path.isfile(f):
                with open(f, "rb") as fh:
                    out[f] = hashlib.md5(fh.read()).hexdigest()
    return out


def onsets(path, floor=0.08, quiet_s=0.08):
    """Times (s) where the signal rises above `floor` x peak after at least `quiet_s` of near-silence."""
    import numpy as np
    import soundfile as sf
    x, sr = sf.read(path, always_2d=True)
    m = np.abs(x).max(axis=1)
    thr = floor * float(m.max())
    idx = np.nonzero(m > thr)[0]
    out, last = [], -10 ** 9
    for i in idx:
        if i - last > quiet_s * sr:
            out.append(i / sr)
        last = i
    return out, float(m.max()), sr


@unittest.skipUnless(ON, "set CHOPPAD_DAW=1 to run against the real Hydrogen and Ardour")
class RealDaws(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="choppad-daw-int-")
        cls.media, song = make_media(bpm=120, pattern={"bars": 2, "events": [
            {"beat": b, "pad": p, "bank": 0, "vel": 1.0} for b, p in EVENTS]})
        cls.project = daw_project.build_project(song, os.path.join(cls.tmp, "kits"), bank=0)
        cls.watched = digest(["~/.hydrogen", "~/.config/ardour9"])

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)
        shutil.rmtree(cls.media, ignore_errors=True)

    def assert_owner_config_untouched(self):
        now = digest(["~/.hydrogen", "~/.config/ardour9"])
        # the owner's own running Hydrogen may autosave its song; only configs matter here
        pick = lambda d: {k: v for k, v in d.items() if k.endswith(("hydrogen.conf",)) or "/ardour9/" in k}  # noqa: E731
        self.assertEqual(pick(now), pick(self.watched))

    def test_hydrogen_h2cli_renders_the_pattern_on_the_beat(self):
        b = H.HydrogenBridge(os.path.join(self.tmp, "state"), headless=True)
        if not b._which("h2cli"):
            self.skipTest("h2cli not installed")
        out = os.path.join(self.tmp, "h2.wav")
        b.render(self.project, out)
        times, peak, sr = onsets(out)
        self.assertGreater(peak, 0.05, "rendered silence")
        self.assertEqual(len(times), len(EXPECTED_S), times)
        for got, want in zip(times, EXPECTED_S):
            self.assertAlmostEqual(got, want, delta=0.02)
        self.assert_owner_config_untouched()

    def test_headless_hydrogen_takes_osc_and_goes_away(self):
        b = H.HydrogenBridge(os.path.join(self.tmp, "state2"), headless=True, volume=0.0)   # silent: volume 0
        if not b.available():
            self.skipTest("hydrogen not installed")
        info = b.launch(self.project)
        try:
            self.assertTrue(os.path.isfile(info["song"]))
            self.assertIsNone(b.proc.poll())
            b.transport("play", bpm=100.0)
            time.sleep(0.5)
            b.note(2, 0.5)
            b.transport("stop")
            b.push(self.project)
            time.sleep(0.5)
            self.assertIsNone(b.proc.poll(), "Hydrogen died after OPEN_SONG/play/stop")
            with open(b.log, errors="replace") as f:
                log = f.read()
            self.assertIn("Osc server started", log)
            self.assertIn("OPEN_SONG_Handler", log)
            self.assertIn("PLAY_Handler", log)
            self.assertNotIn("Failed to load", log)
        finally:
            pid = b.proc.pid
            b.close()
        time.sleep(0.5)
        self.assertFalse(os.path.exists(f"/proc/{pid}"), "bridge Hydrogen still running after close()")
        self.assert_owner_config_untouched()

    def test_ardour_session_renders_the_pattern_on_the_beat(self):
        b = A.ArdourBridge(os.path.join(self.tmp, "state3"), loops=1)
        if not b.available() or not b._which("ardour9-export"):
            self.skipTest("Ardour 9 tools not installed")
        out = os.path.join(self.tmp, "ardour.wav")
        info = b.render(self.project, out)
        times, peak, sr = onsets(out)
        self.assertGreater(peak, 0.05, "rendered silence")
        self.assertEqual(sr, 48000)
        self.assertEqual(len(times), len(EXPECTED_S), times)
        for got, want in zip(times, EXPECTED_S):
            self.assertAlmostEqual(got, want, delta=0.02)
        self.assert_owner_config_untouched()


if __name__ == "__main__":
    unittest.main()
