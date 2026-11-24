"""DawProject building and HydrogenBridge with fakes for the subprocess and OSC (no Hydrogen is started)."""
import json, os, shutil, subprocess, sys, tempfile, time, unittest, wave, xml.etree.ElementTree as ET
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from support import FakeOsc, FakeProc, fake_project, make_media  # noqa: E402
import daw_project  # noqa: E402
import hydrogen as H  # noqa: E402

BASE_CONF = """<?xml version="1.0" encoding="UTF-8"?>
<hydrogen_preferences>
 <restoreLastSong>true</restoreLastSong>
 <audio_engine>
  <audio_driver>Auto</audio_driver>
  <jack_driver>
   <jack_transport_mode>USE_JACK_TRANSPORT</jack_transport_mode>
   <jack_transport_mode_master>NO_JACK_TIME_MASTER</jack_transport_mode_master>
  </jack_driver>
  <osc_configuration>
   <oscServerPort>9000</oscServerPort>
   <oscEnabled>false</oscEnabled>
   <oscFeedbackEnabled>true</oscFeedbackEnabled>
  </osc_configuration>
 </audio_engine>
</hydrogen_preferences>
"""


def put(path, data):
    with open(path, "wb") as f:
        f.write(data)


def get(path):
    with open(path) as f:
        return f.read()


def frames(path):
    with wave.open(path) as w:
        return w.getnframes()


class BuildProject(unittest.TestCase):
    def setUp(self):
        self.media, self.song = make_media(bpm=120, pattern={"bars": 2, "events": [
            {"beat": 0, "pad": 0, "bank": 0, "vel": 1}, {"beat": 1, "pad": 1, "bank": 0, "vel": 0.5}]})
        self.out = tempfile.mkdtemp(prefix="choppad-daw-kits-")
        self.addCleanup(shutil.rmtree, self.media, ignore_errors=True)
        self.addCleanup(shutil.rmtree, self.out, ignore_errors=True)

    def test_kit_rendered_and_described(self):
        p = daw_project.build_project(self.song, self.out)
        self.assertEqual((p.song_id, p.title, p.bank, p.beats_per_bar), ("song1", "Test Song", 0, 4))
        self.assertAlmostEqual(p.bpm, 120, delta=1)
        self.assertEqual(len(p.chops), 16)
        for pad, c in enumerate(p.chops):
            self.assertEqual((c.pad, c.midi_note), (pad, 36 + pad))
            self.assertTrue(os.path.isfile(c.path))
        self.assertTrue(os.path.isfile(os.path.join(p.kit_dir, "drumkit.xml")))
        self.assertRegex(p.kit_name, r"^ChopPad Test Song B1 [0-9a-f]{8}$")
        self.assertEqual(os.path.basename(p.kit_dir), p.kit_name)
        self.assertEqual(p.pattern["bars"], 2)

    def test_same_content_reuses_the_kit_and_changes_change_the_name(self):
        a = daw_project.build_project(self.song, self.out)
        mtime = os.path.getmtime(os.path.join(a.kit_dir, "chop_00.wav"))
        b = daw_project.build_project(self.song, self.out)
        self.assertEqual((a.kit_name, os.path.getmtime(os.path.join(b.kit_dir, "chop_00.wav"))), (b.kit_name, mtime))
        proj = {"overrides": {"0:0": {"start": 0.0, "end": 0.1}}}
        c = daw_project.build_project(self.song, self.out, project=proj)
        self.assertNotEqual(a.kit_name, c.kit_name)
        d = daw_project.build_project(self.song, self.out, fx={"reverb": 0.3, "size": 0.2})
        self.assertNotEqual(a.kit_name, d.kit_name)

    def test_custom_slices_reach_the_kit(self):
        base = daw_project.build_project(self.song, self.out)
        proj = {"overrides": {"0:3": {"start": 0.0, "end": 0.1}}, "pattern": {"bars": 1, "events": []}}
        p = daw_project.build_project(self.song, self.out, project=proj)
        self.assertAlmostEqual(frames(p.chops[3].path), round(0.1 * 44100), delta=2)
        self.assertNotEqual(frames(base.chops[3].path), frames(p.chops[3].path))
        self.assertEqual(frames(base.chops[2].path), frames(p.chops[2].path))   # other pads untouched
        self.assertEqual(p.pattern, {"bars": 1, "events": []})

    def test_bad_overrides_are_skipped_and_nothing_is_mutated(self):
        # was test_effective_chops_...: the bridge has no slice logic of its own any more, it asks choppad_export.bank_pads
        import choppad_export as X
        before = get(os.path.join(self.song, "chops.json"))
        ov = {"0:1": {"start": 2, "end": 1}, "0:2": {"start": "x"}, "1:0": {"start": 0, "end": 1}}
        pads = X.bank_pads(self.song, 0, ov)
        chops = json.loads(before)
        grid = [(chops["chops"][i]["start"], chops["chops"][i]["end"]) for i in chops["banks"][0]]
        self.assertEqual([(p["start"], p["end"]) for p in pads], grid)   # nothing valid for bank 0
        self.assertEqual(get(os.path.join(self.song, "chops.json")), before)

    def test_pick_bank(self):
        ev = lambda *banks: {"events": [{"bank": b} for b in banks]}  # noqa: E731
        self.assertEqual(daw_project.pick_bank(ev(2, 2), None, 3), 2)
        self.assertEqual(daw_project.pick_bank(ev(0, 1), None, 3), 0)
        self.assertEqual(daw_project.pick_bank(ev(), None, 3), 0)
        self.assertEqual(daw_project.pick_bank(ev(2), 1, 3), 1)
        self.assertEqual(daw_project.pick_bank(ev(2), 9, 3), 2)   # clamped
        self.assertEqual(daw_project.pick_bank(ev(7), None, 3), 0)

    def test_short_last_bank_leaves_empty_slots(self):
        p = daw_project.build_project(self.song, self.out, bank=2)
        n = len(json.loads(get(os.path.join(self.song, "chops.json")))["banks"][2])
        self.assertEqual([c is not None for c in p.chops], [True] * n + [False] * (16 - n))

    def test_prune_keeps_newest(self):
        names = [f"ChopPad Test Song B1 {i:08x}" for i in range(5)]
        for i, n in enumerate(names):
            os.makedirs(os.path.join(self.out, n))
            os.utime(os.path.join(self.out, n), (1000 + i, 1000 + i))
        os.makedirs(os.path.join(self.out, "ChopPad Other B1 00000000"))
        daw_project.prune_kits(self.out, names[0])
        left = sorted(os.listdir(self.out))
        self.assertIn(names[0], left)                 # the one in use survives even though it is oldest
        self.assertIn("ChopPad Other B1 00000000", left)
        self.assertEqual(len([d for d in left if d.startswith("ChopPad Test Song")]), 4)


class FakePopen:
    """Stands in for subprocess.Popen: records the command and 'starts Hydrogen' by writing its ready line to the log."""
    def __init__(self, log_ready=True):
        self.calls, self.proc, self.log_ready = [], None, log_ready

    def __call__(self, cmd, **kw):
        self.calls.append((cmd, kw))
        if self.log_ready:
            log = cmd[cmd.index("-L") + 1]
            with open(log, "w") as f:
                f.write("(I) [OscServer::start] Osc server started. Listening on port %s\n" % cmd[cmd.index("-O") + 1])
        self.proc = FakeProc()
        return self.proc


def bridge(tmp, popen=None, **kw):
    base = os.path.join(tmp, "base.conf")
    if not os.path.exists(base):
        put(base, BASE_CONF.encode())
    popen = popen or FakePopen()
    osc = FakeOsc()
    b = H.HydrogenBridge(tmp, popen=popen, which=lambda n: "/usr/bin/" + n, osc_factory=lambda h, p: osc, base_conf=base,
                         port_free=kw.pop("port_free", lambda p: True), sleep=lambda s: None, env={"PATH": "/usr/bin"}, **kw)
    return b, popen, osc, base


class HydrogenBridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="choppad-h2-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        kit = os.path.join(self.tmp, "kits", "ChopPad Test Song B1 abcd1234")
        os.makedirs(kit)
        for i in range(16):
            put(os.path.join(kit, f"chop_{i:02d}.wav"), b"x")
        put(os.path.join(kit, "drumkit.xml"), b"<drumkit_info/>")
        self.kit = kit
        self.project = fake_project(pattern={"bars": 2, "events": [{"beat": 0, "pad": 0, "bank": 0, "vel": 1},
                                                                   {"beat": 2, "pad": 3, "bank": 0, "vel": 0.5}]},
                                    bpm=101.5, wav_dir=kit)

    def test_patch_config_never_writes_the_source(self):
        src = os.path.join(self.tmp, "owner.conf")
        put(src, BASE_CONF.encode())
        before = get(src)
        dst = os.path.join(self.tmp, "copy.conf")
        H.patch_config(src, dst, osc_port=9742)
        self.assertEqual(get(src), before)
        r = ET.parse(dst).getroot()
        self.assertEqual(r.findtext("audio_engine/osc_configuration/oscEnabled"), "true")
        self.assertEqual(r.findtext("audio_engine/osc_configuration/oscServerPort"), "9742")
        self.assertEqual(r.findtext("audio_engine/osc_configuration/oscFeedbackEnabled"), "false")
        self.assertEqual(r.findtext("audio_engine/jack_driver/jack_transport_mode"), "NO_JACK_TRANSPORT")
        self.assertEqual(r.findtext("restoreLastSong"), "false")
        self.assertEqual(r.findtext("audio_engine/audio_driver"), "Auto")   # the rest of the setup is inherited

    def test_launch_starts_own_instance_and_loads_song(self):
        b, popen, osc, base = bridge(self.tmp, headless=True, volume=0.2)
        info = b.launch(self.project)
        cmd, kw = popen.calls[0]
        self.assertEqual(cmd[0], "hydrogen")
        self.assertIn("--child", cmd)
        self.assertEqual(cmd[cmd.index("--config") + 1], b.conf)
        self.assertNotEqual(b.conf, os.path.expanduser("~/.hydrogen/hydrogen.conf"))
        self.assertEqual(cmd[cmd.index("--user-data") + 1], b.userdata)
        self.assertEqual(cmd[cmd.index("-O") + 1], str(H.DEFAULT_PORT))
        self.assertEqual(kw["env"]["QT_QPA_PLATFORM"], "offscreen")
        self.assertTrue(kw["start_new_session"])
        addrs = [a for a, _ in osc.sent]
        self.assertEqual(addrs, ["/Hydrogen/STOP", "/Hydrogen/OPEN_SONG", "/Hydrogen/BPM", "/Hydrogen/MASTER_VOLUME_ABSOLUTE"])
        self.assertEqual(osc.sent[1][1], [info["song"]])
        self.assertTrue(os.path.isabs(info["song"]))
        self.assertAlmostEqual(osc.sent[2][1][0], 101.5)
        self.assertEqual(info["notes"], 2)
        # the kit sits where --user-data looks, and the song names it
        self.assertTrue(os.path.isfile(os.path.join(b.kits, self.project.kit_name, "drumkit.xml")))
        root = ET.parse(info["song"]).getroot()
        self.assertEqual(root.findtext("instrumentList/instrument/drumkit"), self.project.kit_name)
        self.assertEqual(root.findtext("instrumentList/instrument/instrumentComponent/layer/filename"), "chop_00.wav")
        b.close()

    def test_windowed_mode_does_not_set_offscreen(self):
        b, popen, osc, base = bridge(self.tmp, headless=False)
        b.launch(self.project)
        self.assertNotIn("QT_QPA_PLATFORM", popen.calls[0][1]["env"])
        b.close()

    def test_second_launch_reuses_the_process_and_push_reloads(self):
        b, popen, osc, base = bridge(self.tmp)
        b.launch(self.project)
        osc.sent.clear()
        b.push(self.project)
        self.assertEqual(len(popen.calls), 1)
        self.assertEqual([a for a, _ in osc.sent][:2], ["/Hydrogen/STOP", "/Hydrogen/OPEN_SONG"])
        b.close()

    def test_push_while_playing_resumes_play(self):
        b, popen, osc, base = bridge(self.tmp)
        b.launch(self.project)
        b.transport("play")
        osc.sent.clear()
        b.push(self.project)
        self.assertEqual([a for a, _ in osc.sent][-1], "/Hydrogen/PLAY")
        self.assertTrue(b.playing)
        b.close()

    def test_transport_and_notes(self):
        b, popen, osc, base = bridge(self.tmp)
        with self.assertRaises(RuntimeError):
            b.transport("play")            # not open yet
        b.launch(self.project)
        osc.sent.clear()
        b.transport("play", bpm=90.0)
        b.note(3, 0.5)
        b.note(0, 5)                       # velocity clamped
        b.transport("stop")
        self.assertEqual(osc.sent, [("/Hydrogen/BPM", [90.0]), ("/Hydrogen/PLAY", []), ("/Hydrogen/NOTE_ON", [39, 0.5]),
                                    ("/Hydrogen/NOTE_ON", [36, 1.0]), ("/Hydrogen/STOP", [])])
        with self.assertRaises(ValueError):
            b.transport("rewind")
        b.close()

    def test_close_stops_only_what_it_started(self):
        b, popen, osc, base = bridge(self.tmp)
        b.launch(self.project)
        self.assertTrue(os.path.isfile(os.path.join(b.dir, "state")))
        b.close()
        self.assertTrue(popen.proc.terminated)
        self.assertTrue(osc.closed)
        self.assertFalse(os.path.exists(os.path.join(b.dir, "state")))
        with self.assertRaises(RuntimeError):
            b.transport("play")

    def test_attach_to_a_bridge_instance_left_by_another_process(self):
        # a stand-in "Hydrogen": a sleeping process whose command line contains the bridge's config path
        b0, *_ = bridge(self.tmp)
        os.makedirs(b0.dir, exist_ok=True)
        dummy = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)", b0.conf])
        self.addCleanup(lambda: (dummy.kill(), dummy.wait()))
        put(os.path.join(b0.dir, "state"), f"{dummy.pid} 9755\n".encode())
        b, popen, osc, base = bridge(self.tmp)
        b.launch(self.project)
        self.assertEqual(popen.calls, [])             # nothing new was started
        self.assertTrue(b.attached)
        self.assertEqual(b.port, 9755)
        self.assertIn("/Hydrogen/OPEN_SONG", [a for a, _ in osc.sent])
        b.close()
        self.assertIsNone(dummy.poll())               # an attached instance is never killed

    def test_stale_state_file_is_ignored(self):
        b0, *_ = bridge(self.tmp)
        os.makedirs(b0.dir, exist_ok=True)
        put(os.path.join(b0.dir, "state"), b"999999 9755\n")
        b, popen, osc, base = bridge(self.tmp)
        b.launch(self.project)
        self.assertEqual(len(popen.calls), 1)
        b.close()

    def test_picks_a_free_port_and_honours_a_wanted_one(self):
        b, popen, osc, base = bridge(self.tmp, port_free=lambda p: p >= H.DEFAULT_PORT + 2)
        b.launch(self.project)
        self.assertEqual(b.port, H.DEFAULT_PORT + 2)
        b.close()
        b2, *_ = bridge(self.tmp, osc_port=9800, port_free=lambda p: False)
        with self.assertRaises(RuntimeError):
            b2.launch(self.project)

    def test_not_installed_and_startup_failures_are_clear(self):
        b = H.HydrogenBridge(self.tmp, which=lambda n: None)
        self.assertFalse(b.available())
        with self.assertRaises(RuntimeError) as cm:
            b.launch(self.project)
        self.assertIn("not installed", str(cm.exception))
        # starts but never reports its OSC server: closed again, error mentions the log
        t = [0.0]
        popen = FakePopen(log_ready=False)
        b2, popen, osc, base = bridge(self.tmp, popen=popen, wait_s=1.0)
        b2._clock = lambda: t.__setitem__(0, t[0] + 0.3) or t[0]
        with self.assertRaises(RuntimeError) as cm:
            b2.launch(self.project)
        self.assertIn("hydrogen.log", str(cm.exception))
        self.assertTrue(popen.proc.terminated)


if __name__ == "__main__":
    unittest.main()
