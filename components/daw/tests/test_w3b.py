"""W3b on the DAW side: project tempo reaches DawProject.bpm and the .h2song; song mode, pitch and velocity in the song."""
import os, shutil, sys, tempfile, unittest
import xml.etree.ElementTree as ET
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from support import make_media  # noqa: E402
import daw_project  # noqa: E402
import h2song  # noqa: E402


class ProjectTempo(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="choppad-w3b-")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def build(self, **extra):
        d, song = make_media(project_extra=extra)
        self.addCleanup(shutil.rmtree, d, True)
        return daw_project.build_project(song, os.path.join(self.tmp, "kits"))

    def test_song_tempo_when_none_is_set(self):
        p = self.build()
        self.assertEqual(p.bpm, 120.0)

    def test_project_tempo_wins(self):
        p = self.build(tempo={"bpm": 92.5, "follow": True})
        self.assertEqual(p.bpm, 92.5)

    def test_null_tempo_means_the_song_tempo(self):
        p = self.build(tempo={"bpm": None, "follow": True})
        self.assertEqual(p.bpm, 120.0)

    def test_pad_filter_and_envelope_are_baked_into_the_kit(self):
        plain = self.build()
        pads = [{} for _ in range(16)]
        pads[0] = {"filter": {"type": "lp", "cutoff": 300, "q": 2}, "sustain": 0.2, "decay": 0.05}
        shaped = self.build(pads=pads)
        self.assertNotEqual(plain.key, shaped.key, "a different sound is a different kit")
        a = open(os.path.join(plain.kit_dir, "chop_00.wav"), "rb").read()
        b = open(os.path.join(shaped.kit_dir, "chop_00.wav"), "rb").read()
        self.assertNotEqual(a, b)
        self.assertEqual(open(os.path.join(plain.kit_dir, "chop_01.wav"), "rb").read(),
                         open(os.path.join(shaped.kit_dir, "chop_01.wav"), "rb").read(), "pads without settings are unchanged")

    def test_mute_groups_reach_the_h2song(self):
        pads = [{} for _ in range(16)]
        pads[0], pads[1], pads[4] = {"muteGroup": 1}, {"muteGroup": 1}, {"muteGroup": 3}
        p = self.build(pads=pads)
        self.assertEqual(p.pattern["mute_groups"][:5], [1, 1, 0, 0, 3])
        root, _ = h2song.build_song(title=p.title, bpm=p.bpm, beats_per_bar=p.beats_per_bar, wavs=p.wavs(), pattern=p.pattern,
                                    empty_wav="/x.wav")
        groups = [i.find("muteGroup").text for i in root.find("instrumentList")]
        self.assertEqual(groups[:5], ["1", "1", "-1", "-1", "3"])
        # no groups: the old behaviour, every pad in group 0 (choke)
        plain = self.build()
        self.assertNotIn("mute_groups", plain.pattern)
        root, _ = h2song.build_song(title=plain.title, bpm=plain.bpm, beats_per_bar=4, wavs=plain.wavs(), pattern=plain.pattern, empty_wav="/x.wav")
        self.assertEqual({i.find("muteGroup").text for i in root.find("instrumentList")}, {"0"})

    def test_h2song_writes_velocity_and_pitch_of_16_level_hits(self):
        pattern = {"bars": 1, "events": [
            {"beat": 0, "pad": 3, "bank": 0, "vel": 0.25, "pass": 1},
            {"beat": 1, "pad": 3, "bank": 0, "vel": 1, "pass": 1, "pitch": -5},
            {"beat": 2, "pad": 3, "bank": 0, "vel": 0.5, "pass": 1, "pitch": 8, "cutoff": 900}]}
        root, info = h2song.build_song(title="t", bpm=100, beats_per_bar=4, wavs=[f"/w{i}.wav" for i in range(16)], pattern=pattern,
                                       empty_wav="/x.wav")
        notes = root.find("patternList")[0].find("noteList")
        got = [(n.find("position").text, n.find("velocity").text, n.find("pitch").text, n.find("instrument").text) for n in notes]
        self.assertEqual(got, [("0", "0.25", "0.0", "3"), ("48", "1.0", "-5.0", "3"), ("96", "0.5", "8.0", "3")])
        self.assertEqual(info["notes"], 3)

    def test_h2song_carries_the_project_tempo(self):
        p = self.build(tempo={"bpm": 92.5, "follow": True})
        root, _ = h2song.build_song(title=p.title, bpm=p.bpm, beats_per_bar=p.beats_per_bar, wavs=p.wavs(), pattern=p.pattern,
                                    empty_wav="/x.wav")
        self.assertEqual(root.find("bpm").text, "92.5")


if __name__ == "__main__":
    unittest.main()
