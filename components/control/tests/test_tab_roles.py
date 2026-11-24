"""CHOP-11 task 6, server side: tabs that open the song stream with ?role=1 are told whether they are the primary tab (the
first to connect; the only one that executes transport) or a display tab, and the next oldest is promoted when the primary
leaves. Streams without ?role=1 (CLI watchers, other tools) are not part of it and see no `role` events."""
import http.client, json, os, shutil, sys, threading, time, unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from support import make_song_root, start_server  # noqa: E402


class Tab:
    def __init__(self, port, role=True, origin="t"):
        self.conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        self.conn.request("GET", f"/api/songs/song1/events?origin={origin}" + ("&role=1" if role else ""))
        self.sock = self.conn.sock
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
        except (OSError, ValueError, http.client.HTTPException):
            pass

    def roles(self):
        return [d["primary"] for n, d in self.events if n == "role"]

    def wait_roles(self, want, timeout=4.0):
        end = time.time() + timeout
        while time.time() < end and self.roles() != want:
            time.sleep(0.01)
        return self.roles()

    def close(self):
        try:
            self.sock.shutdown(2)  # a reader thread keeps the file open, so close() alone would not send the FIN
            self.resp.close()
            self.conn.close()
        except OSError:
            pass


class TabRoles(unittest.TestCase):
    def setUp(self):
        self.root = make_song_root()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.srv, self.port = start_server(self.root)
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)

    def tab(self, **kw):
        t = Tab(self.port, **kw)
        self.addCleanup(t.close)
        time.sleep(0.15)
        return t

    def test_first_tab_is_primary_later_tabs_only_display(self):
        a, b, c = self.tab(origin="a"), self.tab(origin="b"), self.tab(origin="c")
        self.assertEqual((a.roles(), b.roles(), c.roles()), ([True], [False], [False]))
        self.assertEqual([d["tabs"] for n, d in a.events if n == "role"], [1])

    def test_next_oldest_is_promoted_when_the_primary_leaves(self):
        a, b, c = self.tab(origin="a"), self.tab(origin="b"), self.tab(origin="c")
        a.close()
        self.assertEqual(b.wait_roles([False, True]), [False, True])   # b was the next to connect
        self.assertEqual(c.roles(), [False])                           # c is told nothing new: still display
        b.close()
        self.assertEqual(c.wait_roles([False, True]), [False, True])

    def test_a_returning_tab_comes_back_as_display(self):
        a, b = self.tab(origin="a"), self.tab(origin="b")
        a.close()
        b.wait_roles([False, True])
        a2 = self.tab(origin="a")
        self.assertEqual(a2.roles(), [False])
        self.assertEqual(b.roles(), [False, True])

    def test_streams_without_role_are_not_tabs(self):
        watcher = self.tab(role=False, origin="w")
        a = self.tab(origin="a")
        self.assertEqual(watcher.roles(), [])
        self.assertEqual(a.roles(), [True])        # the watcher did not take the primary role
        self.assertEqual([n for n, _ in watcher.events], [])

    def test_transport_still_goes_to_every_stream_the_tab_decides_what_to_run(self):
        # the server does not filter transport events: tabs ignore them unless primary (components/ui/session.js)
        import urllib.request
        a, b = self.tab(origin="a"), self.tab(origin="b")
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/songs/song1/transport", method="POST",
                                     data=json.dumps({"command": "hit", "pad": 1}).encode(), headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req).read()
        time.sleep(0.2)
        self.assertEqual([n for n, _ in a.events if n == "transport"], ["transport"])
        self.assertEqual([n for n, _ in b.events if n == "transport"], ["transport"])


if __name__ == "__main__":
    unittest.main()
