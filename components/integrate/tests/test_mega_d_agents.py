"""CHOP-13 step D (agents): every CLI verb and MCP tool that takes a song id also takes a mega bank; the new `mega` verbs and tools;
`show` / `song_summary` name the mega bank and say which pads are kept and where from. A real server (choppad.make_server, fake DAW
bridge) on a free port, temporary media root, the real client and the real MCP server in-process."""
import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_mega_d import DRig, zip_pad  # noqa: E402
import test_mega as TM  # noqa: E402

import json, time, zipfile  # noqa: E402
import choppad  # noqa: E402
import choppad_client as cc  # noqa: E402
import choppad_mcp  # noqa: E402
import mega as M  # noqa: E402


class AgentRig(DRig):
    def setUp(self):
        super().setUp()
        self.url = f"http://127.0.0.1:{self.port}"
        self.client = cc.Client(self.url)

    def cli(self, *argv, code=0):
        out, err = [], []
        rc = choppad.run_verb(list(argv), client=self.client, out=out.append, err=err.append)
        self.assertEqual(rc, code, (argv, out, err))
        return out, err

    def cli_json(self, *argv):
        out, _ = self.cli(*argv)
        return json.loads(out[-1])

    def mcp(self, tool, **args):
        srv = choppad_mcp.McpServer(self.client)
        r = srv.call(tool, args)
        text = r["content"][0]["text"]
        return r["isError"], (text if r["isError"] else json.loads(text))


class MegaVerbs(AgentRig):
    def test_the_owners_scenario_through_the_cli(self):
        """import a drum break (song A), lay a pattern, switch the song with keep, add a bass chop, open it in Hydrogen."""
        mid = self.cli_json("mega", "new", "--source", "A", "--name", "My break")["id"]
        self.assertEqual(self.cli_json("mega", "show", mid)["mega"]["name"], "My break")
        self.cli("pattern", "--mega", mid, "set", "--steps", "0:0,8 1:4,12 2:2,6,10,14", "--bank", "0")
        self.cli("pad", "1", "--mega", mid, "--gain", "-3")
        self.cli("tempo", "--mega", mid, "--bpm", "96")
        r = self.cli_json("mega", "source", mid, "B")
        self.assertEqual((r["mega"]["source"], r["kept"], r["released"] > 0), ("B", 3, True))
        self.cli("assign", "--mega", mid, "--bank", "0", "--pad", "5", "--start", "1.0", "--end", "1.5")   # a bass chop of the new song
        shown = self.cli_json("show", "--mega", mid)
        self.assertEqual(shown["mega"]["name"], "My break")
        self.assertEqual(shown["kept"], [{"from": "Drum break", "pads": "0:0-2"}])
        self.assertEqual((shown["project_tempo"], shown["pattern"]["hits"], shown["mega"]["source"]), (96.0, 8, "B"))
        out, _ = self.cli("daw", "open", "--mega", mid, "--daw", "hydrogen")
        opened = json.loads(out[0])
        self.assertEqual((opened["ok"], opened["mega"], opened["bpm"]), (True, mid, 96.0))
        self.assertEqual(self.get(mid)["daw"]["open"], True)
        out, _ = self.cli("daw", "close", "--mega", mid)
        self.assertEqual(json.loads(out[0])["ok"], True)
        self.assertEqual(self.get(mid)["daw"]["open"], False)

    def test_list_rename_release_sweep_remove_and_clear_source(self):
        mid = self.cli_json("mega", "new", "--source", "A")["id"]
        out, _ = self.cli("mega", "list")
        self.assertIn(mid, out[0])
        self.assertEqual(self.cli_json("mega", "rename", mid, "Second", "name")["name"], "Second name")
        self.cli("pattern", "--mega", mid, "set", "--steps", "0:0")
        k = self.cli_json("mega", "keep", mid, "--bank", "0", "--pad", "0")
        self.assertEqual(k["kept"], [{"bank": 0, "pad": 0}])
        self.assertEqual(self.cli_json("mega", "keep", mid, "--bank", "0", "--pad", "0")["already"], [{"bank": 0, "pad": 0}])
        self.assertEqual(len(os.listdir(os.path.join(self.root, "_mega", mid, "samples"))), 1)
        self.cli("mega", "release", mid, "--bank", "0", "--pad", "0")
        self.assertEqual(os.listdir(os.path.join(self.root, "_mega", mid, "samples")), [])       # swept
        self.cli("mega", "keep", mid, "--bank", "0", "--pad", "0")
        r = self.cli_json("mega", "source", mid, "none")
        self.assertEqual((r["mega"]["source"], r["kept"]), (None, 0))
        self.assertIsNone(M.read(self.root, mid)["source"])
        self.assertEqual(self.cli_json("mega", "sweep", mid)["moved"], [])
        self.cli("mega", "remove", mid)
        self.assertNotIn(mid, [m["id"] for m in self.client.megas()])
        self.cli("mega", "show", mid, code=1)
        self.cli("mega", "keep", mid, code=1)
        self.cli("mega", "source", "x", code=1)

    def test_a_mega_bank_with_no_source_still_exports_and_source_verbs_say_pick_a_song(self):
        mid = self.cli_json("mega", "new", "--source", "A")["id"]
        self.cli("pattern", "--mega", mid, "set", "--steps", "0:0 1:4")
        self.cli("mega", "source", mid, "B")
        self.cli("mega", "source", mid, "none")
        out, _ = self.cli("export", "--mega", mid, "zip", "--bank", "0")
        z = json.loads(out[-1])
        self.assertEqual(zipfile.ZipFile(z["path"]).namelist(), ["pad01.wav", "pad02.wav"])
        out, err = self.cli("regrid", "--mega", mid, "--grid", "1/8", code=1)
        self.assertIn("Pick a song first", err[0])

    def test_two_mega_banks_on_one_song_each_verb_hits_the_addressed_one(self):
        x, y = self.cli_json("mega", "new", "--source", "A", "--name", "X")["id"], None
        time.sleep(0.02)
        y = self.cli_json("mega", "new", "--source", "A", "--name", "Y")["id"]
        self.cli("pad", "3", "--mega", x, "--gain", "-6")
        self.cli("fx", "--mega", x, "--preset", "hall")
        self.cli("pattern", "--mega", x, "set", "--steps", "3:0,4", "--bank", "0")
        self.assertEqual(self.get(x)["project"]["pads"][3]["gainDb"], -6)
        self.assertEqual(self.get(y)["project"]["pads"][3].get("gainDb", 0), 0)
        self.assertEqual(len(self.get(x)["project"]["pattern"]["events"]), 2)
        self.assertEqual(self.get(y)["project"].get("pattern", {}).get("events", []), [])
        shown = self.cli_json("show", "--mega", y)
        self.assertEqual((shown["mega"]["id"], shown["pattern"]["hits"]), (y, 0))
        # the song-keyed verb keeps its meaning: the song's most recently updated mega bank (X was edited last)
        self.assertEqual(self.cli_json("show", "A")["mega"]["id"], x)
        out, _ = self.cli("pattern", "--mega", x, "show")
        self.assertIn("2 hits", out[0])
        self.cli("hit", "--mega", y, "3", "--velocity", "0.5")           # (no tab open: only a note, nothing fails)
        self.cli("play", "--mega", y, "stop")

    def test_station_takes_a_mega_bank(self):
        mid = self.cli_json("mega", "new", "--source", "A")["id"]
        out, err = self.cli("station", "--mega", mid, "--server", self.url)
        text = "\n".join(out)
        self.assertIn(f"{self.url}/#/mega/{mid}", text)
        self.assertEqual(self.get(mid)["daw"]["open"], True)
        self.cli("daw", "close", "--mega", mid)

    def test_pattern_verbs_use_the_patterns_own_bar_length(self):
        d = os.path.join(self.root, "B", "chops.json")
        three = json.load(open(d))
        three["beats_per_bar"] = 3
        json.dump(three, open(d, "w"))
        mid = self.cli_json("mega", "new", "--source", "A")["id"]
        self.cli("pattern", "--mega", mid, "set", "--steps", "0:0", "--bars", "1")
        self.cli("mega", "source", mid, "B")
        out, _ = self.cli("pattern", "--mega", mid, "show")
        self.assertIn("bars of 4/4, 16 steps", out[0])             # not 3/4: the pattern was made in 4
        self.cli("pattern", "--mega", mid, "add", "--steps", "1:12")  # step 12 exists in a bar of 4
        self.assertEqual(len(self.get(mid)["project"]["pattern"]["events"]), 2)


class McpMegaTools(AgentRig):
    def test_every_song_tool_takes_a_mega_bank(self):
        tools = choppad_mcp.build_tools(self.client)
        for name, t in tools.items():
            props = t["inputSchema"]["properties"]
            if "id" in props:
                self.assertIn("mega", props, name)
                self.assertNotIn("id", t["inputSchema"]["required"], name)
        for name in ("mega_list", "mega_show", "mega_new", "mega_rename", "mega_remove", "mega_source", "mega_keep", "mega_release", "mega_sweep", "daw_close"):
            self.assertIn(name, tools)
        err, text = self.mcp("pattern_text")
        self.assertTrue(err)
        self.assertIn("mega", text)

    def test_owners_scenario_through_mcp_and_summary_names_the_mega_bank(self):
        err, made = self.mcp("mega_new", source="A", name="Break bank")
        self.assertFalse(err)
        mid = made["id"]
        self.assertEqual(self.mcp("pattern_set", mega=mid, steps="0:0,8 1:4,12", bank=0)[0], False)
        self.assertEqual(self.mcp("pad", mega=mid, pad=0, gain=-2)[0], False)
        err, r = self.mcp("mega_source", mega=mid, song="B")
        self.assertEqual((err, r["kept"], r["mega"]["source"]), (False, 2, "B"))
        err, s = self.mcp("song_summary", mega=mid)
        self.assertFalse(err)
        self.assertEqual((s["mega"]["name"], s["mega"]["source"], s["kept"]), ("Break bank", "B", [{"from": "Drum break", "pads": "0:0-1"}]))
        err, s2 = self.mcp("song_summary", id="B")                     # a song id: its mega bank (the one just made)
        self.assertEqual((s2["mega"]["id"], s2["id"]), (mid, "B"))
        self.assertLess(len(json.dumps(s)), 700)
        err, z = self.mcp("export", mega=mid, kind="zip", bank=0)
        self.assertFalse(err)
        self.assertEqual(len(zipfile.ZipFile(z["path"]).namelist()), 16)
        err, d = self.mcp("daw_open", mega=mid, daw="hydrogen")
        self.assertEqual((err, d["ok"], d["mega"]), (False, True, mid))
        self.assertEqual(self.mcp("daw_close", mega=mid)[0], False)
        self.assertEqual(self.mcp("daw_close", mega=mid)[0], True)    # nothing left to close
        err, r = self.mcp("mega_release", mega=mid, bank=0, pad=1)
        self.assertFalse(err)
        self.assertEqual(self.mcp("mega_sweep", mega=mid)[1]["moved"], [])
        self.assertEqual(self.mcp("mega_rename", mega=mid, name="Renamed")[1]["name"], "Renamed")
        self.assertEqual([m["name"] for m in self.mcp("mega_list")[1]], ["Renamed"])
        self.assertEqual(self.mcp("mega_source", mega=mid, song="none")[1]["mega"]["source"], None)
        err, text = self.mcp("regrid", mega=mid, grid="1/8")
        self.assertTrue(err)
        self.assertIn("Pick a song first", text)
        self.assertEqual(self.mcp("mega_remove", mega=mid)[0], False)
        self.assertEqual(self.mcp("mega_list")[1], [])


if __name__ == "__main__":
    unittest.main()
