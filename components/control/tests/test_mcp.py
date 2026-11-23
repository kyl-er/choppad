"""MCP server: protocol handling in-process, plus one real stdio round trip (subprocess) against a test server."""
import json, os, shutil, subprocess, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from support import ROOT, jread, make_song_root, start_server, mega_id, saved_project  # noqa: E402
import choppad_client as cc  # noqa: E402
import choppad_mcp as M  # noqa: E402

MCP_PY = os.path.join(ROOT, "components", "control", "choppad_mcp.py")
EXPECTED = {"library", "song_summary", "chop_table", "pattern_text", "import_song", "regrid", "assign", "pad", "pattern_set",
            "pattern_clear", "fx", "play", "hit", "export", "daw_open", "daw_sync", "daw_transport"}
EXPECTED |= {"tempo", "resample", "song", "stems"}  # W3b additions (the set above is unchanged)


class McpTest(unittest.TestCase):
    def setUp(self):
        self.root = make_song_root()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.srv, port = start_server(self.root, actions={"sections": lambda d, b: {"sections": [{"label": "A", "start_bar": 0, "end_bar": 8}]}})
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)
        self.url = f"http://127.0.0.1:{port}"
        self.mcp = M.McpServer(cc.Client(self.url))

    def rpc(self, method, params=None, mid=1):
        return self.mcp.handle({"jsonrpc": "2.0", "id": mid, "method": method, **({"params": params} if params is not None else {})})

    def call(self, name, **args):
        r = self.rpc("tools/call", {"name": name, "arguments": args})["result"]
        return r["isError"], r["content"][0]["text"]

    def test_initialize_and_list(self):
        r = self.rpc("initialize", {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "t"}})["result"]
        self.assertEqual(r["protocolVersion"], "2025-03-26")
        self.assertIn("tools", r["capabilities"])
        self.assertEqual(self.rpc("initialize", {"protocolVersion": "1999-01-01"})["result"]["protocolVersion"], M.LATEST)
        self.assertIsNone(self.mcp.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}))
        tools = self.rpc("tools/list")["result"]["tools"]
        self.assertEqual({t["name"] for t in tools}, EXPECTED)
        for t in tools:
            self.assertEqual(t["inputSchema"]["type"], "object")
            self.assertTrue(t["description"])
            self.assertNotIn("_fn", t)
        self.assertEqual(self.rpc("nope")["error"]["code"], -32601)
        self.assertEqual(self.rpc("ping")["result"], {})

    def test_read_tools_are_small(self):
        err, text = self.call("library")
        self.assertFalse(err)
        self.assertEqual(json.loads(text)[0]["id"], "song1")
        err, text = self.call("song_summary", id="song1")
        s = json.loads(text)
        self.assertEqual((s["title"], s["bpm"], s["meter"], s["banks"], s["sections"]), ("Test Song", 120, "4/4", 3, [{"label": "A", "bars": "0-8"}]))
        self.assertLess(len(text), 600)
        rows = json.loads(self.call("chop_table", id="song1", bank=1)[1])
        self.assertEqual((len(rows), rows[0]["label"]), (16, "5.1"))
        self.assertLess(len(self.call("chop_table", id="song1", bank=0)[1]), 2000)
        err, text = self.call("pattern_text", id="song1")
        self.assertFalse(err)
        self.assertEqual(len(text.splitlines()), 2 + 16 + 1)

    def test_edit_tools(self):
        err, text = self.call("pattern_set", id="song1", steps="0:0,8 1:4,12", bars=1)
        self.assertEqual(json.loads(text)["applied"], 1)
        err, text = self.call("fx", id="song1", preset="hall", reverb=0.5)
        self.assertFalse(err)
        self.call("pad", id="song1", pad=4, pitch=-2)
        self.call("assign", id="song1", bank=0, pad=1, start=1, end=1.5)
        p = saved_project(self.root)
        self.assertEqual((len(p["pattern"]["events"]), p["fx"]["reverb"], p["pads"][4]["pitch"], p["overrides"]["0:1"]["end"]),
                         (4, 0.5, -2, 1.5))
        self.assertIn("X...", self.call("pattern_text", id="song1")[1])
        err, text = self.call("hit", id="song1", pad=2)
        self.assertEqual(json.loads(text)["listeners"], 0)
        self.assertIn("no browser", text)

    def test_errors_are_tool_results(self):
        self.assertTrue(self.call("nope")[0])
        err, text = self.call("pad", id="song1")
        self.assertTrue(err)
        self.assertIn("missing argument", text)
        err, text = self.call("pattern_set", id="song1", steps="99:1")
        self.assertTrue(err)
        self.assertIn("pad 99", text)
        err, text = self.call("song_summary", id="ghost")
        self.assertTrue(err)
        self.assertIn("404", text)

    def test_stdio_round_trip(self):
        reqs = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "song_summary", "arguments": {"id": "song1"}}},
        ]
        p = subprocess.run([sys.executable, MCP_PY], input="\n".join(json.dumps(r) for r in reqs) + "\n", capture_output=True,
                           text=True, timeout=30, env={**os.environ, "CHOPPAD_SERVER": self.url})
        self.assertEqual(p.returncode, 0, p.stderr)
        out = [json.loads(line) for line in p.stdout.splitlines()]
        self.assertEqual([o["id"] for o in out], [1, 2, 3])  # the notification got no answer
        self.assertEqual(out[0]["result"]["serverInfo"]["name"], "choppad")
        self.assertEqual({t["name"] for t in out[1]["result"]["tools"]}, EXPECTED)
        self.assertFalse(out[2]["result"]["isError"])
        self.assertEqual(json.loads(out[2]["result"]["content"][0]["text"])["title"], "Test Song")


if __name__ == "__main__":
    unittest.main()
