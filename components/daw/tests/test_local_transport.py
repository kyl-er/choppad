"""CHOP-11 task 3, server side: a tab's own pad hits and pattern play/stop (`POST .../transport` with `local: true`) reach an
open DAW bridge, are never rebroadcast to other tabs, are rate limited, and the `daw` state is on the song and its stream.
All on a fake bridge; no DAW, no ALSA."""
import http.client, json, os, shutil, sys, threading, time, unittest, urllib.error, urllib.request
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import support as ctl  # noqa: E402
from test_manager_server import FakeBridge, manager  # noqa: E402
import server as srvmod  # noqa: E402


class Stream:
    """Minimal SSE client: collects (event, data) on a thread."""
    def __init__(self, port, path):
        self.conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        self.conn.request("GET", path)
        self.resp = self.conn.getresponse()
        self.events = []
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        ev, data = None, []
        try:
            for raw in self.resp:
                line = raw.decode().rstrip("\n")
                if line.startswith("event:"):
                    ev = line[6:].strip()
                elif line.startswith("data:"):
                    data.append(line[5:].strip())
                elif line == "" and ev:
                    self.events.append((ev, json.loads("".join(data))))
                    ev, data = None, []
        except (OSError, ValueError):
            pass

    def wait_for(self, name, timeout=2.0, after=0):
        end = time.time() + timeout
        while time.time() < end:
            hits = [e for e in self.events[after:] if e[0] == name]
            if hits:
                return hits
            time.sleep(0.01)
        return []

    def names(self):
        return [e[0] for e in self.events]

    def close(self):
        self.conn.close()


class LocalTransport(unittest.TestCase):
    def setUp(self):
        self.root = ctl.make_song_root()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.h = FakeBridge()
        self.m, self.port, self.built = manager(hydrogen=self.h)

        def daw_action(media_dir, body):
            return self.m.command(os.path.basename(media_dir), media_dir, body)
        self.srv, p = ctl.start_server(self.root, actions={"daw": daw_action}, on_transport=self.m.on_transport,
                                       daw_state=self.m.state_for)
        self.m.on_change = self.srv.announce_daw
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)
        self.addCleanup(self.m.close)
        self.p = p
        self.base = f"http://127.0.0.1:{p}"
        self.streams = []

    def stream(self, origin="tab"):
        s = Stream(self.p, f"/api/songs/song1/events?origin={origin}")
        self.streams.append(s)
        self.addCleanup(s.close)
        return s

    def post(self, path, body, origin="tab1"):
        req = urllib.request.Request(self.base + path, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json", "X-ChopPad-Origin": origin})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def get(self, path):
        with urllib.request.urlopen(self.base + path) as r:
            return json.loads(r.read())

    def open_bridge(self, **extra):
        code, r = self.post("/api/songs/song1/daw", {"daw": "hydrogen", "command": "open", **extra})
        self.assertEqual((code, r["ok"]), (200, True))
        self.h.calls.clear()

    def local(self, body, origin="tab1"):
        return self.post("/api/songs/song1/transport", {**body, "local": True}, origin)

    # ---- state ----
    def test_daw_state_on_the_song_and_on_the_stream_and_on_open_and_close(self):
        self.assertEqual(self.get("/api/songs/song1")["daw"], {"open": False, "daw": None, "bank": None})
        s = self.stream()
        self.assertEqual(s.wait_for("daw")[0][1], {"open": False, "daw": None, "bank": None})   # first message of every stream
        self.open_bridge()
        got = s.wait_for("daw", after=1)
        self.assertEqual(got[0][1], {"open": True, "daw": "hydrogen", "bank": 0})
        self.assertEqual(self.get("/api/songs/song1")["daw"], {"open": True, "daw": "hydrogen", "bank": 0})
        late = self.stream("late")
        self.assertEqual(late.wait_for("daw")[0][1]["open"], True)    # a tab that connects later learns it at once
        self.m.close()
        self.assertEqual(s.wait_for("daw", after=2)[0][1], {"open": False, "daw": None, "bank": None})

    def test_sync_to_another_bank_updates_the_state(self):
        s = self.stream()
        self.open_bridge()
        self.post("/api/songs/song1/daw", {"daw": "hydrogen", "command": "sync", "bank": 2})
        banks = [d["bank"] for n, d in s.events if n == "daw"]
        self.assertEqual(banks[-1], 2)

    # ---- delivery ----
    def test_local_hit_reaches_the_bridge_and_is_not_rebroadcast(self):
        a, b = self.stream("tab1"), self.stream("tab2")
        a.wait_for("daw"), b.wait_for("daw")
        self.open_bridge()
        code, r = self.local({"command": "hit", "pad": 3, "velocity": 0.5, "bank": 0})
        self.assertEqual((code, r["delivered"]), (200, True))
        self.assertEqual(self.h.calls, [("note", 3, 0.5)])
        self.assertEqual(self.port.backend.msgs[:1], [bytes([0x90, 39, 64])])      # and the MIDI port
        time.sleep(0.3)
        self.assertNotIn("transport", a.names() + b.names())   # no other tab was told to play it

    def test_agent_driven_hit_is_still_broadcast_and_mirrored(self):
        b = self.stream("tab2")
        b.wait_for("daw")
        self.open_bridge()
        code, r = self.post("/api/songs/song1/transport", {"command": "hit", "pad": 2, "velocity": 1})
        self.assertEqual(code, 200)
        self.assertEqual(b.wait_for("transport")[0][1]["pad"], 2)
        self.assertEqual(self.h.calls, [("note", 2, 1.0)])

    def test_local_play_and_stop_drive_the_bridge_transport(self):
        self.open_bridge()
        self.assertEqual(self.local({"command": "play-pattern"})[1]["delivered"], True)
        self.assertEqual(self.local({"command": "stop"})[1]["delivered"], True)
        self.assertEqual([c[0] for c in self.h.calls], ["play", "stop"])

    def test_other_bank_other_song_and_closed_bridge_deliver_nothing(self):
        self.assertEqual(self.local({"command": "hit", "pad": 1})[1], {"ok": True, "delivered": False, "reason": "no bridge open"})
        self.assertEqual(self.h.calls, [])
        self.open_bridge()
        self.local({"command": "hit", "pad": 1, "bank": 1})            # the kit holds bank 0
        self.assertEqual(self.h.calls, [])

    def test_bad_input_is_rejected_like_any_transport(self):
        self.open_bridge()
        self.assertEqual(self.local({"command": "hit", "pad": 99})[0], 400)
        self.assertEqual(self.local({"command": "hit", "pad": 1, "velocity": 2})[0], 400)
        self.assertEqual(self.local({"command": "dance"})[0], 400)
        self.assertEqual(self.h.calls, [])

    # ---- rate limit and latency ----
    def test_rate_limited_per_song(self):
        self.open_bridge()
        old = srvmod.LOCAL_RATE
        srvmod.LOCAL_RATE = (1, 3)
        self.addCleanup(setattr, srvmod, "LOCAL_RATE", old)
        codes = [self.local({"command": "hit", "pad": 0})[0] for _ in range(10)]
        self.assertEqual(codes.count(200), 3)
        self.assertEqual(codes.count(429), 7)
        self.assertEqual(len(self.h.calls), 3)

    def test_a_slow_open_never_blocks_a_local_hit(self):
        self.open_bridge()
        started, release = threading.Event(), threading.Event()

        def hold():
            with self.m._lock:      # what a launch that takes seconds does
                started.set()
                release.wait(5)
        t = threading.Thread(target=hold)
        t.start()
        started.wait(2)
        t0 = time.monotonic()
        code, r = self.local({"command": "hit", "pad": 0})
        took = time.monotonic() - t0
        release.set()
        t.join()
        self.assertEqual(code, 200)
        self.assertLess(took, 0.2)            # answered at once; the hit was dropped instead of queued behind the open
        self.assertEqual(self.h.calls, [])


if __name__ == "__main__":
    unittest.main()
