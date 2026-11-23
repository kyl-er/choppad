"""CHOP-13 step D, item 8: the DAW bridge with a mega bank. The Hydrogen kit and song are built from the mega bank: kept pads from their
sample files at their own gain, sound and pitch, free pads from the source song, the mega bank's tempo, the pattern's own bar length.
No Hydrogen is started: the structural checks read the kit and the .h2song the bridge writes (the real-DAW tests are opt-in, in
components/daw/tests with CHOPPAD_DAW=1). Synthetic songs, temporary media root."""
import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_mega_d import DRig  # noqa: E402
import test_mega as TM  # noqa: E402

import math, shutil, wave, xml.etree.ElementTree as ET  # noqa: E402
import choppad_export as X  # noqa: E402
import daw_project as DP  # noqa: E402
import hydrogen  # noqa: E402
import mega as M  # noqa: E402


class MegaBridge(DRig):
    def build(self, mid, bank=0, **settings):
        doc = M.read(self.root, mid)
        t = M.Target(self.root, doc)
        return DP.build_mega_project(self.root, doc, t.chops, t.media_dir, os.path.join(self.tmp, "kits"), bank=bank, **settings), doc

    def write_song(self, project):
        """The bridge's own writer (kit install + .h2song) on a private state folder; nothing is launched."""
        b = hydrogen.HydrogenBridge(os.path.join(self.tmp, "daw-state"))
        path, info = b._write_song(project)
        return b, path, info, ET.parse(path).getroot()

    def test_kit_and_song_are_built_from_the_mega_bank(self):
        mid = self.new_mega("A", "Break bank")
        self.acts(mid, *self.hits(0, 1, 2), {"type": "tempo.set", "bpm": 93, "follow": False},
                  {"type": "pad.set", "pad": 0, "params": {"gainDb": -6, "pitch": 2}},
                  {"type": "pattern.load", "pattern": {"bars": 2, "beatsPerBar": 3, "events": [
                      {"beat": 0, "pad": 0, "bank": 0, "vel": 1, "pass": 1}, {"beat": 1, "pad": 1, "bank": 0, "vel": 0.5, "pass": 1},
                      {"beat": 5.5, "pad": 7, "bank": 0, "vel": 1, "pass": 1}, {"beat": 4, "pad": 2, "bank": 0, "vel": 1, "pass": 1}]}})
        self.switch(mid, "B")     # pads 0, 1, 2 and 7 (the pattern hits them) are kept from A, pad 0 with its own sound; the rest show B
        kept = M.read(self.root, mid)["project"]["overrides"]["0:0"]
        self.assertTrue(kept["kept"] and kept["gain"] > 1.7)
        proj, doc = self.build(mid)
        self.assertEqual((proj.song_id, proj.bpm, proj.beats_per_bar, proj.bank), (mid, 93.0, 3, 0))   # the mega bank's tempo and the pattern's own bars
        self.assertIn("Break bank", proj.kit_name)
        self.assertEqual([c is not None for c in proj.chops], [True] * 16)            # 4 kept + B's chops: all 16 pads sound
        b, path, info, root = self.write_song(proj)
        self.assertEqual(float(root.find("bpm").text), 93.0)
        self.assertEqual(root.find("patternList/pattern/size").text, str(2 * 3 * 48))     # 2 bars of 3 beats, 48 ticks per quarter
        insts = root.findall("instrumentList/instrument")
        self.assertEqual(len(insts), 16)
        want_gain = X.hydrogen_gain(-6 + 20 * math.log10(kept["gain"]))
        self.assertAlmostEqual(float(insts[0].find("gain").text), want_gain, places=4)    # its own gain plus the level it played at
        self.assertEqual(float(insts[0].find("pitchOffset").text), 2.0)                   # its own pitch
        g1 = M.read(self.root, mid)["project"]["overrides"]["0:1"]["gain"]
        self.assertAlmostEqual(float(insts[1].find("gain").text), X.hydrogen_gain(20 * math.log10(g1)), places=4)
        self.assertEqual(float(insts[9].find("gain").text), 1.0)                          # a free pad: no level of its own
        # the notes: positions in ticks, velocity, only hits on the exported bank
        notes = [(int(n.find("position").text), float(n.find("velocity").text), int(n.find("instrument").text)) for n in root.findall("patternList/pattern/noteList/note")]
        self.assertEqual(sorted(notes), [(0, 1.0, 0), (48, 0.5, 1), (192, 1.0, 2), (264, 1.0, 7)])
        # the kit: kept pads play their sample files, free pads the source song
        kit = os.path.join(b.kits, proj.kit_name)
        sample = TM.read_wav(os.path.join(self.root, "_mega", mid, "samples", os.path.basename(kept["src"])))
        self.assertEqual(TM.read_wav(os.path.join(kit, "chop_00.wav"))[2], sample[2])
        b_pad9 = {p["pad"]: p for p in X.bank_pads(os.path.join(self.root, "B"), 0, {}, [])}[9]
        self.assertEqual(TM.read_wav(os.path.join(kit, "chop_09.wav"))[2], X.pad_pcm(b_pad9, {})[3])
        kept7 = M.read(self.root, mid)["project"]["overrides"]["0:7"]
        self.assertEqual(TM.read_wav(os.path.join(kit, "chop_07.wav"))[2], TM.read_wav(os.path.join(self.root, "_mega", mid, "samples", os.path.basename(kept7["src"])))[2])
        # the same project twice is the same kit (content-addressed): nothing is rendered again
        self.assertEqual(self.build(mid)[0].kit_name, proj.kit_name)

    def test_without_a_source_song_and_with_no_tempo_set(self):
        mid = self.new_mega("A")
        self.acts(mid, *self.hits(0, 1))
        proj, _ = self.build(mid)
        self.assertEqual((proj.bpm, proj.beats_per_bar), (120.0, 4))                       # the source's own tempo while none is set
        self.switch(mid, "B")
        self.assertEqual(M.read(self.root, mid)["project"]["tempo"], {"bpm": 120.0, "follow": False})   # §15: written on the first switch
        self.post(f"/api/mega/{mid}/source", {"song": None})
        proj, _ = self.build(mid)
        self.assertEqual(sum(c is not None for c in proj.chops), 2)                        # only the kept pads
        self.assertEqual(proj.bpm, 120.0)
        b, path, info, root = self.write_song(proj)
        self.assertEqual(len(root.findall("instrumentList/instrument")), 16)               # always 16 instruments (empty pads are silent)

    def test_two_mega_banks_on_one_song_make_two_projects(self):
        x, y = self.new_mega("A", "X"), self.new_mega("A", "Y")
        self.acts(x, {"type": "slice.set", "bank": 0, "pad": 0, "start": 1.0, "end": 1.5}, {"type": "tempo.set", "bpm": 80, "follow": False})
        self.acts(y, {"type": "slice.set", "bank": 0, "pad": 0, "start": 2.0, "end": 2.5}, {"type": "tempo.set", "bpm": 150, "follow": False})
        px, _ = self.build(x)
        py, _ = self.build(y)
        self.assertEqual((px.song_id, py.song_id, px.bpm, py.bpm), (x, y, 80.0, 150.0))
        self.assertNotEqual(px.kit_name, py.kit_name)
        self.assertNotEqual(TM.read_wav(px.chops[0].path)[2], TM.read_wav(py.chops[0].path)[2])

    def test_an_old_pattern_without_its_own_bar_length_reads_the_chop_table(self):
        mid = self.new_mega("A")
        self.acts(mid, {"type": "pattern.load", "pattern": {"bars": 2, "events": [{"beat": 0, "pad": 0, "bank": 0, "vel": 1, "pass": 1}]}})
        self.assertNotIn("beatsPerBar", M.read(self.root, mid)["project"]["pattern"])
        self.assertEqual(self.build(mid)[0].beats_per_bar, 4)


if __name__ == "__main__":
    unittest.main()
