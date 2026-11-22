import os, sys, tempfile, unittest
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from _common import *  # noqa: E402,F403

import json, shutil, threading, urllib.error, urllib.request, wave  # noqa: E402
from choppad import make_server, run_pipeline  # noqa: E402


class KitsApi(unittest.TestCase):
    """/api/kits and kit export on two synthetic click songs (120 and 90 BPM)."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="choppad-kits-api-")
        cls.addClassCleanup(shutil.rmtree, cls.tmp, ignore_errors=True)
        cls.media_root = os.path.join(cls.tmp, "media")
        cls.ids = []
        for bpm in (120, 90):
            wav = os.path.join(cls.tmp, f"song{bpm}.wav")
            truth = click_track(wav, bpm, n_beats=40, pickup=2)
            src = os.path.join(cls.tmp, f"song{bpm}.mp4")
            make_video(src, wav, truth["duration_s"])
            cls.ids.append(os.path.basename(run_pipeline(src, cls.media_root)))
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

    def kit_doc(self):
        a, b = self.ids
        r = self.call(f"/api/songs/{b}/stretch", {"start": 2.0, "end": 2.6, "ratio": 0.5})[1]
        pads = [None] * 16
        pads[0] = {"song_id": a, "start": 1.0, "end": 1.5, "params": {"gainDb": -3, "pitch": 2, "bogus": 1}}
        pads[3] = {"song_id": b, "start": 2.0, "end": 2.6, "params": {}, "stretch": {"ratio": 0.5, "url": r["url"]}}
        pads[4] = {"song_id": b, "start": 4.0, "end": 4.5}
        return {"name": "Mixed Kit!", "pads": pads, "fx": {"reverb": 0.2, "nope": 1}}

    def test_kits_crud_and_validation(self):
        self.assertEqual(self.call("/api/kits"), (200, []))
        code, kit = self.call("/api/kits", self.kit_doc())
        self.assertEqual(code, 201, kit)
        self.assertEqual((kit["id"], kit["schema"], kit["name"]), ("mixed-kit", "choppad.kit/1", "Mixed Kit!"))
        self.assertEqual(len(kit["pads"]), 16)
        self.assertEqual(kit["pads"][0]["params"], {"gainDb": -3, "pitch": 2})   # unknown keys dropped
        self.assertEqual(kit["fx"], {"reverb": 0.2})
        on_disk = json.load(open(os.path.join(self.media_root, "_kits", "mixed-kit", "kit.json")))
        self.assertEqual({k: v for k, v in kit.items() if k != "id"}, on_disk)
        # the library does not list the kit folder as a song; the kit list shows it
        self.assertNotIn("_kits", [s["id"] for s in self.call("/api/songs")[1]])
        code, lst = self.call("/api/kits")
        self.assertEqual([(k["id"], k["pads"], k["songs"]) for k in lst], [("mixed-kit", 3, sorted(self.ids))])
        self.assertEqual(self.call("/api/kits/mixed-kit")[1]["pads"][3]["stretch"]["ratio"], 0.5)
        # the same name again gets a new id
        self.assertEqual(self.call("/api/kits", self.kit_doc())[1]["id"], "mixed-kit-2")
        # update keeps what is not sent
        code, upd = self.call("/api/kits/mixed-kit", {"name": "Renamed"}, "PUT")
        self.assertEqual((code, upd["name"], len(upd["pads"]), upd["pads"][4]["start"]), (200, "Renamed", 16, 4.0))
        # bad documents are 400 with a message, unknown kit is 404
        bad = [{"pads": []}, {"name": "x", "pads": "no"},
               {"name": "x", "pads": [{"song_id": "nope", "start": 0, "end": 1}]},
               {"name": "x", "pads": [{"song_id": self.ids[0], "start": 2, "end": 1}]},
               {"name": "x", "pads": [{"song_id": self.ids[0], "start": 0, "end": 1,
                                       "stretch": {"ratio": 1, "url": "/media/other/stretch/0123456789abcdef.wav"}}]},
               {"name": "x", "pads": [None] * 17}]
        for doc in bad:
            code, e = self.call("/api/kits", doc)
            self.assertEqual(code, 400, doc)
            self.assertTrue(e["error"])
        self.assertEqual(self.call("/api/kits/nope")[0], 404)
        self.assertEqual(self.call("/api/kits/mixed-kit", method="DELETE")[0], 200)
        self.assertEqual(self.call("/api/kits/mixed-kit")[0], 404)
        self.call("/api/kits/mixed-kit-2", method="DELETE")

    def test_save_bank_as_kit_and_export_mixed_kit(self):
        a, b = self.ids
        code, kit = self.call(f"/api/songs/{a}/savekit", {"bank": 0, "name": "from bank"})
        self.assertEqual(code, 200, kit)
        used = [p for p in kit["pads"] if p]
        bank0 = self.call(f"/api/songs/{a}")[1]["chops"]["banks"][0]
        self.assertEqual(len(used), len(bank0))
        self.assertTrue(all(p["song_id"] == a for p in used))
        # a bank with a pad from another song: its override names that song
        ov = {"0:2": {"start": 3.0, "end": 3.4, "song": b}}
        code, kit2 = self.call(f"/api/songs/{a}/savekit", {"bank": 0, "name": "mixed bank", "overrides": ov})
        self.assertEqual((kit2["pads"][2]["song_id"], kit2["pads"][2]["start"]), (b, 3.0))
        self.assertEqual(kit2["pads"][1]["song_id"], a)
        # export: SFZ points at both songs' audio; the Hydrogen kit holds both (temp dir, not ~/.local)
        code, out = self.call(f"/api/kits/{kit2['id']}/export", {"kind": "sfz"})
        self.assertEqual(code, 200, out)
        self.assertIn(f"sample=../../{b}/audio.wav", open(out["path"]).read())
        os.environ["CHOPPAD_HYDROGEN_DIR"] = os.path.join(self.tmp, "h2")
        self.addCleanup(os.environ.pop, "CHOPPAD_HYDROGEN_DIR", None)
        code, out = self.call(f"/api/kits/{kit2['id']}/export", {"kind": "hydrogen"})
        self.assertEqual(code, 200, out)
        with wave.open(os.path.join(out["path"], "chop_02.wav")) as w:
            self.assertAlmostEqual(w.getnframes() / w.getframerate(), 0.4, delta=0.001)
        self.assertEqual(self.call(f"/api/kits/{kit2['id']}/export", {"kind": "zip"})[0], 400)
        # a kit whose song has disappeared fails with a clear message
        keep = os.path.join(self.tmp, "keep-b")
        shutil.move(os.path.join(self.media_root, b), keep)
        try:
            code, e = self.call(f"/api/kits/{kit2['id']}/export", {"kind": "sfz"})
            self.assertEqual(code, 400)
            self.assertIn(f"needs song '{b}'", e["error"])
        finally:
            shutil.move(keep, os.path.join(self.media_root, b))
        for k in (kit, kit2):
            self.call(f"/api/kits/{k['id']}", method="DELETE")


if __name__ == "__main__":
    unittest.main()
