"""DawManager, the /daw endpoint, transport mirroring, the CLI verbs and the MCP tools, all on fakes (no DAW, no ALSA)."""
import io, json, os, shutil, sys, threading, time, unittest, urllib.error, urllib.request
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import support as ctl  # noqa: E402  (synthetic song root + a real server on a free port)
from support import FakeBackend, fake_project  # noqa: E402
import manager as M  # noqa: E402
import midi_port  # noqa: E402
import choppad_client as cc  # noqa: E402
import choppad_mcp as mcp  # noqa: E402
import choppad  # noqa: E402


class FakeBridge:
    def __init__(self, name="hydrogen", installed=True, transport_error=None, launch_error=None):
        self.name, self.installed, self.calls = name, installed, []
        self.transport_error, self.launch_error = transport_error, launch_error

    def available(self):
        return self.installed

    def launch(self, project):
        self.calls.append(("launch", project.song_id))
        if self.launch_error:
            raise self.launch_error
        return {"song": "/x/s.h2song", "kit": project.kit_name}

    def push(self, project):
        self.calls.append(("push", project.song_id))
        return {"song": "/x/s.h2song", "pushed": True}

    def transport(self, command, *, bpm=None):
        self.calls.append((command, bpm))
        if self.transport_error:
            raise self.transport_error

    def note(self, pad, vel):
        self.calls.append(("note", pad, vel))

    def close(self):
        self.calls.append(("close",))


def manager(**bridges):
    port = midi_port.Port(FakeBackend())
    built = []

    def build(media_dir, out_root, bank=None):
        built.append((media_dir, bank))
        return fake_project(sid=os.path.basename(media_dir), bank=bank or 0, bpm=101.0)
    m = M.DawManager("/tmp/unused", bridges={k: (lambda b=b: b) for k, b in bridges.items()}, midi_factory=lambda: port,
                     project_builder=build)
    return m, port, built


class Manager(unittest.TestCase):
    def test_open_sync_play_stop_flow(self):
        h = FakeBridge()
        m, port, built = manager(hydrogen=h, ardour=FakeBridge("ardour"))
        r = m.command("song1", "/media/song1", {"daw": "hydrogen", "command": "open"})
        self.assertTrue(r["ok"])
        self.assertEqual((r["daw"], r["bpm"], r["detail"]["kit"]), ("hydrogen", 101.0, "ChopPad Test Song B1 abcd1234"))
        self.assertEqual(r["midi"], {"port": "ChopPad", "address": "130:0"})
        self.assertEqual(h.calls, [("launch", "song1")])
        m.command("song1", "/media/song1", {"daw": "hydrogen", "command": "sync", "bank": 2})
        self.assertEqual(h.calls[-1], ("push", "song1"))
        self.assertEqual(built[-1], ("/media/song1", 2))
        r = m.command("song1", "/media/song1", {"daw": "hydrogen", "command": "play"})
        self.assertEqual((r["daw_transport"], r["midi"]), ("ok", "start+clock"))
        self.assertEqual(h.calls[-1], ("play", 101.0))
        self.assertEqual(port.backend.msgs[0], bytes([midi_port.START]))
        self.assertTrue(port.clock_gen.running)
        r = m.command("song1", "/media/song1", {"daw": "hydrogen", "command": "stop"})
        self.assertEqual(r["midi"], "stop")
        self.assertFalse(port.clock_gen.running)
        self.assertEqual(port.backend.msgs[-1], bytes([midi_port.STOP]))
        m.close()
        self.assertEqual(h.calls[-1], ("close",))
        self.assertTrue(port.backend.closed)

    def test_sync_before_open_launches(self):
        h = FakeBridge()
        m, *_ = manager(hydrogen=h)
        m.command("song1", "/media/song1", {"daw": "hydrogen", "command": "sync"})
        self.assertEqual(h.calls, [("launch", "song1")])

    def test_errors_carry_http_status(self):
        m, *_ = manager(hydrogen=FakeBridge(), ardour=FakeBridge("ardour", installed=False))
        cases = [({"daw": "bitwig", "command": "open"}, 400), ({"daw": "hydrogen", "command": "dance"}, 400),
                 ({"command": "open"}, 400), ("nope", 400), ({"daw": "hydrogen", "command": "play"}, 409),
                 ({"daw": "ardour", "command": "open"}, 501)]
        for body, code in cases:
            with self.subTest(body), self.assertRaises(M.DawError) as cm:
                m.command("song1", "/media/song1", body)
            self.assertEqual(cm.exception.http_status, code)

    def test_bridge_runtime_failure_is_a_502(self):
        m, *_ = manager(hydrogen=FakeBridge(launch_error=RuntimeError("Hydrogen exited at start")))
        with self.assertRaises(M.DawError) as cm:
            m.command("song1", "/media/song1", {"daw": "hydrogen", "command": "open"})
        self.assertEqual(cm.exception.http_status, 502)
        self.assertIn("exited", str(cm.exception))

    def test_not_implemented_is_reported_not_raised_and_midi_still_goes_out(self):
        a = FakeBridge("ardour", transport_error=NotImplementedError("enable OSC by hand"))
        m, port, _ = manager(ardour=a)
        m.command("song1", "/media/song1", {"daw": "ardour", "command": "open"})
        r = m.command("song1", "/media/song1", {"daw": "ardour", "command": "play"})
        self.assertTrue(r["ok"])
        self.assertIn("not implemented: enable OSC by hand", r["daw_transport"])
        self.assertEqual(r["midi"], "start+clock")
        self.assertEqual(port.backend.msgs[0], bytes([midi_port.START]))
        m.close()

    def test_launch_not_implemented_marks_ok_false(self):
        a = FakeBridge("ardour", launch_error=NotImplementedError("already open"))
        m, *_ = manager(ardour=a)
        r = m.command("song1", "/media/song1", {"daw": "ardour", "command": "open"})
        self.assertFalse(r["ok"])
        self.assertEqual(r["not_implemented"], "already open")

    def test_midi_failure_does_not_break_the_bridge(self):
        def boom():
            raise OSError("no ALSA")
        m = M.DawManager("/tmp/unused", bridges={"hydrogen": lambda: FakeBridge()}, midi_factory=boom,
                         project_builder=lambda d, o, bank=None: fake_project())
        r = m.command("song1", "/media/song1", {"daw": "hydrogen", "command": "open"})
        self.assertTrue(r["ok"])
        self.assertEqual(r["midi"]["port"], None)
        self.assertIn("no ALSA", r["midi"]["error"])
        r = m.command("song1", "/media/song1", {"daw": "hydrogen", "command": "play"})
        self.assertNotIn("midi", r)

    def test_mirrors_hits_and_transport_for_the_song_in_the_daw_only(self):
        h = FakeBridge()
        m, port, _ = manager(hydrogen=h)
        m.command("song1", "/media/song1", {"daw": "hydrogen", "command": "open"})
        h.calls.clear()
        m.on_transport("other", {"command": "hit", "pad": 1, "velocity": 1})       # another song: ignored
        m.on_transport("song1", {"command": "hit", "pad": 3, "velocity": 0.5})
        m.on_transport("song1", {"command": "hit", "pad": 4, "velocity": 1, "bank": 1})   # not the exported bank
        self.assertEqual(h.calls, [("note", 3, 0.5)])
        self.assertEqual(port.backend.msgs[:1], [bytes([0x90, 39, 64])])
        time.sleep(midi_port.__dict__.get("X", 0) + M.NOTE_LENGTH_S + 0.1)
        self.assertIn(bytes([0x80, 39, 0]), port.backend.msgs)
        m.on_transport("song1", {"command": "play-pattern"})
        m.on_transport("song1", {"command": "stop"})
        self.assertEqual([c[0] for c in h.calls], ["note", "play", "stop"])
        m.close()

    def test_a_broken_bridge_never_breaks_the_hook(self):
        h = FakeBridge(transport_error=RuntimeError("gone"))
        m, *_ = manager(hydrogen=h)
        m.command("song1", "/media/song1", {"daw": "hydrogen", "command": "open"})
        m.on_transport("song1", {"command": "play-pattern"})   # must not raise


class Endpoint(unittest.TestCase):
    def setUp(self):
        self.root = ctl.make_song_root()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.h = FakeBridge()
        self.m, self.port, self.built = manager(hydrogen=self.h)

        def daw_action(media_dir, body):
            return self.m.command(os.path.basename(media_dir), media_dir, body)
        self.srv, p = ctl.start_server(self.root, actions={"daw": daw_action}, on_transport=self.m.on_transport)
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)
        self.base = f"http://127.0.0.1:{p}"

    def post(self, path, body):
        req = urllib.request.Request(self.base + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_open_then_play_over_http(self):
        code, r = self.post("/api/songs/song1/daw", {"daw": "hydrogen", "command": "open"})
        self.assertEqual((code, r["ok"], r["daw"]), (200, True, "hydrogen"))
        code, r = self.post("/api/songs/song1/daw", {"daw": "hydrogen", "command": "play"})
        self.assertEqual((code, r["daw_transport"]), (200, "ok"))

    def test_status_codes(self):
        self.assertEqual(self.post("/api/songs/song1/daw", {"daw": "hydrogen", "command": "play"})[0], 409)
        code, r = self.post("/api/songs/song1/daw", {"daw": "nope", "command": "open"})
        self.assertEqual(code, 400)
        self.assertIn("daw must be one of", r["error"])
        self.assertEqual(self.post("/api/songs/ghost/daw", {"daw": "hydrogen", "command": "open"})[0], 404)

    def test_agent_driven_hit_reaches_the_daw(self):
        self.post("/api/songs/song1/daw", {"daw": "hydrogen", "command": "open"})
        self.h.calls.clear()
        code, r = self.post("/api/songs/song1/transport", {"command": "hit", "pad": 2, "velocity": 0.8})
        self.assertEqual((code, r["ok"]), (200, True))
        self.assertEqual(self.h.calls, [("note", 2, 0.8)])
        self.post("/api/songs/song1/transport", {"command": "play-pattern"})
        self.assertEqual(self.h.calls[-1], ("play", 101.0))


class CliAndMcp(unittest.TestCase):
    class FakeClient:
        server = "http://127.0.0.1:1"

        def __init__(self, songs=({"id": "newest"},), reply=None):
            self._songs, self.calls = list(songs), []
            self.reply = reply or {"ok": True, "daw": "hydrogen", "command": "open", "song": "newest",
                                   "detail": {"song": "/s.h2song", "osc_port": 9731, "headless": False},
                                   "midi": {"port": "ChopPad", "address": "130:0"}}

        def songs(self):
            return self._songs

        def daw(self, sid, daw, command, bank=None):
            self.calls.append((sid, daw, command, bank))
            return self.reply

    def verb(self, argv, client):
        out, err = [], []
        code = choppad.run_verb(argv, client=client, out=out.append, err=err.append)
        return code, out, err

    def test_daw_verb_needs_an_id_and_forwards(self):
        c = self.FakeClient()
        self.assertEqual(self.verb(["daw", "open"], c)[0], 2)
        self.assertEqual(c.calls, [])
        code, out, err = self.verb(["daw", "sync", "song1", "--daw", "ardour", "--bank", "1"], c)
        self.assertEqual((code, c.calls), (0, [("song1", "ardour", "sync", 1)]))
        self.assertEqual(json.loads(out[0])["ok"], True)

    def test_daw_verb_prints_not_implemented_note(self):
        c = self.FakeClient(reply={"ok": False, "not_implemented": "enable OSC by hand"})
        code, out, err = self.verb(["daw", "play", "s", "--daw", "ardour"], c)
        self.assertEqual(code, 0)
        self.assertIn("enable OSC by hand", err[0])

    def test_station_with_a_running_server_opens_newest_song(self):
        c = self.FakeClient()
        code, out, err = self.verb(["station", "--daw", "hydrogen", "--server", "http://127.0.0.1:1"], c)
        self.assertEqual(code, 0)
        self.assertEqual(c.calls, [("newest", "hydrogen", "open", None)])
        text = "\n".join(out)
        self.assertIn("http://127.0.0.1:1/#/song/newest", text)
        self.assertIn("MIDI out: ALSA port 'ChopPad'", text)
        self.assertIn("already running", text)

    def test_station_empty_library_and_dead_explicit_server(self):
        code, out, err = self.verb(["station", "--server", "http://127.0.0.1:1"], self.FakeClient(songs=()))
        self.assertEqual(code, 1)
        self.assertIn("library is empty", err[0])

        class Dead(self.FakeClient):
            def songs(self):
                raise cc.ControlError("cannot reach the ChopPad server")
        code, out, err = self.verb(["station", "--server", "http://127.0.0.1:1"], Dead())
        self.assertEqual(code, 1)    # an explicit server that does not answer is an error, not a reason to start another
        self.assertIn("cannot reach", err[0])

    def test_mcp_tools_forward_to_the_client(self):
        c = self.FakeClient()
        s = mcp.McpServer(c)
        names = {t["name"] for t in s.tools_list()}
        self.assertTrue({"daw_open", "daw_sync", "daw_transport"} <= names)
        r = s.call("daw_open", {"id": "song1", "daw": "hydrogen", "bank": 2})
        self.assertFalse(r["isError"])
        s.call("daw_sync", {"id": "song1", "daw": "ardour"})
        s.call("daw_transport", {"id": "song1", "daw": "hydrogen", "command": "stop"})
        self.assertEqual(c.calls, [("song1", "hydrogen", "open", 2), ("song1", "ardour", "sync", None), ("song1", "hydrogen", "stop", None)])
        self.assertTrue(s.call("daw_transport", {"id": "song1", "daw": "hydrogen"})["isError"])   # missing command

    def test_client_method_posts_the_contract_body(self):
        seen = {}

        class C(cc.Client):
            def post(self, path, body=None, timeout=None):
                seen.update(path=path, body=body)
                return {"ok": True}
        C("http://x").daw("a b", "ardour", "open", 3)
        self.assertEqual(seen, {"path": "/api/songs/a%20b/daw", "body": {"daw": "ardour", "command": "open", "bank": 3}})


if __name__ == "__main__":
    unittest.main()
