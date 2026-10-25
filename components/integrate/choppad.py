"""choppad CLI: `choppad <url|file> [--grid 1/4] [--meter 4/4] [--bpm N] [--max-duration 420]
   [--cookies-from-browser B] [--serve] [--port 8960] [--clean] [--force]`.

Runs ingest -> rhythm -> chop into media/<id>/, then (with --serve) serves the web UI at
http://127.0.0.1:<port>/?id=<id> with /media/<id>/* static. See contracts/README.md.
"""

import argparse
import json
import os
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from choppad_ingest import ingest as _ingest
from choppad_rhythm import analyze as _analyze
from choppad_chop import chop as _chop

REPO_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
UI_DIR = os.path.join(REPO_DIR, "components", "ui")
MIDI_JS = os.path.join(REPO_DIR, "components", "midi", "midi.js")

STATIC_MIME = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".wav": "audio/wav",
    ".mp4": "video/mp4",
    ".json": "application/json",
    ".ico": "image/x-icon",
}


def run_pipeline(source: str, media_root: str, grid: str = "1/4", meter: str | None = None,
                 bpm: float | None = None, **ingest_kw) -> str:

    media_dir = _ingest(source, media_root, **ingest_kw)

    audio_path = os.path.join(media_dir, "audio.wav")
    analysis = _analyze(audio_path, meter=meter, bpm=bpm)
    analysis_path = os.path.join(media_dir, "analysis.json")
    with open(analysis_path, "w") as f:
        json.dump(analysis, f, indent=2)

    chops = _chop(analysis, grid=grid, meter=meter)
    chops_path = os.path.join(media_dir, "chops.json")
    with open(chops_path, "w") as f:
        json.dump(chops, f, indent=2)

    return media_dir


def _serve_file(path, handler):
    if not os.path.isfile(path) or os.path.isdir(path):
        handler.send_error(404)
        return
    try:
        fsize = os.path.getsize(path)
    except OSError:
        handler.send_error(404)
        return
    ext = os.path.splitext(path)[1].lower()
    mime = STATIC_MIME.get(ext, "application/octet-stream")
    handler.send_response(200)
    handler.send_header("Content-Type", mime)
    handler.send_header("Content-Length", str(fsize))
    handler.end_headers()
    with open(path, "rb") as f:
        handler.wfile.write(f.read())


def _serve_media_range(path, handler):
    if not os.path.isfile(path) or os.path.isdir(path):
        handler.send_error(404)
        return
    try:
        fsize = os.path.getsize(path)
    except OSError:
        handler.send_error(404)
        return
    ext = os.path.splitext(path)[1].lower()
    mime = STATIC_MIME.get(ext, "application/octet-stream")

    range_header = handler.headers.get("Range")
    if not range_header:
        handler.send_response(200)
        handler.send_header("Content-Type", mime)
        handler.send_header("Content-Length", str(fsize))
        handler.send_header("Accept-Ranges", "bytes")
        handler.end_headers()
        with open(path, "rb") as f:
            handler.wfile.write(f.read())
        return

    try:
        unit, ranges = range_header.split("=", 1)
        if unit != "bytes":
            raise ValueError
        start_str, end_str = ranges.split("-", 1)
        start = int(start_str) if start_str else 0
        end = int(end_str) if end_str else fsize - 1
        end = min(end, fsize - 1)
        if start > end or start >= fsize:
            handler.send_error(416)
            return
        content_length = end - start + 1
    except (ValueError, IndexError):
        handler.send_error(400)
        return

    handler.send_response(206)
    handler.send_header("Content-Type", mime)
    handler.send_header("Content-Range", f"bytes {start}-{end}/{fsize}")
    handler.send_header("Content-Length", str(content_length))
    handler.send_header("Accept-Ranges", "bytes")
    handler.end_headers()
    with open(path, "rb") as f:
        f.seek(start)
        remaining = content_length
        while remaining > 0:
            chunk = f.read(min(65536, remaining))
            if not chunk:
                break
            try:
                handler.wfile.write(chunk)
            except (BrokenPipeError, ConnectionResetError):
                return
            remaining -= len(chunk)


def _serve_media_root(path, handler):
    entries = []
    try:
        for entry in sorted(os.listdir(path)):
            full = os.path.join(path, entry)
            if os.path.isdir(full):
                entries.append(f'<li><a href="{entry}/">{entry}/</a></li>')
            else:
                entries.append(f'<li><a href="{entry}">{entry}</a></li>')
    except OSError:
        handler.send_error(404)
        return
    html = f"<html><body><h1>media/</h1><ul>{''.join(entries)}</ul></body></html>"
    data = html.encode("utf-8")
    handler.send_response(200)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


class _Handler(SimpleHTTPRequestHandler):
    media_root = None

    def log_message(self, format, *args):
        pass

    def send_error(self, code, message=None, explain=None):
        SimpleHTTPRequestHandler.send_error(self, code, message, explain)

    def do_GET(self):
        parsed = urlparse(self.path)
        p = parsed.path

        try:
            if p == "/":
                _serve_file(os.path.join(UI_DIR, "index.html"), self)
            elif p == "/midi.js":
                _serve_file(MIDI_JS, self)
            elif p == "/main.js":
                _serve_file(os.path.join(UI_DIR, "main.js"), self)
            elif p.startswith("/media/"):
                rel = p[len("/media/"):]
                if rel.endswith("/"):
                    rel = rel[:-1]
                rel = rel.lstrip("/")

                if not rel or rel == ".":
                    _serve_media_root(self.media_root, self)
                elif "/" in rel:
                    parts = rel.split("/", 1)
                    sub_id, sub_file = parts
                    media_path = os.path.join(self.media_root, sub_id, sub_file)
                    _serve_media_range(media_path, self)
                else:
                    _serve_media_range(os.path.join(self.media_root, rel), self)
            else:
                _serve_file(os.path.join(UI_DIR, p.lstrip("/")), self)
        except Exception:
            self.send_error(500)

    def do_POST(self):
        if self.path != "/api/regrid":
            self.send_error(404)
            return

        content_length = int(self.headers.get("Content-Length", 0))
        if content_length <= 0:
            self.send_error(400)
            return

        try:
            body = json.loads(self.rfile.read(content_length))
        except (json.JSONDecodeError, ValueError):
            self.send_error(400)
            return

        mid = body.get("id")
        if not mid or not isinstance(mid, str):
            self.send_error(400)
            return

        analysis_path = os.path.join(self.media_root, mid, "analysis.json")
        if not os.path.isfile(analysis_path):
            self.send_error(404)
            return

        with open(analysis_path) as f:
            analysis = json.load(f)

        grid = body.get("grid", "1/4")
        meter = body.get("meter")
        beat_offset = body.get("beat_offset", 0)
        bpm_mult = body.get("bpm_mult", 1)

        chops = _chop(analysis, grid=grid, meter=meter, beat_offset=beat_offset, bpm_mult=bpm_mult)

        chops_path = os.path.join(self.media_root, mid, "chops.json")
        with open(chops_path, "w") as f:
            json.dump(chops, f, indent=2)

        data = json.dumps(chops).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def make_server(media_root: str, port: int = 8960):
    class BoundHandler(_Handler):
        pass

    BoundHandler.media_root = media_root
    return ThreadingHTTPServer(("127.0.0.1", port), BoundHandler)


def main():
    parser = argparse.ArgumentParser(
        description="ChopPad: YouTube/media → pads. Ingest → rhythm → chop → play.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("source", nargs="?", help="URL or local file")
    parser.add_argument("--grid", default="1/4", help="grid resolution (default 1/4)")
    parser.add_argument("--meter", default=None, help="time signature override (e.g. 4/4)")
    parser.add_argument("--bpm", type=float, default=None, help="BPM override")
    parser.add_argument("--max-duration", type=float, default=420, help="max source duration in seconds")
    parser.add_argument("--cookies-from-browser", default=None, help="browser for yt-dlp cookies")
    parser.add_argument("--serve", action="store_true", help="start web UI server after pipeline")
    parser.add_argument("--port", type=int, default=8960, help="server port (default 8960)")
    parser.add_argument("--clean", action="store_true", help="remove media/ before pipeline")
    parser.add_argument("--force", action="store_true", help="force re-ingest even if cached")

    args = parser.parse_args()

    repo = os.environ.get("CHOPPAD_REPO", REPO_DIR)
    media_root = os.path.join(repo, "media")

    if args.clean and os.path.isdir(media_root):
        import shutil
        shutil.rmtree(media_root)

    if args.source:
        kw = {}
        if args.cookies_from_browser:
            kw["cookies_from_browser"] = args.cookies_from_browser
        if args.force:
            kw["force"] = True
        kw["max_duration_s"] = args.max_duration

        media_dir = run_pipeline(
            args.source, media_root,
            grid=args.grid, meter=args.meter, bpm=args.bpm,
            **kw,
        )
        media_id = os.path.basename(media_dir)
    else:
        entries = os.listdir(media_root) if os.path.isdir(media_root) else []
        subdirs = sorted(e for e in entries if os.path.isdir(os.path.join(media_root, e)))
        if not subdirs:
            print("No media dirs found in", media_root, file=sys.stderr)
            sys.exit(1)
        media_id = subdirs[-1]

    if args.serve:
        print(f"Serving at http://127.0.0.1:{args.port}/?id={media_id}")
        srv = make_server(media_root, port=args.port)
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            srv.shutdown()
    else:
        print(json.dumps({"id": media_id, "media_root": media_root}))


if __name__ == "__main__":
    main()
