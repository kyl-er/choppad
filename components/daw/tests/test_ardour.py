"""ArdourBridge: layout maths, session-XML injection, and the build flow with a fake runner (no Ardour is started)."""
import os, re, shutil, sys, tempfile, unittest, xml.etree.ElementTree as ET
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from support import FakeProc, fake_project  # noqa: E402
import ardour as A  # noqa: E402

SESSION_XML = """<?xml version="1.0" encoding="UTF-8"?>
<Session version="7003" name="s" sample-rate="48000" id-counter="100">
  <Sources/>
  <Regions/>
  <Routes>
%s
  </Routes>
  <Playlists>
%s
  </Playlists>
</Session>
"""


def session_with_tracks(*names):
    routes, lists = [], []
    for i, n in enumerate(names):
        routes.append(f'    <Route id="{10 + i}" name="{n}" audio-playlist="{50 + i}"/>')
        lists.append(f'    <Playlist id="{50 + i}" name="{n}.1" type="audio" orig-track-id="{10 + i}"/>')
    return SESSION_XML % ("\n".join(routes), "\n".join(lists))


def write(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


class Layout(unittest.TestCase):
    def test_superclock(self):
        self.assertEqual(A.SUPERCLOCK // A.SESSION_RATE, 5880)         # whole ticks per sample at 48 kHz
        self.assertEqual(A.superclock(1.0), 282240000)
        self.assertEqual(A.superclock(0.5), 141120000)

    def test_hits_repeat_per_loop_at_the_right_seconds(self):
        ev = [{"beat": 0, "pad": 0, "bank": 0, "vel": 1}, {"beat": 1.5, "pad": 2, "bank": 0, "vel": 0.5},
              {"beat": 3, "pad": 2, "bank": 1, "vel": 1}]                  # other bank: not in this kit
        p = fake_project(pattern={"bars": 2, "events": ev}, bpm=120.0, beats_per_bar=4)
        hits, total = A.hit_layout(p, loops=3)
        self.assertAlmostEqual(total, 3 * 8 * 0.5)                         # 3 loops of 2 bars of 4 beats at 0.5 s
        self.assertEqual(hits[0], [(0.0, 1.0), (4.0, 1.0), (8.0, 1.0)])
        self.assertEqual([(round(s, 6), v) for s, v in hits[2]], [(0.75, 0.5), (4.75, 0.5), (8.75, 0.5)])
        self.assertEqual(sorted(hits), [0, 2])

    def test_invalid_and_empty_pad_hits_dropped(self):
        ev = [{"beat": 9, "pad": 0, "bank": 0}, {"beat": 0, "pad": 20, "bank": 0}, {"beat": 0, "pad": 15, "bank": 0},
              {"beat": "x", "pad": 1, "bank": 0}, {"beat": 0, "pad": 1, "bank": 0, "vel": 0}]
        p = fake_project(pattern={"bars": 2, "events": ev}, empty_from=15)
        hits, _ = A.hit_layout(p, 1)
        self.assertEqual(sorted(hits), [1])
        self.assertEqual(hits[1], [(0.0, 0.05)])                           # velocity floor: never a silent region


class InjectAudio(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="choppad-ard-xml-")
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        self.f = os.path.join(self.d, "s.ardour")

    def test_sources_regions_and_playlist_entries(self):
        write(self.f, session_with_tracks("01 Pad", "02 Pad"))
        tracks = [{"name": "01 Pad", "wav": "/x/chop_00.wav", "frames": 48000, "channels": 2, "hits": [(0.0, 1.0), (2.5, 0.5)]},
                  {"name": "02 Pad", "wav": "/x/chop_01.wav", "frames": 24000, "channels": 1, "hits": [(1.0, 1.0)]}]
        self.assertEqual(A.inject_audio(self.f, tracks), 3)
        root = ET.parse(self.f).getroot()
        srcs = root.findall("Sources/Source")
        self.assertEqual([(s.get("origin"), s.get("channel")) for s in srcs],
                         [("/x/chop_00.wav", "0"), ("/x/chop_00.wav", "1"), ("/x/chop_01.wav", "0")])
        whole = root.findall("Regions/Region")
        self.assertEqual([r.get("whole-file") for r in whole], ["1", "1"])
        self.assertEqual(whole[0].get("length"), f"a{48000 * 5880}@a0")
        self.assertEqual((whole[0].get("source-0"), whole[0].get("source-1")), (srcs[0].get("id"), srcs[1].get("id")))
        pl = root.findall("Playlists/Playlist")[0].findall("Region")
        self.assertEqual([r.get("position") for r in pl], ["a0", f"a{int(2.5 * 282240000)}"])
        self.assertEqual(pl[1].get("length"), f"a{48000 * 5880}@a{int(2.5 * 282240000)}")
        self.assertEqual([r.get("scale-amplitude") for r in pl], ["1.0", "0.5"])
        self.assertEqual([r.get("layering-index") for r in pl], ["0", "1"])
        self.assertEqual(len(root.findall("Playlists/Playlist")[1].findall("Region")), 1)
        ids = [e.get("id") for e in root.iter() if e.tag in ("Source", "Region") and e.get("id")]
        self.assertEqual(len(ids), len(set(ids)))                          # unique ids
        self.assertGreater(int(root.get("id-counter")), max(map(int, ids)))  # and the counter is past them

    def test_missing_track_is_an_error(self):
        write(self.f, session_with_tracks("01 Pad"))
        with self.assertRaises(RuntimeError):
            A.inject_audio(self.f, [{"name": "nope", "wav": "/x/a.wav", "frames": 1, "channels": 2, "hits": []}])

    def test_hits_are_sorted_by_time(self):
        write(self.f, session_with_tracks("01 Pad"))
        A.inject_audio(self.f, [{"name": "01 Pad", "wav": "/x/a.wav", "frames": 480, "channels": 2, "hits": [(3.0, 1), (1.0, 1)]}])
        pos = [r.get("position") for r in ET.parse(self.f).getroot().find("Playlists/Playlist").findall("Region")]
        self.assertEqual(pos, [f"a{A.superclock(1.0)}", f"a{A.superclock(3.0)}"])


class LuaText(unittest.TestCase):
    def test_setup_script(self):
        s = A.lua_setup(["01 Pad", 'we"ird; os.exit()'], 101.0361, 3)
        self.assertIn('"01 Pad"', s)
        self.assertNotIn('os.exit()', s.split("for _, n in ipairs")[1].split("end")[0])   # names are sanitised, not code
        self.assertIn("Temporal.Tempo(101.0361, 101.0361, 4)", s)
        self.assertIn("Temporal.Meter(3, 4)", s)
        self.assertIn('s:save_state("")', s)

    def test_finish_script(self):
        s = A.lua_finish(9.5)
        self.assertIn("set_session_extents", s)
        self.assertIn("9.5", s)


class FakeRunner:
    """Pretends to be ardour9-new_session / ardour9-lua / ardour9-export: creates the files the real tools would."""
    def __init__(self):
        self.calls = []

    def __call__(self, argv, **kw):
        self.calls.append((argv, kw))
        tool = os.path.basename(argv[0])
        out = ""
        if tool == "ardour9-new_session":
            d, name = argv[-2], argv[-1]
            os.makedirs(d)
            write(os.path.join(d, name + ".ardour"), session_with_tracks())
            out = f"Created session in '{d}'"
        elif tool == "ardour9-lua" and argv[1].endswith("setup.lua"):
            with open(argv[1]) as sf:
                names = re.search(r"ipairs\(\{(.*?)\}\)", sf.read()).group(1)
            names = re.findall(r'"([^"]*)"', names)
            f = os.path.join(argv[2], argv[3] + ".ardour")
            write(f, session_with_tracks(*names))
            out = "setup ok"
        elif tool == "ardour9-lua":
            out = "finish ok"
        elif tool == "ardour9-export":
            open(argv[argv.index("-o") + 1], "wb").close()
        return type("R", (), {"returncode": -6 if tool != "ardour9-export" else 0, "stdout": out, "stderr": "FATAL: exception not rethrown"})()


def fake_resample(src, dst, rate=48000):
    with open(dst, "wb") as f:
        f.write(b"RIFF")
    return 4800


class BuildSession(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="choppad-ard-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.run_ = FakeRunner()
        self.popen_calls = []

        def popen(cmd, **kw):
            self.popen_calls.append((cmd, kw))
            self.proc = FakeProc(777)
            return self.proc
        self.b = A.ArdourBridge(self.tmp, loops=2, run=self.run_, popen=popen, which=lambda n: "/usr/bin/" + n, resample=fake_resample)
        ev = [{"beat": 0, "pad": 0, "bank": 0, "vel": 1}, {"beat": 1, "pad": 4, "bank": 0, "vel": 1}]
        self.project = fake_project(pattern={"bars": 1, "events": ev}, bpm=120.0)

    def test_build_flow_and_private_config(self):
        info = self.b.build_session(self.project)
        tools = [os.path.basename(c[0][0]) for c in self.run_.calls]
        self.assertEqual(tools, ["ardour9-new_session", "ardour9-lua", "ardour9-lua"])
        self.assertEqual(self.run_.calls[0][0][1:3], ["-s", "48000"])
        for _, kw in self.run_.calls:     # never the owner's ~/.config/ardour9
            self.assertTrue(kw["env"]["XDG_CONFIG_HOME"].startswith(self.tmp))
            self.assertTrue(kw["env"]["XDG_CACHE_HOME"].startswith(self.tmp))
        self.assertEqual((info["tracks"], info["regions"]), (2, 4))      # 2 pads x 2 loops
        self.assertAlmostEqual(info["seconds"], 4.0)
        self.assertTrue(os.path.isfile(info["file"]))
        self.assertEqual(os.listdir(os.path.join(self.tmp, "ardour", "sessions")), [info["name"]])   # staging dir cleaned up
        root = ET.parse(info["file"]).getroot()
        origins = {s.get("origin") for s in root.findall("Sources/Source")}
        self.assertEqual(origins, {os.path.join(info["dir"], "chops", "chop_00.wav"), os.path.join(info["dir"], "chops", "chop_04.wav")})
        self.assertEqual({r.get("name") for r in root.findall("Routes/Route")}, {"01 Pad", "05 Pad"})

    def test_same_content_is_reused(self):
        a = self.b.build_session(self.project)
        n = len(self.run_.calls)
        b = self.b.build_session(self.project)
        self.assertTrue(b["reused"])
        self.assertEqual((len(self.run_.calls), a["file"]), (n, b["file"]))
        self.project.bpm = 99.0          # tempo is part of the session: a new one is built
        c = self.b.build_session(self.project)
        self.assertNotEqual(a["name"], c["name"])

    def test_empty_pattern_lays_out_the_chops(self):
        p = fake_project(pattern={"bars": 2, "events": []}, empty_from=3)
        info = self.b.build_session(p)
        self.assertEqual((info["tracks"], info["regions"]), (3, 3))
        self.assertAlmostEqual(info["seconds"], 1.5)    # 3 chops, one beat each at 120 BPM

    def test_tool_failure_is_reported_and_leaves_no_session(self):
        def bad(argv, **kw):
            return type("R", (), {"returncode": 3, "stdout": "ERROR: load_session failed", "stderr": ""})()
        self.b._run = bad
        with self.assertRaises(RuntimeError) as cm:
            self.b.build_session(self.project)
        self.assertIn("ardour9-new_session failed", str(cm.exception))
        sessions = os.path.join(self.tmp, "ardour", "sessions")
        self.assertEqual(os.listdir(sessions) if os.path.isdir(sessions) else [], [])

    def test_launch_opens_the_gui_and_transport_is_honestly_unimplemented(self):
        info = self.b.launch(self.project)
        cmd, kw = self.popen_calls[0]
        self.assertEqual(cmd[0], "ardour9")
        self.assertEqual(cmd[-1], info["file"])
        self.assertTrue(kw["start_new_session"])
        self.assertEqual(info["pid"], 777)
        with self.assertRaises(NotImplementedError) as cm:
            self.b.transport("play")
        self.assertIn("Preferences", str(cm.exception))
        with self.assertRaises(NotImplementedError):     # an open Ardour cannot be told to reload
            self.b.push(self.project)
        self.b.close()
        self.assertTrue(self.proc.terminated)

    def test_render_uses_ardour9_export(self):
        out = os.path.join(self.tmp, "r.wav")
        self.b.render(self.project, out)
        argv = self.run_.calls[-1][0]
        self.assertEqual(os.path.basename(argv[0]), "ardour9-export")
        self.assertEqual(argv[argv.index("-o") + 1], out)

    def test_not_installed(self):
        b = A.ArdourBridge(self.tmp, which=lambda n: None)
        self.assertFalse(b.available())
        with self.assertRaises(RuntimeError):
            b.build_session(self.project)


if __name__ == "__main__":
    unittest.main()
