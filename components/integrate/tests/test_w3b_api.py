"""W3b HTTP side: resample upload and the files a pad can play (resample, stems), transport 'resample', kits and exports with
resample pads. A synthetic song, the real server (choppad.make_server) on a free port, no network."""
import os, sys, tempfile, unittest
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from _common import *  # noqa: E402,F403

import io, json, shutil, threading, urllib.error, urllib.request, wave  # noqa: E402
import numpy as np  # noqa: E402
from choppad import make_server  # noqa: E402


def wav_bytes(seconds=0.5, sr=48000, ch=2, sw=2, freq=440.0):
    n = int(seconds * sr)
    t = np.arange(n) / sr
    x = (0.4 * np.sin(2 * np.pi * freq * t) * 32767).astype("<i2")
    data = np.repeat(x[:, None], ch, axis=1) if ch > 1 else x[:, None]
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(ch); w.setsampwidth(sw); w.setframerate(sr)
        w.writeframes(data.astype("<i2").tobytes() if sw == 2 else (data // 256).astype("i1").tobytes())
    return buf.getvalue()


class Rig(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="choppad-w3b-api-")
        cls.addClassCleanup(shutil.rmtree, cls.tmp, ignore_errors=True)
        cls.root = os.path.join(cls.tmp, "media")
        cls.sid = "song1"
        d = os.path.join(cls.root, cls.sid)
        os.makedirs(d)
        click_track(os.path.join(d, "audio.wav"), 120)
        json.dump({**load_fixture("meta"), "id": cls.sid, "title": "Test"}, open(os.path.join(d, "meta.json"), "w"))
        json.dump(load_fixture("chops"), open(os.path.join(d, "chops.json"), "w"))
        cls.dir = d
        cls.srv = make_server(cls.root, port=0)
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.addClassCleanup(cls.srv.shutdown)

    def call(self, path, body=None, method=None, raw=None, headers=None):
        data = raw if raw is not None else (None if body is None else json.dumps(body).encode())
        h = {"Content-Type": "application/json"} if raw is None else {"Content-Type": "audio/wav"}
        h.update(headers or {})
        req = urllib.request.Request(self.base + path, data=data, headers=h, method=method or ("POST" if data is not None else "GET"))
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def jcall(self, *a, **k):
        code, body = self.call(*a, **k)
        return code, json.loads(body or b"{}")


class Resample(Rig):
    def test_upload_stores_the_wav_by_content_and_serves_it(self):
        data = wav_bytes(0.5, 48000)
        code, r = self.jcall(f"/api/songs/{self.sid}/resample", raw=data)
        self.assertEqual(code, 200, r)
        self.assertRegex(r["url"], rf"^/media/{self.sid}/resample/[0-9a-f]{{16}}\.wav$")
        self.assertAlmostEqual(r["duration"], 0.5, places=3)
        self.assertEqual((r["sample_rate"], r["channels"]), (48000, 2))
        self.assertTrue(os.path.isfile(os.path.join(self.dir, "resample", r["url"].rsplit("/", 1)[1])))
        self.assertEqual(self.call(r["url"])[1], data)
        code2, r2 = self.jcall(f"/api/songs/{self.sid}/resample", raw=data)   # the same take is stored once
        self.assertEqual(r2["url"], r["url"])
        self.assertNotEqual(self.jcall(f"/api/songs/{self.sid}/resample", raw=wav_bytes(0.5, 48000, freq=550))[1]["url"], r["url"])
        self.assertEqual(self.jcall(f"/api/songs/{self.sid}/resample", raw=wav_bytes(0.3, 44100, ch=1))[0], 200)

    def test_bad_bodies_and_songs(self):
        up = f"/api/songs/{self.sid}/resample"
        for name, body in (("empty", b""), ("not a wav", b"RIFFxxxxWAVEnope"), ("8 bit", wav_bytes(sw=1)),
                           ("too short", wav_bytes(0.0005)), ("odd rate", wav_bytes(0.2, 4000))):
            with self.subTest(name):
                code, r = self.jcall(up, raw=body)
                self.assertEqual(code, 400, r)
                self.assertTrue(r["error"])
        self.assertEqual(self.jcall("/api/songs/nope/resample", raw=wav_bytes())[0], 404)
        self.assertFalse(os.path.isdir(os.path.join(os.path.dirname(self.dir), "nope")))

    def test_media_routes(self):
        self.assertEqual(self.call(f"/media/{self.sid}/resample/0123456789abcdef.wav")[0], 404)   # well formed, not there
        self.assertEqual(self.call(f"/media/{self.sid}/resample/../meta.json")[0], 404)
        self.assertEqual(self.call(f"/media/{self.sid}/resample/short.wav")[0], 404)
        self.assertEqual(self.call(f"/media/{self.sid}/stems/guitar.wav")[0], 404)
        self.assertEqual(self.call(f"/media/{self.sid}/stems/drums.wav")[0], 404)
        os.makedirs(os.path.join(self.dir, "stems"), exist_ok=True)
        self.addCleanup(shutil.rmtree, os.path.join(self.dir, "stems"), True)
        open(os.path.join(self.dir, "stems", "drums.wav"), "wb").write(wav_bytes(0.1))
        self.assertEqual(self.call(f"/media/{self.sid}/stems/drums.wav")[0], 200)

    def test_capture_worklet_is_served(self):
        code, body = self.call("/capture-worklet.js")
        self.assertEqual(code, 200)
        self.assertIn(b'registerProcessor("choppad-capture"', body)


class TransportResample(Rig):
    def test_validation(self):
        t = f"/api/songs/{self.sid}/transport"
        ok = {"command": "resample", "source": "pattern", "bars": 2, "pad": 3}
        code, r = self.jcall(t, ok)
        self.assertEqual((code, r["ok"], r["listeners"]), (200, True, 0))
        self.assertEqual(self.jcall(t, {**ok, "bars": None, "seconds": 1.5})[0], 200)
        for bad in ({"source": "tape"}, {"bars": None}, {"bars": -1}, {"pad": 16}, {"pad": None}, {"pad": True}):
            with self.subTest(bad):
                self.assertEqual(self.jcall(t, {**ok, **bad})[0], 400)


class PadsWithResample(Rig):
    """A resample on a pad reaches the exports and kits."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.up = cls.jcall_cls(f"/api/songs/{cls.sid}/resample", wav_bytes(1.0, 48000))

    @classmethod
    def jcall_cls(cls, path, raw):
        req = urllib.request.Request(cls.base + path, data=raw, headers={"Content-Type": "audio/wav"}, method="POST")
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read())

    def overrides(self):
        return {"0:9": {"start": 0, "end": 1.0, "src": self.up["url"]}}

    def test_sfz_uses_the_resample_file_and_its_sample_rate(self):
        code, r = self.jcall(f"/api/songs/{self.sid}/export", {"kind": "sfz", "bank": 0, "overrides": self.overrides()})
        self.assertEqual(code, 200, r)
        line = [l for l in open(r["path"]).read().splitlines() if "key=45 " in l][0]
        self.assertIn(f"sample=resample/{self.up['url'].rsplit('/', 1)[1]}", line)
        self.assertIn("offset=0", line)
        self.assertIn("end=48000", line, "the end is in frames of the 48 kHz file")

    def test_hydrogen_and_zip_cut_the_resample(self):
        code, r = self.jcall(f"/api/songs/{self.sid}/export", {"kind": "zip", "bank": 0, "overrides": self.overrides()})
        self.assertEqual(code, 200, r)
        import zipfile
        with zipfile.ZipFile(r["path"]) as z, wave.open(io.BytesIO(z.read("pad10.wav"))) as w:
            self.assertEqual((w.getframerate(), w.getnframes()), (48000, 48000))

    def test_kit_keeps_the_resample_and_checks_it(self):
        pads = [None] * 16
        pads[0] = {"song_id": self.sid, "start": 0, "end": 1.0, "src": self.up["url"]}
        code, kit = self.jcall("/api/kits", {"name": "Resample Kit", "pads": pads})
        self.addCleanup(self.call, f"/api/kits/{kit.get('id', 'x')}", None, "DELETE")
        self.assertEqual(code, 201, kit)
        self.assertEqual(kit["pads"][0]["src"], self.up["url"])
        bad = [None] * 16
        bad[0] = {"song_id": self.sid, "start": 0, "end": 1.0, "src": "/media/other/resample/0123456789abcdef.wav"}
        self.assertEqual(self.jcall("/api/kits", {"name": "bad", "pads": bad})[0], 400)
        bad[0] = {"song_id": self.sid, "start": 0, "end": 1.0, "stem": "guitar"}
        self.assertEqual(self.jcall("/api/kits", {"name": "bad", "pads": bad})[0], 400)
        # a kit export of a missing resample fails with a clear message
        code, r = self.jcall(f"/api/kits/{kit['id']}/export", {"kind": "zip"})
        self.assertEqual(code, 200, r)


class StemsApi(Rig):
    """POST /api/songs/<id>/stems against a fake stems service, stems on pads (stretch, exports, kits)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        sys.path.insert(0, os.path.join(ROOT, "components", "stems", "tests"))  # noqa: F405
        from test_stems import FakeService, make_zip, wav_bytes as stem_wav
        import soundfile as sf
        data, sr = sf.read(os.path.join(cls.dir, "audio.wav"), dtype="float32", always_2d=True)
        cls.svc = FakeService()
        cls.svc.reply = (200, make_zip({f"{n}.wav": stem_wav(data * g, sr=sr) for n, g in
                                        (("drums", 0.5), ("bass", 0.4), ("vocals", 0.3), ("other", 0.2))}), "application/zip")
        cls.addClassCleanup(cls.svc.close)
        cls.saved_env = {k: os.environ.get(k) for k in ("CHOPPAD_STEMS_BACKEND", "CHOPPAD_STEMS_URL")}

        def restore():
            for k, v in cls.saved_env.items():
                os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        cls.addClassCleanup(restore)
        cls.addClassCleanup(shutil.rmtree, os.path.join(cls.dir, "stems"), True)

    def test_a_song_without_a_backend_answers_501_with_the_steps(self):
        os.environ.pop("CHOPPAD_STEMS_BACKEND", None)
        shutil.rmtree(os.path.join(self.dir, "stems"), ignore_errors=True)
        code, r = self.jcall(f"/api/songs/{self.sid}/stems", {})
        self.assertEqual(code, 501)
        self.assertIn("CHOPPAD_STEMS_BACKEND=remote", r["error"])
        self.assertEqual(self.jcall("/api/songs/nope/stems", {})[0], 404)

    def test_separate_serves_and_records_the_stems(self):
        shutil.rmtree(os.path.join(self.dir, "stems"), ignore_errors=True)
        os.environ.update(CHOPPAD_STEMS_BACKEND="remote", CHOPPAD_STEMS_URL=self.svc.url)
        code, r = self.jcall(f"/api/songs/{self.sid}/stems", {})
        self.assertEqual(code, 200, r)
        self.assertEqual(r["stems"], ["drums", "bass", "vocals", "other"])
        self.assertEqual(r["urls"]["drums"], f"/media/{self.sid}/stems/drums.wav")
        code, body = self.call(r["urls"]["drums"])
        self.assertEqual((code, body[:4]), (200, b"RIFF"))
        with open(os.path.join(self.dir, "meta.json")) as f:
            self.assertEqual(json.load(f)["stems"], ["drums", "bass", "vocals", "other"])
        n = len(self.svc.requests)
        self.assertEqual(self.jcall(f"/api/songs/{self.sid}/stems", {})[0], 200)
        self.assertEqual(len(self.svc.requests), n, "cached: the service is not asked again")

    def test_a_service_that_fails_is_a_502(self):
        shutil.rmtree(os.path.join(self.dir, "stems"), ignore_errors=True)
        os.environ.update(CHOPPAD_STEMS_BACKEND="remote", CHOPPAD_STEMS_URL="http://127.0.0.1:9/x")
        code, r = self.jcall(f"/api/songs/{self.sid}/stems", {})
        self.assertEqual(code, 502)
        self.assertIn("could not reach", r["error"])

    def test_stems_on_pads(self):
        os.environ.update(CHOPPAD_STEMS_BACKEND="remote", CHOPPAD_STEMS_URL=self.svc.url)
        self.assertEqual(self.jcall(f"/api/songs/{self.sid}/stems", {"force": True})[0], 200)
        ov = {"0:2": {"start": 1.0, "end": 1.5, "stem": "drums"}}
        # stretch a slice of the stem: cached by stem, from the stem's audio
        code, a = self.jcall(f"/api/songs/{self.sid}/stretch", {"start": 1.0, "end": 1.5, "ratio": 2.0, "stem": "drums"})
        self.assertEqual(code, 200, a)
        code, b = self.jcall(f"/api/songs/{self.sid}/stretch", {"start": 1.0, "end": 1.5, "ratio": 2.0})
        self.assertNotEqual(a["url"], b["url"], "the same slice of the mix is a different rendering")
        self.assertAlmostEqual(a["duration"], 1.0, delta=0.01)
        self.assertEqual(self.jcall(f"/api/songs/{self.sid}/stretch", {"start": 1.0, "end": 1.5, "ratio": 2.0, "stem": "guitar"})[0], 400)
        # exports play the stem file
        code, r = self.jcall(f"/api/songs/{self.sid}/export", {"kind": "sfz", "bank": 0, "overrides": ov})
        self.assertEqual(code, 200, r)
        with open(r["path"]) as f:
            line = [l for l in f.read().splitlines() if "key=38 " in l][0]
        self.assertIn("sample=stems/drums.wav", line)
        self.assertIn("offset=44100", line)
        import zipfile

        def pad3(overrides):
            r = self.jcall(f"/api/songs/{self.sid}/export", {"kind": "zip", "bank": 0, "overrides": overrides})[1]
            with zipfile.ZipFile(r["path"]) as z, wave.open(io.BytesIO(z.read("pad03.wav"))) as w:
                return w.getnframes() / w.getframerate(), np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
        dur, quiet = pad3(ov)
        self.assertAlmostEqual(dur, 0.5, delta=0.001)
        _, mix = pad3({"0:2": {"start": 1.0, "end": 1.5}})
        self.assertLess(np.abs(quiet).max(), 0.6 * np.abs(mix).max() + 1, "the drums stem was exported, not the mix")
        # kits keep the stem
        pads = [None] * 16
        pads[0] = {"song_id": self.sid, "start": 1.0, "end": 1.5, "stem": "bass"}
        code, kit = self.jcall("/api/kits", {"name": "Stem Kit", "pads": pads})
        self.addCleanup(self.call, f"/api/kits/{kit.get('id', 'x')}", None, "DELETE")
        self.assertEqual((code, kit["pads"][0]["stem"]), (201, "bass"))
        self.assertEqual(self.jcall(f"/api/kits/{kit['id']}/export", {"kind": "zip"})[0], 200)
        # a stem the song does not have is refused clearly
        shutil.rmtree(os.path.join(self.dir, "stems"))
        code, r = self.jcall(f"/api/songs/{self.sid}/export", {"kind": "zip", "bank": 0, "overrides": ov})
        self.assertEqual(code, 400)
        self.assertIn("stem of 'song1' is missing", r["error"])


if __name__ == "__main__":
    unittest.main()
