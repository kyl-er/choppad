"""OSC encoding and the .h2song writer: structure, tick maths, bank filtering. Pure; no Hydrogen."""
import os, socket, struct, sys, unittest, xml.etree.ElementTree as ET
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import support  # noqa: E402,F401
import osc  # noqa: E402
import h2song  # noqa: E402


class OscEncoding(unittest.TestCase):
    def test_known_bytes(self):
        # liblo / spec example shapes: address and type tags NUL-padded to 4, args big-endian
        self.assertEqual(osc.encode("/Hydrogen/PLAY"), b"/Hydrogen/PLAY\x00\x00,\x00\x00\x00")
        self.assertEqual(osc.encode("/a", 1), b"/a\x00\x00,i\x00\x00\x00\x00\x00\x01")
        self.assertEqual(osc.encode("/a", 1.0), b"/a\x00\x00,f\x00\x00" + struct.pack(">f", 1.0))
        self.assertEqual(osc.encode("/a", "hi"), b"/a\x00\x00,s\x00\x00hi\x00\x00")
        self.assertEqual(osc.encode("/a", "four"), b"/a\x00\x00,s\x00\x00four\x00\x00\x00\x00")  # exact multiple still gets a NUL block

    def test_roundtrip_and_types(self):
        pkt = osc.encode("/Hydrogen/NOTE_ON", 36, 0.5, "ChopPad Kit é")
        addr, args = osc.decode(pkt)
        self.assertEqual((addr, args[0], args[2]), ("/Hydrogen/NOTE_ON", 36, "ChopPad Kit é"))
        self.assertAlmostEqual(args[1], 0.5)
        self.assertEqual(len(pkt) % 4, 0)

    def test_rejects_bad_input(self):
        with self.assertRaises(ValueError):
            osc.encode("no-slash")
        with self.assertRaises(TypeError):
            osc.encode("/a", True)
        with self.assertRaises(TypeError):
            osc.encode("/a", None)

    def test_client_sends_datagram(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        srv.bind(("127.0.0.1", 0))
        srv.settimeout(2)
        c = osc.OscClient("127.0.0.1", srv.getsockname()[1])
        c.send("/Hydrogen/BPM", 101.5)
        data, _ = srv.recvfrom(1024)
        c.close()
        srv.close()
        self.assertEqual(osc.decode(data)[0], "/Hydrogen/BPM")
        self.assertAlmostEqual(osc.decode(data)[1][0], 101.5)


WAVS = [f"chop_{i:02d}.wav" for i in range(16)]


def song(**kw):
    kw.setdefault("title", "T")
    kw.setdefault("bpm", 120.0)
    kw.setdefault("beats_per_bar", 4)
    kw.setdefault("wavs", WAVS)
    kw.setdefault("pattern", {"bars": 2, "events": []})
    kw.setdefault("empty_wav", "empty.wav")
    root, info = h2song.build_song(**kw)
    return root, info


class H2Song(unittest.TestCase):
    def test_tick_maths(self):
        self.assertEqual(h2song.TICKS_PER_QUARTER, 48)
        self.assertEqual(h2song.ticks(0), 0)
        self.assertEqual(h2song.ticks(0.25), 12)        # one 1/16 step
        self.assertEqual(h2song.ticks(1), 48)
        self.assertEqual(h2song.ticks(2.5), 120)
        self.assertEqual(h2song.pattern_ticks(1), 192)  # one 4/4 bar
        self.assertEqual(h2song.pattern_ticks(2), 384)
        self.assertEqual(h2song.pattern_ticks(2, 3), 288)  # 3/4
        self.assertEqual(h2song.pattern_ticks(1.5), 288)

    def test_structure(self):
        ev = [{"beat": 0, "pad": 0, "bank": 0, "vel": 1.0}, {"beat": 1.5, "pad": 3, "bank": 0, "vel": 0.5},
              {"beat": 7.75, "pad": 15, "bank": 0, "vel": 0.2}]
        root, info = song(pattern={"bars": 2, "events": ev}, bpm=101.5, kit_name="Kit X")
        self.assertEqual(root.tag, "song")
        self.assertEqual(float(root.findtext("bpm")), 101.5)
        self.assertEqual(root.findtext("mode"), "song")
        self.assertEqual(root.findtext("loopEnabled"), "true")
        insts = root.findall("instrumentList/instrument")
        self.assertEqual(len(insts), 16)
        for i, ins in enumerate(insts):
            self.assertEqual(int(ins.findtext("id")), i)
            self.assertEqual(int(ins.findtext("midiOutNote")), 36 + i)
            self.assertEqual(ins.findtext("drumkit"), "Kit X")
            self.assertEqual(ins.findtext("drumkitLookup"), "1")
            self.assertEqual(ins.findtext("instrumentComponent/layer/filename"), WAVS[i])
            self.assertEqual(ins.findtext("muteGroup"), "0")
            self.assertEqual(ins.findtext("isStopNote"), "false")
        pat = root.find("patternList/pattern")
        self.assertEqual((pat.findtext("size"), pat.findtext("denominator")), ("384", "4"))
        notes = [(int(n.findtext("position")), int(n.findtext("instrument")), float(n.findtext("velocity")), n.findtext("length"))
                 for n in pat.findall("noteList/note")]
        self.assertEqual(notes, [(0, 0, 1.0, "-1"), (72, 3, 0.5, "-1"), (372, 15, 0.2, "-1")])
        self.assertEqual(root.findtext("patternSequence/group/patternID"), "ChopPad")
        self.assertEqual(info, {"ticks": 384, "notes": 3, "dropped_other_bank": 0, "dropped_invalid": 0})

    def test_events_sorted_and_bad_ones_dropped(self):
        ev = [{"beat": 3, "pad": 1, "bank": 0}, {"beat": 1, "pad": 0, "bank": 0}, {"beat": 1, "pad": 2, "bank": 1},
              {"beat": 8, "pad": 0, "bank": 0},    # past the loop end (2 bars of 4)
              {"beat": -1, "pad": 0, "bank": 0}, {"beat": 1, "pad": 99, "bank": 0}, {"pad": 1}]
        root, info = song(pattern={"bars": 2, "events": ev})
        pos = [int(n.findtext("position")) for n in root.findall("patternList/pattern/noteList/note")]
        self.assertEqual(pos, [48, 144])
        self.assertEqual((info["notes"], info["dropped_other_bank"], info["dropped_invalid"]), (2, 1, 4))

    def test_empty_pad_gets_silent_instrument_and_no_hits(self):
        wavs = WAVS[:14] + [None, None]
        root, info = song(wavs=wavs, pattern={"bars": 1, "events": [{"beat": 0, "pad": 15, "bank": 0}, {"beat": 0, "pad": 2, "bank": 0}]})
        insts = root.findall("instrumentList/instrument")
        self.assertEqual(len(insts), 16)
        self.assertEqual(insts[15].findtext("instrumentComponent/layer/filename"), "empty.wav")
        self.assertEqual(float(insts[15].findtext("gain")), 0.0)
        self.assertEqual(info["notes"], 1)
        with self.assertRaises(ValueError):
            song(wavs=wavs, empty_wav=None)

    def test_meter_changes_pattern_size(self):
        root, _ = song(beats_per_bar=3, pattern={"bars": 4, "events": []})
        self.assertEqual(root.findtext("patternList/pattern/size"), str(4 * 3 * 48))

    def test_choke_and_loop_flags(self):
        root, _ = song(choke=False, loop=False)
        self.assertEqual(root.findtext("instrumentList/instrument/muteGroup"), "-1")
        self.assertEqual(root.findtext("loopEnabled"), "false")

    def test_write_song_is_valid_xml_and_atomic(self):
        import tempfile
        d = tempfile.mkdtemp()
        path = os.path.join(d, "s.h2song")
        info = h2song.write_song(path, title="A & B <x>", bpm=90, beats_per_bar=4, wavs=WAVS,
                                 pattern={"bars": 1, "events": [{"beat": 0, "pad": 0, "bank": 0}]}, empty_wav="e.wav")
        self.assertEqual(info["notes"], 1)
        root = ET.parse(path).getroot()
        self.assertEqual(root.findtext("name"), "A & B <x>")
        self.assertEqual(os.listdir(d), ["s.h2song"])  # no .tmp left behind

    def test_wrong_wav_count(self):
        with self.assertRaises(ValueError):
            song(wavs=WAVS[:3])


if __name__ == "__main__":
    unittest.main()
