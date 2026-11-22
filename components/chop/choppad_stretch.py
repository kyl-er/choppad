"""stretch: render a slice of a song at another length, same pitch, with the rubberband CLI.

stretch_slice(media_dir, start, end, ratio, source="audio.wav") -> {"path", "hash", "duration", "ratio", "file"}
  ratio = new length / old length (0.5 = twice as fast). Cached in <media_dir>/stretch/<hash>.wav (PCM s16, same
  sample rate and channels as the source); the hash covers the audio file, the slice and the ratio.
  `source` (W3b) = the file of the song to cut the slice from: "audio.wav" or "stems/<drums|bass|vocals|other>.wav".
ratio_for_tempo(from_bpm, to_bpm) -> ratio   (72 -> 101 BPM is 0.7129)
"""
import hashlib
import os
import shutil
import subprocess
import tempfile

import numpy as np
import soundfile as sf

MIN_RATIO, MAX_RATIO = 0.25, 4.0
RUBBERBAND = shutil.which("rubberband") or "/usr/bin/rubberband"


class StretchError(ValueError):
    pass


def ratio_for_tempo(from_bpm: float, to_bpm: float) -> float:
    if not (from_bpm > 0 and to_bpm > 0):
        raise StretchError("tempos must be positive")
    return from_bpm / to_bpm


SOURCES = ("audio.wav",) + tuple(f"stems/{n}.wav" for n in ("drums", "bass", "vocals", "other"))


def _source_path(media_dir: str, source: str) -> str:
    if source not in SOURCES:
        raise StretchError(f"source must be one of {', '.join(SOURCES)}")
    path = os.path.join(media_dir, *source.split("/"))
    if not os.path.isfile(path):
        raise StretchError(f"{source} is missing (run the stems step first)" if source != "audio.wav" else "audio.wav is missing")
    return path


def stretch_hash(media_dir: str, start: float, end: float, ratio: float, source: str = "audio.wav") -> str:
    st = os.stat(_source_path(media_dir, source))
    # the plain song keeps its v1 key, so renderings made before stems existed stay valid
    key = f"v1:{st.st_size}:{st.st_mtime_ns}:{start:.5f}:{end:.5f}:{ratio:.6f}" + ("" if source == "audio.wav" else f":{source}")
    return hashlib.sha1(key.encode()).hexdigest()[:16]


def stretch_slice(media_dir: str, start: float, end: float, ratio: float, source: str = "audio.wav") -> dict:
    if not (end > start >= 0):
        raise StretchError("slice needs 0 <= start < end")
    if not MIN_RATIO <= ratio <= MAX_RATIO:
        raise StretchError(f"ratio must be between {MIN_RATIO} and {MAX_RATIO}")
    if not os.path.isfile(RUBBERBAND):
        raise StretchError("rubberband is not installed")
    src_path = _source_path(media_dir, source)
    h = stretch_hash(media_dir, start, end, ratio, source)
    out_dir = os.path.join(media_dir, "stretch")
    out = os.path.join(out_dir, f"{h}.wav")
    if not os.path.isfile(out):
        os.makedirs(out_dir, exist_ok=True)
        info = sf.info(src_path)
        sr = info.samplerate
        a, b = int(round(start * sr)), min(int(round(end * sr)), info.frames)
        if b - a < 64:
            raise StretchError("slice is outside the song or too short")
        seg, _ = sf.read(src_path, start=a, stop=b, dtype="float32", always_2d=True)
        with tempfile.TemporaryDirectory(prefix="choppad-rb-") as tmp:
            src, dst = os.path.join(tmp, "in.wav"), os.path.join(tmp, "out.wav")
            sf.write(src, seg, sr, subtype="FLOAT")
            p = subprocess.run([RUBBERBAND, "--time", f"{ratio:.8f}", "--crisp", "6", src, dst],
                               capture_output=True, text=True, timeout=120)
            if p.returncode != 0 or not os.path.isfile(dst):
                raise StretchError(f"rubberband failed: {(p.stderr or p.stdout).strip()[-200:]}")
            y, _ = sf.read(dst, dtype="float32", always_2d=True)
            y = np.clip(y, -1.0, 1.0)
            tmp_out = out + ".part"
            sf.write(tmp_out, y, sr, subtype="PCM_16", format="WAV")
            os.replace(tmp_out, out)
    info = sf.info(out)
    return {"path": out, "hash": h, "file": f"{h}.wav", "duration": info.frames / info.samplerate, "ratio": ratio}
