import os, sys, tempfile, unittest
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from _common import *  # noqa: E402,F403
from choppad_ingest import enforce_cap, ingest  # noqa: E402
import json, subprocess, time, wave  # noqa: E402


def probe(path, *args):
    return subprocess.run(["ffprobe", "-v", "error", *args, path], capture_output=True, text=True).stdout.strip()


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
        cls.video = os.path.join(cls.out, cls.meta["video"])

    def test_meta_valid(self):
        validate_meta(self.meta, media_dir=self.out)

    def test_audio_format(self):
        with wave.open(os.path.join(self.out, self.meta["audio"])) as w:
            self.assertEqual((w.getframerate(), w.getnchannels(), w.getsampwidth()), (44100, 2, 2))
            self.assertAlmostEqual(w.getnframes() / 44100, self.truth["duration_s"], delta=0.15)

    def test_video_has_no_audio_stream(self):
        self.assertEqual(probe(self.video, "-select_streams", "a", "-show_entries", "stream=index", "-of", "csv=p=0"), "")

    def test_video_short_gop_no_bframes(self):
        info = probe(self.video, "-select_streams", "v:0", "-show_entries", "stream=codec_name,height,has_b_frames", "-of", "csv=p=0")
        codec, height, bframes = info.split(",")[:3]
        self.assertEqual(codec, "h264"); self.assertLessEqual(int(height), 480); self.assertEqual(int(bframes), 0)
        keys = probe(self.video, "-select_streams", "v:0", "-show_entries", "packet=flags", "-of", "csv=p=0").split()
        gaps, last = [], None
        for i, f in enumerate(keys):
            if "K" in f:
                if last is not None:
                    gaps.append(i - last)
                last = i
        self.assertTrue(gaps and max(gaps) <= 6, f"keyframe gaps {gaps[:10]}")
        self.assertEqual(self.meta["video_info"]["gop"], max(gaps))

    def test_duration_matches(self):
        self.assertAlmostEqual(self.meta["duration_s"], self.truth["duration_s"], delta=0.15)

    def test_idempotent(self):
        before = os.path.getmtime(os.path.join(self.out, "audio.wav"))
        self.assertEqual(ingest(self.src, os.path.join(self.tmp, "media")), self.out)
        self.assertEqual(os.path.getmtime(os.path.join(self.out, "audio.wav")), before)

    def test_no_raw_download_left_behind(self):
        self.assertEqual(sorted(os.listdir(self.out)), ["audio.wav", "meta.json", "video.mp4"])

    def test_rejects_too_long(self):
        with self.assertRaises(ValueError):
            ingest(self.src, os.path.join(self.tmp, "media-short"), max_duration_s=1)


class Cap(unittest.TestCase):
    def test_lru_eviction(self):
        root = tempfile.mkdtemp(prefix="choppad-cap-")
        for i, name in enumerate(["old", "mid", "new"]):
            d = os.path.join(root, name); os.makedirs(d)
            open(os.path.join(d, "blob"), "wb").write(b"x" * 1000)
            t = time.time() - (3 - i) * 100; os.utime(d, (t, t))
        removed = enforce_cap(root, 2100, keep="old")
        self.assertEqual(removed, ["mid"])
        self.assertEqual(sorted(os.listdir(root)), ["new", "old"])


@unittest.skipUnless(os.environ.get("CHOPPAD_NET") == "1", "network test: set CHOPPAD_NET=1")
class YouTube(unittest.TestCase):
    def test_eviction_never_removes_underscore_dirs(self):
        root = tempfile.mkdtemp(prefix="choppad-cap-")
        for i, name in enumerate(["_kits", "old", "new"]):
            d = os.path.join(root, name); os.makedirs(d)
            open(os.path.join(d, "blob"), "wb").write(b"x" * 1000)
            t = time.time() - (3 - i) * 100; os.utime(d, (t, t))
        removed = enforce_cap(root, 1100, keep="new")
        self.assertEqual(removed, ["old"])
        self.assertEqual(sorted(os.listdir(root)), ["_kits", "new"])

    def test_youtube_id_is_media_id(self):
        tmp = tempfile.mkdtemp()
        out = ingest("https://www.youtube.com/watch?v=jNQXAC9IVRw", tmp)  # "Me at the zoo", 19 s
        meta = json.load(open(os.path.join(out, "meta.json")))
        self.assertEqual(meta["id"], "jNQXAC9IVRw")
        validate_meta(meta, media_dir=out)


if __name__ == "__main__":
    unittest.main()
