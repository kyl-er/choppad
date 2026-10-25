import os, sys, tempfile, unittest
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from _common import *  # noqa: E402,F403

from choppad_ingest import ingest  # noqa: E402
import json, wave  # noqa: E402


class LocalFile(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="choppad-ingest-")
        wav = os.path.join(cls.tmp, "click.wav")
        cls.truth = click_track(wav, 120, n_beats=12)
        cls.src = os.path.join(cls.tmp, "clip.mp4")
        make_video(cls.src, wav, cls.truth["duration_s"])
        cls.out = ingest(cls.src, os.path.join(cls.tmp, "media"))
        cls.meta = json.load(open(os.path.join(cls.out, "meta.json")))

    def test_meta_valid(self):
        validate_meta(self.meta, media_dir=self.out)

    def test_audio_format(self):
        with wave.open(os.path.join(self.out, self.meta["audio"])) as w:
            self.assertEqual((w.getframerate(), w.getnchannels(), w.getsampwidth()), (44100, 2, 2))
            self.assertAlmostEqual(w.getnframes() / 44100, self.truth["duration_s"], delta=0.15)

    def test_duration_matches(self):
        self.assertAlmostEqual(self.meta["duration_s"], self.truth["duration_s"], delta=0.15)

    def test_idempotent(self):
        before = os.path.getmtime(os.path.join(self.out, "audio.wav"))
        self.assertEqual(ingest(self.src, os.path.join(self.tmp, "media")), self.out)
        self.assertEqual(os.path.getmtime(os.path.join(self.out, "audio.wav")), before)


@unittest.skipUnless(os.environ.get("CHOPPAD_NET") == "1", "network test: set CHOPPAD_NET=1")
class YouTube(unittest.TestCase):
    def test_youtube_id_is_media_id(self):
        tmp = tempfile.mkdtemp()
        out = ingest("https://www.youtube.com/watch?v=jNQXAC9IVRw", tmp)  # "Me at the zoo", 19 s
        meta = json.load(open(os.path.join(out, "meta.json")))
        self.assertEqual(meta["id"], "jNQXAC9IVRw")
        validate_meta(meta, media_dir=out)


if __name__ == "__main__":
    unittest.main()
