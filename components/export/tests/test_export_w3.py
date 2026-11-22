import json, os, shutil, sys, tempfile, unittest, wave
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from _common import *  # noqa: E402,F403
from choppad_export import bank_pads, export_hydrogen, export_sfz, export_zip, kit_pads, write_hydrogen, write_sfz, write_zip  # noqa: E402
from choppad_stretch import stretch_slice  # noqa: E402


def wav_info(path):
    with wave.open(path, "rb") as w:
        return w.getnframes() / w.getframerate(), w.getframerate()


class ExportW3(unittest.TestCase):
    """Custom slices, stretched pads and kits with pads from two songs."""

    @classmethod
    def setUpClass(cls):
        cls.root = tempfile.mkdtemp(prefix="choppad-export-w3-")
        cls.addClassCleanup(shutil.rmtree, cls.root, ignore_errors=True)
        cls.dirs = {}
        for sid, bpm in (("songA", 120), ("songB", 90)):
            d = os.path.join(cls.root, sid)
            os.makedirs(d)
            click_track(os.path.join(d, "audio.wav"), bpm)
            meta = {**load_fixture("meta"), "id": sid}
            json.dump(meta, open(os.path.join(d, "meta.json"), "w"))
            json.dump(load_fixture("chops"), open(os.path.join(d, "chops.json"), "w"))
            cls.dirs[sid] = d

    def test_override_slice_is_exported(self):
        d = self.dirs["songA"]
        pads = bank_pads(d, 0, {"0:3": {"start": 5.0, "end": 5.6}})
        self.assertEqual(len(pads), 16)
        p3 = next(p for p in pads if p["pad"] == 3)
        self.assertEqual((p3["start"], p3["end"]), (5.0, 5.6))
        out = os.path.join(self.root, "h2-override")
        export_hydrogen(d, out, bank=0, overrides={"0:3": {"start": 5.0, "end": 5.6}})
        dur, _ = wav_info(os.path.join(out, "chop_03.wav"))
        self.assertAlmostEqual(dur, 0.6, delta=0.001)
        # the saved project is used when no overrides are passed
        json.dump({"pads": [], "overrides": {"0:0": {"start": 2.0, "end": 2.25}}}, open(os.path.join(d, "project.json"), "w"))
        self.addCleanup(os.remove, os.path.join(d, "project.json"))
        self.assertEqual(bank_pads(d, 0)[0]["end"], 2.25)

    def test_stretched_pad_exports_its_rendering(self):
        d = self.dirs["songA"]
        r = stretch_slice(d, 2.0, 2.5, 2.0)
        ov = {"0:1": {"start": 2.0, "end": 2.5, "stretch": {"ratio": 2.0, "url": f"/media/songA/stretch/{r['file']}"}}}
        out = os.path.join(self.root, "h2-stretch")
        export_hydrogen(d, out, bank=0, overrides=ov)
        self.assertAlmostEqual(wav_info(os.path.join(out, "chop_01.wav"))[0], 1.0, delta=0.002)
        self.assertAlmostEqual(wav_info(os.path.join(out, "chop_00.wav"))[0], 0.5, delta=0.002)
        sfz = export_sfz(d, out_path=os.path.join(d, "stretch.sfz"), bank=0, overrides=ov)
        region = [l for l in open(sfz).read().splitlines() if "key=37" in l][0]
        self.assertIn(f"sample=stretch/{r['file']}", region)
        self.assertNotIn("offset=", region)
        # a missing rendering fails with a clear message
        bad = {"0:1": {"start": 2.0, "end": 2.5, "stretch": {"ratio": 2.0, "url": "/media/songA/stretch/0123456789abcdef.wav"}}}
        with self.assertRaisesRegex(ValueError, "stretch the pad again"):
            bank_pads(d, 0, bad)

    def test_kit_with_pads_from_two_songs(self):
        a, b = self.dirs["songA"], self.dirs["songB"]
        rb = stretch_slice(b, 1.0, 1.7, 0.5)
        kit = {"schema": "choppad.kit/1", "name": "mix", "fx": {}, "pads": [None] * 16}
        kit["pads"][0] = {"song_id": "songA", "start": 0.5, "end": 1.0, "params": {}}
        kit["pads"][5] = {"song_id": "songB", "start": 2.0, "end": 2.6, "params": {}}
        kit["pads"][6] = {"song_id": "songB", "start": 1.0, "end": 1.7, "params": {},
                          "stretch": {"ratio": 0.5, "url": f"/media/songB/stretch/{rb['file']}"}}
        pads = kit_pads(self.root, kit)
        self.assertEqual([p["pad"] for p in pads], [0, 5, 6])
        out = os.path.join(self.root, "h2-kit")
        write_hydrogen(pads, out, "mix")
        self.assertAlmostEqual(wav_info(os.path.join(out, "chop_00.wav"))[0], 0.5, delta=0.001)
        self.assertAlmostEqual(wav_info(os.path.join(out, "chop_05.wav"))[0], 0.6, delta=0.001)
        self.assertAlmostEqual(wav_info(os.path.join(out, "chop_06.wav"))[0], 0.35, delta=0.002)
        self.assertFalse(os.path.exists(os.path.join(out, "chop_01.wav")))
        sfz = write_sfz(pads, os.path.join(self.root, "kit.sfz"), default_audio=os.path.join(a, "audio.wav"))
        lines = [l for l in open(sfz).read().splitlines() if l.startswith("<region>")]
        self.assertEqual(len(lines), 3)
        self.assertNotIn("sample=songB", lines[0])
        self.assertIn("sample=songB/audio.wav", lines[1])
        self.assertIn("key=41", lines[1])
        self.assertIn("sample=songB/stretch/", lines[2])

    def test_kit_missing_song_is_a_clear_error(self):
        kit = {"pads": [{"song_id": "gone", "start": 0, "end": 1}] + [None] * 15}
        with self.assertRaisesRegex(ValueError, "needs song 'gone'"):
            kit_pads(self.root, kit)
        with self.assertRaises(ValueError):
            kit_pads(self.root, {"pads": [{"song_id": "../etc", "start": 0, "end": 1}] + [None] * 15})

    def test_zip_of_the_bank(self):
        import io, zipfile
        d = self.dirs["songA"]
        r = stretch_slice(d, 2.0, 2.5, 2.0)
        ov = {"0:1": {"start": 2.0, "end": 2.5, "stretch": {"ratio": 2.0, "url": f"/media/songA/stretch/{r['file']}"}},
              "0:2": {"start": 5.0, "end": 5.3, "song": "songB"}}
        path = export_zip(d, bank=0, overrides=ov)
        self.assertTrue(path.endswith("songA-bank1.zip"))
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            self.assertEqual(names, [f"pad{i:02d}.wav" for i in range(1, 17)])
            self.assertIsNone(z.testzip())
            durs = []
            for n in names[:3]:
                with wave.open(io.BytesIO(z.read(n))) as w:
                    self.assertEqual((w.getnchannels(), w.getsampwidth(), w.getframerate()), (2, 2, 44100))
                    durs.append(w.getnframes() / w.getframerate())
        self.assertAlmostEqual(durs[0], 0.5, delta=0.001)   # grid chop at 120 BPM
        self.assertAlmostEqual(durs[1], 1.0, delta=0.002)   # stretched x2
        self.assertAlmostEqual(durs[2], 0.3, delta=0.001)   # slice of the other song
        # a short last bank has fewer files
        with zipfile.ZipFile(export_zip(d, out_path=os.path.join(self.root, "b2.zip"), bank=2, overrides={})) as z:
            self.assertEqual(len(z.namelist()), len(load_fixture("chops")["banks"][2]))


if __name__ == "__main__":
    unittest.main()
