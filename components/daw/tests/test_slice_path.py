"""CHOP-11: one pad-to-audio path for the Export button and the DAW bridge, Hydrogen kit quality (mute group, gain, pitch,
swing, schema validity) and the safety rules for every command line the bridges build. No DAW is started."""
import json, os, shutil, subprocess, sys, tempfile, unittest, wave, xml.etree.ElementTree as ET
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from support import FakeOsc, FakeProc, fake_project  # noqa: E402
from _common import click_track, load_fixture  # noqa: E402
import choppad_export as X  # noqa: E402
import daw_project  # noqa: E402
import h2song  # noqa: E402
import hydrogen as H  # noqa: E402
import ardour as A  # noqa: E402
from choppad_stretch import stretch_slice  # noqa: E402

NS = {"h": "http://www.hydrogen-music.org/drumkit"}
XSD = "/usr/share/hydrogen/data/xsd/drumkit.xsd"


def wav_bytes(path):
    with wave.open(path) as w:
        return w.getparams()[:3], w.readframes(w.getnframes())


class TwoSongs(unittest.TestCase):
    """songA (bank 0 of the contract sample chops) and songB; A's project has a stretched pad, a pad cut from B, a custom
    slice, and per-pad gain and pitch."""

    @classmethod
    def setUpClass(cls):
        cls.root = tempfile.mkdtemp(prefix="choppad-slice-path-")
        cls.addClassCleanup(shutil.rmtree, cls.root, ignore_errors=True)
        cls.dirs = {}
        for sid, bpm in (("songA", 120), ("songB", 90)):
            d = os.path.join(cls.root, sid)
            os.makedirs(d)
            click_track(os.path.join(d, "audio.wav"), bpm)
            json.dump({"id": sid, "title": f"Song {sid[-1]}", "duration_s": 20.0}, open(os.path.join(d, "meta.json"), "w"))
            json.dump(load_fixture("chops"), open(os.path.join(d, "chops.json"), "w"))
            cls.dirs[sid] = d
        a = cls.dirs["songA"]
        cls.st = stretch_slice(a, 2.0, 2.5, 2.0)                 # 0.5 s slice stretched to 1.0 s
        cls.stb = stretch_slice(cls.dirs["songB"], 1.0, 1.7, 0.5)
        cls.project = {
            "pads": [{"gainDb": -6.0 if p == 1 else 0.0, "pitch": 7.0 if p == 4 else 0.0} for p in range(16)],
            "overrides": {
                "0:1": {"start": 2.0, "end": 2.5, "stretch": {"ratio": 2.0, "url": f"/media/songA/stretch/{cls.st['file']}"}},
                "0:4": {"start": 3.0, "end": 3.6, "song": "songB"},
                "0:5": {"start": 5.0, "end": 5.25},
                "0:6": {"start": 1.0, "end": 1.7, "song": "songB", "stretch": {"ratio": 0.5, "url": f"/media/songB/stretch/{cls.stb['file']}"}},
            },
            "pattern": {"bars": 1, "events": [{"beat": 0, "pad": 1, "bank": 0, "vel": 1}, {"beat": 0.25, "pad": 4, "bank": 0, "vel": 1},
                                              {"beat": 0.5, "pad": 5, "bank": 0, "vel": 1}]},
            "fx": {},
        }
        json.dump(cls.project, open(os.path.join(a, "project.json"), "w"))

    def build(self, **kw):
        out = tempfile.mkdtemp(prefix="choppad-slice-kits-")
        self.addCleanup(shutil.rmtree, out, ignore_errors=True)
        return daw_project.build_project(self.dirs["songA"], out, **kw)

    def test_bridge_kit_equals_the_export_path_for_stretched_and_other_song_pads(self):
        p = self.build(bank=0)
        # the Export button's path: bank_pads(project overrides) -> render_bank_kit
        exp = tempfile.mkdtemp(prefix="choppad-slice-export-")
        self.addCleanup(shutil.rmtree, exp, ignore_errors=True)
        X.render_bank_kit(X.bank_pads(self.dirs["songA"], 0, self.project["overrides"]), exp, "x")
        for pad in (1, 4, 5, 6, 0, 9):
            self.assertEqual(wav_bytes(p.chops[pad].path), wav_bytes(os.path.join(exp, f"chop_{pad:02d}.wav")), f"pad {pad + 1}")
        frames = lambda pad: len(wav_bytes(p.chops[pad].path)[1]) // 4  # noqa: E731
        self.assertAlmostEqual(frames(1) / 44100, 1.0, delta=0.003)    # the stretch rendering, not the 0.5 s song region
        self.assertAlmostEqual(frames(4) / 44100, 0.6, delta=0.001)    # cut from songB
        self.assertAlmostEqual(frames(5) / 44100, 0.25, delta=0.001)   # custom slice
        self.assertAlmostEqual(frames(6) / 44100, 0.35, delta=0.003)   # other song AND stretched
        # and it really is songB's audio: a slice at the same time of songA's audio differs
        a_only = X.bank_pads(self.dirs["songA"], 0, {"0:4": {"start": 3.0, "end": 3.6}})
        pcm_a = X.pad_pcm(next(q for q in a_only if q["pad"] == 4), {})[3]
        self.assertNotEqual(pcm_a, wav_bytes(p.chops[4].path)[1])

    def test_the_bridge_has_no_slice_logic_of_its_own(self):
        self.assertFalse(hasattr(daw_project, "effective_chops"))
        src = open(daw_project.__file__).read()
        for needle in ("chops[\"chops\"].append", "overrides.get(", "wave.open", "audio.wav"):
            self.assertNotIn(needle, src)

    def test_kit_key_follows_stretch_other_song_gain_and_pitch(self):
        a = self.build(bank=0)
        proj = json.loads(json.dumps(self.project))
        proj["pads"][1]["gainDb"] = -3.0
        b = self.build(bank=0, project=proj)
        self.assertNotEqual(a.kit_name, b.kit_name)
        proj2 = json.loads(json.dumps(self.project))
        del proj2["overrides"]["0:1"]["stretch"]
        self.assertNotEqual(a.kit_name, self.build(bank=0, project=proj2).kit_name)
        proj3 = json.loads(json.dumps(self.project))
        proj3["overrides"]["0:4"].pop("song")
        self.assertNotEqual(a.kit_name, self.build(bank=0, project=proj3).kit_name)

    def test_gain_pitch_choke_in_the_kit_and_the_song(self):
        p = self.build(bank=0)
        self.assertEqual((p.chops[1].gain_db, p.chops[4].pitch), (-6.0, 7.0))
        root = ET.parse(os.path.join(p.kit_dir, "drumkit.xml")).getroot()
        insts = root.findall("h:instrumentList/h:instrument", NS)
        self.assertEqual(len(insts), 16)
        by_id = {int(i.findtext("h:id", namespaces=NS)): i for i in insts}
        self.assertAlmostEqual(float(by_id[1].findtext("h:gain", namespaces=NS)), 10 ** (-6 / 20), places=4)
        self.assertEqual(float(by_id[4].findtext("h:pitchOffset", namespaces=NS)), 7.0)
        self.assertEqual(float(by_id[0].findtext("h:gain", namespaces=NS)), 1.0)
        self.assertEqual({i.findtext("h:muteGroup", namespaces=NS) for i in insts}, {"0"})   # choke on (default): one group
        song = tempfile.mktemp(suffix=".h2song")
        self.addCleanup(lambda: os.path.exists(song) and os.remove(song))
        h2song.write_song(song, title="t", bpm=120, beats_per_bar=4, wavs=[os.path.basename(w) if w else None for w in p.wavs()],
                          pattern=p.pattern, empty_wav="empty.wav", gains_db=[c.gain_db if c else 0 for c in p.chops],
                          pitches=[c.pitch if c else 0 for c in p.chops])
        si = ET.parse(song).getroot().findall("instrumentList/instrument")
        self.assertAlmostEqual(float(si[1].findtext("gain")), 10 ** (-6 / 20), places=4)
        self.assertEqual(float(si[4].findtext("pitchOffset")), 7.0)

    def test_choke_off_gives_no_mute_group(self):
        p = self.build(bank=0, choke=False)
        root = ET.parse(os.path.join(p.kit_dir, "drumkit.xml")).getroot()
        self.assertEqual({i.text for i in root.iter("{%s}muteGroup" % NS["h"])}, {"-1"})
        self.assertFalse(p.choke)
        self.assertNotEqual(p.kit_name, self.build(bank=0).kit_name)

    def test_gain_and_pitch_units(self):
        self.assertEqual(X.hydrogen_gain(0), 1.0)
        self.assertEqual(X.hydrogen_gain(100), 5.0)       # Hydrogen's gain knob stops at 5
        self.assertEqual(X.hydrogen_gain(-200), 0.0)
        self.assertEqual(X.hydrogen_pitch(30), 24.0)
        self.assertEqual(X.hydrogen_pitch(-7), -7.0)

    @unittest.skipUnless(shutil.which("xmllint") and os.path.isfile(XSD), "needs xmllint and Hydrogen's drumkit.xsd")
    def test_kit_is_valid_against_the_hydrogen_schema(self):
        p = self.build(bank=0)
        r = subprocess.run(["xmllint", "--noout", "--schema", XSD, os.path.join(p.kit_dir, "drumkit.xml")], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)


class Swing(unittest.TestCase):
    EV = [{"beat": b, "pad": 0, "bank": 0, "vel": 1} for b in (0, 0.25, 0.5, 0.75, 1.0, 1.125)]

    def positions(self, **kw):
        root, _ = h2song.build_song(title="t", bpm=120, beats_per_bar=4, wavs=[f"c{i}.wav" for i in range(16)],
                                    pattern={"bars": 1, "events": self.EV}, empty_wav="e.wav", **kw)
        return [int(n.findtext("position")) for n in root.findall("patternList/pattern/noteList/note")]

    def test_swing_delays_odd_sixteenths_like_the_browser_sequencer(self):
        self.assertEqual(self.positions(), [0, 12, 24, 36, 48, 54])
        # swing 0.5 of a 1/16 step = 6 ticks later on steps 1 and 3; the off-grid 1.125 is untouched
        self.assertEqual(self.positions(swing=0.5, quant="1/16"), [0, 18, 24, 42, 48, 54])
        # 1/8 quantize: odd 1/8 steps (beats 0.5, 1.5 ...) move by swing * 0.5 beat = 12 ticks
        self.assertEqual(self.positions(swing=0.5, quant="1/8"), [0, 12, 36, 36, 48, 54])
        self.assertEqual(self.positions(swing=0.5, quant="off"), [0, 12, 24, 36, 48, 54])

    def test_swung_beat_matches_the_js(self):
        self.assertEqual(daw_project.swung_beat(0.25, 0.25, 0.5), 0.375)
        self.assertEqual(daw_project.swung_beat(0.5, 0.25, 0.5), 0.5)
        self.assertEqual(daw_project.swung_beat(0.3, 0.25, 0.5), 0.3)
        self.assertEqual(daw_project.swung_beat(0.25, 0, 0.5), 0.25)

    def test_swing_reaches_the_song_through_the_bridge(self):
        tmp = tempfile.mkdtemp(prefix="choppad-swing-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        p = fake_project(pattern={"bars": 1, "events": [{"beat": 0.25, "pad": 0, "bank": 0, "vel": 1}]}, wav_dir=os.path.join(tmp, "k"))
        p.swing, p.quant = 0.5, "1/16"
        b = H.HydrogenBridge(tmp, which=lambda n: "/usr/bin/" + n)
        os.makedirs(p.kit_dir)
        open(os.path.join(p.kit_dir, "drumkit.xml"), "w").write("<drumkit_info/>")
        for i in range(16):
            open(os.path.join(p.kit_dir, f"chop_{i:02d}.wav"), "wb").write(b"x")
        path, _ = b._write_song(p)
        self.assertEqual(int(ET.parse(path).getroot().findtext("patternList/pattern/noteList/note/position")), 18)

    def test_swing_reaches_ardour_hits(self):
        p = fake_project(pattern={"bars": 1, "events": [{"beat": 0.25, "pad": 0, "bank": 0, "vel": 1}]})
        p.swing = 0.5
        hits, _ = A.hit_layout(p, 1)
        self.assertAlmostEqual(hits[0][0][0], 0.375 * 0.5)    # 120 BPM: 0.5 s per beat


class SafetyOfCommandLines(unittest.TestCase):
    """The owner's own Hydrogen and Ardour configs must never be touched: every hydrogen and h2cli command line the
    bridges build carries `--config <bridge's private copy>`, every Ardour tool runs with a private XDG_CONFIG_HOME."""

    OWNER_H2 = os.path.expanduser("~/.hydrogen")
    OWNER_ARDOUR = os.path.expanduser("~/.config/ardour9")

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="choppad-safe-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.calls = []
        kit = os.path.join(self.tmp, "kits", "ChopPad Test Song B1 abcd1234")
        os.makedirs(kit)
        for i in range(16):
            open(os.path.join(kit, f"chop_{i:02d}.wav"), "wb").write(b"x")
        open(os.path.join(kit, "drumkit.xml"), "w").write("<drumkit_info/>")
        self.project = fake_project(pattern={"bars": 1, "events": [{"beat": 0, "pad": 0, "bank": 0, "vel": 1}]}, wav_dir=kit)

    def rec_popen(self, cmd, **kw):
        self.calls.append((list(cmd), kw))
        if "-L" in cmd and "-O" in cmd:
            with open(cmd[cmd.index("-L") + 1], "w") as f:
                f.write("Osc server started\n")
        return FakeProc()

    def rec_run(self, argv, **kw):
        self.calls.append((list(argv), kw))
        if "-o" in argv and os.path.basename(argv[0]) in ("h2cli", "ardour9-export"):
            open(argv[argv.index("-o") + 1], "wb").close()
        tool = os.path.basename(argv[0])
        if tool == "ardour9-new_session":
            os.makedirs(argv[-2])
            open(os.path.join(argv[-2], argv[-1] + ".ardour"), "w").write("<Session/>")
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    def assert_hydrogen_call_is_private(self, cmd, bridge_dir):
        self.assertIn("--config", cmd, cmd)
        conf = os.path.abspath(cmd[cmd.index("--config") + 1])
        self.assertTrue(conf.startswith(bridge_dir + os.sep), f"{conf} is not inside the bridge folder")
        self.assertFalse(conf.startswith(self.OWNER_H2), conf)
        self.assertNotEqual(conf, os.path.abspath(H.OWNER_CONF))
        self.assertIn("--user-data", cmd, "kits and songs must not land in the owner's data folder either")
        self.assertTrue(os.path.abspath(cmd[cmd.index("--user-data") + 1]).startswith(bridge_dir + os.sep))

    def test_hydrogen_and_h2cli_always_get_a_private_config(self):
        base = os.path.join(self.tmp, "base.conf")
        open(base, "w").write("<hydrogen_preferences><audio_engine/></hydrogen_preferences>")
        orig_run = subprocess.run
        subprocess.run = self.rec_run           # H.render calls subprocess.run directly
        try:
            b = H.HydrogenBridge(self.tmp, popen=self.rec_popen, which=lambda n: "/usr/bin/" + n, osc_factory=lambda h, p: FakeOsc(),
                                 base_conf=base, port_free=lambda p: True, sleep=lambda s: None, env={"PATH": "/usr/bin"})
            b.render(self.project, os.path.join(self.tmp, "o.wav"))
            b.launch(self.project)
            b.close()
        finally:
            subprocess.run = orig_run
        tools = [os.path.basename(c[0][0]) for c in self.calls]
        self.assertEqual(tools, ["h2cli", "hydrogen"])
        for cmd, _ in self.calls:
            self.assert_hydrogen_call_is_private(cmd, os.path.abspath(b.dir))

    def test_every_hydrogen_command_the_bridge_module_can_build_is_covered(self):
        # a new subprocess call in hydrogen.py must come with a --config; fail loudly if someone adds one without a test here
        src = open(H.__file__).read()
        self.assertEqual(src.count("subprocess.run("), 1)
        self.assertEqual(src.count("self._popen("), 1)
        self.assertEqual(src.count('"--config"'), 2)

    def test_every_ardour_tool_runs_with_a_private_xdg_config(self):
        from test_ardour import FakeRunner, fake_resample
        runner = FakeRunner()
        b = A.ArdourBridge(self.tmp, loops=1, run=runner, popen=self.rec_popen, which=lambda n: "/usr/bin/" + n, resample=fake_resample)
        b.render(self.project, os.path.join(self.tmp, "r.wav"))
        b.launch(self.project)
        calls = runner.calls + self.calls
        self.assertGreaterEqual(len(runner.calls), 4)   # new_session, two lua scripts, export
        self.assertEqual(len(self.calls), 1)            # the GUI
        xdg = os.path.abspath(os.path.join(b.dir, "xdg"))
        for argv, kw in calls:
            self.assertTrue(os.path.basename(argv[0]).startswith("ardour9"), argv)
            self.assertIn("env", kw, f"{argv[0]} would inherit the owner's XDG_CONFIG_HOME")
            for k in ("XDG_CONFIG_HOME", "XDG_CACHE_HOME"):
                self.assertTrue(os.path.abspath(kw["env"][k]).startswith(xdg + os.sep), (argv[0], k))
            self.assertFalse(os.path.abspath(kw["env"]["XDG_CONFIG_HOME"]).startswith(self.OWNER_ARDOUR))
        b.close()


if __name__ == "__main__":
    unittest.main()
