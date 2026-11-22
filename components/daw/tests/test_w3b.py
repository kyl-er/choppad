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

    def test_h2song_carries_the_project_tempo(self):
        p = self.build(tempo={"bpm": 92.5, "follow": True})
        root, _ = h2song.build_song(title=p.title, bpm=p.bpm, beats_per_bar=p.beats_per_bar, wavs=p.wavs(), pattern=p.pattern,
                                    empty_wav="/x.wav")
        self.assertEqual(root.find("bpm").text, "92.5")


if __name__ == "__main__":
    unittest.main()
