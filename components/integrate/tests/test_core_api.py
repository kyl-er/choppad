import os, sys, tempfile, unittest
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from _common import *  # noqa: E402,F403

import json, shutil, threading, urllib.error, urllib.request  # noqa: E402
from choppad import make_server, run_pipeline  # noqa: E402


class CoreApi(unittest.TestCase):
    """W3 endpoints on a synthetic click song: transient regrid, stretch, kits."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="choppad-core-api-")
        cls.addClassCleanup(shutil.rmtree, cls.tmp, ignore_errors=True)
        cls.media_root = os.path.join(cls.tmp, "media")
        cls.ids = []
        cls.truths = []
        for bpm in (120, 90):
            wav = os.path.join(cls.tmp, f"song{bpm}.wav")
            cls.truths.append(click_track(wav, bpm, n_beats=40, pickup=2))
            src = os.path.join(cls.tmp, f"song{bpm}.mp4")
            make_video(src, wav, cls.truths[-1]["duration_s"])
            cls.ids.append(os.path.basename(run_pipeline(src, cls.media_root)))
        cls.id, cls.id2 = cls.ids
        cls.srv = make_server(cls.media_root, port=0)
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.addClassCleanup(cls.srv.shutdown)

    def call(self, path, body=None, method=None):
        req = urllib.request.Request(self.base + path, data=None if body is None else json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"}, method=method or ("POST" if body is not None else "GET"))
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    # ---------------- transient regrid ----------------
    def test_regrid_transient(self):
        code, ch = self.call(f"/api/songs/{self.id}/regrid", {"mode": "transient", "sensitivity": 0.5})
        self.assertEqual(code, 200)
        self.assertEqual((ch["mode"], ch["sensitivity"]), ("transient", 0.5))
        starts = [c["start"] for c in ch["chops"]]
        self.assertEqual(len(starts), len(self.truths[0]["beats"]))
        for t, s in zip(self.truths[0]["beats"], starts):
            self.assertAlmostEqual(t, s, delta=0.010)
        self.assertEqual(json.load(open(os.path.join(self.media_root, self.id, "chops.json"))), ch)
        self.assertTrue(os.path.isfile(os.path.join(self.media_root, self.id, "onsets.json")))
        # grid mode again, tagged
        code, ch = self.call(f"/api/songs/{self.id}/regrid", {"mode": "grid", "grid": "1/4"})
        self.assertEqual((code, ch["mode"], ch["grid"]), (200, "grid", "1/4"))
        validate_chops(ch)
        # old-style call without a mode is a grid chop
        code, ch = self.call("/api/regrid", {"id": self.id, "grid": "1/8"})
        self.assertEqual((ch["mode"], ch["grid"]), ("grid", "1/8"))
        code, _ = self.call(f"/api/songs/{self.id}/regrid", {"mode": "bogus"})
        self.assertEqual(code, 400)
        self.call(f"/api/songs/{self.id}/regrid", {"grid": "1/4"})

    # ---------------- time-stretch ----------------
    def test_stretch_endpoint(self):
        import wave
        code, ch = self.call(f"/api/songs/{self.id}/regrid", {"mode": "grid", "grid": "1/4"})
        pad0 = ch["chops"][ch["banks"][0][0]]
        dur = pad0["end"] - pad0["start"]
        code, r = self.call(f"/api/songs/{self.id}/stretch", {"bank": 0, "pad": 0, "ratio": 2.0})
        self.assertEqual(code, 200, r)
        self.assertAlmostEqual(r["duration"], dur * 2, delta=0.003)
        self.assertEqual((r["start"], r["end"]), (pad0["start"], pad0["end"]))
        self.assertRegex(r["url"], rf"^/media/{self.id}/stretch/[0-9a-f]{{16}}\.wav$")
        with urllib.request.urlopen(self.base + r["url"]) as resp:
            body = resp.read()
        self.assertEqual(body[:4], b"RIFF")
        path = os.path.join(self.media_root, self.id, "stretch", r["url"].rsplit("/", 1)[1])
        with wave.open(path) as w:
            self.assertAlmostEqual(w.getnframes() / w.getframerate(), r["duration"], delta=1e-4)
        # cached: same hash, same file; target_bpm = source tempo / target (120 -> 150 BPM)
        code, r2 = self.call(f"/api/songs/{self.id}/stretch", {"bank": 0, "pad": 0, "ratio": 2.0})
        self.assertEqual(r2["hash"], r["hash"])
        code, r3 = self.call(f"/api/songs/{self.id}/stretch", {"bank": 0, "pad": 0, "target_bpm": 150})
        self.assertAlmostEqual(r3["ratio"], 120 / 150, places=3)
        # explicit start/end win over the pad (custom slice), bad input is a 400
        code, r4 = self.call(f"/api/songs/{self.id}/stretch", {"start": 3.0, "end": 3.5, "ratio": 1.5})
        self.assertAlmostEqual(r4["duration"], 0.75, delta=0.003)
        for bad in ({"bank": 0, "pad": 0}, {"bank": 0, "pad": 0, "ratio": 99}, {"bank": 7, "pad": 3, "ratio": 1.2},
                    {"start": 5, "end": 4, "ratio": 1.2}):
            code, e = self.call(f"/api/songs/{self.id}/stretch", bad)
            self.assertEqual(code, 400, bad)
            self.assertIn("error", e)
        self.assertEqual(self.call("/media/%s/stretch/../audio.wav" % self.id)[0], 404)
        self.assertEqual(self.call(f"/media/{self.id}/stretch/nothex.wav")[0], 404)


if __name__ == "__main__":
    unittest.main()
