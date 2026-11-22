"""CLI verbs: argument parsing, the steps text form, and every verb against a real server on a synthetic song."""
import json, os, shutil, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from support import jread, make_song_root, start_server  # noqa: E402
import choppad_client as cc  # noqa: E402
import choppad  # noqa: E402  (components/integrate/choppad.py)


class Parsing(unittest.TestCase):
    def parse(self, *argv):
        return choppad.build_verb_parser().parse_args(list(argv))

    def test_steps_text_form(self):
        self.assertEqual(cc.parse_steps("1:0,8 3:4,12"), {1: [0, 8], 3: [4, 12]})
        self.assertEqual(cc.parse_steps("0:0;0:8"), {0: [0, 8]})
        self.assertEqual(cc.parse_steps("2:31", bars=2, beats_per_bar=4), {2: [31]})
        for bad in ("", "x", "1:", "16:0", "1:a", "1:32", "-1:0"):
            with self.subTest(bad), self.assertRaises(cc.ControlError):
                cc.parse_steps(bad, bars=2)

    def test_events_from_steps(self):
        ev = cc.pattern_events({1: [8, 0]}, bank=2, vel=0.8)
        self.assertEqual(ev, [{"beat": 0, "pad": 1, "bank": 2, "vel": 0.8, "pass": -1},
                              {"beat": 2, "pad": 1, "bank": 2, "vel": 0.8, "pass": -1}])

    def test_verbs_parse(self):
        a = self.parse("pad", "s1", "3", "--gain", "-3", "--pitch", "2", "--no-reverse", "--mode", "gate")
        self.assertEqual((a.verb, a.id, a.pad, a.gain, a.pitch, a.reverse, a.mode), ("pad", "s1", 3, -3.0, 2.0, False, "gate"))
        a = self.parse("pattern", "s1", "set", "--steps", "1:0,8", "--bars", "1", "--bank", "1")
        self.assertEqual((a.op, a.steps, a.bars, a.bank), ("set", "1:0,8", 1.0, 1))
        a = self.parse("assign", "s1", "--bank", "0", "--pad", "5", "--start", "1.5", "--end", "2")
        self.assertEqual((a.bank, a.pad, a.start, a.end), (0, 5, 1.5, 2.0))
        a = self.parse("regrid", "s1", "--grid", "1/8", "--mult", "2", "--server", "http://h:1")
        self.assertEqual((a.grid, a.mult, a.server), ("1/8", 2.0, "http://h:1"))
        a = self.parse("fx", "s1", "--preset", "warm", "--reverb", "0.3")
        self.assertEqual((a.preset, a.reverb, a.tone), ("warm", 0.3, None))
        self.assertEqual(self.parse("play", "s1", "song", "--from", "12").start, 12.0)
        self.assertEqual(self.parse("export", "s1", "sfz", "--bank", "2").bank, 2)
        for argv in (["pattern", "s1", "bogus"], ["play", "s1", "dance"], ["fx", "s1", "--preset", "nope"], []):
            with self.subTest(argv), self.assertRaises(SystemExit):
                with open(os.devnull, "w") as dn:
                    old, sys.stderr = sys.stderr, dn
                    try:
                        self.parse(*argv)
                    finally:
                        sys.stderr = old

    def test_launcher_form_still_dispatches_to_the_old_path(self):
        self.assertNotIn("http://example.com/x", choppad.VERBS)
        self.assertNotIn("--serve", choppad.VERBS)  # `choppad <url> --serve` never reaches run_verb

    def test_daw_is_a_stub(self):
        out, err = [], []
        self.assertEqual(choppad.run_verb(["daw", "open"], out=out.append, err=err.append), 2)
        self.assertIn("W2", err[0])

    def test_fx_and_pad_params(self):
        self.assertEqual(cc.fx_params("dry", reverb=0.2)["reverb"], 0.2)
        with self.assertRaises(cc.ControlError):
            cc.fx_params()
        self.assertEqual(cc.pad_params(mode="default"), {"mode": None})
        with self.assertRaises(cc.ControlError):
            cc.pad_params(mode="loud")


class Verbs(unittest.TestCase):
    def setUp(self):
        self.root = make_song_root()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.calls = []

        def regrid(d, **kw):
            self.calls.append(kw)
            doc = {**jread(os.path.join(d, "chops.json")), "grid": kw["grid"], "bpm_mult": kw["bpm_mult"], "beat_offset": kw["beat_offset"]}
            with open(os.path.join(d, "chops.json"), "w") as f:
                json.dump(doc, f)
            return doc
        self.srv, port = start_server(self.root, regrid=regrid, actions={
            "sections": lambda d, b: {"sections": [{"label": "intro", "start_bar": 0, "end_bar": 4}]},
            "export": lambda d, b: {"path": "/tmp/x", "kind": b["kind"], "bank": b["bank"]}})
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)
        self.url = f"http://127.0.0.1:{port}"

    def run_cli(self, *argv, code=0):
        out, err = [], []
        rc = choppad.run_verb([*argv, "--server", self.url], out=out.append, err=err.append)
        self.assertEqual(rc, code, (out, err))
        return out, err

    def project(self):
        return jread(os.path.join(self.root, "song1", "project.json"))

    def test_read_verbs(self):
        out, _ = self.run_cli("songs")
        self.assertIn("song1", out[0])
        self.assertIn("Test Song", out[0])
        out, _ = self.run_cli("show", "song1")
        s = json.loads(out[0])
        self.assertEqual((s["bpm"], s["meter"], s["banks"], s["sections"]), (120, "4/4", 3, [{"label": "intro", "bars": "0-4"}]))
        self.assertNotIn("peaks", out[0])

    def test_edit_verbs_land_in_project_json(self):
        _, err = self.run_cli("pad", "song1", "3", "--gain", "-3", "--pitch", "2", "--reverse", "--mode", "gate")
        self.assertIn("no browser", err[0])  # nobody has the song open
        self.run_cli("assign", "song1", "--bank", "1", "--pad", "2", "--start", "3.5", "--end", "4")
        self.run_cli("fx", "song1", "--preset", "warm", "--reverb", "0.5")
        out, _ = self.run_cli("pattern", "song1", "set", "--steps", "1:0,8 3:4,12", "--bars", "1", "--bank", "1", "--vel", "0.9")
        self.assertEqual(json.loads(out[0])["applied"], 1)
        p = self.project()
        self.assertEqual((p["pads"][3]["gainDb"], p["pads"][3]["pitch"], p["pads"][3]["reverse"], p["pads"][3]["mode"]), (-3, 2, True, "gate"))
        self.assertEqual(p["overrides"]["1:2"], {"start": 3.5, "end": 4})
        self.assertEqual((p["fx"]["tone"], p["fx"]["reverb"]), (-0.35, 0.5))
        self.assertEqual(p["pattern"]["bars"], 1)
        self.assertEqual([(e["pad"], e["beat"], e["bank"], e["vel"]) for e in p["pattern"]["events"]],
                         [(1, 0, 1, 0.9), (1, 2, 1, 0.9), (3, 1, 1, 0.9), (3, 3, 1, 0.9)])
        # add merges and skips what is already there; show draws it; clear empties it
        self.run_cli("pattern", "song1", "add", "--steps", "1:0 2:2", "--bank", "1")
        self.assertEqual(len(self.project()["pattern"]["events"]), 5)
        out, _ = self.run_cli("pattern", "song1", "show")
        self.assertIn("p01  X... .... X...", out[0])
        self.assertIn("5 hits", out[0])
        self.run_cli("pattern", "song1", "clear")
        self.assertEqual(self.project()["pattern"]["events"], [])
        self.run_cli("assign", "song1", "--bank", "1", "--pad", "2", "--clear")
        self.assertEqual(self.project()["overrides"], {})

    def test_pattern_bars_only_and_errors(self):
        self.run_cli("pattern", "song1", "set", "--steps", "0:0,60", "--bars", "4")
        self.run_cli("pattern", "song1", "set", "--bars", "1")
        self.assertEqual([e["pad"] for e in self.project()["pattern"]["events"]], [0])  # the hit at step 60 fell off
        _, err = self.run_cli("pattern", "song1", "set", "--steps", "0:99", code=1)
        self.assertIn("outside the loop", err[0])
        _, err = self.run_cli("pad", "song1", "3", code=1)
        self.assertIn("nothing to change", err[0])
        _, err = self.run_cli("show", "ghost", code=1)
        self.assertIn("404", err[0])

    def test_regrid_play_hit_export(self):
        out, _ = self.run_cli("regrid", "song1", "--grid", "1/8", "--mult", "2")
        self.assertEqual(json.loads(out[0])["grid"], "1/8")
        self.assertEqual(self.calls[0]["bpm_mult"], 2)
        out, err = self.run_cli("hit", "song1", "5", "--velocity", "0.5")
        self.assertEqual(json.loads(out[0])["listeners"], 0)
        self.run_cli("play", "song1", "pattern")
        _, err = self.run_cli("play", "song1", "stop")
        self.assertIn("nothing is playing", err[0])
        out, _ = self.run_cli("export", "song1", "sfz", "--bank", "1")
        self.assertEqual(json.loads(out[0])["bank"], 1)

    def test_unreachable_server_message(self):
        out, err = [], []
        rc = choppad.run_verb(["songs", "--server", "http://127.0.0.1:9"], out=out.append, err=err.append)
        self.assertEqual(rc, 1)
        self.assertIn("cannot reach the ChopPad server", err[0])

    def test_text_views(self):
        d = cc.Client(self.url).song("song1")
        rows = cc.chop_table(d, 2)
        self.assertEqual(len(rows), 8)  # short last bank
        self.assertEqual(rows[0], {"pad": 0, "label": "9.1", "start": 16.0, "end": 16.5, "custom": False})
        txt = cc.pattern_text({"bars": 1, "events": [{"beat": 1, "pad": 0, "bank": 0, "vel": 0.5, "pass": -1},
                                                     {"beat": 0.3, "pad": 1, "bank": 0, "vel": 1, "pass": -1}]}, 4)
        self.assertIn("p00  .... x... .... ....", txt)
        self.assertIn("1 hits (x soft", txt)
        self.assertIn("1 hits off the grid", txt)
        self.assertEqual(len(txt.splitlines()), 2 + 16 + 1)


if __name__ == "__main__":
    unittest.main()
