"""CHOP-13: bank_pads and pad_pcm for kept pads (contracts §15): the pad plays its sample file with override.params as its sound."""
import os, sys, tempfile, unittest
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from _common import *  # noqa: E402,F403
import json, shutil, struct, wave  # noqa: E402
import choppad_export as X  # noqa: E402

SR = 44100
SRC = "/media/_mega/m1/samples/0123456789abcdef.wav"


def write_wav(path, samples):
    with wave.open(path, "wb") as w:
        w.setnchannels(2), w.setsampwidth(2), w.setframerate(SR)
        w.writeframes(b"".join(struct.pack("<hh", s, s) for s in samples))


class KeptPads(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="choppad-export-mega-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.song = os.path.join(self.root, "a")
        os.makedirs(self.song)
        write_wav(os.path.join(self.song, "audio.wav"), [1000] * (SR * 3))
        with open(os.path.join(self.song, "chops.json"), "w") as f:
            json.dump({"banks": [[0, 1]], "chops": [{"index": 0, "start": 0.0, "end": 0.5}, {"index": 1, "start": 0.5, "end": 1.0}]}, f)
        os.makedirs(os.path.join(self.root, "_mega", "m1", "samples"))
        self.sample = os.path.join(self.root, "_mega", "m1", "samples", "0123456789abcdef.wav")
        write_wav(self.sample, [5000] * (SR // 2))
        self.kept = {"0:1": {"start": 0, "end": 0.5, "src": SRC, "kept": True, "params": {"gainDb": -6, "pitch": 5, "reverse": False}}}

    def test_a_kept_pad_plays_its_file_with_its_own_params(self):
        pads = {p["pad"]: p for p in X.bank_pads(self.song, 0, self.kept, [{"gainDb": 3}, {"gainDb": 3, "pitch": 1}])}
        self.assertEqual((pads[1]["audio"], pads[1]["kept"], pads[1]["gain_db"], pads[1]["pitch"]), (self.sample, True, -6.0, 5.0))
        self.assertEqual(pads[1]["params"]["gainDb"], -6)               # not the shared pad's 3
        self.assertEqual((pads[0]["audio"], pads[0]["kept"], pads[0]["gain_db"]), (os.path.join(self.song, "audio.wav"), False, 3.0))

    def test_without_params_a_kept_pad_is_neutral_and_pcm_is_the_file_as_it_is(self):
        kept = {"0:1": {**self.kept["0:1"], "params": {}}}
        p = [p for p in X.bank_pads(self.song, 0, kept, [{"gainDb": 3}] * 2) if p["pad"] == 1][0]
        self.assertEqual((p["gain_db"], p["pitch"]), (0.0, 0.0))
        sr, nch, sw, data = X.pad_pcm(p, {})
        with wave.open(self.sample) as w:
            self.assertEqual(data, w.readframes(w.getnframes()))        # no second declick

    def test_a_kept_pad_with_a_filter_bakes_it_into_exports(self):
        kept = {"0:1": {**self.kept["0:1"], "params": {"filter": {"type": "lp", "cutoff": 200, "q": 0.7}}}}
        p = [p for p in X.bank_pads(self.song, 0, kept, []) if p["pad"] == 1][0]
        plain = [p for p in X.bank_pads(self.song, 0, {"0:1": {**kept["0:1"], "params": {}}}, []) if p["pad"] == 1][0]
        self.assertNotEqual(X.pad_pcm(p, {})[3], X.pad_pcm(plain, {})[3])

    def test_pad_wav_bytes_neutral_leaves_the_params_out(self):
        p = [p for p in X.bank_pads(self.song, 0, {}, [{"filter": {"type": "lp", "cutoff": 200, "q": 0.7}}]) if p["pad"] == 0][0]
        baked, dur = X.pad_wav_bytes(p)
        neutral, dur2 = X.pad_wav_bytes(p, neutral=True)
        self.assertNotEqual(baked, neutral)
        self.assertAlmostEqual(dur2, 0.5, places=3)
        self.assertEqual(neutral, X.pad_wav_bytes({**p, "params": {}})[0])

    def test_no_source_song_and_only(self):
        pads = X.bank_pads(None, 0, self.kept, [], media_root=self.root)        # a mega bank without a source song
        self.assertEqual([p["pad"] for p in pads], [1])
        with self.assertRaises(X.NoSourceSong):
            X.bank_pads(None, 0, {"0:0": {"start": 0, "end": 1}}, [], media_root=self.root)
        self.assertEqual([p["pad"] for p in X.bank_pads(self.song, 0, {}, [], only=1)], [1])
        # `only` looks at that pad alone: another pad's missing file cannot fail it
        bad = {"0:0": {"start": 0, "end": 1, "src": "/media/a/resample/0123456789abcdef.wav"}}
        self.assertEqual([p["pad"] for p in X.bank_pads(self.song, 0, bad, [], only=1)], [1])
        with self.assertRaises(ValueError):
            X.bank_pads(self.song, 0, bad, [])

    def test_song_project_prefers_the_newest_mega_bank_and_falls_back_to_project_json(self):
        self.assertEqual(X.song_project(self.song), {})
        with open(os.path.join(self.song, "project.json"), "w") as f:
            json.dump({"pads": [], "overrides": {"0:0": {"start": 1, "end": 2}}}, f)
        self.assertEqual(X.project_overrides(self.song), {"0:0": {"start": 1, "end": 2}})
        for mid, upd, ov in (("m1", 5, {"0:1": {"start": 0, "end": 1}}), ("m2", 9, {"0:2": {"start": 0, "end": 1}}), ("m3", 99, {"0:3": {}})):
            os.makedirs(os.path.join(self.root, "_mega", mid), exist_ok=True)
            with open(os.path.join(self.root, "_mega", mid, "mega.json"), "w") as f:
                json.dump({"id": mid, "source": "a" if mid != "m3" else "b", "updated": upd, "project": {"pads": [], "overrides": ov}}, f)
        self.assertEqual(list(X.project_overrides(self.song)), ["0:2"])
        os.makedirs(os.path.join(self.root, "_mega", ".trash", "m9"))
        with open(os.path.join(self.root, "_mega", ".trash", "m9", "mega.json"), "w") as f:
            json.dump({"id": "m9", "source": "a", "updated": 1000, "project": {"overrides": {"0:9": {}}}}, f)
        self.assertEqual(list(X.project_overrides(self.song)), ["0:2"])


if __name__ == "__main__":
    unittest.main()
