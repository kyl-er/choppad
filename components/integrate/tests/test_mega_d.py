"""CHOP-13 step D (server): the per-project operations are keyed by DRUM PAD MEGA BANK (contracts §15), the library `mega` events,
clearing a source, deleting a mega bank that a tab has open, the pattern's own bar length, kits of kept pads, sample housekeeping.
Synthetic songs, the real server (choppad.make_server on a free port, the fake DAW bridge), temporary media roots only."""
import os, sys, tempfile, unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
import test_mega as TM  # noqa: E402  (Rig, Stream, write_song, ...)
from _common import *  # noqa: E402,F403

import io, json, shutil, time, wave, zipfile  # noqa: E402
from unittest import mock  # noqa: E402
import numpy as np  # noqa: E402
import choppad_actions as A  # noqa: E402
import choppad_export as X  # noqa: E402
import mega as M  # noqa: E402

SR = TM.SR


def wav_bytes(seconds=0.5, hz=440.0, sr=SR):
    t = np.arange(int(seconds * sr)) / sr
    x = (0.4 * np.sin(2 * np.pi * hz * t) * 32767).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(sr)
        w.writeframes(x.tobytes())
    return buf.getvalue()


def zip_pad(path_or_bytes, name):
    with zipfile.ZipFile(path_or_bytes) as z, wave.open(io.BytesIO(z.read(name))) as w:
        return w.readframes(w.getnframes())


class DRig(TM.Rig):
    """Songs A (3 banks) and B (1 bank); the server is choppad.make_server with a fake DAW bridge and a private Hydrogen kit folder."""
    def start(self, maker=None):
        from choppad import make_server
        self.daw_log = os.path.join(self.tmp, "daw.log")
        self.hydro = os.path.join(self.tmp, "hydrogen-kits")
        env = mock.patch.dict(os.environ, {"CHOPPAD_DAW_FAKE": self.daw_log, "CHOPPAD_HYDROGEN_DIR": self.hydro})
        env.start()
        self.addCleanup(env.stop)
        super().start(lambda r: make_server(r, port=0))

    def daw_calls(self):
        if not os.path.isfile(self.daw_log):
            return []
        with open(self.daw_log) as f:
            return [json.loads(l) for l in f if l.strip()]

    def new_mega(self, source=None, name=None):
        code, r = self.call("/api/mega", {"source": source, **({"name": name} if name else {})})
        self.assertEqual(code, 201, r)
        return r["mega"]["id"]

    def post(self, path, body, origin="t"):
        return self.call(path, body, headers={"X-ChopPad-Origin": origin})

    def switch(self, mid, song):
        code, r = self.post(f"/api/mega/{mid}/source", {"song": song})
        self.assertEqual(code, 200, r)
        return r

    def audio(self, song, start, end):
        _, nch, frames = TM.read_wav(os.path.join(self.root, song, "audio.wav"))
        return frames[round(start * SR) * 4: round(end * SR) * 4]


# ------------------------------------------------------------------------------------------------------------------------
class Exports(DRig):
    def test_zip_of_a_mega_bank_with_no_source_song_has_the_kept_pads(self):
        mid = self.new_mega("A")
        self.acts(mid, *self.hits(0, 1, 2, 3))
        want = {i: X.pad_pcm({**p, "params": {}}, {})[3] for i, p in
                ((p["pad"], p) for p in X.bank_pads(os.path.join(self.root, "A"), 0, {}, []))}
        self.switch(mid, "B")
        code, r = self.post(f"/api/mega/{mid}/source", {"song": None})
        self.assertEqual(code, 200, r)
        self.assertEqual(sorted((k["bank"], k["pad"]) for k in r["kept"]), [])   # nothing new: they were kept by the switch to B
        doc = M.read(self.root, mid)
        self.assertIsNone(doc["source"])
        self.assertEqual(self.get(mid)["chops"]["banks"], [])
        code, z = self.post(f"/api/mega/{mid}/export", {"kind": "zip", "bank": 0})
        self.assertEqual(code, 200, z)
        self.assertEqual(sorted(zipfile.ZipFile(z["path"]).namelist()), ["pad01.wav", "pad02.wav", "pad03.wav", "pad04.wav"])
        for i in range(4):
            self.assertEqual(zip_pad(z["path"], f"pad{i + 1:02d}.wav"), want[i])
        code, body = self.call(z["url"], raw=True)           # and it can be downloaded
        self.assertEqual((code, zipfile.ZipFile(io.BytesIO(body)).namelist()[0]), (200, "pad01.wav"))
        code, s = self.post(f"/api/mega/{mid}/export", {"kind": "sfz", "bank": 0})
        self.assertEqual(code, 200, s)
        self.assertEqual(len([l for l in open(s["path"]) if l.startswith("<region>")]), 4)
        code, h = self.post(f"/api/mega/{mid}/export", {"kind": "hydrogen", "bank": 0})
        self.assertEqual(code, 200, h)
        self.assertTrue(h["path"].startswith(self.hydro))                  # never the owner's folder
        self.assertEqual(len([f for f in os.listdir(h["path"]) if f.endswith(".wav")]), 4)
        self.assertEqual(self.post(f"/api/mega/{mid}/export", {"kind": "zip", "bank": 2})[0], 400)   # a bank with no pads
        self.assertEqual(self.post(f"/api/mega/{mid}/export", {"kind": "mp3"})[0], 400)

    def test_export_with_kept_pads_other_song_pads_free_pads_and_a_stretch_rendering(self):
        mid = self.new_mega("A")
        self.acts(mid, *self.hits(0, 1))
        self.switch(mid, "B")                                              # pads 0, 1 kept from A; the rest are B's chops
        # pad 5 plays a slice of song A (another song), pad 6 a stretch rendering made from B's audio
        code, st = self.post("/api/mega/%s/stretch" % mid, {"bank": 0, "pad": 6, "ratio": 2.0, "start": 0.0, "end": 0.5})
        self.assertEqual(code, 200, st)
        self.assertTrue(st["url"].startswith("/media/B/stretch/"))        # rendered into the source song's folder
        self.acts(mid, {"type": "kit.load", "bank": 0, "song": "B", "pads": [None] * 5 + [{"song_id": "A", "start": 2.0, "end": 2.5}]},
                  {"type": "slice.stretch", "bank": 0, "pad": 6, "stretch": {"ratio": st["ratio"], "url": st["url"]}})
        code, z = self.post(f"/api/mega/{mid}/export", {"kind": "zip", "bank": 0})
        self.assertEqual(code, 200, z)
        self.assertEqual(len(zipfile.ZipFile(z["path"]).namelist()), 16)
        a0 = X.bank_pads(os.path.join(self.root, "A"), 0, {}, [])[0]
        self.assertEqual(zip_pad(z["path"], "pad01.wav"), X.pad_pcm({**a0, "params": {}}, {})[3])      # kept: song A's audio
        b_pads = {p["pad"]: p for p in X.bank_pads(os.path.join(self.root, "B"), 0, {}, [])}
        self.assertEqual(zip_pad(z["path"], "pad03.wav"), X.pad_pcm(b_pads[2], {})[3])                  # free: song B
        other = {"pad": 5, "start": 2.0, "end": 2.5, "audio": os.path.join(self.root, "A", "audio.wav"), "stretch": None, "sr": SR, "params": {}}
        self.assertEqual(zip_pad(z["path"], "pad06.wav"), X.pad_pcm(other, {})[3])                      # another song's slice
        with wave.open(os.path.join(self.root, "B", "stretch", os.path.basename(st["url"]))) as w:
            self.assertGreater(len(zip_pad(z["path"], "pad07.wav")), 1.8 * 0.5 * SR * 4)                # the stretch rendering
            self.assertEqual(w.getnframes() * 4, len(zip_pad(z["path"], "pad07.wav")))

    def test_song_keyed_export_is_the_same_code_on_the_songs_mega_bank(self):
        mid = self.mega_of("A")
        self.acts(mid, *self.hits(0))
        code, a = self.post("/api/songs/A/export", {"kind": "zip", "bank": 0})
        code2, b = self.post(f"/api/mega/{mid}/export", {"kind": "zip", "bank": 0})
        self.assertEqual((code, code2), (200, 200))
        self.assertEqual(zipfile.ZipFile(a["path"]).namelist(), zipfile.ZipFile(b["path"]).namelist())
        self.assertEqual(a["mega"], mid)


class SourceOps(DRig):
    def test_source_song_operations_answer_409_with_a_plain_sentence_when_there_is_no_source(self):
        mid = self.new_mega(None)
        for op, body in (("regrid", {"grid": "1/8"}), ("reanalyze", {"bpm": 100}), ("sections", {}), ("key", {}), ("stems", {}),
                         ("stretch", {"bank": 0, "pad": 0, "ratio": 2}), ("advise", {})):
            code, r = self.post(f"/api/mega/{mid}/{op}", body)
            self.assertEqual(code, 409, (op, r))
            self.assertIn("Pick a song first", r["error"])
            self.assertNotIn("Traceback", r["error"])
        self.assertEqual(self.post("/api/mega/nope/regrid", {})[0], 404)

    def test_regrid_acts_on_the_source_and_tells_every_mega_bank_with_that_source(self):
        d = TM.write_song(self.root, "R", "Regrid me", 300, 16)       # a song with a real analysis (the regrid re-chops it)
        with open(os.path.join(d, "analysis.json"), "w") as f:
            json.dump(load_fixture("analysis"), f)  # noqa: F405
        a, b = self.new_mega("R"), self.new_mega("R")
        sa = self.stream(f"/api/mega/{a}/events", "tabA")
        sb = self.stream(f"/api/mega/{b}/events", "tabB")
        code, ch = self.post(f"/api/mega/{a}/regrid", {"grid": "1/8"})
        self.assertEqual(code, 200, ch)
        self.assertEqual(ch["grid"], "1/8")
        self.assertEqual(self.get(b)["chops"]["grid"], "1/8")
        self.assertEqual(len(sb.wait(1)), 1)
        self.assertEqual(sb.of("chops")[0]["chops"]["grid"], "1/8")
        self.assertEqual(sa.of("chops")[0]["chops"]["grid"], "1/8")

    def test_stretch_of_a_kept_pad_is_400_with_the_plain_reason_and_a_free_pad_renders(self):
        mid = self.new_mega("A")
        self.acts(mid, *self.hits(0))
        self.switch(mid, "B")
        code, r = self.post(f"/api/mega/{mid}/stretch", {"bank": 0, "pad": 0, "ratio": 0.5})
        self.assertEqual(code, 400, r)
        self.assertIn("is kept", r["error"])
        code, r = self.post(f"/api/mega/{mid}/stretch", {"bank": 0, "pad": 1, "ratio": 0.5})
        self.assertEqual(code, 200, r)
        self.assertTrue(r["url"].startswith("/media/B/stretch/"))
        code, r = self.post(f"/api/mega/{mid}/stretch", {"bank": 0, "pad": 1, "target_bpm": 180})   # B's own tempo is 90
        self.assertEqual(code, 200, r)
        self.assertAlmostEqual(r["ratio"], 0.5, places=3)


class Resample(DRig):
    def test_a_resample_upload_is_stored_under_the_mega_bank_and_survives_the_song(self):
        mid = self.new_mega("A")
        req = TM.urllib.request.Request(f"http://127.0.0.1:{self.port}/api/mega/{mid}/resample", data=wav_bytes(1.0, 330),
                                        headers={"Content-Type": "audio/wav"}, method="POST")
        with TM.urllib.request.urlopen(req) as r:
            up = json.loads(r.read())
        self.assertRegex(up["url"], rf"^/media/_mega/{mid}/samples/[0-9a-f]{{16}}\.wav$")
        self.assertTrue(os.path.isfile(os.path.join(self.root, "_mega", mid, "samples", up["hash"] + ".wav")))
        self.assertEqual(self.call(up["url"], raw=True)[0], 200)
        self.acts(mid, {"type": "slice.src", "bank": 0, "pad": 9, "src": up["url"], "start": 0, "end": up["duration"]})
        self.assertEqual(self.get(mid)["project"]["overrides"]["0:9"]["src"], up["url"])
        # the song goes away from the mega bank: the resample is still there and exports with no source
        self.assertEqual(self.post(f"/api/mega/{mid}/source", {"song": None})[0], 200)
        code, z = self.post(f"/api/mega/{mid}/export", {"kind": "zip", "bank": 0})
        self.assertEqual(code, 200, z)
        self.assertEqual(zipfile.ZipFile(z["path"]).namelist(), ["pad10.wav"])
        self.assertEqual(zip_pad(z["path"], "pad10.wav")[:8], X.pad_pcm({"pad": 9, "start": 0, "end": up["duration"], "stretch": None, "params": {},
                         "audio": os.path.join(self.root, "_mega", mid, "samples", up["hash"] + ".wav"), "sr": SR}, {})[3][:8])
        # bad uploads
        for body in (b"", b"not a wav"):
            req = TM.urllib.request.Request(f"http://127.0.0.1:{self.port}/api/mega/{mid}/resample", data=body, method="POST")
            with self.assertRaises(TM.urllib.error.HTTPError) as cm:
                TM.urllib.request.urlopen(req)
            self.assertEqual(cm.exception.code, 400)
        req = TM.urllib.request.Request(f"http://127.0.0.1:{self.port}/api/mega/nope/resample", data=wav_bytes(), method="POST")
        with self.assertRaises(TM.urllib.error.HTTPError) as cm:
            TM.urllib.request.urlopen(req)
        self.assertEqual(cm.exception.code, 404)


class Daw(DRig):
    def test_open_sync_play_stop_close_keyed_by_mega_bank(self):
        mid = self.new_mega("A", "Break")
        self.acts(mid, *self.hits(0, 1), {"type": "tempo.set", "bpm": 97, "follow": False})
        code, r = self.post(f"/api/mega/{mid}/daw", {"daw": "hydrogen", "command": "open"})
        self.assertEqual((code, r["ok"], r["mega"], r["song"], r["bpm"]), (200, True, mid, "A", 97.0))
        self.assertEqual(self.daw_calls()[0]["song"], mid)       # the bridge's project is the mega bank
        self.assertEqual(self.get(mid)["daw"], {"open": True, "daw": "hydrogen", "bank": 0})
        self.assertEqual(self.call(f"/api/songs/A")[1]["daw"]["open"], True)    # the song's mega bank is this one
        self.assertEqual(self.post(f"/api/mega/{mid}/daw", {"daw": "hydrogen", "command": "sync", "bank": 1})[0], 200)
        self.assertEqual(self.get(mid)["daw"]["bank"], 1)
        self.assertEqual(self.post(f"/api/mega/{mid}/daw", {"daw": "hydrogen", "command": "play"})[1]["daw_transport"], "ok")
        self.assertEqual(self.post(f"/api/mega/{mid}/daw", {"daw": "hydrogen", "command": "stop"})[0], 200)
        ops = [(c["op"], c.get("command")) for c in self.daw_calls()]
        self.assertEqual(ops, [("launch", None), ("push", None), ("transport", "play"), ("transport", "stop")])
        code, r = self.post(f"/api/mega/{mid}/daw", {"daw": "hydrogen", "command": "close"})
        self.assertEqual((code, r["ok"], r["closed"]), (200, True, ["hydrogen"]))
        self.assertEqual(self.daw_calls()[-1]["op"], "close")      # the bridge this server started is shut
        self.assertEqual(self.get(mid)["daw"], {"open": False, "daw": None, "bank": None})
        self.assertEqual(self.post(f"/api/mega/{mid}/daw", {"daw": "hydrogen", "command": "close"})[0], 409)   # nothing left to close
        self.assertEqual(self.post(f"/api/mega/{mid}/daw", {"daw": "hydrogen", "command": "play"})[0], 409)
        self.assertEqual(self.post(f"/api/mega/{mid}/daw", {"daw": "nope", "command": "open"})[0], 400)
        self.assertEqual(self.post(f"/api/mega/{mid}/daw", {"daw": "hydrogen", "command": "dance"})[0], 400)
        self.assertEqual(self.post("/api/mega/nope/daw", {"daw": "hydrogen", "command": "open"})[0], 404)

    def test_a_mega_bank_with_no_source_opens_in_the_bridge(self):
        mid = self.new_mega("A")
        self.acts(mid, *self.hits(0, 1))
        self.switch(mid, "B")
        self.assertEqual(self.post(f"/api/mega/{mid}/source", {"song": None})[0], 200)
        code, r = self.post(f"/api/mega/{mid}/daw", {"daw": "hydrogen", "command": "open"})
        self.assertEqual((code, r["ok"], r["song"]), (200, True, None), r)

    def test_daw_events_and_mirroring_follow_the_mega_bank(self):
        mid = self.new_mega("A")
        s = self.stream(f"/api/mega/{mid}/events", "tab")
        self.post(f"/api/mega/{mid}/daw", {"daw": "hydrogen", "command": "open"})
        end = time.time() + 3
        while time.time() < end and not s.of("daw"):
            time.sleep(0.02)
        self.assertEqual(s.of("daw")[-1], {"open": True, "daw": "hydrogen", "bank": 0})
        self.post(f"/api/mega/{mid}/transport", {"command": "hit", "pad": 3, "velocity": 0.5})
        self.assertIn(("note", 3), [(c["op"], c.get("pad")) for c in self.daw_calls()])
        code, r = self.post(f"/api/mega/{mid}/transport", {"command": "hit", "pad": 4, "velocity": 1, "local": True})
        self.assertEqual((code, r["delivered"]), (200, True))
        self.post(f"/api/mega/{mid}/daw", {"daw": "hydrogen", "command": "close"})
        end = time.time() + 3
        while time.time() < end and s.of("daw")[-1:] != [{"open": False, "daw": None, "bank": None}]:
            time.sleep(0.02)
        self.assertEqual(s.of("daw")[-1], {"open": False, "daw": None, "bank": None})


class TwoMegaBanksOnOneSong(DRig):
    """The problem step D solves: two mega banks share song A; "the song's mega bank" (the newer one) must not catch what was addressed
    to the other."""
    def setUp(self):
        super().setUp()
        self.x = self.new_mega("A", "X")
        time.sleep(0.02)
        self.y = self.new_mega("A", "Y")
        self.assertEqual(self.mega_of("A"), self.y)
        # same pad on both, different slices; Y's pad 0 is kept from song A's chop 7
        self.acts(self.x, {"type": "slice.set", "bank": 0, "pad": 0, "start": 1.0, "end": 1.5}, {"type": "tempo.set", "bpm": 88, "follow": False},
                  *self.hits(0))
        self.acts(self.y, {"type": "slice.set", "bank": 0, "pad": 0, "start": 2.0, "end": 2.5}, {"type": "tempo.set", "bpm": 133, "follow": False},
                  *self.hits(0))
        self.assertEqual(self.post(f"/api/mega/{self.y}/keep", {"bank": 0, "pad": 0})[0], 200)
        self.assertEqual(self.post(f"/api/mega/{self.x}/keep", {"bank": 1, "pad": 0})[0], 200)   # X keeps a pad of its own on bank 1
        time.sleep(0.02)
        self.acts(self.y, {"type": "pad.set", "pad": 14, "params": {"gainDb": -1}})            # Y was edited last: "the song's mega bank"

    def test_export_hits_the_addressed_mega_bank(self):
        for mid, (s, e) in ((self.x, (1.0, 1.5)), (self.y, (2.0, 2.5))):
            code, z = self.post(f"/api/mega/{mid}/export", {"kind": "zip", "bank": 0})
            self.assertEqual(code, 200, z)
            self.assertEqual(z["mega"], mid)
            names = zipfile.ZipFile(z["path"]).namelist()
            self.assertIn("pad01.wav", names)
            a = X.bank_pads(os.path.join(self.root, "A"), 0, {f"0:0": {"start": s, "end": e}}, [], only=0)[0]
            self.assertEqual(zip_pad(z["path"], "pad01.wav"), X.pad_pcm({**a, "params": {}}, {})[3])
        z1 = self.post(f"/api/mega/{self.x}/export", {"kind": "zip", "bank": 1})
        self.assertEqual(z1[0], 200)       # X's kept pad on bank 1
        self.assertEqual(self.post(f"/api/mega/{self.y}/export", {"kind": "zip", "bank": 1})[0], 200)   # a free bank of the source: 16 chops
        self.assertEqual(len(zipfile.ZipFile(self.post(f"/api/mega/{self.y}/export", {"kind": "zip", "bank": 1})[1]["path"]).namelist()), 16)
        self.assertEqual(len(zipfile.ZipFile(z1[1]["path"]).namelist()), 16)

    def test_savekit_hits_the_addressed_mega_bank(self):
        code, kx = self.post(f"/api/mega/{self.x}/savekit", {"bank": 0, "name": "kit X"})   # X's pad 1 is a slice 1.0-1.5, not kept
        self.assertEqual(code, 200, kx)
        self.assertEqual((kx["pads"][0]["song_id"], kx["pads"][0]["start"], kx["pads"][0]["end"]), ("A", 1.0, 1.5))
        code, kz = self.post(f"/api/mega/{self.x}/savekit", {"bank": 2, "name": "kit X2"})
        self.assertEqual(code, 200, kz)
        self.assertEqual(sum(1 for p in kz["pads"] if p), 8)      # bank 3 of song A has 8 chops

    def test_daw_hits_the_addressed_mega_bank(self):
        code, r = self.post(f"/api/mega/{self.x}/daw", {"daw": "hydrogen", "command": "open"})
        self.assertEqual((code, r["mega"], r["bpm"]), (200, self.x, 88.0))
        self.assertEqual(self.get(self.x)["daw"]["open"], True)
        self.assertEqual(self.get(self.y)["daw"]["open"], False)
        self.assertEqual(self.call("/api/songs/A")[1]["daw"]["open"], False)    # "the song's mega bank" is Y, which holds nothing
        self.assertEqual(self.daw_calls()[-1]["song"], self.x)
        # a hit addressed to Y does not reach the bridge that holds X; one addressed to X does
        self.post(f"/api/mega/{self.y}/transport", {"command": "hit", "pad": 2, "velocity": 1})
        self.assertNotIn("note", [c["op"] for c in self.daw_calls()])
        self.post(f"/api/mega/{self.x}/transport", {"command": "hit", "pad": 2, "velocity": 1})
        self.assertEqual([c["op"] for c in self.daw_calls()].count("note"), 1)
        # Y closes nothing; opening Y later takes over the bridge, with Y's tempo
        self.assertEqual(self.post(f"/api/mega/{self.y}/daw", {"daw": "hydrogen", "command": "close"})[0], 409)
        code, r = self.post(f"/api/mega/{self.y}/daw", {"daw": "hydrogen", "command": "open"})
        self.assertEqual((code, r["bpm"]), (200, 133.0))
        self.assertEqual((self.get(self.x)["daw"]["open"], self.get(self.y)["daw"]["open"]), (False, True))

    def test_stretch_and_actions_hit_the_addressed_mega_bank(self):
        code, r = self.post(f"/api/mega/{self.y}/stretch", {"bank": 0, "pad": 0, "ratio": 0.5})
        self.assertEqual(code, 400, r)                       # Y's pad 0 is kept
        self.assertIn("is kept", r["error"])
        code, r = self.post(f"/api/mega/{self.x}/stretch", {"bank": 0, "pad": 0, "ratio": 0.5})
        self.assertEqual(code, 200, r)                       # X's pad 0 is a slice (1.0-1.5): rendered from it
        self.assertEqual((r["start"], r["end"]), (1.0, 1.5))
        # the same through the song-keyed route lands on "the song's mega bank" = Y
        code, r = self.post("/api/songs/A/stretch", {"bank": 0, "pad": 0, "ratio": 0.5})
        self.assertEqual(code, 400)
        # actions
        self.acts(self.x, {"type": "pad.set", "pad": 5, "params": {"gainDb": -7}})
        self.assertEqual(self.get(self.x)["project"]["pads"][5]["gainDb"], -7)
        self.assertEqual(self.get(self.y)["project"]["pads"][5]["gainDb"], 0)
        self.assertEqual(self.get(self.x)["project"]["tempo"]["bpm"], 88)
        self.assertEqual(self.get(self.y)["project"]["tempo"]["bpm"], 133)


class LibraryEvents(DRig):
    def test_mega_events_on_the_library_stream(self):
        lib = self.stream("/api/events", "lib")
        mid = self.new_mega("A", "First")
        self.call(f"/api/mega/{mid}", {"name": "Renamed"}, method="PUT")
        self.switch(mid, "B")
        self.post(f"/api/mega/{mid}/source", {"song": None})
        self.call(f"/api/mega/{mid}", method="DELETE")
        end = time.time() + 3
        while time.time() < end and len(lib.of("mega")) < 5:
            time.sleep(0.02)
        evs = lib.of("mega")
        self.assertEqual([e["change"] for e in evs], ["created", "renamed", "source", "source", "removed"])
        self.assertTrue(all(e["id"] == mid for e in evs))
        self.assertEqual((evs[0]["name"], evs[0]["source"]), ("First", "A"))
        self.assertEqual((evs[1]["name"], evs[2]["source"], evs[3]["source"]), ("Renamed", "B", None))

    def test_a_mega_bank_made_for_a_song_on_first_use_is_announced_once(self):
        lib = self.stream("/api/events", "lib")
        self.call("/api/songs/B")
        self.call("/api/songs/B")
        time.sleep(0.3)
        self.assertEqual([(e["change"], e["source"]) for e in lib.of("mega")], [("created", "B")])


class ClearSource(DRig):
    def test_clearing_keeps_the_used_pads_like_a_switch_and_leaves_no_source(self):
        mid = self.new_mega("A")
        self.acts(mid, *self.hits(0, 1), {"type": "pad.set", "pad": 1, "params": {"gainDb": -4}})
        s = self.stream(f"/api/mega/{mid}/events", "other-tab")
        code, r = self.post(f"/api/mega/{mid}/source", {"song": None})
        self.assertEqual(code, 200, r)
        self.assertEqual(sorted((k["bank"], k["pad"]) for k in r["kept"]), [(0, 0), (0, 1)])
        self.assertIsNone(r["mega"]["source"])
        types = [a["type"] for a in r["actions"]]
        self.assertEqual(types, ["tempo.set", "slice.keep", "slice.keep", "source.set"])        # the tempo of the old source is written first
        self.assertEqual(r["actions"][-1], {"type": "source.set", "song": None, "chops": None})
        doc = M.read(self.root, mid)
        self.assertEqual((doc["source"], doc["project"]["tempo"]), (None, {"bpm": 120.0, "follow": False}))
        self.assertEqual({k for k, v in doc["project"]["overrides"].items() if v.get("kept")}, {"0:0", "0:1"})
        self.assertEqual(doc["project"]["overrides"]["0:1"]["params"]["gainDb"], -4)
        got = s.wait(1)
        self.assertEqual([a["type"] for a in got[0][1]["actions"]], types)         # an open tab follows
        self.assertEqual(self.get(mid)["mega"]["source"], None)
        self.assertEqual(self.get(mid)["chops"]["banks"], [])
        # clearing again is a no-op; nothing about it is written
        saved = TM.slurp(self.mega_file(mid))
        code, r = self.post(f"/api/mega/{mid}/source", {"song": None})
        self.assertEqual((code, r["kept"], r["actions"]), (200, [], []))
        self.assertEqual(TM.slurp(self.mega_file(mid)), saved)
        # a source can be set again afterwards (kept pads stay kept)
        self.switch(mid, "B")
        self.assertEqual(self.get(mid)["mega"]["source"], "B")
        self.assertEqual(self.get(mid)["project"]["tempo"]["bpm"], 120.0)             # the user's tempo is still 120

    def test_a_pad_the_pattern_hits_that_has_no_audio_is_not_a_problem_and_unused_pads_are_released(self):
        mid = self.new_mega("A")
        code, r = self.post(f"/api/mega/{mid}/source", {"song": None})   # nothing used at all
        self.assertEqual((code, r["kept"], r["released"]), (200, [], 40))   # 40 chops over three banks (16, 16, 8)


class DeleteWhileOpen(DRig):
    def test_removed_event_on_the_mega_banks_own_stream_first(self):
        mid = self.new_mega("A")
        s = self.stream(f"/api/mega/{mid}/events", "tab1")
        lib = self.stream("/api/events", "lib")
        self.post(f"/api/mega/{mid}/daw", {"daw": "hydrogen", "command": "open"})
        code, r = self.call(f"/api/mega/{mid}", method="DELETE", headers={"X-ChopPad-Origin": "tab1"})
        self.assertEqual((code, r["ok"]), (200, True))
        end = time.time() + 3
        while time.time() < end and not s.of("mega"):
            time.sleep(0.02)
        self.assertEqual([(e["change"], e["id"]) for e in s.of("mega")], [("removed", mid)])   # even the deleting tab is told
        while time.time() < end and not [e for e in lib.of("mega") if e["change"] == "removed"]:
            time.sleep(0.02)
        self.assertEqual([e["change"] for e in lib.of("mega")][-1], "removed")
        # the folder is in .trash, the bridge was closed, a late write is a plain 404
        self.assertFalse(os.path.exists(os.path.join(self.root, "_mega", mid)))
        self.assertEqual(self.daw_calls()[-1]["op"], "close")
        self.assertEqual(self.acts_raw(mid)[0], 404)

    def acts_raw(self, mid):
        return self.post(f"/api/mega/{mid}/actions", {"actions": [{"type": "pad.select", "pad": 1}]})


class Kits(DRig):
    """A kit may hold kept pads: their audio is COPIED into the kit folder, so the kit never depends on a mega bank's files."""
    def make_kit(self):
        mid = self.new_mega("A")
        self.acts(mid, *self.hits(0, 1), {"type": "pad.set", "pad": 1, "params": {"gainDb": -4, "pitch": 3}})
        self.switch(mid, "B")                                       # pads 1, 2 are kept (from A); the other 14 are B's chops
        code, kit = self.post(f"/api/mega/{mid}/savekit", {"bank": 0, "name": "Break Kit"})
        self.assertEqual(code, 200, kit)
        return mid, kit

    def test_savekit_of_kept_pads_copies_their_audio_into_the_kit(self):
        mid, kit = self.make_kit()
        for pad in (0, 1):
            p = kit["pads"][pad]
            self.assertNotIn("song_id", p)
            self.assertRegex(p["src"], rf"^/media/_kits/{kit['id']}/samples/[0-9a-f]{{16}}\.wav$")
            mine = os.path.join(self.root, "_mega", mid, "samples", os.path.basename(p["src"]))
            copy = os.path.join(self.root, "_kits", kit["id"], "samples", os.path.basename(p["src"]))
            self.assertTrue(os.path.isfile(mine) and os.path.isfile(copy))
            self.assertEqual(TM.slurp(mine), TM.slurp(copy))
            self.assertEqual((p["start"], p["from"]["song"]), (0, "A"))
            self.assertAlmostEqual(p["gain"], 0.891 / 0.5, places=2)       # the level it played at, kept with it
        self.assertEqual((kit["pads"][1]["params"]["gainDb"], kit["pads"][1]["params"]["pitch"]), (-4, 3))   # its own sound
        self.assertEqual((kit["pads"][2]["song_id"], kit["pads"][5]["song_id"]), ("B", "B"))
        stored = json.load(open(os.path.join(self.root, "_kits", kit["id"], "kit.json")))
        self.assertEqual(stored["pads"], kit["pads"])
        self.assertEqual(self.call("/api/kits")[1][0]["kept"], 2)

    def test_the_kit_does_not_depend_on_the_mega_bank_and_exports(self):
        mid, kit = self.make_kit()
        want = X.pad_pcm({**X.bank_pads(os.path.join(self.root, "A"), 0, {}, [])[0], "params": {}}, {})[3]
        self.assertEqual(self.call(f"/api/mega/{mid}", method="DELETE")[0], 200)   # the mega bank and its samples go (to .trash)
        for kind in ("zip", "sfz", "hydrogen"):
            code, r = self.call(f"/api/kits/{kit['id']}/export", {"kind": kind})
            self.assertEqual(code, 200, (kind, r))
        code, r = self.call(f"/api/kits/{kit['id']}/export", {"kind": "zip"})
        self.assertEqual(zip_pad(r["path"], "pad01.wav"), want)
        self.assertEqual(len(zipfile.ZipFile(r["path"]).namelist()), 16)
        # a kit entry whose sound is missing is refused clearly
        os.remove(os.path.join(self.root, "_kits", kit["id"], "samples", os.path.basename(kit["pads"][0]["src"])))
        code, r = self.call(f"/api/kits/{kit['id']}/export", {"kind": "zip"})
        self.assertEqual(code, 400)
        self.assertIn("missing", r["error"])

    def test_loading_the_kit_into_a_mega_bank_copies_the_samples_and_emits_slice_keep(self):
        mid, kit = self.make_kit()
        other = self.new_mega("B", "Other")
        s = self.stream(f"/api/mega/{other}/events", "tab2")
        code, r = self.post(f"/api/mega/{other}/loadkit", {"kit": kit["id"], "bank": 0})
        self.assertEqual(code, 200, r)
        self.assertEqual([a["type"] for a in r["actions"]], ["kit.load", "slice.keep", "slice.keep"])
        keeps = [a for a in r["actions"] if a["type"] == "slice.keep"]
        self.assertEqual([(a["bank"], a["pad"]) for a in keeps], [(0, 0), (0, 1)])
        for a in keeps:
            self.assertRegex(a["src"], rf"^/media/_mega/{other}/samples/")
            self.assertTrue(os.path.isfile(os.path.join(self.root, "_mega", other, "samples", os.path.basename(a["src"]))))
            self.assertEqual(TM.slurp(os.path.join(self.root, "_mega", other, "samples", os.path.basename(a["src"]))),
                             TM.slurp(os.path.join(self.root, "_kits", kit["id"], "samples", os.path.basename(a["src"]))))
        self.assertEqual(keeps[1]["params"]["gainDb"], -4)
        ov = self.get(other)["project"]["overrides"]
        self.assertTrue(ov["0:0"]["kept"] and ov["0:1"]["kept"])
        self.assertAlmostEqual(ov["0:0"]["gain"], 1.782, places=2)
        self.assertEqual(ov["0:0"]["from"]["song"], "A")
        self.assertNotIn("song", ov["0:2"])                    # the kit's pad 3 is song B's chop and the mega bank's source is B
        self.assertEqual(self.get(other)["project"]["pads"][1]["gainDb"], 0)    # the kept pad's sound lives in its override
        self.assertEqual(len(s.wait(1)), 1)
        self.assertEqual([a["type"] for a in s.of("actions")[0]["actions"]], ["kit.load", "slice.keep", "slice.keep"])
        # the kit is untouched, and a kit load twice changes nothing more
        self.assertEqual(len(os.listdir(os.path.join(self.root, "_kits", kit["id"], "samples"))), 2)
        code, again = self.post(f"/api/mega/{other}/loadkit", {"kit": kit["id"], "bank": 0})
        self.assertEqual(code, 200)
        self.assertNotIn("slice.keep", [a["type"] for a in again["actions"]])        # the pads are kept already: nothing is copied again
        self.assertEqual(len(os.listdir(os.path.join(self.root, "_mega", other, "samples"))), 2)
        self.assertEqual(self.post(f"/api/mega/{other}/loadkit", {"kit": "nope", "bank": 0})[0], 404)
        self.assertEqual(self.post(f"/api/mega/{other}/loadkit", {"kit": kit["id"], "bank": -1})[0], 400)
        self.assertEqual(self.post("/api/mega/nope/loadkit", {"kit": kit["id"]})[0], 404)

    def test_old_kits_still_load_and_kit_bodies_are_checked(self):
        pads = [None] * 16
        pads[0] = {"song_id": "A", "start": 1.0, "end": 1.5}
        code, kit = self.call("/api/kits", {"name": "Old", "pads": pads})
        self.assertEqual(code, 201, kit)
        mid = self.new_mega("B")
        code, r = self.post(f"/api/mega/{mid}/loadkit", {"kit": kit["id"], "bank": 0})
        self.assertEqual((code, [a["type"] for a in r["actions"]]), (200, ["kit.load"]))
        self.assertEqual(self.get(mid)["project"]["overrides"]["0:0"], {"start": 1.0, "end": 1.5, "song": "A"})
        for bad in ({"src": "/media/_kits/x/samples/0123456789abcdef.wav", "start": 0, "end": 1},      # file does not exist
                    {"src": "/media/B/audio.wav", "start": 0, "end": 1},
                    {"src": "/media/_mega/m0123456789a/samples/0123456789abcdef.wav", "start": 1, "end": 1}):
            code, r = self.call("/api/kits", {"name": "Bad", "pads": [bad]})
            self.assertEqual(code, 400, (bad, r))

    def test_a_mega_bank_resample_goes_into_the_kit_as_a_kept_pad(self):
        mid = self.new_mega("A")
        up = json.loads(TM.urllib.request.urlopen(TM.urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/mega/{mid}/resample", data=wav_bytes(1.0), headers={"Content-Type": "audio/wav"}, method="POST")).read())
        self.acts(mid, {"type": "slice.src", "bank": 0, "pad": 4, "src": up["url"], "start": 0, "end": up["duration"]})
        code, kit = self.post(f"/api/mega/{mid}/savekit", {"bank": 0, "name": "Resample kit"})
        self.assertEqual(code, 200, kit)
        self.assertRegex(kit["pads"][4]["src"], r"^/media/_kits/")
        self.assertTrue(os.path.isfile(os.path.join(self.root, "_kits", kit["id"], "samples", up["hash"] + ".wav")))


class Sweep(DRig):
    def samples(self, mid):
        return sorted(os.listdir(os.path.join(self.root, "_mega", mid, "samples")))

    def trash(self, mid):
        d = os.path.join(self.root, "_mega", mid, ".trash")
        return sorted(os.listdir(d)) if os.path.isdir(d) else []

    def test_a_pad_kept_released_and_kept_again(self):
        mid = self.new_mega("A")
        self.acts(mid, *self.hits(0))
        self.assertEqual(self.post(f"/api/mega/{mid}/keep", {"bank": 0, "pad": 0})[0], 200)
        [name] = self.samples(mid)
        self.assertEqual(self.trash(mid), [])
        self.acts(mid, {"type": "slice.clear", "bank": 0, "pad": 0})                 # released: the sample no override uses is swept
        self.assertEqual((self.samples(mid), self.trash(mid)), ([], [name]))
        self.assertEqual(self.post(f"/api/mega/{mid}/keep", {"bank": 0, "pad": 0})[0], 200)   # kept again: the same content, the same name
        self.assertEqual((self.samples(mid), self.trash(mid)), ([name], [name]))
        self.assertEqual(self.get(mid)["project"]["overrides"]["0:0"]["src"], f"/media/_mega/{mid}/samples/{name}")
        self.acts(mid, {"type": "slice.clear", "bank": 0, "pad": 0})
        self.assertEqual((self.samples(mid), self.trash(mid)), ([], sorted([name, name[:-4] + "-2.wav"])))   # nothing is ever deleted
        self.assertEqual(self.call(f"/media/_mega/{mid}/samples/{name}", raw=True)[0], 404)
        # replacing a kept pad by another sound sweeps the old sample
        self.post(f"/api/mega/{mid}/keep", {"bank": 0, "pad": 0})
        self.acts(mid, {"type": "slice.set", "bank": 0, "pad": 0, "start": 3.0, "end": 3.5})
        self.assertEqual(self.samples(mid), [])

    def test_on_demand_sweeps_orphans_and_keeps_what_overrides_use(self):
        mid = self.new_mega("A")
        self.acts(mid, *self.hits(0, 1))
        self.post(f"/api/mega/{mid}/keep", {"bank": 0, "pad": 0})
        self.post(f"/api/mega/{mid}/keep", {"bank": 0, "pad": 1})
        keep = self.samples(mid)
        stray = "0123456789abcdef.wav"
        shutil.copy(os.path.join(self.root, "_mega", mid, "samples", keep[0]), os.path.join(self.root, "_mega", mid, "samples", stray))
        open(os.path.join(self.root, "_mega", mid, "samples", "notes.txt"), "w").write("not a sample")
        code, r = self.post(f"/api/mega/{mid}/sweep", {})
        self.assertEqual((code, r["moved"], r["kept"]), (200, [stray], 2))
        self.assertEqual((sorted(f for f in self.samples(mid) if f.endswith(".wav")), self.trash(mid)), (keep, [stray]))
        self.assertIn("notes.txt", self.samples(mid))                  # anything that is not a sample file is left alone
        self.assertEqual(self.post(f"/api/mega/{mid}/sweep", {})[1]["moved"], [])
        self.assertEqual(self.post("/api/mega/nope/sweep", {})[0], 404)

    def test_a_sample_a_song_pattern_needs_is_never_swept(self):
        mid = self.new_mega("A")
        self.acts(mid, *self.hits(0), {"type": "song.addPattern", "bars": 1}, *self.hits(5, 6))   # pad 1 is hit by pattern 1 only
        self.assertEqual(self.get(mid)["project"]["pattern"]["events"][0]["pad"], 5)
        for pad in (0, 5, 6):
            self.post(f"/api/mega/{mid}/keep", {"bank": 0, "pad": pad})
        names = self.samples(mid)
        self.assertEqual(len(names), 3)
        self.assertEqual(self.post(f"/api/mega/{mid}/sweep", {})[1]["moved"], [])
        self.acts(mid, {"type": "pattern.clear"})
        self.assertEqual(self.samples(mid), names)                      # the pad of the cleared pattern is still an override: its sample stays

    def test_a_kit_copy_in_progress_and_a_fresh_upload_are_not_swept(self):
        mid = self.new_mega("A")
        self.acts(mid, *self.hits(0))
        self.post(f"/api/mega/{mid}/keep", {"bank": 0, "pad": 0})
        self.acts(mid, {"type": "slice.set", "bank": 0, "pad": 0, "start": 1.0, "end": 1.5})   # now nothing refers to it
        self.assertEqual(self.samples(mid), [])
        up = json.loads(TM.urllib.request.urlopen(TM.urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/mega/{mid}/resample", data=wav_bytes(0.5, 500), headers={"Content-Type": "audio/wav"}, method="POST")).read())
        self.post(f"/api/mega/{mid}/sweep", {})                         # the upload is no override's yet
        self.assertIn(up["hash"] + ".wav", self.samples(mid))
        M.pin_for(mid, [up["hash"] + ".wav"], seconds=-1)                # its protection has run out
        with M.pinned(mid, [up["hash"] + ".wav"]):                       # but a kit copy of it is in progress
            self.assertEqual(M.sweep_samples(self.root, mid)["moved"], [])
        self.assertEqual(M.sweep_samples(self.root, mid)["moved"], [up["hash"] + ".wav"])


class BarLength(DRig):
    """The pattern keeps its own bar length (project.pattern.beatsPerBar): the server stamps it, the writers of MIDI, .h2song and the
    DAW project read it."""
    def test_the_first_hit_stamps_the_bar_length_and_it_is_saved_and_survives_a_source_with_another_meter(self):
        d = os.path.join(self.root, "B", "chops.json")
        three = json.load(open(d))
        three["beats_per_bar"] = 3
        with open(d, "w") as f:
            json.dump(three, f)
        mid = self.new_mega("A")
        self.acts(mid, *self.hits(0))
        self.assertEqual(self.get(mid)["project"]["pattern"]["beatsPerBar"], 4)
        self.switch(mid, "B")
        self.assertEqual(self.get(mid)["chops"]["beats_per_bar"], 3)
        self.acts(mid, *self.hits(1, start=2))
        self.assertEqual(self.get(mid)["project"]["pattern"]["beatsPerBar"], 4)           # not 3
        self.acts(mid, {"type": "pattern.setBars", "bars": 1})
        self.assertEqual([e["beat"] for e in self.get(mid)["project"]["pattern"]["events"]], [0, 2])   # 1 bar of 4 beats, not 3
        # a pattern with no events yet takes the open song's bar length with its first hit
        other = self.new_mega("B")
        self.acts(other, *self.hits(0))
        self.assertEqual(self.get(other)["project"]["pattern"]["beatsPerBar"], 3)

    def test_the_daw_project_and_the_h2song_use_it(self):
        import daw_project as DP
        mid = self.new_mega("A")
        self.acts(mid, *self.hits(0, 1, start=0), {"type": "pattern.setBars", "bars": 2})
        doc = M.read(self.root, mid)
        self.assertEqual(doc["project"]["pattern"]["beatsPerBar"], 4)             # the hit came first: 4 from the chop table of A
        doc["project"]["pattern"]["beatsPerBar"] = 3
        proj = DP.build_mega_project(self.root, doc, M.chops_of(self.root, doc), M.song_dir(self.root, "A"), os.path.join(self.tmp, "kits"), bank=0)
        self.assertEqual(proj.beats_per_bar, 3)
        import h2song
        root, info = h2song.build_song(title="t", bpm=120, beats_per_bar=proj.beats_per_bar, wavs=proj.wavs(), pattern=proj.pattern,
                                       empty_wav="e.wav")
        self.assertEqual(root.find("patternList/pattern/size").text, str(2 * 3 * 48))     # 2 bars of 3 beats
        old = {**doc["project"], "pattern": {"bars": 2, "events": []}}
        self.assertEqual(DP.project_beats_per_bar(old, {"beats_per_bar": 5}), 5)           # an old pattern reads the chop table


if __name__ == "__main__":
    unittest.main()
