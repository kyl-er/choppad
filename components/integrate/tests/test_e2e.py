import os, sys, tempfile, unittest
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from _common import *  # noqa: E402,F403

import json, threading, time, urllib.request  # noqa: E402
from choppad import make_server, run_pipeline  # noqa: E402


class EndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="choppad-e2e-")
        wav = os.path.join(cls.tmp, "song.wav")
        cls.truth = click_track(wav, 120, n_beats=40, pickup=2)
        src = os.path.join(cls.tmp, "song.mp4")
        make_video(src, wav, cls.truth["duration_s"])
        cls.media_root = os.path.join(cls.tmp, "media")
        cls.dir = run_pipeline(src, cls.media_root)
        cls.id = os.path.basename(cls.dir)

    def read(self, name):
        return json.load(open(os.path.join(self.dir, name)))

    def test_all_artifacts_valid(self):
        meta = self.read("meta.json")
        validate_meta(meta, media_dir=self.dir)
        validate_analysis(self.read("analysis.json"), duration_s=meta["duration_s"])
        validate_chops(self.read("chops.json"))

    def test_tempo_survives_pipeline(self):
        self.assertAlmostEqual(self.read("chops.json")["bpm"], 120, delta=1.2)
        self.assertGreaterEqual(len(self.read("chops.json")["chops"]), 32)

    def test_server_serves_ui_and_media(self):
        srv = make_server(self.media_root, port=0)
        port = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            base = f"http://127.0.0.1:{port}"
            html = urllib.request.urlopen(f"{base}/").read().decode()
            self.assertIn("<script", html)
            for f in ("main.js", "engine.js", "ui.js", "layout.js", "midi.js"):
                self.assertEqual(urllib.request.urlopen(f"{base}/{f}").status, 200)
            chops = json.loads(urllib.request.urlopen(f"{base}/media/{self.id}/chops.json").read())
            validate_chops(chops)
            req = urllib.request.Request(f"{base}/media/{self.id}/video.mp4", headers={"Range": "bytes=0-99"})
            self.assertEqual(urllib.request.urlopen(req).status, 206)  # <video> seeking needs Range support
            t = time.perf_counter()
            body = json.dumps({"id": self.id, "grid": "1/8", "meter": None, "beat_offset": 1, "bpm_mult": 1}).encode()
            req = urllib.request.Request(f"{base}/api/regrid", data=body, headers={"Content-Type": "application/json"})
            regrid = json.loads(urllib.request.urlopen(req).read())
            self.assertLess(time.perf_counter() - t, 0.05 + 0.05)  # 50 ms chop budget + local HTTP overhead
            validate_chops(regrid)
            self.assertEqual((regrid["grid"], regrid["beat_offset"]), ("1/8", 1))
            self.assertEqual(json.load(open(os.path.join(self.dir, "chops.json"))), regrid)
        finally:
            srv.shutdown()


if __name__ == "__main__":
    unittest.main()
