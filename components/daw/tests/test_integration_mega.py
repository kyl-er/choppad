"""Opt-in (CHOPPAD_DAW=1) real-DAW check of a mega bank project (CHOP-13 step D): h2cli renders the Hydrogen song built from a mega bank (a kept
pad from its own sample file, a free pad from the source song, the mega bank's tempo, the pattern's own bar length). Headless, offline,
private config (`--config`), base config = the system default, never the owner's. Default runs skip it and start nothing."""
import glob, hashlib, json, os, shutil, sys, tempfile, unittest, wave
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from support import make_media  # noqa: E402
import daw_project  # noqa: E402
import hydrogen as H  # noqa: E402
from test_integration import onsets  # noqa: E402

ON = os.environ.get("CHOPPAD_DAW") == "1"
MID = "m0123456789a"
BPM, BEATS = 100.0, [(0.0, 0), (1.0, 1), (4.5, 0)]   # (beat, pad): a pattern of 2 bars of 3 beats = 6 beats; the hit at 4.5 is in bar 2


def digest(paths):
    out = {}
    for root in paths:
        for f in glob.glob(os.path.join(os.path.expanduser(root), "**"), recursive=True):
            if os.path.isfile(f):
                with open(f, "rb") as fh:
                    out[f] = hashlib.md5(fh.read()).hexdigest()
    return out


@unittest.skipUnless(ON, "set CHOPPAD_DAW=1 to run against the real Hydrogen")
class RealMegaProject(unittest.TestCase):
    def test_h2cli_renders_a_mega_bank_on_the_beat_with_its_own_bar_length(self):
        import numpy as np
        watched = digest(["~/.hydrogen", "~/.config/ardour9"])
        tmp = tempfile.mkdtemp(prefix="choppad-daw-mega-")
        root, song = make_media(bpm=120, title="Source")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        samples = os.path.join(root, "_mega", MID, "samples")
        os.makedirs(samples)
        t = np.arange(int(0.4 * 44100)) / 44100
        tone = (0.6 * np.sin(2 * np.pi * 880 * t) * 32767).astype("<i2")
        sample = os.path.join(samples, "0123456789abcdef.wav")
        with wave.open(sample, "wb") as w:
            w.setnchannels(2), w.setsampwidth(2), w.setframerate(44100)
            w.writeframes(np.repeat(tone[:, None], 2, axis=1).tobytes())
        kept = {"start": 0, "end": 0.4, "src": f"/media/_mega/{MID}/samples/0123456789abcdef.wav", "kept": True, "params": {}}
        doc = {"id": MID, "name": "Real project", "source": os.path.basename(song),
               "project": {"pads": [{} for _ in range(16)], "overrides": {"0:0": kept}, "tempo": {"bpm": BPM, "follow": False},
                           "pattern": {"bars": 2, "beatsPerBar": 3, "events": [{"beat": b, "pad": p, "bank": 0, "vel": 1.0, "pass": 1} for b, p in BEATS]}}}
        with open(os.path.join(song, "chops.json")) as f:
            chops = json.load(f)
        project = daw_project.build_mega_project(root, doc, chops, song, os.path.join(tmp, "kits"), bank=0)
        self.assertEqual((project.bpm, project.beats_per_bar), (BPM, 3))
        b = H.HydrogenBridge(os.path.join(tmp, "state"), headless=True, base_conf=H.DEFAULT_CONF)
        if not b._which("h2cli"):
            self.skipTest("h2cli not installed")
        out = os.path.join(tmp, "h2.wav")
        b.render(project, out)
        times, peak, sr = onsets(out)
        self.assertGreater(peak, 0.05, "rendered silence")
        want = [beat * 60.0 / BPM for beat, _ in BEATS]
        self.assertEqual(len(times), len(want), times)
        for got, w in zip(times, want):
            self.assertAlmostEqual(got, w, delta=0.03)
        pick = lambda d: {k: v for k, v in d.items() if k.endswith("hydrogen.conf") or "/ardour9/" in k}  # noqa: E731
        self.assertEqual(pick(digest(["~/.hydrogen", "~/.config/ardour9"])), pick(watched))


if __name__ == "__main__":
    unittest.main()
