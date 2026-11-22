"""rendered: files a pad can play besides the song's audio.wav (W3b).

  media/<id>/resample/<hash>.wav   what the browser rendered or recorded from the master bus (POST /api/songs/<id>/resample,
                                   WAV body); the name is the hash of the content, so the same take is stored once
  media/<id>/stems/<name>.wav      the four stems (components/stems)
  media/<id>/stretch/<hash>.wav    time-stretched slices (components/chop/choppad_stretch.py)

A pad override names one of them: `src: "/media/<id>/resample/<hash>.wav"` or `stem: "drums"` (contracts §13).
Plain functions; server.py only routes to them.
"""
import hashlib
import io
import os
import re
import wave

MAX_BYTES = 120 * 2**20
MAX_SECONDS = 600
STEMS = ("drums", "bass", "vocals", "other")
RESAMPLE_URL = re.compile(r"^/media/([A-Za-z0-9_-]{1,64})/resample/([0-9a-f]{16})\.wav$")


def save_resample(media_dir, data):
    """Store a WAV (PCM 16-bit, mono or stereo) under resample/. Returns {url, hash, duration, sample_rate, channels}.
    Raises ValueError (clear message) for anything that is not a usable WAV."""
    if not data or len(data) > MAX_BYTES:
        raise ValueError(f"the body must be a WAV file of at most {MAX_BYTES // 2**20} MB")
    try:
        with wave.open(io.BytesIO(data), "rb") as w:
            ch, sw, sr, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
    except (wave.Error, EOFError):
        raise ValueError("the body is not a PCM WAV file") from None
    if sw != 2 or ch not in (1, 2):
        raise ValueError("the WAV must be 16-bit PCM, mono or stereo")
    if not 8000 <= sr <= 192000 or n < 64:
        raise ValueError("the WAV needs a sample rate of 8-192 kHz and some audio")
    if n / sr > MAX_SECONDS:
        raise ValueError(f"the WAV is longer than {MAX_SECONDS} s")
    h = hashlib.sha1(data).hexdigest()[:16]
    out_dir = os.path.join(media_dir, "resample")
    path = os.path.join(out_dir, f"{h}.wav")
    if not os.path.isfile(path):
        os.makedirs(out_dir, exist_ok=True)
        tmp = path + ".part"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    sid = os.path.basename(os.path.abspath(media_dir))
    return {"url": f"/media/{sid}/resample/{h}.wav", "hash": h, "duration": n / sr, "sample_rate": sr, "channels": ch}


def resample_path(media_root, url):
    """File of a resample url, or ValueError (used by the exporters)."""
    m = RESAMPLE_URL.match(url or "")
    if not m:
        raise ValueError(f"bad resample url {url!r}")
    path = os.path.join(media_root, m.group(1), "resample", f"{m.group(2)}.wav")
    if not os.path.isfile(path):
        raise ValueError(f"resample {url} is missing; resample it again")
    return path
