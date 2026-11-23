"""CHOP-13 step A: the drum pad mega bank store, the lazy non-destructive migration, the mega endpoints, keeping pads, the
source switch and the owner's scenario (contracts §15). Synthetic songs whose slots are told apart by pitch, the real server
(server.make_server on a free port, choppad.make_server for the export), temporary media roots only, no network."""
import os, sys, tempfile, unittest
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from _common import *  # noqa: E402,F403

import hashlib, http.client, json, shutil, threading, time, urllib.error, urllib.request, wave, zipfile, io  # noqa: E402
import numpy as np  # noqa: E402
sys.path.insert(0, os.path.join(ROOT, "components", "control"))  # noqa: F405
sys.path.insert(0, os.path.join(ROOT, "components", "daw"))  # noqa: F405
import choppad_actions as A  # noqa: E402
import choppad_export as X  # noqa: E402
import mega as M  # noqa: E402
import server as srvmod  # noqa: E402

SR = 44100


def write_song(root, sid, title, base_hz, n_chops, bpm=120.0, project=None):
    """A song folder: audio.wav where half-second slot k is a sine of base_hz + 37 k (so every chop sounds different), and the
    json files the server needs. 16 chops per bank."""
    d = os.path.join(root, sid)
    os.makedirs(d, exist_ok=True)
    total = n_chops * 0.5 + 1.0
    t = np.arange(int(total * SR)) / SR
    slot = np.minimum((t / 0.5).astype(int), n_chops - 1)
    x = (0.5 * np.sin(2 * np.pi * (base_hz + 37 * slot) * t) * 32767).astype("<i2")
    with wave.open(os.path.join(d, "audio.wav"), "wb") as w:
        w.setnchannels(2), w.setsampwidth(2), w.setframerate(SR)
        w.writeframes(np.repeat(x[:, None], 2, axis=1).tobytes())
    chops = {"schema": "choppad.chops/2", "grid": "1/4", "bpm": bpm, "beats_per_bar": 4,
             "chops": [{"index": i, "start": i * 0.5, "end": i * 0.5 + 0.5, "bar": i // 4, "beat": i % 4} for i in range(n_chops)],
             "banks": [list(range(s, min(s + 16, n_chops))) for s in range(0, n_chops, 16)]}
    for name, doc in (("meta.json", {"id": sid, "title": title, "duration_s": total}), ("chops.json", chops),
                      ("analysis.json", {"duration_s": total, "bpm": bpm, "meter": {"beats_per_bar": 4, "beat_unit": 4}, "beats": [], "downbeats": []}),
                      ("peaks.json", {"duration_s": total, "peaks": [0.5] * 10})):
        with open(os.path.join(d, name), "w") as f:
            json.dump(doc, f)
    if project is not None:
        with open(os.path.join(d, "project.json"), "w") as f:
            json.dump(project, f)
    return d


def slurp(path):
    with open(path, "rb") as f:
        return f.read()


def read_wav(path):
    with wave.open(path, "rb") as w:
        return w.getframerate(), w.getnchannels(), w.readframes(w.getnframes())


def sha(path):
    return hashlib.sha1(slurp(path)).hexdigest()


class Stream:
    """A minimal SSE client."""
    def __init__(self, port, path, headers=None):
        self.conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        self.conn.request("GET", path, headers=headers or {})
        self.resp = self.conn.getresponse()
        self.events, self.cv = [], threading.Condition()
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        ev, data = None, []
        try:
            for raw in self.resp:
                line = raw.decode().rstrip("\n")
                if line.startswith(":") or line.startswith("retry:"):
                    continue
                if line.startswith("event:"):
                    ev = line[6:].strip()
                elif line.startswith("data:"):
                    data.append(line[5:].strip())
                elif line == "" and ev:
                    with self.cv:
                        self.events.append((ev, json.loads("\n".join(data))))
                        self.cv.notify_all()
                    ev, data = None, []
        except (OSError, ValueError, http.client.HTTPException):
            pass

    def wait(self, n, timeout=3.0):
        end = time.time() + timeout
        with self.cv:
            while len(self.events) < n and time.time() < end:
                self.cv.wait(0.05)
        return list(self.events)

    def of(self, kind):
        return [d for e, d in self.events if e == kind]

    def close(self):
        self.conn.close()


class Rig(unittest.TestCase):
    """Songs `A` (a "drum break": 3 banks, 120 bpm) and `B` (1 bank, 90 bpm) in a temporary media root, a server on a free port."""
    project_a = None   # an old project.json to migrate, or None

    @classmethod
    def make_template(cls):
        cls.tmpl = tempfile.mkdtemp(prefix="choppad-mega-tmpl-")
        cls.addClassCleanup(shutil.rmtree, cls.tmpl, ignore_errors=True)
        write_song(cls.tmpl, "A", "Drum break", 300, 40, bpm=120.0)
        write_song(cls.tmpl, "B", "Bass and loop", 1500, 16, bpm=90.0)

    @classmethod
    def setUpClass(cls):
        cls.make_template()

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="choppad-mega-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.root = os.path.join(self.tmp, "media")
        shutil.copytree(self.tmpl, self.root)
        self.start()

    def start(self, maker=None):
        self.srv = (maker or (lambda r: srvmod.make_server(r, port=0)))(self.root)
        self.port = self.srv.server_address[1]
        threading.Thread(target=lambda: self.srv.serve_forever(poll_interval=0.05), daemon=True).start()
        self.streams = []
        self.addCleanup(self.stop)

    def stop(self):
        for s in self.streams:
            s.close()
        self.srv.shutdown()
        self.srv.server_close()

    def stream(self, path, origin):
        s = Stream(self.port, path, {"X-ChopPad-Origin": origin})
        self.streams.append(s)
        return s

    def call(self, path, body=None, method=None, headers=None, raw=False):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=None if body is None else json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json", **(headers or {})}, method=method or ("POST" if body is not None else "GET"))
        try:
            with urllib.request.urlopen(req) as r:
                data = r.read()
                return r.status, (data if raw else json.loads(data or b"{}"))
        except urllib.error.HTTPError as e:
            data = e.read()
            return e.code, (data if raw else json.loads(data or b"{}"))

    # ---- helpers ----
    def mega_of(self, sid):
        code, doc = self.call(f"/api/songs/{sid}")
        self.assertEqual(code, 200)
        return doc["mega"]

    def acts(self, mid, *actions, origin="t"):
        code, r = self.call(f"/api/mega/{mid}/actions", {"actions": list(actions)}, headers={"X-ChopPad-Origin": origin})
        self.assertEqual(code, 200, r)
        return r

    def get(self, mid):
        code, r = self.call(f"/api/mega/{mid}")
        self.assertEqual(code, 200)
        return r

    def mega_file(self, mid):
        return os.path.join(self.root, "_mega", mid, "mega.json")

    def pads_of(self, mid, bank):
        """The pad list an exporter or an offline render sees for bank `bank` of the mega bank (bank_pads)."""
        doc = M.read(self.root, mid)
        d = M.song_dir(self.root, doc["source"])
        return X.bank_pads(d, bank, doc["project"].get("overrides"), doc["project"].get("pads"),
                           chops=M.chops_of(self.root, doc), media_root=self.root)

    def hits(self, *pads, bank=0, start=0):
        return [{"type": "pattern.addEvent", "event": {"beat": start + i * 0.5, "pad": p, "bank": bank, "vel": 1, "pass": 1}} for i, p in enumerate(pads)]


# ------------------------------------------------------------------------------------------------------------------------
class Store(Rig):
    def test_create_read_list_rename_delete(self):
        a = M.create(self.root, "My break", "A")
        self.assertEqual(M.read(self.root, a["id"])["name"], "My break")
        self.assertEqual((a["schema"], a["source"]), ("choppad.mega/1", "A"))
        self.assertEqual(a["project"], {"pads": [{} for _ in range(16)], "overrides": {}})
        self.assertTrue(os.path.isdir(os.path.join(self.root, "_mega", a["id"], "samples")))
        b = M.create(self.root, None, None)
        self.assertEqual(b["name"], "Drum pad mega bank")
        self.assertEqual(M.create(self.root, None, "B")["name"], "Bass and loop")   # named after its song by default
        self.assertEqual({d["id"] for d in M.list_docs(self.root)} >= {a["id"], b["id"]}, True)
        M.rename(self.root, a["id"], "Renamed")
        self.assertEqual(M.read(self.root, a["id"])["name"], "Renamed")
        with self.assertRaises(ValueError):
            M.rename(self.root, a["id"], "  ")
        with self.assertRaises(ValueError):
            M.rename(self.root, a["id"], "x" * 81)
        with self.assertRaises(ValueError):
            M.create(self.root, None, "nope")          # unknown song

    def test_ids_are_safe_for_paths(self):
        for bad in ("../x", "a/b", "", ".trash", "a.b", None, "x" * 65):
            with self.assertRaises(KeyError):
                M.read(self.root, bad)
        a = M.create(self.root, None, "A")
        self.assertRegex(a["id"], r"^[A-Za-z0-9_-]{1,64}$")
        self.assertEqual(self.call("/api/mega/..%2f..%2fA")[0], 404)

    def test_delete_moves_to_trash_and_listing_ignores_it(self):
        a = M.create(self.root, "x", "A")
        dest = M.delete(self.root, a["id"])
        self.assertTrue(os.path.isfile(os.path.join(dest, "mega.json")))
        self.assertEqual(os.path.dirname(dest), os.path.join(self.root, "_mega", ".trash"))
        self.assertRegex(os.path.basename(dest), r"^" + a["id"] + r"-\d{8}-\d{6}")
        self.assertFalse(os.path.exists(os.path.join(self.root, "_mega", a["id"])))
        self.assertNotIn(a["id"], [d["id"] for d in M.list_docs(self.root)])
        with self.assertRaises(KeyError):
            M.read(self.root, a["id"])
        # a second mega bank deleted in the same second does not collide
        b = M.create(self.root, "y", "A")
        os.makedirs(os.path.join(self.root, "_mega", ".trash", f"{b['id']}-{time.strftime('%Y%m%d-%H%M%S')}"), exist_ok=True)
        M.delete(self.root, b["id"])
        self.assertEqual(len(os.listdir(os.path.join(self.root, "_mega", ".trash"))), 3)

    def test_write_is_atomic(self):
        a = M.create(self.root, "x", "A")
        path = self.mega_file(a["id"])
        bad, stop = [], threading.Event()

        def reader():
            while not stop.is_set():
                try:
                    with open(path) as f:
                        json.load(f)
                except ValueError as e:
                    bad.append(str(e))
        t = threading.Thread(target=reader)
        t.start()
        try:
            a["project"]["overrides"] = {f"0:{i % 16}": {"start": 0, "end": 1} for i in range(300)}
            for _ in range(100):
                M.write(self.root, a)
        finally:
            stop.set()
            t.join()
        self.assertEqual(bad, [])
        self.assertEqual(sorted(os.listdir(os.path.dirname(path))), ["mega.json", "samples"])

    def test_mega_for_song_is_the_most_recently_updated_one(self):
        first = M.mega_for_song(self.root, "A")
        second = M.create(self.root, "second", "A")
        other = M.create(self.root, "other song", "B")
        self.assertEqual(M.mega_for_song(self.root, "A")["id"], second["id"])    # newest wins
        first = M.read(self.root, first["id"])
        M.write(self.root, M.touch(first, time.time() + 10))
        self.assertEqual(M.mega_for_song(self.root, "A")["id"], first["id"])
        self.assertEqual(M.mega_for_song(self.root, "B")["id"], other["id"])
        self.assertEqual(len(M.megas_with_source(self.root, "A")), 2)
        with self.assertRaises(KeyError):
            M.mega_for_song(self.root, "nope")

    def test_used_pads(self):
        project = {"overrides": {"1:3": {"start": 0, "end": 1}, "junk": {}, "0:99": {}},
                   "pattern": {"bars": 2, "events": [{"beat": 0, "pad": 2, "bank": 0}, {"beat": 1, "pad": 3}]},
                   "song": {"patterns": [{"id": "p1", "events": [{"beat": 0, "pad": 5, "bank": 2}]}, {"id": "p2", "events": []}]}}
        self.assertEqual(M.used_pads(project), [(0, 2), (0, 3), (1, 3), (2, 5)])
        self.assertEqual(A.bank_count(project, {"banks": [[0], [1]]}), 2)   # overrides reach bank 1 only
        self.assertEqual(A.bank_count({"overrides": {"4:0": {}}}, {"banks": [[0]]}), 5)
        self.assertEqual(A.bank_count({}, None), 1)


# ------------------------------------------------------------------------------------------------------------------------
class Migration(Rig):
    OLD_A = {"pads": [{"gainDb": -3, "pitch": 2}] + [{} for _ in range(15)], "overrides": {"0:1": {"start": 3.0, "end": 3.5}},
             "pattern": {"bars": 2, "events": [{"beat": 0, "pad": 0, "bank": 0, "vel": 1, "pass": 1}]}, "fx": {"reverb": 0.3},
             "tempo": {"bpm": 101, "follow": False}}
    OLD_B = {"pads": [{} for _ in range(16)], "overrides": {}}

    def setUp(self):
        super().setUp()

    def with_projects(self):
        for sid, doc in (("A", self.OLD_A), ("B", self.OLD_B)):
            with open(os.path.join(self.root, sid, "project.json"), "w") as f:
                json.dump(doc, f)
        self.files = {sid: os.path.join(self.root, sid, "project.json") for sid in "AB"}
        self.before = {sid: (slurp(p), os.stat(p).st_mtime_ns) for sid, p in self.files.items()}

    def test_get_song_gives_the_same_project_and_project_json_is_untouched(self):
        self.with_projects()
        for sid, doc in (("A", self.OLD_A), ("B", self.OLD_B)):
            code, r = self.call(f"/api/songs/{sid}")
            self.assertEqual(code, 200)
            self.assertEqual(r["project"], doc, "the project of the first mega bank is what project.json held")
            self.assertEqual(self.call(f"/api/songs/{sid}")[1]["project"], doc)
            self.assertEqual(r["mega"], self.mega_of(sid))
            self.assertEqual(M.read(self.root, r["mega"])["source"], sid)
            self.assertEqual(M.read(self.root, r["mega"])["name"], r["meta"]["title"])
        self.assertEqual({sid: (slurp(p), os.stat(p).st_mtime_ns) for sid, p in self.files.items()}, self.before)

    def test_edits_never_write_project_json_and_survive_in_the_mega_bank(self):
        self.with_projects()
        mid = self.mega_of("A")
        code, r = self.call("/api/songs/A/actions", {"actions": [{"type": "pad.set", "pad": 4, "params": {"gainDb": -8}},
                                                                 {"type": "pattern.toggleStep", "pad": 2, "step": 4}]})
        self.assertEqual((code, r["applied"], r["mega"]), (200, 2, mid))
        self.assertEqual(self.call("/api/songs/A")[1]["project"]["pads"][4]["gainDb"], -8)
        self.assertEqual(self.get(mid)["project"]["pads"][4]["gainDb"], -8)
        self.assertEqual(self.call("/api/songs/A/project", self.get(mid)["project"], method="PUT")[0], 200)
        self.assertEqual({sid: (slurp(p), os.stat(p).st_mtime_ns) for sid, p in self.files.items()}, self.before)
        self.assertEqual(M.read(self.root, mid)["project"]["tempo"], {"bpm": 101, "follow": False})

    def test_no_project_json_gives_an_empty_project_and_nothing_is_created_in_the_song(self):
        before = sorted(os.listdir(os.path.join(self.root, "A")))
        code, r = self.call("/api/songs/A")
        self.assertEqual(r["project"], {"pads": [{} for _ in range(16)], "overrides": {}})
        self.assertEqual(sorted(os.listdir(os.path.join(self.root, "A"))), before)

    def test_deleting_the_migrated_mega_bank_does_not_bring_the_old_project_back(self):
        self.with_projects()
        mid = self.mega_of("A")
        self.assertEqual(self.call(f"/api/mega/{mid}", method="DELETE")[0], 200)
        r = self.call("/api/songs/A")[1]
        self.assertNotEqual(r["mega"], mid)
        self.assertEqual(r["project"], {"pads": [{} for _ in range(16)], "overrides": {}})
        self.assertEqual(slurp(self.files["A"]), self.before["A"][0])

    def test_export_defaults_read_the_mega_bank_without_creating_one(self):
        self.with_projects()
        d = os.path.join(self.root, "A")
        self.assertEqual(X.project_overrides(d), self.OLD_A["overrides"])             # no mega bank yet: the old file, read only
        self.assertFalse(os.path.exists(os.path.join(self.root, "_mega")))
        mid = self.mega_of("A")
        self.acts(mid, {"type": "slice.set", "bank": 0, "pad": 1, "start": 5, "end": 6})
        self.assertEqual(X.project_overrides(d)["0:1"], {"start": 5, "end": 6})        # now the mega bank's


# ------------------------------------------------------------------------------------------------------------------------
class Endpoints(Rig):
    def test_list_create_get_rename_delete(self):
        mid = self.mega_of("A")
        self.acts(mid, *self.hits(0, 1), {"type": "bank.set", "bank": 1})
        code, lst = self.call("/api/mega")
        self.assertEqual(code, 200)
        self.assertEqual([k for k in lst[0]], ["id", "name", "source", "source_title", "created", "updated", "used_pads", "banks"])
        self.assertEqual((lst[0]["id"], lst[0]["source"], lst[0]["source_title"], lst[0]["used_pads"], lst[0]["banks"]), (mid, "A", "Drum break", 2, 3))
        code, new = self.call("/api/mega", {"name": "Fresh", "source": "B"})
        self.assertEqual(code, 201)
        self.assertEqual((new["mega"]["name"], new["mega"]["source"], new["mega"]["used_pads"], new["mega"]["banks"]), ("Fresh", "B", 0, 1))
        self.assertEqual(new["project"], {"pads": [{} for _ in range(16)], "overrides": {}})
        self.assertEqual(new["chops"]["bpm"], 90.0)
        empty = self.call("/api/mega", {})[1]
        self.assertEqual((empty["mega"]["source"], empty["chops"]["banks"], empty["mega"]["banks"]), (None, [], 1))
        self.assertEqual(self.call("/api/mega", {"source": "nope"})[0], 404)
        self.assertEqual(self.call("/api/mega", {"source": 5})[0], 404)
        g = self.get(new["mega"]["id"])
        self.assertEqual(g["view"], {"bank": None, "selected": None})
        self.assertEqual(self.get(mid)["view"]["bank"], 1)
        code, r = self.call(f"/api/mega/{new['mega']['id']}", {"name": "Renamed"}, method="PUT")
        self.assertEqual((code, r["mega"]["name"]), (200, "Renamed"))
        self.assertEqual(self.call(f"/api/mega/{mid}", {"name": ""}, method="PUT")[0], 400)
        self.assertEqual(self.call("/api/mega/nope")[0], 404)
        self.assertEqual(self.call("/api/mega/nope/actions", {"actions": []})[0], 404)
        self.assertEqual(self.call("/api/mega/nope", method="DELETE")[0], 404)
        self.assertEqual(self.call(f"/api/mega/{mid}/actions", {"nope": 1})[0], 400)
        self.assertEqual(self.call(f"/api/mega/{mid}/junk", {})[0], 404)
        self.assertEqual(self.call(f"/api/mega/{mid}/project", {})[0], 405)
        self.assertEqual(self.call(f"/api/mega/{mid}", method="DELETE")[0], 200)
        self.assertNotIn(mid, [m["id"] for m in self.call("/api/mega")[1]])
        self.assertTrue(any(n.startswith(mid) for n in os.listdir(os.path.join(self.root, "_mega", ".trash"))))

    def test_song_and_mega_endpoints_are_the_same_bank_and_the_same_events(self):
        mid = self.mega_of("A")
        song_stream = self.stream("/api/songs/A/events?origin=s", "s")
        mega_stream = self.stream(f"/api/mega/{mid}/events?origin=m", "m")
        song_stream.wait(0), mega_stream.wait(0)
        time.sleep(0.2)
        code, r = self.call("/api/songs/A/actions", {"actions": [{"type": "pad.set", "pad": 2, "params": {"gainDb": -5}}]}, headers={"X-ChopPad-Origin": "x"})
        self.assertEqual((code, r["applied"], r["listeners"]), (200, 1, 2))
        code, r = self.call(f"/api/mega/{mid}/actions", {"actions": [{"type": "fx.set", "params": {"reverb": 0.4}}]}, headers={"X-ChopPad-Origin": "y"})
        self.assertEqual((code, r["applied"], r["listeners"]), (200, 1, 2))
        for s in (song_stream, mega_stream):
            ev = [e for e in s.wait(2) if e[0] == "actions"]
            self.assertEqual([a["type"] for _, d in ev for a in d["actions"]], ["pad.set", "fx.set"])
        self.assertEqual(self.call("/api/songs/A")[1]["project"]["fx"]["reverb"], 0.4)
        self.assertEqual(self.get(mid)["project"]["pads"][2]["gainDb"], -5)
        # transport, too
        self.call(f"/api/mega/{mid}/transport", {"command": "play-pattern"}, headers={"X-ChopPad-Origin": "z"})
        self.call("/api/songs/A/transport", {"command": "stop"}, headers={"X-ChopPad-Origin": "z"})
        for s in (song_stream, mega_stream):
            self.assertEqual([d["command"] for d in [e[1] for e in s.wait(4) if e[0] == "transport"]], ["play-pattern", "stop"])
        self.assertEqual(self.call(f"/api/mega/{mid}/transport", {"command": "nope"})[0], 400)

    def test_option_a_new_mega_bank_for_the_new_song_leaves_the_first_untouched(self):
        mid = self.mega_of("A")
        self.acts(mid, *self.hits(0, 1, 2, 3), {"type": "pad.set", "pad": 0, "params": {"gainDb": -3}})
        before = slurp(self.mega_file(mid))
        code, new = self.call("/api/mega", {"source": "B"})
        self.assertEqual(code, 201)
        self.assertEqual(new["project"], {"pads": [{} for _ in range(16)], "overrides": {}})
        self.assertEqual(new["mega"]["source"], "B")
        self.assertEqual(slurp(self.mega_file(mid)), before)
        self.assertEqual(self.call("/api/songs/B")[1]["mega"], new["mega"]["id"])    # the song-keyed endpoints follow the song
        self.assertEqual(self.call("/api/songs/A")[1]["mega"], mid)

    def test_put_project_on_the_mega_endpoint_broadcasts_the_difference(self):
        mid = self.mega_of("A")
        s = self.stream(f"/api/mega/{mid}/events?origin=s", "s")
        time.sleep(0.2)
        proj = self.get(mid)["project"]
        proj["overrides"]["0:2"] = {"start": 1, "end": 2}
        code, _ = self.call(f"/api/mega/{mid}/project", proj, method="PUT", headers={"X-ChopPad-Origin": "p"})
        self.assertEqual(code, 200)
        ev = [d for e, d in s.wait(1) if e == "actions"]
        self.assertEqual([a["type"] for a in ev[0]["actions"]], ["slice.setMany"])
        self.assertEqual(self.call(f"/api/mega/{mid}/project", {"pads": 1}, method="PUT")[0], 400)

    def test_chops_events_reach_every_mega_bank_of_the_source(self):
        mid = self.mega_of("A")
        other = self.call("/api/mega", {"source": "A"})[1]["mega"]["id"]
        s1 = self.stream(f"/api/mega/{mid}/events?origin=1", "1")
        s2 = self.stream(f"/api/mega/{other}/events?origin=2", "2")
        s3 = self.stream("/api/songs/B/events?origin=3", "3")
        time.sleep(0.2)
        self.srv.RequestHandlerClass  # (regrid itself needs the choppad pipeline; the fan-out helper is what is under test)
        for sid in M.megas_with_source(self.root, "A"):
            self.srv.hub.publish(sid, "chops", {"origin": "", "chops": {"x": 1}})
        self.assertEqual(len(s1.wait(1)), 1)
        self.assertEqual(len(s2.wait(1)), 1)
        time.sleep(0.2)
        self.assertEqual(s3.of("chops"), [])


# ------------------------------------------------------------------------------------------------------------------------
class Media(Rig):
    def test_kept_samples_are_served_with_range_and_nothing_else_from_mega(self):
        mid = self.mega_of("A")
        self.acts(mid, *self.hits(0))
        r = self.call(f"/api/mega/{mid}/keep", {"bank": 0, "pad": 0})[1]
        self.assertEqual(len(r["kept"]), 1)
        url = self.get(mid)["project"]["overrides"]["0:0"]["src"]
        path = os.path.join(self.root, "_mega", mid, "samples", url.rsplit("/", 1)[1])
        code, body = self.call(url, raw=True)
        self.assertEqual((code, body), (200, slurp(path)))
        conn = http.client.HTTPConnection("127.0.0.1", self.port)
        conn.request("GET", url, headers={"Range": "bytes=0-99"})
        resp = conn.getresponse()
        data = resp.read()
        self.assertEqual((resp.status, len(data), resp.getheader("Content-Range").split("/")[0], resp.getheader("Content-Type")),
                         (206, 100, "bytes 0-99", "audio/wav"))
        self.assertEqual(data, slurp(path)[:100])
        conn.request("HEAD", url)
        self.assertEqual(conn.getresponse().status, 200)
        conn.close()

    def test_path_escapes_and_other_files_of_mega_are_404(self):
        mid = self.mega_of("A")
        name = "0123456789abcdef.wav"
        for path in (f"/media/_mega/{mid}/mega.json", "/media/_mega/migrated.json", f"/media/_mega/{mid}/samples/mega.json",
                     f"/media/_mega/{mid}/samples/{name}",                              # well formed, but not there
                     f"/media/_mega/{mid}/samples/../mega.json", f"/media/_mega/{mid}/samples/%2e%2e/mega.json",
                     f"/media/_mega/..%2fA/samples/{name}", f"/media/_mega/../A/audio.wav", f"/media/_mega/.trash/x/samples/{name}",
                     f"/media/_mega/{mid}/samples/{name}/x", f"/media/_mega/{mid}/samples/ABCDEF0123456789.wav", "/media/_mega", "/media/_mega/",
                     f"/media/_mega/{mid}/samples/0123456789abcdef.wav%00.wav"):
            self.assertEqual(self.call(path, raw=True)[0], 404, path)

    def test_bank_pads_refuses_sample_urls_that_escape(self):
        d = os.path.join(self.root, "A")
        secret = os.path.join(self.tmp, "secret.wav")
        shutil.copy(os.path.join(d, "audio.wav"), secret)
        for src in ("/media/_mega/../../secret/samples/0123456789abcdef.wav", "/media/_mega/m/samples/../../../secret.wav",
                    "/media/_mega/m/samples/0123456789abcdef.wav/../../x", "/media/_mega//samples/0123456789abcdef.wav", "/media/_mega/m/other/0123456789abcdef.wav"):
            with self.assertRaises(ValueError, msg=src):
                X.bank_pads(d, 0, {"0:0": {"start": 0, "end": 1, "src": src, "kept": True, "params": {}}}, [])
        with self.assertRaises(ValueError):     # well formed but missing
            X.bank_pads(d, 0, {"0:0": {"start": 0, "end": 1, "src": "/media/_mega/m/samples/0123456789abcdef.wav", "kept": True}}, [])
        # a "kept" flag with a plain resample url is not a kept pad: it must still be a resample url of a song
        with self.assertRaises(ValueError):
            X.bank_pads(d, 0, {"0:0": {"start": 0, "end": 1, "src": "/media/_mega/m/samples/x.wav"}}, [])


# ------------------------------------------------------------------------------------------------------------------------
class Keep(Rig):
    def expected_pcm(self, sid, bank, pad, overrides=None):
        d = os.path.join(self.root, sid)
        p = [p for p in X.bank_pads(d, bank, overrides or {}, []) if p["pad"] == pad][0]
        return X.pad_pcm({**p, "params": {}}, {})

    def test_keep_a_grid_pad_renders_the_slice_without_sound_params_and_fx(self):
        mid = self.mega_of("A")
        self.acts(mid, {"type": "pad.set", "pad": 2, "params": {"gainDb": -6, "pitch": 3, "filter": {"type": "lp", "cutoff": 500}, "attack": 0.2}},
                  {"type": "fx.set", "params": {"reverb": 0.9, "tone": 0.5}}, {"type": "bank.set", "bank": 1})
        code, r = self.call(f"/api/mega/{mid}/keep", {"bank": 1, "pad": 2})
        self.assertEqual((code, r["kept"], r["failed"], r["already"]), (200, [{"bank": 1, "pad": 2}], [], []))
        o = self.get(mid)["project"]["overrides"]["1:2"]
        # gain: the level the chop played at before it was kept (the song's normalising gain; contracts §15)
        self.assertEqual({k for k in o}, {"start", "end", "src", "kept", "params", "from", "gain"})
        self.assertTrue(0 < o["gain"] <= 4)
        self.assertTrue(o["kept"])
        self.assertEqual((o["from"], o["start"]), ({"song": "A", "start": 9.0, "end": 9.5}, 0))     # chop 18 = bank 1, pad 2
        self.assertEqual((o["params"]["gainDb"], o["params"]["pitch"], o["params"]["filter"], o["params"]["attack"]), (-6, 3, {"type": "lp", "cutoff": 500, "q": 0.7}, 0.2))
        path = os.path.join(self.root, "_mega", mid, "samples", o["src"].rsplit("/", 1)[1])
        sr, nch, frames = read_wav(path)
        exp_sr, exp_nch, _, exp = self.expected_pcm("A", 1, 2)
        self.assertEqual((sr, nch, frames), (exp_sr, exp_nch, exp), "the audio of the pad as played: the slice, nothing baked in")
        self.assertAlmostEqual(o["end"], len(frames) / (nch * 2) / sr, places=6)
        self.assertEqual(o["src"], f"/media/_mega/{mid}/samples/{sha(path)[:16]}.wav")
        # the middle of the file IS the song's audio (only the 2.5 ms / 3 ms declick touches the edges)
        _, _, whole = read_wav(os.path.join(self.root, "A", "audio.wav"))
        a, b = round(9.0 * SR) * 4, round(9.5 * SR) * 4
        self.assertEqual(frames[2000:-2000], whole[a:b][2000:-2000])
        # the master fx are not in it, and the pad plays through bank_pads from its own file with its own params
        pads = {p["pad"]: p for p in self.pads_of(mid, 1)}
        self.assertEqual((pads[2]["audio"], pads[2]["params"]["pitch"], pads[2]["pitch"], pads[2]["gain_db"], pads[2]["kept"]), (path, 3, 3.0, -6.0, True))
        self.assertEqual(self.get(mid)["project"]["fx"]["reverb"], 0.9)

    def test_keep_a_stretched_pad_keeps_the_stretched_audio(self):
        d = os.path.join(self.root, "A")
        os.makedirs(os.path.join(d, "stretch"))
        n = int(1.5 * SR)
        st = (0.4 * np.sin(2 * np.pi * 777 * np.arange(n) / SR) * 32767).astype("<i2")
        sp = os.path.join(d, "stretch", "0123456789abcdef.wav")
        with wave.open(sp, "wb") as w:
            w.setnchannels(2), w.setsampwidth(2), w.setframerate(SR)
            w.writeframes(np.repeat(st[:, None], 2, axis=1).tobytes())
        mid = self.mega_of("A")
        self.acts(mid, {"type": "slice.stretch", "bank": 0, "pad": 4, "stretch": {"ratio": 3, "url": "/media/A/stretch/0123456789abcdef.wav"}})
        r = self.call(f"/api/mega/{mid}/keep", {"bank": 0, "pad": 4})[1]
        self.assertEqual((r["kept"], r["failed"]), ([{"bank": 0, "pad": 4}], []))
        o = self.get(mid)["project"]["overrides"]["0:4"]
        self.assertNotIn("stretch", o)
        sr, nch, frames = read_wav(os.path.join(self.root, "_mega", mid, "samples", o["src"].rsplit("/", 1)[1]))
        pad = [p for p in X.bank_pads(d, 0, {"0:4": {"start": 2.0, "end": 2.5, "stretch": {"ratio": 3, "url": "/media/A/stretch/0123456789abcdef.wav"}}}, []) if p["pad"] == 4][0]
        self.assertEqual(frames, X.pad_pcm({**pad, "params": {}}, {})[3])
        self.assertAlmostEqual(o["end"], 1.5, places=2)
        self.assertEqual(o["from"], {"song": "A", "start": 2.0, "end": 2.5})

    def test_params_are_frozen_and_a_second_keep_is_a_no_op(self):
        mid = self.mega_of("A")
        self.acts(mid, {"type": "pad.set", "pad": 2, "params": {"gainDb": -6}})
        self.call(f"/api/mega/{mid}/keep", {"bank": 0, "pad": 2})
        s = self.stream(f"/api/mega/{mid}/events?origin=s", "s")
        time.sleep(0.2)
        self.acts(mid, {"type": "pad.set", "pad": 2, "bank": 1, "params": {"gainDb": 4}})       # the shared pad, another bank
        self.assertEqual(self.get(mid)["project"]["overrides"]["0:2"]["params"]["gainDb"], -6)
        self.assertEqual(self.get(mid)["project"]["pads"][2]["gainDb"], 4)
        self.acts(mid, {"type": "pad.set", "pad": 2, "bank": 0, "params": {"gainDb": -1}})      # the kept pad's own sound
        self.assertEqual((self.get(mid)["project"]["overrides"]["0:2"]["params"]["gainDb"], self.get(mid)["project"]["pads"][2]["gainDb"]), (-1, 4))
        updated = M.read(self.root, mid)["updated"]
        files = os.listdir(os.path.join(self.root, "_mega", mid, "samples"))
        before = slurp(self.mega_file(mid))
        n = len(s.events)
        code, r = self.call(f"/api/mega/{mid}/keep", {"bank": 0, "pad": 2})
        self.assertEqual((code, r["kept"], r["already"], r["failed"]), (200, [], [{"bank": 0, "pad": 2}], []))
        self.assertEqual((slurp(self.mega_file(mid)), os.listdir(os.path.join(self.root, "_mega", mid, "samples"))), (before, files))
        self.assertEqual(M.read(self.root, mid)["updated"], updated)
        time.sleep(0.2)
        self.assertEqual(len(s.events), n, "a no-op broadcasts nothing")

    def test_equal_audio_is_stored_once(self):
        mid = self.mega_of("A")
        self.acts(mid, {"type": "slice.set", "bank": 0, "pad": 0, "start": 1, "end": 1.5}, {"type": "slice.set", "bank": 2, "pad": 7, "start": 1, "end": 1.5})
        for b, p in ((0, 0), (2, 7)):
            self.call(f"/api/mega/{mid}/keep", {"bank": b, "pad": p})
        o = self.get(mid)["project"]["overrides"]
        self.assertEqual(o["0:0"]["src"], o["2:7"]["src"])
        self.assertEqual(len(os.listdir(os.path.join(self.root, "_mega", mid, "samples"))), 1)

    def test_keep_errors(self):
        mid = self.mega_of("A")
        for body in ({}, {"bank": -1, "pad": 0}, {"bank": 0, "pad": 16}, {"bank": "0", "pad": 0}, {"bank": True, "pad": 0}):
            self.assertEqual(self.call(f"/api/mega/{mid}/keep", body)[0], 400, body)
        r = self.call(f"/api/mega/{mid}/keep", {"bank": 9, "pad": 0})[1]       # no such pad: reported, nothing changes
        self.assertEqual((r["kept"], r["failed"]), ([], [{"bank": 9, "pad": 0, "reason": "the pad is empty"}]))
        self.assertEqual(self.get(mid)["project"]["overrides"], {})

    def test_keep_reports_a_pad_whose_song_is_gone_and_leaves_it_unchanged(self):
        mid = self.mega_of("A")
        self.acts(mid, {"type": "slice.set", "bank": 0, "pad": 3, "start": 1, "end": 2})
        shutil.rmtree(os.path.join(self.root, "A"))
        r = self.call(f"/api/mega/{mid}/keep", {"bank": 0, "pad": 3})[1]
        self.assertEqual(r["kept"], [])
        self.assertEqual((r["failed"][0]["bank"], r["failed"][0]["pad"]), (0, 3))
        self.assertIn("gone", r["failed"][0]["reason"])
        self.assertEqual(self.get(mid)["project"]["overrides"]["0:3"], {"start": 1, "end": 2})


# ------------------------------------------------------------------------------------------------------------------------
class SourceSwitch(Rig):
    def switch(self, mid, song="B", origin="t"):
        code, r = self.call(f"/api/mega/{mid}/source", {"song": song}, headers={"X-ChopPad-Origin": origin})
        self.assertEqual(code, 200, r)
        return r

    def test_used_pads_are_kept_unused_released_pattern_untouched(self):
        mid = self.mega_of("A")
        self.acts(mid, *self.hits(0, 1, 2, 3), {"type": "slice.set", "bank": 1, "pad": 9, "start": 12, "end": 12.5},
                  {"type": "pad.set", "pad": 1, "params": {"gainDb": -7}})
        self.acts(mid, {"type": "pattern.addEvent", "event": {"beat": 3, "pad": 6, "bank": 2, "vel": 0.5, "pass": 2}})
        before = self.get(mid)["project"]
        r = self.switch(mid)
        self.assertEqual(sorted((k["bank"], k["pad"]) for k in r["kept"]), [(0, 0), (0, 1), (0, 2), (0, 3), (1, 9), (2, 6)])
        self.assertEqual(r["failed"], [])
        self.assertEqual((r["mega"]["source"], r["mega"]["id"]), ("B", mid))
        after = self.get(mid)
        self.assertEqual(after["project"]["pattern"], before["pattern"])
        self.assertEqual(sorted(after["project"]["overrides"]), ["0:0", "0:1", "0:2", "0:3", "1:9", "2:6"])
        for k, o in after["project"]["overrides"].items():
            self.assertTrue(o["kept"] and o["src"].startswith(f"/media/_mega/{mid}/samples/"), k)
        self.assertEqual(after["project"]["overrides"]["0:1"]["params"]["gainDb"], -7)
        self.assertEqual(after["project"]["overrides"]["1:9"]["from"], {"song": "A", "start": 12, "end": 12.5})
        self.assertEqual(after["chops"]["bpm"], 90.0)
        # released: every cell of A's chop table that is not kept now shows B's chop (40 chops; 6 kept)
        self.assertEqual(r["released"], 40 - 6)
        self.assertEqual(self.call("/api/songs/B")[1]["mega"], mid)
        self.assertEqual(self.call("/api/songs/A")[1]["mega"] != mid, True)    # A now has (is given) a mega bank of its own

    def test_scenario_break_then_loop_and_bass_keeps_the_drums(self):
        """Owner (2026-11-22): record a break on pads 1-4, load a new song, play the loop and bass over the kept drums."""
        mid = self.mega_of("A")
        self.acts(mid, {"type": "pattern.setBars", "bars": 2}, *self.hits(0, 1, 2, 3), *self.hits(0, 2, start=2),
                  {"type": "pad.set", "pad": 0, "params": {"gainDb": -3}})
        a_pads = {p["pad"]: p for p in X.bank_pads(os.path.join(self.root, "A"), 0, {}, [], chops=M.chops_of(self.root, M.read(self.root, mid)))}
        a_pcm = {i: X.pad_pcm({**a_pads[i], "params": {}}, {}) for i in range(4)}
        tempo_before = self.get(mid)["project"].get("tempo")
        self.assertIsNone(tempo_before)
        r = self.switch(mid)
        self.assertEqual(sorted(k["pad"] for k in r["kept"]), [0, 1, 2, 3])
        pads = {p["pad"]: p for p in self.pads_of(mid, 0)}
        b_chops = M.chops_of(self.root, M.read(self.root, mid))
        self.assertEqual(sorted(pads), list(range(16)))
        b_audio = os.path.join(self.root, "B", "audio.wav")
        for i in range(4):          # the pattern's four pads still sound like song A's chops
            self.assertTrue(pads[i]["kept"])
            self.assertIn(f"{os.sep}_mega{os.sep}{mid}{os.sep}samples{os.sep}", pads[i]["audio"])
            sr, nch, frames = read_wav(pads[i]["audio"])
            self.assertEqual((sr, nch, frames), (a_pcm[i][0], a_pcm[i][1], a_pcm[i][3]), f"pad {i + 1}")
        self.assertEqual(pads[0]["gain_db"], -3.0)
        for i in range(4, 16):      # the rest are song B's chops
            ch = b_chops["chops"][b_chops["banks"][0][i]]
            self.assertEqual((pads[i]["audio"], pads[i]["start"], pads[i]["end"], pads[i]["kept"]), (b_audio, ch["start"], ch["end"], False), f"pad {i + 1}")
        # the offline-renderable list: every pad of bank 0 has a file and a slice, and the kept ones sound the same as before
        for p in pads.values():
            self.assertTrue(os.path.isfile(p["audio"]))
        self.assertEqual(X.pad_pcm({**pads[1], "params": {}}, {})[0:2], a_pcm[1][0:2])
        # the tempo is the user's: the old song's 120 was written down, B's 90 did not take over
        proj = self.get(mid)["project"]
        self.assertEqual(proj["tempo"], {"bpm": 120.0, "follow": False})
        self.assertEqual(A.project_bpm(A.initial_state(proj, b_chops)), 120.0)
        import daw_project
        out = os.path.join(self.tmp, "daw")
        dp = daw_project.build_project(os.path.join(self.root, "B"), out, bank=0)
        self.assertEqual((dp.bpm, [c is not None for c in dp.chops].count(True)), (120.0, 16))
        self.assertTrue(os.path.isfile(dp.chops[0].path))
        self.assertEqual(dp.pattern["events"], proj["pattern"]["events"])
        # option (a) instead: a new mega bank for B is empty, the first one stays as it is
        before = slurp(self.mega_file(mid))
        new = self.call("/api/mega", {"source": "B"})[1]
        self.assertEqual((new["project"]["overrides"], new["project"]["pattern"] if "pattern" in new["project"] else None), ({}, None))
        self.assertEqual(slurp(self.mega_file(mid)), before)

    def test_tempo_rule(self):
        mid = self.mega_of("A")
        self.acts(mid, *self.hits(0))
        self.switch(mid)
        self.assertEqual(self.get(mid)["project"]["tempo"], {"bpm": 120.0, "follow": False})
        # a tempo the user set stays, and so does follow
        mid2 = self.call("/api/mega", {"source": "A"})[1]["mega"]["id"]
        self.acts(mid2, {"type": "tempo.set", "bpm": 100, "follow": True}, *self.hits(0))
        self.switch(mid2)
        self.assertEqual(self.get(mid2)["project"]["tempo"], {"bpm": 100, "follow": True})
        # switching again does not touch it
        self.switch(mid2, "A")
        self.assertEqual(self.get(mid2)["project"]["tempo"], {"bpm": 100, "follow": True})
        # no source, no tempo to freeze
        mid3 = self.call("/api/mega", {})[1]["mega"]["id"]
        self.switch(mid3, "B")
        self.assertNotIn("tempo", self.get(mid3)["project"])

    def test_bank_clamp_and_banks_a_kept_pad_needs(self):
        mid = self.mega_of("A")
        self.acts(mid, {"type": "bank.set", "bank": 2}, *self.hits(0, bank=0))
        self.assertEqual(self.get(mid)["view"]["bank"], 2)
        r = self.switch(mid)                    # kept pad on bank 0 only, B has one bank: the open bank 2 no longer exists
        self.assertEqual(r["view"]["bank"], 0)
        self.assertEqual(self.get(mid)["view"]["bank"], 0)
        self.assertEqual(self.get(mid)["mega"]["banks"], 1)
        mid2 = self.call("/api/mega", {"source": "A"})[1]["mega"]["id"]
        self.acts(mid2, {"type": "bank.set", "bank": 2}, *self.hits(0, bank=2))
        r = self.switch(mid2)                   # a kept pad on bank 2: bank 2 stays, and it still shows the kept pad
        self.assertEqual((r["view"]["bank"], r["mega"]["banks"]), (2, 3))
        pads = self.pads_of(mid2, 2)
        self.assertEqual([(p["pad"], p["kept"]) for p in pads], [(0, True)])      # the rest of bank 2 is empty for B
        r = self.acts(mid2, {"type": "bank.set", "bank": 9})
        self.assertEqual(r["view"]["bank"], 2)

    def test_events_on_both_streams_in_one_batch_and_idempotence(self):
        mid = self.mega_of("A")
        self.acts(mid, *self.hits(0, 1))
        song_stream = self.stream("/api/songs/A/events?origin=s", "s")
        mega_stream = self.stream(f"/api/mega/{mid}/events?origin=m", "m")
        time.sleep(0.2)
        r = self.switch(mid, origin="x")
        self.assertEqual([a["type"] for a in r["actions"]], ["tempo.set", "slice.keep", "slice.keep", "source.set"])   # the origin tab does not get its own batch by stream
        for s in (song_stream, mega_stream):
            ev = s.wait(1)
            self.assertEqual([e[0] for e in ev], ["actions"])
            self.assertEqual([a["type"] for a in ev[0][1]["actions"]], ["tempo.set", "slice.keep", "slice.keep", "source.set"])
            self.assertEqual(ev[0][1]["origin"], "x")
            ks = ev[0][1]["actions"][1]
            self.assertTrue(ks["src"].startswith(f"/media/_mega/{mid}/samples/") and ks["params"]["gainDb"] == 0 and ks["from"]["song"] == "A")
            self.assertEqual(ev[0][1]["actions"][3]["chops"]["bpm"], 90.0)
        # the actions replay on a browser's reducer to the saved state
        st = A.initial_state(M.read(self.root, mid)["project"], None)
        base = A.initial_state({"pads": [{} for _ in range(16)], "overrides": {}, "pattern": self.get(mid)["project"]["pattern"]}, M.chops_of(self.root, {"source": "A"}))
        for a in song_stream.of("actions")[0]["actions"]:
            base = A.reduce(base, a)
        self.assertEqual(base["project"]["overrides"], st["project"]["overrides"])
        self.assertEqual((base["source"], base["project"]["tempo"]), ("B", {"bpm": 120.0, "follow": False}))
        # idempotent: the same switch again does nothing
        saved, n = slurp(self.mega_file(mid)), len(mega_stream.events)
        r2 = self.switch(mid, origin="x")
        self.assertEqual((r2["kept"], r2["released"], r2["failed"], r2["actions"]), ([], 0, [], []))
        time.sleep(0.2)
        self.assertEqual((slurp(self.mega_file(mid)), len(mega_stream.events)), (saved, n))
        self.assertEqual(len(r["kept"]), 2)
        # and the state a tab gets from the server is the same as what the events said
        self.assertEqual(self.get(mid)["project"]["overrides"], st["project"]["overrides"])

    def test_unknown_song_is_404_and_changes_nothing(self):
        mid = self.mega_of("A")
        self.acts(mid, *self.hits(0, 1))
        before, files = slurp(self.mega_file(mid)), sorted(os.listdir(os.path.join(self.root, "_mega", mid, "samples")))
        for body in ({"song": "nope"}, {"song": "../A"}, {"song": ""}, {"song": 5}, {}):   # {"song": null} clears the source since step D (test_mega_d)
            self.assertEqual(self.call(f"/api/mega/{mid}/source", body)[0], 404, body)
        self.assertEqual((slurp(self.mega_file(mid)), sorted(os.listdir(os.path.join(self.root, "_mega", mid, "samples")))), (before, files))
        self.assertEqual(self.call("/api/mega/nope/source", {"song": "B"})[0], 404)

    def test_pads_that_cannot_be_kept_are_reported_and_left_unchanged_but_the_switch_happens(self):
        mid = self.mega_of("A")
        self.acts(mid, *self.hits(0, 1),
                  {"type": "kit.load", "bank": 0, "song": "A", "pads": [None, None, {"song_id": "ghost", "start": 0, "end": 1}]})   # a pad from a song that is not there
        self.acts(mid, *self.hits(2, start=4))
        r = self.switch(mid)
        self.assertEqual(sorted(k["pad"] for k in r["kept"]), [0, 1])
        self.assertEqual([(f["bank"], f["pad"]) for f in r["failed"]], [(0, 2)])
        self.assertIn("ghost", r["failed"][0]["reason"])
        proj = self.get(mid)["project"]
        self.assertEqual(proj["overrides"]["0:2"], {"start": 0, "end": 1, "song": "ghost"})
        self.assertEqual(self.get(mid)["mega"]["source"], "B")

    def test_song_gone_fails_every_pad_and_the_switch_still_happens(self):
        mid = self.mega_of("A")
        self.acts(mid, *self.hits(0, 1))
        shutil.rmtree(os.path.join(self.root, "A"))
        r = self.switch(mid)
        self.assertEqual((r["kept"], len(r["failed"])), ([], 2))
        self.assertTrue(all("gone" in f["reason"] for f in r["failed"]))
        self.assertEqual(self.get(mid)["mega"]["source"], "B")
        self.assertNotIn("tempo", self.get(mid)["project"])          # A's tempo is not known any more

    def test_empty_pads_hit_by_the_pattern_are_not_failures(self):
        mid = self.mega_of("A")
        self.acts(mid, *self.hits(0), *self.hits(3, bank=7))          # bank 7 does not exist: nothing to keep
        r = self.switch(mid)
        self.assertEqual(([k["pad"] for k in r["kept"]], r["failed"]), ([0], []))
        self.assertEqual(sorted(self.get(mid)["project"]["overrides"]), ["0:0"])

    def test_switch_back_and_forth_keeps_rendered_pads_and_their_audio(self):
        mid = self.mega_of("A")
        self.acts(mid, *self.hits(0, 1))
        self.switch(mid)
        o1 = self.get(mid)["project"]["overrides"]
        self.acts(mid, {"type": "slice.set", "bank": 0, "pad": 5, "start": 0, "end": 0.5}, *self.hits(5, start=4))     # a pad of song B
        r = self.switch(mid, "A")
        self.assertEqual([k["pad"] for k in r["kept"]], [5])                  # only the new one: 0 and 1 are already kept
        o2 = self.get(mid)["project"]["overrides"]
        self.assertEqual((o2["0:0"], o2["0:1"]), (o1["0:0"], o1["0:1"]))
        self.assertEqual(o2["0:5"]["from"]["song"], "B")
        self.assertEqual(len(os.listdir(os.path.join(self.root, "_mega", mid, "samples"))), 3)

    def test_two_switches_racing_leave_one_consistent_source(self):
        mid = self.mega_of("A")
        self.acts(mid, *self.hits(0, 1))
        out = []
        ts = [threading.Thread(target=lambda s=s: out.append(self.call(f"/api/mega/{mid}/source", {"song": s})[0])) for s in ("B", "A", "B")]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(out, [200, 200, 200])
        doc = M.read(self.root, mid)
        self.assertIn(doc["source"], ("A", "B"))
        self.assertEqual(sorted(doc["project"]["overrides"]), ["0:0", "0:1"])


# ------------------------------------------------------------------------------------------------------------------------
class ExportThroughSongRoute(Rig):
    """The song-keyed export (choppad.make_server) with no overrides in the body reads the song's mega bank: after a switch the
    kept pads export as the audio they were."""
    @classmethod
    def setUpClass(cls):
        super().setUpClass()

    def start(self, maker=None):
        from choppad import make_server
        super().start(lambda r: make_server(r, port=0))

    def test_zip_export_of_kept_and_released_pads(self):
        mid = self.mega_of("A")
        self.acts(mid, *self.hits(0, 1), {"type": "pad.set", "pad": 1, "params": {"gainDb": -4}})
        a_pads = {p["pad"]: p for p in X.bank_pads(os.path.join(self.root, "A"), 0, {}, [])}
        want = {i: X.pad_pcm({**a_pads[i], "params": {}}, {})[3] for i in (0, 1)}
        self.assertEqual(self.call(f"/api/mega/{mid}/source", {"song": "B"})[0], 200)
        code, r = self.call("/api/songs/B/export", {"kind": "zip", "bank": 0})
        self.assertEqual(code, 200, r)
        with zipfile.ZipFile(r["path"]) as z:
            names = sorted(z.namelist())
            self.assertEqual(len(names), 16)
            for i in (0, 1):
                with wave.open(io.BytesIO(z.read(f"pad{i + 1:02d}.wav"))) as w:
                    self.assertEqual(w.readframes(w.getnframes()), want[i])
            with wave.open(io.BytesIO(z.read("pad05.wav"))) as w:
                self.assertNotEqual(w.readframes(w.getnframes()), want[0])
        # a kept pad has no slice of the song to stretch (with or without start/end in the request)
        for extra in ({}, {"start": 0, "end": 0.5}):
            code, r = self.call("/api/songs/B/stretch", {"bank": 0, "pad": 0, "ratio": 0.5, **extra})
            self.assertEqual(code, 400, r)
            self.assertIn("kept", r["error"])
        # saving a kit from a mega bank that has kept pads is refused clearly (kits of kept pads are step D)
        code, r = self.call("/api/songs/B/savekit", {"bank": 0, "name": "k"})
        self.assertEqual(code, 400)
        self.assertIn("kept", r["error"])


if __name__ == "__main__":
    unittest.main()
