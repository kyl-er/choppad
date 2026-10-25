"""ingest: URL or local media file -> media/<id>/{video.mp4, audio.wav, meta.json}. See contracts/README.md §1."""

import hashlib
import json
import os
import subprocess
import tempfile
import time
import urllib.request


def ingest(source: str, media_root: str, *, max_duration_s: float = 420, cookies_from_browser: str | None = None,
           force: bool = False, cap_bytes: int = 500 * 2**20) -> str:
    """Fetch/convert `source`, enforce the cache cap, return the media dir. Idempotent unless force=True.
    Raises ValueError if the source is longer than max_duration_s (checked before downloading)."""

    is_url = "://" in source or source.startswith(("http:", "https:", "www."))
    if is_url:
        info = _url_info(source, cookies_from_browser)
        duration = info.get("duration") or 0
        if duration > max_duration_s:
            raise ValueError(f"source duration {duration}s exceeds max_duration_s {max_duration_s}")
        media_id = info.get("id") or hashlib.sha1(source.encode()).hexdigest()[:10]
    else:
        if not os.path.isfile(source):
            raise ValueError(f"source is not a local file: {source}")
        duration_s = float(subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", source],
            capture_output=True, text=True).stdout.strip() or 0)
        if duration_s > max_duration_s:
            raise ValueError(f"source duration {duration_s:.1f}s exceeds max_duration_s {max_duration_s}")
        media_id = "local-" + hashlib.sha1(os.path.abspath(source).encode()).hexdigest()[:10]

    media_dir = os.path.join(media_root, media_id)

    if not force and os.path.isfile(os.path.join(media_dir, "meta.json")) and \
            os.path.isfile(os.path.join(media_dir, "audio.wav")) and \
            os.path.isfile(os.path.join(media_dir, "video.mp4")):
        os.utime(media_dir, None)
        enforce_cap(media_root, cap_bytes, keep=media_id)
        return media_dir

    os.makedirs(media_dir, exist_ok=True)

    if is_url:
        raw = os.path.join(media_dir, "_raw_download")
        _download_url(source, cookies_from_browser, raw)
        input_file = raw
    else:
        input_file = source
        raw = None

    audio_wav = os.path.join(media_dir, "audio.wav")
    video_mp4 = os.path.join(media_dir, "video.mp4")

    subprocess.run([
        "ffmpeg", "-y", "-i", input_file,
        "-vn", "-ac", "2", "-ar", "44100", "-c:a", "pcm_s16le",
        audio_wav,
        "-an", "-c:v", "libx264", "-profile:v", "baseline",
        "-vf", "scale=-2:480,fps=30",
        "-g", "6", "-keyint_min", "1", "-sc_threshold", "0", "-bf", "0",
        "-preset", "veryfast", "-tune", "fastdecode", "-crf", "25",
        "-pix_fmt", "yuv420p", "-avoid_negative_ts", "make_zero",
        video_mp4
    ], check=True, capture_output=True)

    if raw and os.path.isfile(raw):
        os.unlink(raw)

    video_info = _probe_video_info(video_mp4)
    duration_s = float(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", audio_wav],
        capture_output=True, text=True).stdout.strip() or 0)

    if is_url:
        title = info.get("title", "")
        meta_source = info.get("webpage_url") or source
    else:
        title = os.path.basename(source)
        meta_source = os.path.abspath(source)

    meta = {
        "schema": "choppad.meta/2",
        "id": media_id,
        "source": meta_source,
        "title": title,
        "duration_s": round(duration_s, 6),
        "video": "video.mp4",
        "audio": "audio.wav",
        "audio_sr": 44100,
        "audio_channels": 2,
        "video_info": video_info,
    }
    # strip trailing seqnum from id for youtube-dl YouTube IDs
    _json = meta["id"]
    if _json != media_id:
        meta["id"] = media_id
    with open(os.path.join(media_dir, "meta.json"), "w") as f:
        json.dump(meta, f, indent=2)

    enforce_cap(media_root, cap_bytes, keep=media_id)
    return media_dir


def enforce_cap(media_root: str, cap_bytes: int, keep: str | None = None) -> list[str]:
    """Delete least-recently-used <id> dirs (by mtime) until media_root <= cap_bytes; never delete `keep`.
    Returns the removed dir names."""

    def _total_size(root):
        total = 0
        for dirpath, _, filenames in os.walk(root):
            for fn in filenames:
                fp = os.path.join(dirpath, fn)
                try:
                    total += os.path.getsize(fp)
                except OSError:
                    pass
        return total

    if not os.path.isdir(media_root):
        return []

    total = _total_size(media_root)
    if total <= cap_bytes:
        return []

    dirs = []
    for entry in os.scandir(media_root):
        if entry.is_dir():
            st = entry.stat()
            dirs.append((st.st_mtime, entry.name, entry.path))

    dirs.sort(key=lambda x: x[0])

    removed = []
    for _, name, path in dirs:
        if total <= cap_bytes:
            break
        if name == keep:
            continue
        total -= _total_size(path)
        _rmtree(path)
        removed.append(name)

    return removed


def _rmtree(path):
    import shutil
    shutil.rmtree(path, ignore_errors=True)


def _url_info(source, cookies_from_browser):
    args = ["yt-dlp", "--dump-single-json", "--no-playlist", "--js-runtimes", "node"]
    if cookies_from_browser:
        args.extend(["--cookies-from-browser", cookies_from_browser])
    args.append(source)
    raw = subprocess.run(args, capture_output=True, text=True)
    if raw.returncode != 0:
        raise RuntimeError(f"yt-dlp metadata failed: {raw.stderr[:500]}")
    return json.loads(raw.stdout)


def _download_url(source, cookies_from_browser, dest):
    args = ["yt-dlp", "--no-playlist", "--js-runtimes", "node",
            "-o", dest]
    if cookies_from_browser:
        args.extend(["--cookies-from-browser", cookies_from_browser])
    args.append(source)
    subprocess.run(args, check=True, capture_output=True)

    if os.path.isfile(dest):
        return
    for entry in os.scandir(os.path.dirname(dest) or "."):
        if entry.name.startswith("_raw_download") and entry.is_file():
            os.rename(entry.path, dest)
            return
    raise FileNotFoundError(f"download did not produce {dest}")


def _probe_video_info(video_path):
    info_raw = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=codec_name,height,has_b_frames",
         "-of", "csv=p=0", video_path],
        capture_output=True, text=True).stdout.strip()
    codec, height, bframes = info_raw.split(",")[:3]

    keys_raw = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "packet=flags", "-of", "csv=p=0", video_path],
        capture_output=True, text=True).stdout.strip()

    flags = keys_raw.split() if keys_raw else []
    last_k = None
    max_gap = 1
    for i, f in enumerate(flags):
        if "K" in f:
            if last_k is not None:
                gap = i - last_k
                if gap > max_gap:
                    max_gap = gap
            last_k = i

    has_audio = bool(subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a",
         "-show_entries", "stream=index", "-of", "csv=p=0", video_path],
        capture_output=True, text=True).stdout.strip())

    return {
        "codec": codec,
        "height": int(height),
        "gop": max_gap,
        "b_frames": int(bframes),
        "has_audio": has_audio,
    }
