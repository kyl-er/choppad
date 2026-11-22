"""Actions endpoint, server-sent events, origin suppression, transport and shutdown (contracts §10).
Runs the real server (make_server(port=0)) in a thread on a synthetic song folder; stdlib only."""
import http.client, json, os, shutil, sys, threading, time, unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from support import CHOPS, jread, jwrite, make_song_root, srvmod, start_server  # noqa: E402


class Stream:
    """A minimal SSE client: reads events on a thread into a list."""
    def __init__(self, port, path, headers=None):
        self.conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        self.conn.request("GET", path, headers=headers or {})
        self.resp = self.conn.getresponse()
        self.events, self.ended = [], threading.Event()
        self.cv = threading.Condition()
        self.t = threading.Thread(target=self._read, daemon=True)
        self.t.start()

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
        finally:
            self.ended.set()
            with self.cv:
                self.cv.notify_all()

    def wait(self, n, timeout=3.0):
        end = time.time() + timeout
        with self.cv:
            while len(self.events) < n and time.time() < end:
                self.cv.wait(0.05)
        return list(self.events)

    def close(self):
        try:
            self.conn.close()
        except OSError:
            pass


class ControlServer(unittest.TestCase):
    def setUp(self):
        self.root = make_song_root()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.regrids = []

        def regrid(d, **kw):
            self.regrids.append(kw)
            doc = {**CHOPS, "grid": kw["grid"]}
            jwrite(os.path.join(d, "chops.json"), doc)
            return doc
        self.srv, self.port = start_server(self.root, regrid=regrid)
        self.streams = []
        self.addCleanup(self.stop)

    def stop(self):
        for s in self.streams:
            s.close()
        t = time.time()
        self.srv.shutdown()
        self.srv.server_close()
        self.assertLess(time.time() - t, 3, "shutdown hung")

    def stream(self, path="/api/songs/song1/events", **kw):
        s = Stream(self.port, path, **kw)
        self.streams.append(s)
        return s

    def req(self, method, path, body=None, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request(method, path, body=json.dumps(body) if body is not None else None,
                  headers={"Content-Type": "application/json", **(headers or {})})
        r = c.getresponse()
        out = json.loads(r.read() or b"{}")
        c.close()
        return r.status, out

    def post_actions(self, actions, origin=None):
        return self.req("POST", "/api/songs/song1/actions", {"actions": actions}, {"X-ChopPad-Origin": origin} if origin else None)

    def project_file(self):
        return jread(os.path.join(self.root, "song1", "project.json"))

    def test_binds_loopback_only(self):
        self.assertEqual(self.srv.server_address[0], "127.0.0.1")

    def test_actions_apply_and_persist(self):
        code, out = self.post_actions([
            {"type": "pad.set", "pad": 0, "params": {"pitch": 5, "gainDb": -3}},
            {"type": "pattern.toggleStep", "pad": 2, "step": 4, "bank": 0},
            {"type": "fx.set", "params": {"reverb": 0.2}},
            {"type": "pad.select", "pad": 3},
            {"type": "fx.set", "params": {"reverb": 0.2}},  # no change
            {"type": "nope"},
        ])
        self.assertEqual(code, 200)
        self.assertEqual((out["applied"], out["ignored"]), (4, 2))
        self.assertEqual(out["project"]["pads"][0]["pitch"], 5)
        self.assertEqual(self.project_file(), out["project"])  # what is on disk is what came back
        self.assertEqual([e["pad"] for e in out["project"]["pattern"]["events"]], [2])
        self.assertEqual(out["view"]["selected"], 3)
        leftovers = [f for f in os.listdir(os.path.join(self.root, "song1")) if f.startswith(".tmp-")]
        self.assertEqual(leftovers, [])  # temp file renamed away

    def test_view_actions_are_forwarded_but_not_saved(self):
        s = self.stream()
        s.wait(0, 0.1)
        time.sleep(0.1)
        code, out = self.post_actions([{"type": "bank.set", "bank": 1}, {"type": "settings.set", "params": {"swing": 0.3}}])
        self.assertEqual(out["applied"], 2)
        self.assertFalse(os.path.exists(os.path.join(self.root, "song1", "project.json")))
        self.assertEqual([a["type"] for a in s.wait(1)[0][1]["actions"]], ["bank.set", "settings.set"])
        # the server remembers the bank, so a step without one lands on it
        _, out = self.post_actions([{"type": "pattern.toggleStep", "pad": 1, "step": 0}])
        self.assertEqual(out["project"]["pattern"]["events"][0]["bank"], 1)

    def test_bad_requests(self):
        self.assertEqual(self.req("POST", "/api/songs/song1/actions", {"actions": "x"})[0], 400)
        self.assertEqual(self.req("POST", "/api/songs/song1/actions", {"nope": 1})[0], 400)
        self.assertEqual(self.req("POST", "/api/songs/ghost/actions", {"actions": []})[0], 404)
        self.assertEqual(self.req("GET", "/api/songs/ghost/events")[0], 404)
        code, out = self.post_actions([{"type": "chops.replace", "chops": CHOPS}])  # chops go through /regrid
        self.assertEqual(out["applied"], 0)

    def test_fan_out_and_origin_suppression(self):
        a = self.stream("/api/songs/song1/events?origin=tabA")
        b = self.stream("/api/songs/song1/events?origin=tabB")
        anon = self.stream()
        time.sleep(0.2)
        self.assertEqual(self.srv.hub.listeners("song1"), 3)
        self.post_actions([{"type": "pad.set", "pad": 1, "params": {"pitch": 2}}], origin="tabA")
        got_b, got_anon = b.wait(1), anon.wait(1)
        self.assertEqual(got_b[0][0], "actions")
        self.assertEqual(got_b[0][1]["origin"], "tabA")  # clients that filter themselves can see who wrote it
        self.assertEqual(got_b[0][1]["actions"], [{"type": "pad.set", "pad": 1, "params": {"pitch": 2}}])
        self.assertEqual(len(got_anon), 1)
        self.post_actions([{"type": "pad.set", "pad": 1, "params": {"pitch": 3}}], origin="tabB")
        self.assertEqual(len(a.wait(1)), 1)  # tab A heard B write but never its own
        self.assertEqual([e[1]["origin"] for e in a.events], ["tabB"])
        time.sleep(0.2)
        self.assertEqual(len(b.events), 1)  # and B did not hear itself

    def test_events_after_other_song_are_separate(self):
        os.makedirs(os.path.join(self.root, "song2"))
        for f in ("meta.json", "chops.json"):
            shutil.copy(os.path.join(self.root, "song1", f), os.path.join(self.root, "song2", f))
        other = self.stream("/api/songs/song2/events")
        time.sleep(0.2)
        self.post_actions([{"type": "pad.set", "pad": 0, "params": {"pitch": 1}}])
        time.sleep(0.3)
        self.assertEqual(other.events, [])

    def test_put_project_still_works_and_broadcasts(self):
        s = self.stream()
        time.sleep(0.2)
        doc = {"pads": [{"pitch": 7}] + [{} for _ in range(15)], "overrides": {"0:1": {"start": 1, "end": 2}},
               "fx": {"reverb": 0.4}, "pattern": {"bars": 1, "events": [{"beat": 0, "pad": 0, "bank": 0, "vel": 1, "pass": -1}]}}
        code, out = self.req("PUT", "/api/songs/song1/project", doc, {"X-ChopPad-Origin": "tabZ"})
        self.assertEqual((code, out), (200, {"ok": True}))
        self.assertEqual(self.project_file()["pads"][0]["pitch"], 7)
        ev = s.wait(1)[0]
        self.assertEqual(ev[0], "actions")
        self.assertEqual(ev[1]["origin"], "tabZ")
        types = {a["type"] for a in ev[1]["actions"]}
        self.assertEqual(types, {"pad.set", "slice.setMany", "fx.set", "pattern.load"})
        self.assertEqual(self.req("PUT", "/api/songs/song1/project", {"pads": 1})[0], 400)

    def test_transport_event(self):
        s = self.stream()
        time.sleep(0.2)
        code, out = self.req("POST", "/api/songs/song1/transport", {"command": "hit", "pad": 3, "velocity": 0.5})
        self.assertEqual((code, out["listeners"]), (200, 1))
        self.req("POST", "/api/songs/song1/transport", {"command": "play-pattern"})
        ev = s.wait(2)
        self.assertEqual(ev[0], ("transport", {"origin": "", "command": "hit", "pad": 3, "velocity": 0.5}))
        self.assertEqual(ev[1][1]["command"], "play-pattern")
        self.assertEqual(self.req("POST", "/api/songs/song1/transport", {"command": "explode"})[0], 400)
        self.assertEqual(self.req("POST", "/api/songs/song1/transport", {"command": "hit", "pad": 99})[0], 400)

    def test_regrid_broadcasts_chops(self):
        s = self.stream()
        time.sleep(0.2)
        code, _ = self.req("POST", "/api/songs/song1/regrid", {"grid": "1/8"}, {"X-ChopPad-Origin": "t1"})
        self.assertEqual(code, 200)
        ev = s.wait(1)[0]
        self.assertEqual((ev[0], ev[1]["origin"], ev[1]["chops"]["grid"]), ("chops", "t1", "1/8"))

    def test_dead_clients_are_dropped(self):
        import socket
        raw = socket.create_connection(("127.0.0.1", self.port), timeout=3)
        raw.sendall(b"GET /api/songs/song1/events HTTP/1.1\r\nHost: x\r\n\r\n")
        raw.recv(4096)  # headers: the stream is open
        live = self.stream()
        time.sleep(0.2)
        self.assertEqual(self.srv.hub.listeners("song1"), 2)
        raw.close()  # the tab went away without a word
        end = time.time() + 3
        while self.srv.hub.listeners("song1") > 1 and time.time() < end:
            self.req("POST", "/api/songs/song1/transport", {"command": "stop"})
            time.sleep(0.05)
        self.assertEqual(self.srv.hub.listeners("song1"), 1)
        self.assertGreaterEqual(len(live.wait(1)), 1)

    def test_shutdown_ends_open_streams(self):
        streams = [self.stream() for _ in range(3)]
        time.sleep(0.2)
        t = time.time()
        self.srv.shutdown()
        self.assertLess(time.time() - t, 2)
        for s in streams:
            self.assertTrue(s.ended.wait(2), "event stream stayed open after shutdown")
        self.srv.shutdown = lambda: None  # cleanup calls it again; already stopped

    def test_atomic_write_leaves_old_file_on_failure(self):
        path = os.path.join(self.root, "song1", "project.json")
        srvmod.write_json_atomic(path, {"ok": 1})
        with self.assertRaises(TypeError):
            srvmod.write_json_atomic(path, {"bad": object()})
        self.assertEqual(jread(path), {"ok": 1})
        self.assertEqual([f for f in os.listdir(os.path.dirname(path)) if f.startswith(".tmp-")], [])


if __name__ == "__main__":
    unittest.main()
