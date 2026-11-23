"""CHOP-13 step D: DawManager keyed by drum pad mega bank (command_mega, close_key, the `close` command), on fakes (no DAW, no ALSA)."""
import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from support import FakeBackend, fake_project  # noqa: E402
import manager as M  # noqa: E402
import midi_port  # noqa: E402


class FakeBridge:
    def __init__(self, name="hydrogen"):
        self.name, self.calls = name, []

    def available(self):
        return True

    def launch(self, project):
        self.calls.append(("launch", project.song_id))
        return {"kit": project.kit_name}

    def push(self, project):
        self.calls.append(("push", project.song_id))
        return {"pushed": True}

    def transport(self, command, *, bpm=None):
        self.calls.append((command, bpm))

    def note(self, pad, vel):
        self.calls.append(("note", pad, vel))

    def close(self):
        self.calls.append(("close",))


def rig():
    h = FakeBridge()
    port = midi_port.Port(FakeBackend())
    changed = []
    m = M.DawManager("/tmp/unused", bridges={"hydrogen": lambda: h, "ardour": lambda: FakeBridge("ardour")}, midi_factory=lambda: port,
                     on_change=changed.append)
    return m, h, port, changed


def builder(bpm=101.0, calls=None):
    def build(out_root, bank, **settings):
        if calls is not None:
            calls.append((out_root, bank, settings))
        return fake_project(sid="ignored", bank=bank or 0, bpm=bpm)
    return build


class MegaKeyed(unittest.TestCase):
    def test_open_play_close_and_state_by_mega_key(self):
        m, h, port, changed = rig()
        seen = []
        r = m.command_mega("m1", builder(calls=seen), {"daw": "hydrogen", "command": "open", "bank": 2, "swing": 0.2}, extra={"song": "S"})
        self.assertEqual((r["ok"], r["mega"], r["song"], r["bank"], r["bpm"]), (True, "m1", "S", 2, 101.0))
        self.assertEqual(seen[0][1:], (2, {"swing": 0.2}))
        self.assertEqual(m.state_for("m1"), {"open": True, "daw": "hydrogen", "bank": 2})
        self.assertEqual(m.state_for("S"), {"open": False, "daw": None, "bank": None})   # nothing is held for the song
        self.assertEqual(changed, ["m1"])
        r = m.command_mega("m1", builder(), {"daw": "hydrogen", "command": "play"})
        self.assertEqual((r["daw_transport"], r["midi"]), ("ok", "start+clock"))
        m.on_transport("m1", {"command": "hit", "pad": 3, "velocity": 0.5})
        m.on_transport("m2", {"command": "hit", "pad": 4, "velocity": 1})                # another mega bank: not mirrored
        self.assertIn(("note", 3, 0.5), h.calls)
        self.assertNotIn(("note", 4, 1.0), h.calls)
        r = m.command_mega("m1", builder(), {"daw": "hydrogen", "command": "close"})
        self.assertEqual((r["ok"], r["closed"]), (True, ["hydrogen"]))
        self.assertEqual(h.calls[-1], ("close",))
        self.assertEqual(m.state_for("m1"), {"open": False, "daw": None, "bank": None})
        self.assertEqual(changed, ["m1", "m1"])
        self.assertTrue(port.backend.closed)                                              # no bridge left: the MIDI port goes too
        with self.assertRaises(M.DawError) as cm:
            m.command_mega("m1", builder(), {"daw": "hydrogen", "command": "close"})
        self.assertEqual(cm.exception.http_status, 409)
        with self.assertRaises(M.DawError) as cm:
            m.command_mega("m1", builder(), {"daw": "hydrogen", "command": "play"})
        self.assertEqual(cm.exception.http_status, 409)

    def test_close_of_another_key_leaves_the_bridge_alone_and_open_takes_over(self):
        m, h, _, changed = rig()
        m.command_mega("m1", builder(), {"daw": "hydrogen", "command": "open"})
        with self.assertRaises(M.DawError) as cm:
            m.command_mega("m2", builder(), {"daw": "hydrogen", "command": "close"})
        self.assertEqual(cm.exception.http_status, 409)
        self.assertEqual(m.state_for("m1")["open"], True)
        m.command_mega("m2", builder(bpm=88.0), {"daw": "hydrogen", "command": "open"})   # the bridge now holds m2
        self.assertEqual((m.state_for("m1")["open"], m.state_for("m2")["open"]), (False, True))
        self.assertEqual(changed[-1], "m2")
        self.assertIn("m1", changed)                                                       # the tabs of m1 learn it let go

    def test_validation_and_the_song_keyed_command_has_no_close(self):
        m, *_ = rig()
        for body in ({"daw": "nope", "command": "open"}, {"daw": "hydrogen", "command": "dance"}, "x"):
            with self.assertRaises(M.DawError) as cm:
                m.command_mega("m1", builder(), body)
            self.assertEqual(cm.exception.http_status, 400)
        self.assertIn("close", M.MEGA_COMMANDS)
        self.assertNotIn("close", M.COMMANDS)
        with self.assertRaises(M.DawError):
            m.command("song1", "/media/song1", {"daw": "hydrogen", "command": "close"})


if __name__ == "__main__":
    unittest.main()
