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


if __name__ == "__main__":
    unittest.main()
