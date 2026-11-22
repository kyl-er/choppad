"""W3b sampler core (second slice) on the control side: reducer extras, diff_project, client verbs, CLI and MCP tools,
against a real server on a synthetic song. The stretch endpoint is faked (no rubberband, no audio)."""
import json, os, shutil, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from support import CHOPS, jread, make_song_root, start_server  # noqa: E402
import choppad_actions as A  # noqa: E402
import choppad_client as cc  # noqa: E402
import choppad_mcp  # noqa: E402
import choppad  # noqa: E402


def fake_stretch(media_dir, body):
    """Stands in for POST /stretch: ratio = song tempo / target, a url that depends on the slice and ratio."""
    ratio = round(CHOPS["bpm"] / float(body["target_bpm"]), 6) if body.get("target_bpm") else float(body["ratio"])
    h = format(abs(hash((body.get("start"), body.get("end"), ratio, body.get("stem")))) % 16**16, "016x")
    return {"url": f"/media/{os.path.basename(media_dir)}/stretch/{h}.wav", "ratio": ratio, "duration": 0.5 * ratio,
            "hash": h, "start": body.get("start"), "end": body.get("end")}


class Rig(unittest.TestCase):
    def setUp(self):
        self.root = make_song_root()
        self.srv, port = start_server(self.root, actions={"stretch": fake_stretch})
        self.c = cc.Client(f"http://127.0.0.1:{port}")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)

    def project(self):
        return jread(os.path.join(self.root, "song1", "project.json"))


class TempoReducer(unittest.TestCase):
    def test_project_bpm_and_diff(self):
        s = A.initial_state(chops=CHOPS)
        self.assertEqual(A.project_bpm(s), 120)
        s2 = A.reduce(s, {"type": "tempo.set", "bpm": 92})
        self.assertEqual(A.project_bpm(s2), 92)
        self.assertNotIn("tempo", s["project"])
        self.assertEqual(A.diff_project(s["project"], s2["project"]), [{"type": "tempo.set", "bpm": 92, "follow": True}])
        self.assertEqual(A.diff_project(s2["project"], s["project"]), [{"type": "tempo.set", "bpm": None, "follow": True}])

    def test_bad_saved_tempo_is_dropped(self):
        self.assertNotIn("tempo", A.initial_state(project={"tempo": {"bpm": 2}}, chops=CHOPS)["project"])
        self.assertNotIn("tempo", A.initial_state(project={"tempo": "fast"}, chops=CHOPS)["project"])


class TempoClient(Rig):
    def set_pattern(self, *pads, bank=0):
        self.c.actions("song1", [{"type": "pattern.load", "pattern": {"bars": 1, "events": [
            {"beat": i, "pad": p, "bank": bank, "vel": 1, "pass": -1} for i, p in enumerate(pads)]}}])

    def test_tempo_set_show_and_clear(self):
        r = self.c.tempo("song1")
        self.assertEqual((r["tempo"], r["follow"], r["song_bpm"]), (None, True, 120))
        r = self.c.tempo("song1", bpm=92)
        self.assertEqual(r["tempo"], 92)
        self.assertEqual(self.project()["tempo"], {"bpm": 92, "follow": True})
        r = self.c.tempo("song1", song=True)
        self.assertEqual(r["tempo"], None)
        with self.assertRaises(cc.ControlError):
            self.c.tempo("song1", bpm=5)  # out of range: the server ignores it, nothing changes

    def test_fit_pattern_marks_pads_and_leaves_hand_stretched_alone(self):
        self.set_pattern(0, 1, 2)
        self.c.actions("song1", [{"type": "slice.set", "bank": 0, "pad": 2, "start": 5, "end": 5.5},
                                 {"type": "slice.stretch", "bank": 0, "pad": 2, "stretch": {"ratio": 1.5, "url": "/media/song1/stretch/aaaaaaaaaaaaaaaa.wav"}}])
        self.c.tempo("song1", bpm=96)
        out = self.c.fit_tempo("song1", "pattern")
        self.assertEqual(sorted(f["pad"] for f in out["fitted"]), [0, 1])
        self.assertEqual([s["reason"] for s in out["skipped"]], ["stretched by hand"])
        ov = self.project()["overrides"]
        self.assertEqual(ov["0:0"]["stretch"]["fit"], 96)
        self.assertAlmostEqual(ov["0:0"]["stretch"]["ratio"], 1.25)
        self.assertNotIn("fit", ov["0:2"]["stretch"])
        self.assertEqual(ov["0:2"]["stretch"]["url"], "/media/song1/stretch/aaaaaaaaaaaaaaaa.wav")
        # fitting again does nothing: they are fitted to this tempo already
        again = self.c.fit_tempo("song1", "pattern")
        self.assertEqual(again["fitted"], [])

    def test_tempo_change_refits_fitted_pads_only_and_song_tempo_clears_them(self):
        self.set_pattern(0, 1)
        self.c.tempo("song1", bpm=96, fit="pattern")
        hand = {"ratio": 2, "url": "/media/song1/stretch/bbbbbbbbbbbbbbbb.wav"}
        self.c.actions("song1", [{"type": "slice.set", "bank": 0, "pad": 5, "start": 1, "end": 2},
                                 {"type": "slice.stretch", "bank": 0, "pad": 5, "stretch": hand}])
        r = self.c.tempo("song1", bpm=80)
        self.assertEqual(r["refitted"], 2)
        ov = self.project()["overrides"]
        self.assertEqual((ov["0:0"]["stretch"]["fit"], round(ov["0:0"]["stretch"]["ratio"], 4)), (80, 1.5))
        self.assertEqual(ov["0:5"]["stretch"], hand)
        r = self.c.tempo("song1", song=True)
        ov = self.project()["overrides"]
        self.assertNotIn("stretch", ov.get("0:0", {}))
        self.assertEqual(ov["0:5"]["stretch"], hand)

    def test_follow_off_keeps_old_fits(self):
        self.set_pattern(0)
        self.c.tempo("song1", bpm=96, fit="pattern", follow=False)
        r = self.c.tempo("song1", bpm=80)
        self.assertNotIn("refitted", r)
        self.assertEqual(self.project()["overrides"]["0:0"]["stretch"]["fit"], 96)

    def test_fit_needs_a_tempo_and_a_good_scope(self):
        with self.assertRaises(cc.ControlError):
            self.c.fit_tempo("song1", "pattern")
        self.c.tempo("song1", bpm=100)
        with self.assertRaises(cc.ControlError):
            self.c.fit_tempo("song1", "nope")

    def test_fit_bank_uses_the_bank_on_screen(self):
        self.c.actions("song1", [{"type": "bank.set", "bank": 2}])
        self.c.tempo("song1", bpm=100)
        out = self.c.fit_tempo("song1", "bank")
        self.assertEqual(len(out["fitted"]), 8)  # the last bank is short
        self.assertTrue(all(f["bank"] == 2 for f in out["fitted"]))

    def test_matching_tempo_needs_no_stretch(self):
        self.set_pattern(0)
        self.c.tempo("song1", bpm=120)
        out = self.c.fit_tempo("song1", "pattern")
        self.assertEqual(self.project().get("overrides", {}), {})
        self.assertEqual(out["fitted"][0]["ratio"], 1.0)


class TempoVerbs(Rig):
    def test_cli_and_mcp(self):
        lines = []
        rc = choppad.run_verb(["tempo", "song1", "--bpm", "100"], client=self.c, out=lines.append, err=lambda s: None)
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(lines[0])["tempo"], 100)
        a = choppad.build_verb_parser().parse_args(["tempo", "s", "--song", "--no-follow", "--fit", "bank"])
        self.assertEqual((a.song, a.follow, a.fit), (True, False, "bank"))
        tools = choppad_mcp.build_tools(self.c)
        out = tools["tempo"]["_fn"]({"id": "song1", "bpm": 90})
        self.assertEqual(out["tempo"], 90)
        self.assertIn("tempo", {t["name"] for t in choppad_mcp.McpServer(self.c).tools_list()})


class PadSound(Rig):
    def test_pad_filter_and_envelope_through_the_pad_verb(self):
        lines = []
        rc = choppad.run_verb(["pad", "song1", "4", "--filter-type", "lp", "--cutoff", "900", "--q", "2",
                               "--decay", "0.2", "--sustain", "0.5"], client=self.c, out=lines.append, err=lambda s: None)
        self.assertEqual(rc, 0)
        p = self.project()["pads"][4]
        self.assertEqual((p["filter"], p["decay"], p["sustain"]), ({"type": "lp", "cutoff": 900, "q": 2}, 0.2, 0.5))
        self.c.pad("song1", 4, cutoff=300)   # only the given field changes
        self.assertEqual(self.project()["pads"][4]["filter"], {"type": "lp", "cutoff": 300, "q": 2})
        self.c.pad("song1", 4, reset=True)
        self.assertEqual(self.project()["pads"][4]["filter"]["type"], "off")
        for bad in ({"filter_type": "notch"}, {"sustain": 2}):
            with self.assertRaises(cc.ControlError):
                self.c.pad("song1", 4, **bad)

    def test_pad_tool_in_mcp(self):
        tools = choppad_mcp.build_tools(self.c)
        tools["pad"]["_fn"]({"id": "song1", "pad": 1, "filter_type": "hp", "cutoff": 2000})
        self.assertEqual(self.project()["pads"][1]["filter"]["type"], "hp")


if __name__ == "__main__":
    unittest.main()
