"""W3b sampler core (second slice) on the control side: reducer extras, diff_project, client verbs, CLI and MCP tools,
against a real server on a synthetic song. The stretch endpoint is faked (no rubberband, no audio)."""
import json, os, shutil, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from support import CHOPS, jread, make_song_root, start_server, mega_id, saved_project  # noqa: E402
import choppad_actions as A  # noqa: E402
import choppad_client as cc  # noqa: E402
import choppad_mcp  # noqa: E402
import choppad  # noqa: E402
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "stems"))  # noqa: E402


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
        return saved_project(self.root)


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


class SongMode(Rig):
    def test_reducer_twins_and_diff(self):
        s = A.initial_state(project={"pattern": {"bars": 2, "events": [{"beat": 0, "pad": 1, "bank": 0, "vel": 1, "pass": 1}]}}, chops=CHOPS)
        self.assertNotIn("song", s["project"])
        s2 = A.reduce(A.reduce(s, {"type": "song.addPattern", "name": "B"}), {"type": "song.setChain", "chain": [{"pattern": "p1", "repeats": 2}, {"pattern": "p2"}]})
        self.assertEqual(s2["project"]["song"]["current"], "p2")
        self.assertEqual(A.diff_project(s["project"], s2["project"])[-1]["type"], "song.load")
        back = s
        for act in A.diff_project(s["project"], s2["project"]):
            back = A.reduce(back, act)
        self.assertEqual(back["project"]["song"], s2["project"]["song"])
        self.assertEqual(A.song_timeline(s2["project"], 4, force=True)["total"], 24)
        self.assertEqual(A.song_timeline(s2["project"], 4)["total"], 8, "pattern mode: just the edited pattern")
        self.assertEqual([e["beat"] for e in A.timeline_events(A.song_timeline(s2["project"], 4, force=True))], [0, 8])

    def test_verbs_edit_the_song_and_show_it(self):
        out = []
        run = lambda *argv: choppad.run_verb(list(argv), client=self.c, out=out.append, err=lambda s: None)  # noqa: E731
        self.c.actions("song1", [{"type": "pattern.load", "pattern": {"bars": 1, "events": [{"beat": 0, "pad": 0, "bank": 0, "vel": 1, "pass": -1}]}}])
        self.assertEqual(run("song", "song1", "add", "--name", "Chorus", "--bars", "2", "--copy"), 0)
        song = self.project()["song"]
        self.assertEqual([(p["id"], p["name"], p["bars"], len(p["events"])) for p in song["patterns"]], [("p1", "Pattern 1", 1, 1), ("p2", "Chorus", 2, 1)])
        self.assertEqual(run("song", "song1", "rename", "p1", "Verse", "one"), 0)
        self.assertEqual(run("song", "song1", "chain", "p1:2", "p2"), 0)
        self.assertEqual(run("song", "song1", "mode", "song"), 0)
        song = self.project()["song"]
        self.assertEqual((song["patterns"][0]["name"], song["chain"], song["mode"]),
                         ("Verse one", [{"pattern": "p1", "repeats": 2}, {"pattern": "p2", "repeats": 1}], "song"))
        self.assertEqual(run("song", "song1", "select", "p1"), 0)
        self.assertEqual(self.project()["song"]["current"], "p1")
        out.clear()
        run("song", "song1", "show")
        shown = json.loads(out[0])
        self.assertEqual((shown["mode"], shown["chain_bars"], shown["editing"]), ("song", 4, "p1"))
        self.assertEqual(run("song", "song1", "remove", "p2"), 0)
        self.assertEqual(self.project()["song"]["chain"], [{"pattern": "p1", "repeats": 2}])
        for bad in (("chain", "p1:0"), ("chain", "p1:x"), ("mode", "disco"), ("select",), ("rename", "p1")):
            with self.subTest(bad):
                self.assertEqual(run("song", "song1", *bad), 1)
        tools = choppad_mcp.build_tools(self.c)
        tools["song"]["_fn"]({"id": "song1", "op": "add", "name": "Bridge"})
        self.assertEqual(tools["song"]["_fn"]({"id": "song1", "op": "show"})["patterns"][-1]["name"], "Bridge")

    def test_old_project_show(self):
        shown = self.c.song_show("song1")
        self.assertEqual((shown["mode"], len(shown["patterns"]), shown["chain"]), ("pattern", 1, []))
        self.assertEqual(cc.parse_chain("p1:2 p2;p3,p4:3"), [{"pattern": "p1", "repeats": 2}, {"pattern": "p2", "repeats": 1},
                                                              {"pattern": "p3", "repeats": 1}, {"pattern": "p4", "repeats": 3}])


class StemsVerbs(Rig):
    def setUp(self):
        super().setUp()
        self.srv.shutdown()
        self.srv.server_close()
        names = ["drums", "bass", "vocals", "other"]
        self.srv, port = start_server(self.root, actions={"stretch": fake_stretch, "stems": lambda d, body: {
            "stems": names, "urls": {n: f"/media/song1/stems/{n}.wav" for n in names}, "backend": "remote"}})
        self.c = cc.Client(f"http://127.0.0.1:{port}")

    def test_cli_separates_and_puts_a_stem_on_a_pad(self):
        out = []
        rc = choppad.run_verb(["stems", "song1", "--pad", "4", "--stem", "bass", "--bank", "1"], client=self.c, out=out.append, err=lambda s: None)
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out[0])["stems"], ["drums", "bass", "vocals", "other"])
        self.assertEqual(self.project()["overrides"]["1:4"]["stem"], "bass")
        self.assertEqual(choppad.run_verb(["stems", "song1", "--pad", "4", "--stem", "mix", "--bank", "1"], client=self.c, out=out.append, err=lambda s: None), 0)
        self.assertNotIn("stem", self.project()["overrides"]["1:4"])

    def test_checks(self):
        errs = []
        self.assertEqual(choppad.run_verb(["stems", "song1", "--stem", "drums"], client=self.c, out=lambda s: None, err=errs.append), 1)
        self.assertIn("--stem needs --pad", errs[0])
        with self.assertRaises(cc.ControlError):
            self.c.pad_stem("song1", 0, 0, "guitar")
        tools = choppad_mcp.build_tools(self.c)
        r = tools["stems"]["_fn"]({"id": "song1", "pad": 2, "bank": 0, "stem": "vocals"})
        self.assertEqual(r["stems"][0], "drums")
        self.assertEqual(self.project()["overrides"]["0:2"]["stem"], "vocals")

    def test_a_server_without_a_backend_says_so(self):
        self.srv.shutdown()
        self.srv.server_close()
        from choppad_stems import StemsUnavailable

        def none(d, body):
            raise StemsUnavailable("stem separation is off")
        self.srv, port = start_server(self.root, actions={"stems": none})
        c = cc.Client(f"http://127.0.0.1:{port}")
        with self.assertRaisesRegex(cc.ControlError, "HTTP 501: stem separation is off"):
            c.stems("song1")


class Resample(Rig):
    URL = "/media/song1/resample/0123456789abcdef.wav"

    def test_needs_a_browser_and_checks_its_arguments(self):
        with self.assertRaisesRegex(cc.ControlError, "no browser"):
            self.c.resample("song1", 3, bars=2)
        for kw in ({"pad": 3}, {"pad": 3, "bars": 1, "seconds": 2}, {"pad": 16, "bars": 1}, {"pad": 3, "bars": 1, "source": "tape"}):
            with self.subTest(kw), self.assertRaises(cc.ControlError):
                self.c.resample("song1", **kw)

    def test_waits_until_the_pad_plays_the_new_file(self):
        sent = []

        def fake_browser(sid, command, **kw):   # what the open tab does: render, upload, put it on the pad
            sent.append((command, kw))
            self.c.actions(sid, [{"type": "slice.src", "bank": kw["bank"], "pad": kw["pad"], "src": self.URL, "start": 0, "end": 2.0}])
            return {"ok": True, "listeners": 1}
        self.c.transport = fake_browser
        r = self.c.resample("song1", 5, bars=2, bank=1)
        self.assertEqual(sent, [("resample", {"source": "pattern", "pad": 5, "bank": 1, "bars": 2})])
        self.assertEqual((r["src"], r["end"]), (self.URL, 2.0))

    def test_cli_and_mcp_shapes(self):
        a = choppad.build_verb_parser().parse_args(["resample", "s", "--pad", "3", "--seconds", "2.5", "--source", "master"])
        self.assertEqual((a.pad, a.seconds, a.source, a.bars), (3, 2.5, "master", None))
        self.assertIn("resample", {t["name"] for t in choppad_mcp.McpServer(self.c).tools_list()})


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

    def test_mute_group(self):
        choppad.run_verb(["pad", "song1", "2", "--mute-group", "3"], client=self.c, out=lambda s: None, err=lambda s: None)
        self.assertEqual(self.project()["pads"][2]["muteGroup"], 3)
        for bad in (9, -1):
            with self.assertRaises(cc.ControlError):
                self.c.pad("song1", 2, mute_group=bad)
        choppad_mcp.build_tools(self.c)["pad"]["_fn"]({"id": "song1", "pad": 2, "mute_group": 0})
        self.assertEqual(self.project()["pads"][2]["muteGroup"], 0)

    def test_pad_tool_in_mcp(self):
        tools = choppad_mcp.build_tools(self.c)
        tools["pad"]["_fn"]({"id": "song1", "pad": 1, "filter_type": "hp", "cutoff": 2000})
        self.assertEqual(self.project()["pads"][1]["filter"]["type"], "hp")


if __name__ == "__main__":
    unittest.main()
