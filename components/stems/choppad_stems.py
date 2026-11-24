"""stems: split a song into drums, bass, vocals and other (contracts §12). No local model.

separate(media_dir, *, backend=None, force=False) -> {"drums": path, "bass": path, "vocals": path, "other": path}

Backends (the `backend` argument, else $CHOPPAD_STEMS_BACKEND, else "none"):
  none    the default: raises StemsUnavailable with the steps to turn a backend on
  remote  POSTs audio.wav (raw body, Content-Type audio/wav, X-Filename audio.wav) to $CHOPPAD_STEMS_URL (http or https) and
          expects a zip of drums.wav, bass.wav, vocals.wav and other.wav in return. Optional: $CHOPPAD_STEMS_KEY (sent as
          a Bearer token), $CHOPPAD_STEMS_TIMEOUT (seconds, default 900).

The result is cached in media/<id>/stems/<name>.wav (16-bit PCM, the song's sample rate) and meta.json gains "stems": [...].
A cache that is complete is returned without any request. A returned stem must have the song's sample rate and length
(within 50 ms) and 1 or 2 channels, or the whole answer is rejected and nothing is kept.
A cloud-GPU backend can be added later as one more function in BACKENDS, without touching anything else.
"""
import io
import json
import os
import shutil
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile

STEMS = ("drums", "bass", "vocals", "other")
LENGTH_TOLERANCE_S = 0.05
MAX_ZIP_BYTES = 1500 * 2**20


class StemsUnavailable(RuntimeError):
    """No backend is set up. The message says how to set one up."""
    http_status = 501


class StemsError(RuntimeError):
    """A backend answered with something that is not four usable stems."""
    http_status = 502


def stems_dir(media_dir):
    return os.path.join(media_dir, "stems")


def cached(media_dir):
    """The stems already in media/<id>/stems/ as {name: path}, or None unless all four are there."""
    paths = {n: os.path.join(stems_dir(media_dir), f"{n}.wav") for n in STEMS}
    return paths if all(os.path.isfile(p) for p in paths.values()) else None


def _backend_name(backend):
    return (backend or os.environ.get("CHOPPAD_STEMS_BACKEND") or "none").strip().lower()


def _none(media_dir):
    raise StemsUnavailable(
        "stem separation is off: there is no local model. Point it at a service that splits audio into stems: set "
        "CHOPPAD_STEMS_BACKEND=remote and CHOPPAD_STEMS_URL=<endpoint> (it receives audio.wav and returns a zip of "
        "drums.wav, bass.wav, vocals.wav and other.wav).")


def _remote(media_dir):
    """POST audio.wav to $CHOPPAD_STEMS_URL; returns the path of the downloaded zip (a temp file the caller removes)."""
    url = os.environ.get("CHOPPAD_STEMS_URL", "").strip()
    if not url:
        raise StemsUnavailable("the remote stems backend needs CHOPPAD_STEMS_URL (the endpoint that takes audio.wav and returns a zip of stems)")
    if urllib.parse.urlparse(url).scheme not in ("http", "https"):
        raise StemsUnavailable("CHOPPAD_STEMS_URL must be an http or https URL")
    try:
        timeout = float(os.environ.get("CHOPPAD_STEMS_TIMEOUT") or 900)
    except ValueError:
        timeout = 900.0
    audio = os.path.join(media_dir, "audio.wav")
    headers = {"Content-Type": "audio/wav", "X-Filename": "audio.wav", "Content-Length": str(os.path.getsize(audio)), "Accept": "application/zip"}
    if os.environ.get("CHOPPAD_STEMS_KEY"):
        headers["Authorization"] = "Bearer " + os.environ["CHOPPAD_STEMS_KEY"]
    fd, tmp = tempfile.mkstemp(prefix="choppad-stems-", suffix=".zip")
    try:
        with open(audio, "rb") as body, os.fdopen(fd, "wb") as out:
            req = urllib.request.Request(url, data=body, method="POST", headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                size = 0
                while True:
                    chunk = r.read(1 << 20)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > MAX_ZIP_BYTES:
                        raise StemsError("the stems service answered with more than 1.5 GB")
                    out.write(chunk)
    except urllib.error.HTTPError as e:
        os.unlink(tmp)
        raise StemsError(f"the stems service answered HTTP {e.code}: {(e.read() or b'')[:200].decode('utf-8', 'replace')}".rstrip(": ")) from None
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise StemsError(f"could not reach the stems service at {url}: {getattr(e, 'reason', e)}") from None
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return tmp


BACKENDS = {"none": _none, "remote": _remote}


def _store(media_dir, zip_path):
    """Check the four stems in the zip against audio.wav and put them in media/<id>/stems/. Returns {name: path}."""
    import soundfile as sf
    info = sf.info(os.path.join(media_dir, "audio.wav"))
    out_dir = stems_dir(media_dir)
    stage = tempfile.mkdtemp(prefix=".stems-", dir=media_dir)
    try:
        try:
            z = zipfile.ZipFile(zip_path)
        except zipfile.BadZipFile:
            raise StemsError("the stems service did not return a zip file") from None
        with z:
            members = {os.path.basename(n): n for n in z.namelist() if not n.endswith("/")}
            missing = [n for n in STEMS if f"{n}.wav" not in members]
            if missing:
                raise StemsError(f"the zip is missing {', '.join(m + '.wav' for m in missing)} (got {', '.join(sorted(members)) or 'nothing'})")
            for n in STEMS:
                raw = z.read(members[f"{n}.wav"])
                try:
                    data, sr = sf.read(io.BytesIO(raw), dtype="float32", always_2d=True)
                except Exception:  # noqa: BLE001 - any decoder error means the member is not a WAV
                    raise StemsError(f"{n}.wav is not a readable WAV file") from None
                if sr != info.samplerate:
                    raise StemsError(f"{n}.wav has sample rate {sr}, the song has {info.samplerate}")
                if data.shape[1] not in (1, 2):
                    raise StemsError(f"{n}.wav has {data.shape[1]} channels; expected 1 or 2")
                if abs(len(data) - info.frames) > LENGTH_TOLERANCE_S * info.samplerate:
                    raise StemsError(f"{n}.wav is {len(data) / sr:.2f} s long, the song is {info.frames / info.samplerate:.2f} s")
                if len(data) < info.frames:  # up to 50 ms short: pad with silence so every stem has the song's length
                    data = _pad(data, info.frames)
                sf.write(os.path.join(stage, f"{n}.wav"), data[: info.frames], sr, subtype="PCM_16", format="WAV")
        os.makedirs(out_dir, exist_ok=True)
        for n in STEMS:
            os.replace(os.path.join(stage, f"{n}.wav"), os.path.join(out_dir, f"{n}.wav"))
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    return {n: os.path.join(out_dir, f"{n}.wav") for n in STEMS}


def _pad(data, frames):
    import numpy as np
    return np.concatenate([data, np.zeros((frames - len(data), data.shape[1]), dtype=data.dtype)])


def _record_in_meta(media_dir):
    path = os.path.join(media_dir, "meta.json")
    with open(path, encoding="utf-8") as f:
        meta = json.load(f)
    meta["stems"] = list(STEMS)
    tmp = path + ".part"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)
    os.replace(tmp, path)


def separate(media_dir, *, backend=None, force=False):
    """Stems of the song in `media_dir`: {"drums", "bass", "vocals", "other"} -> WAV path. See the module docstring."""
    have = None if force else cached(media_dir)
    if have:
        return have
    name = _backend_name(backend)
    fn = BACKENDS.get(name)
    if fn is None:
        raise StemsUnavailable(f"unknown stems backend {name!r}; use one of {', '.join(BACKENDS)}")
    if not os.path.isfile(os.path.join(media_dir, "audio.wav")):
        raise StemsError("the song has no audio.wav")
    result = fn(media_dir)
    try:
        paths = _store(media_dir, result)
    finally:
        if isinstance(result, str) and os.path.isfile(result):
            os.unlink(result)
    _record_in_meta(media_dir)
    return paths
