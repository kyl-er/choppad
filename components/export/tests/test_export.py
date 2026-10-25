import json, os, re, struct, sys, tempfile, unittest, wave, xml.etree.ElementTree as ET
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from _common import *  # noqa: E402,F403
from choppad_export import export_hydrogen, export_sfz  # noqa: E402


class Export(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="choppad-export-")
        cls.meta = load_fixture("meta")
        cls.chops = load_fixture("chops")
        click_track(os.path.join(cls.tmp, "audio.wav"), 120)
        json.dump(cls.meta, open(os.path.join(cls.tmp, "meta.json"), "w"))
        json.dump(cls.chops, open(os.path.join(cls.tmp, "chops.json"), "w"))

    def test_sfz_zero_copy_regions_and_choke_group(self):
        sfz_path = export_sfz(self.tmp, bank=0)
        self.assertTrue(os.path.isfile(sfz_path))
        txt = open(sfz_path).read()
        self.assertIn("<group>", txt)
        self.assertIn("group=1", txt)
        self.assertIn("off_by=1", txt)
        self.assertIn("loop_mode=one_shot", txt)
        regions = [l for l in txt.splitlines() if "<region>" in l]
        self.assertEqual(len(regions), 16)
        ch0 = self.chops["chops"][0]
        self.assertIn(f"offset={round(ch0['start'] * 44100)}", regions[0])
        self.assertIn(f"end={round(ch0['end'] * 44100)}", regions[0])
        self.assertIn("key=36", regions[0])
        self.assertIn("key=51", regions[15])

    def test_sfz_short_final_bank(self):
        sfz_path = export_sfz(self.tmp, out_path=os.path.join(self.tmp, "bank2.sfz"), bank=2)
        regions = [l for l in open(sfz_path).read().splitlines() if "<region>" in l]
        self.assertEqual(len(regions), len(self.chops["banks"][2]))

    def test_hydrogen_drumkit_xml_and_declicked_wavs(self):
        kit_dir = os.path.join(self.tmp, "h2kit")
        xml_path = export_hydrogen(self.tmp, kit_dir, bank=0, kit_name="GoldenKit")
        self.assertEqual(xml_path, os.path.join(kit_dir, "drumkit.xml"))
        root = ET.parse(xml_path).getroot()
        strip_ns = lambda t: re.sub(r"^\{.*\}", "", t)
        self.assertEqual(strip_ns(root.tag), "drumkit_info")
        insts = [e for e in root.iter() if strip_ns(e.tag) == "instrument"]
        self.assertEqual(len(insts), 16)
        notes = [int(next(c.text for c in inst if strip_ns(c.tag) == "midiOutNote")) for inst in insts]
        mutes = [int(next(c.text for c in inst if strip_ns(c.tag) == "muteGroup")) for inst in insts]
        self.assertEqual(notes, list(range(36, 52)))
        self.assertEqual(set(mutes), {0})
        wav0 = os.path.join(kit_dir, "chop_00.wav")
        self.assertTrue(os.path.isfile(wav0))
        with wave.open(wav0) as w:
            self.assertEqual((w.getframerate(), w.getnchannels(), w.getsampwidth()), (44100, 2, 2))
            self.assertAlmostEqual(w.getnframes() / 44100, 0.5, delta=0.01)
            raw = w.readframes(w.getnframes())
            first_l, _ = struct.unpack_from("<hh", raw, 0)
            last_l, _ = struct.unpack_from("<hh", raw, len(raw) - 4)
            self.assertEqual(first_l, 0, "fade-in must start at 0")
            self.assertEqual(last_l, 0, "fade-out must end at 0")


if __name__ == "__main__":
    unittest.main()
