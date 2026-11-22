"""stems: the none and remote backends against a local fake HTTP server that returns a zip (no real service, no network)."""
import io, json, os, shutil, sys, tempfile, threading, unittest, wave, zipfile
from http.server import BaseHTTPRequestHandler, HTTPServer
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))
from _common import *  # noqa: E402,F403
sys.path.insert(0, os.path.join(ROOT, "components", "stems"))  # noqa: F405
import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402
import choppad_stems as S  # noqa: E402

SR = 22050


def tone(seconds, freq, sr=SR, channels=2, amp=0.3):
    t = np.arange(int(seconds * sr)) / sr
    x = (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)
    return np.repeat(x[:, None], channels, axis=1)


def wav_bytes(data, sr=SR, subtype="PCM_16"):
    b = io.BytesIO()
    sf.write(b, data, sr, subtype=subtype, format="WAV")
    return b.getvalue()


def make_zip(files):
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return b.getvalue()


class FakeService:
    """A stems service: records what it received and answers with `reply` (status, body, content type)."""
    def __init__(self):
        self.requests, self.reply = [], (200, b"", "application/zip")
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                outer.requests.append({"path": self.path, "body": self.rfile.read(n), "headers": dict(self.headers)})
                code, body, ctype = outer.reply
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        self.httpd = HTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/separate"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class Stems(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="choppad-stems-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.dir = os.path.join(self.tmp, "song1")
        os.makedirs(self.dir)
        self.song = tone(3.0, 220)
        sf.write(os.path.join(self.dir, "audio.wav"), self.song, SR, subtype="PCM_16")
        json.dump({"id": "song1", "title": "T", "duration_s": 3.0}, open(os.path.join(self.dir, "meta.json"), "w"))
        self.svc = FakeService()
        self.addCleanup(self.svc.close)
        self.env = {"CHOPPAD_STEMS_BACKEND": "remote", "CHOPPAD_STEMS_URL": self.svc.url}
        for k in ("CHOPPAD_STEMS_BACKEND", "CHOPPAD_STEMS_URL", "CHOPPAD_STEMS_KEY", "CHOPPAD_STEMS_TIMEOUT"):
            old = os.environ.pop(k, None)
            if old is not None:
                self.addCleanup(os.environ.__setitem__, k, old)
        os.environ.update(self.env)
        self.addCleanup(lambda: [os.environ.pop(k, None) for k in ("CHOPPAD_STEMS_BACKEND", "CHOPPAD_STEMS_URL", "CHOPPAD_STEMS_KEY")])

    def good_zip(self, **override):
        files = {f"{n}.wav": wav_bytes(tone(3.0, 110 * (i + 1))) for i, n in enumerate(S.STEMS)}
        files.update(override)
        return make_zip(files)

    def answer(self, body, code=200):
        self.svc.reply = (code, body, "application/zip")

    # ---- the default is off
    def test_none_is_the_default_and_says_what_to_do(self):
        os.environ.pop("CHOPPAD_STEMS_BACKEND")
        with self.assertRaises(S.StemsUnavailable) as c:
            S.separate(self.dir)
        self.assertIn("CHOPPAD_STEMS_URL", str(c.exception))
        with self.assertRaises(S.StemsUnavailable):
            S.separate(self.dir, backend="none")
        self.assertEqual(self.svc.requests, [])
        self.assertEqual(S.StemsUnavailable.http_status, 501)

    def test_unknown_backend_and_missing_url(self):
        with self.assertRaisesRegex(S.StemsUnavailable, "unknown stems backend"):
            S.separate(self.dir, backend="gpu-cloud")
        os.environ.pop("CHOPPAD_STEMS_URL")
        with self.assertRaisesRegex(S.StemsUnavailable, "CHOPPAD_STEMS_URL"):
            S.separate(self.dir)
        os.environ["CHOPPAD_STEMS_URL"] = "file:///etc/passwd"
        with self.assertRaisesRegex(S.StemsUnavailable, "http"):
            S.separate(self.dir)

    # ---- remote
    def test_remote_posts_the_audio_and_stores_four_stems(self):
        self.answer(self.good_zip())
        paths = S.separate(self.dir)
        self.assertEqual(list(paths), ["drums", "bass", "vocals", "other"])
        req = self.svc.requests[0]
        self.assertEqual(req["body"], open(os.path.join(self.dir, "audio.wav"), "rb").read(), "the raw audio.wav is the body")
        self.assertEqual(req["headers"]["Content-Type"], "audio/wav")
        self.assertEqual(req["headers"]["X-Filename"], "audio.wav")
        for n, p in paths.items():
            self.assertEqual(p, os.path.join(self.dir, "stems", f"{n}.wav"))
            info = sf.info(p)
            self.assertEqual((info.samplerate, info.frames, info.subtype), (SR, len(self.song), "PCM_16"))
        self.assertEqual(json.load(open(os.path.join(self.dir, "meta.json")))["stems"], ["drums", "bass", "vocals", "other"])
        self.assertEqual(json.load(open(os.path.join(self.dir, "meta.json")))["title"], "T", "the rest of meta.json is kept")
        self.assertEqual([f for f in os.listdir(self.dir) if f.startswith(".stems-")], [], "no staging folder left behind")

    def test_cache_means_no_second_request_and_force_asks_again(self):
        self.answer(self.good_zip())
        S.separate(self.dir)
        S.separate(self.dir)
        self.assertEqual(len(self.svc.requests), 1)
        os.environ["CHOPPAD_STEMS_BACKEND"] = "none"
        self.assertEqual(sorted(S.separate(self.dir)), sorted(S.STEMS), "stems that are there are returned whatever the backend")
        os.environ["CHOPPAD_STEMS_BACKEND"] = "remote"
        S.separate(self.dir, force=True)
        self.assertEqual(len(self.svc.requests), 2)

    def test_key_is_sent_as_a_bearer_token(self):
        os.environ["CHOPPAD_STEMS_KEY"] = "s3cret"
        self.answer(self.good_zip())
        S.separate(self.dir)
        self.assertEqual(self.svc.requests[0]["headers"]["Authorization"], "Bearer s3cret")

    def test_members_in_folders_and_float_wavs_are_accepted_and_stored_as_pcm16(self):
        files = {f"stems/{n}.wav": wav_bytes(tone(3.0, 200 + i), subtype="FLOAT") for i, n in enumerate(S.STEMS)}
        self.answer(make_zip(files))
        paths = S.separate(self.dir)
        self.assertEqual({sf.info(p).subtype for p in paths.values()}, {"PCM_16"})

    def test_a_stem_a_few_ms_short_is_padded_to_the_song_length(self):
        self.answer(self.good_zip(**{"vocals.wav": wav_bytes(tone(2.97, 300))}))
        paths = S.separate(self.dir)
        self.assertEqual(sf.info(paths["vocals"]).frames, len(self.song))

    def test_mono_stems_are_kept_mono(self):
        self.answer(self.good_zip(**{"bass.wav": wav_bytes(tone(3.0, 60, channels=1))}))
        self.assertEqual(sf.info(S.separate(self.dir)["bass"]).channels, 1)

    def rejected(self, zip_bytes, pattern, code=200):
        self.answer(zip_bytes, code)
        with self.assertRaisesRegex(S.StemsError, pattern):
            S.separate(self.dir)
        self.assertFalse(os.path.isdir(os.path.join(self.dir, "stems")), "nothing is kept from a rejected answer")
        self.assertNotIn("stems", json.load(open(os.path.join(self.dir, "meta.json"))))

    def test_bad_answers_are_rejected_and_nothing_is_kept(self):
        self.rejected(b"this is not a zip", "not return a zip")
        self.rejected(make_zip({"drums.wav": wav_bytes(tone(3.0, 100))}), "missing bass.wav, vocals.wav, other.wav")
        self.rejected(self.good_zip(**{"drums.wav": b"RIFFjunk"}), r"drums\.wav is not a readable WAV")
        self.rejected(self.good_zip(**{"other.wav": wav_bytes(tone(3.0, 100, sr=44100), sr=44100)}), r"other\.wav has sample rate 44100, the song has 22050")
        self.rejected(self.good_zip(**{"vocals.wav": wav_bytes(tone(2.5, 100))}), r"vocals\.wav is 2\.50 s long, the song is 3\.00 s")
        self.rejected(self.good_zip(**{"bass.wav": wav_bytes(np.zeros((len(self.song), 6), dtype=np.float32))}), r"bass\.wav has 6 channels")

    def test_service_errors(self):
        self.rejected(b"model crashed", "HTTP 500: model crashed", code=500)
        self.svc.close()
        os.environ["CHOPPAD_STEMS_URL"] = "http://127.0.0.1:9/nothing"
        with self.assertRaisesRegex(S.StemsError, "could not reach the stems service"):
            S.separate(self.dir)
        self.assertEqual(S.StemsError.http_status, 502)

    def test_songs_without_audio_are_an_error(self):
        os.remove(os.path.join(self.dir, "audio.wav"))
        with self.assertRaisesRegex(S.StemsError, "no audio.wav"):
            S.separate(self.dir)


if __name__ == "__main__":
    unittest.main()
