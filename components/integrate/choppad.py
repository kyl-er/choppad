"""choppad CLI: `choppad <url|file> [--grid 1/4] [--meter 4/4] [--bpm N] [--max-duration 420]
   [--cookies-from-browser B] [--serve] [--port 8960] [--clean] [--force]`.

Runs ingest -> rhythm -> chop into media/<id>/, then (with --serve) serves the web UI at
http://127.0.0.1:<port>/?id=<id> with /media/<id>/* static. See contracts/README.md.
"""

import argparse
import json
import re
import os
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

# Sibling components are plain modules (no package install); make them importable when run as a CLI.
_COMPONENTS = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
for _c in ("ingest", "rhythm", "chop", "advisor", "export"):
    _p = os.path.join(_COMPONENTS, _c)
    if _p not in sys.path:
        sys.path.insert(0, _p)
sys.path.insert(0, os.path.join(_COMPONENTS, "..", "contracts"))

from choppad_ingest import ingest as _ingest
from choppad_rhythm import analyze as _analyze
from choppad_chop import chop as _chop
from choppad_transient import chop_transients as _chop_transients, detect_onsets as _detect_onsets

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
                 bpm: float | None = None, on_stage=None, **ingest_kw) -> str:
    stage = on_stage or (lambda name, progress: None)
    stage("download + transcode", 0.05)
    media_dir = _ingest(source, media_root, **ingest_kw)
    stage("tempo + beats", 0.6)

    audio_path = os.path.join(media_dir, "audio.wav")
    analysis = _analyze(audio_path, meter=meter, bpm=bpm)
    analysis_path = os.path.join(media_dir, "analysis.json")
    with open(analysis_path, "w") as f:
        json.dump(analysis, f, indent=2)

    stage("chopping", 0.85)
    chops = {**_chop(analysis, grid=grid, meter=meter), "mode": "grid"}
    chops_path = os.path.join(media_dir, "chops.json")
    with open(chops_path, "w") as f:
        json.dump(chops, f, indent=2)

    return media_dir


def _onsets(media_dir: str) -> dict:
    """Onset candidates of audio.wav, cached as onsets.json (the sensitivity slider only re-filters them)."""
    audio = os.path.join(media_dir, "audio.wav")
    path = os.path.join(media_dir, "onsets.json")
    if os.path.isfile(path) and os.path.getmtime(path) >= os.path.getmtime(audio):
        return json.load(open(path))
    table = _detect_onsets(audio)
    with open(path, "w") as f:
        json.dump(table, f)
    return table


def regrid(media_dir: str, *, grid="1/4", meter=None, beat_offset=0, bpm_mult=1, mode="grid", sensitivity=0.5) -> dict:
    """Re-run chop on media_dir/analysis.json, write chops.json, return it.
    mode="transient": slices start at detected onsets (sensitivity 0..1) instead of on the tempo grid."""
    analysis = json.load(open(os.path.join(media_dir, "analysis.json")))
    if mode == "transient":
        sens = float(0.5 if sensitivity is None else sensitivity)
        chops = _chop_transients(_onsets(media_dir), analysis, sensitivity=sens, meter=meter, beat_offset=beat_offset)
    elif mode in (None, "grid"):
        chops = {**_chop(analysis, grid=grid, meter=meter, beat_offset=beat_offset, bpm_mult=bpm_mult), "mode": "grid"}
    else:
        raise ValueError("mode must be grid or transient")
    with open(os.path.join(media_dir, "chops.json"), "w") as f:
        json.dump(chops, f, indent=2)
    return chops


def reanalyze(media_dir: str, *, bpm=None, meter=None) -> tuple[dict, dict]:
    """Re-run rhythm with a forced tempo/meter (e.g. from tap tempo), then chop with defaults."""
    analysis = _analyze(os.path.join(media_dir, "audio.wav"), meter=meter, bpm=bpm)
    with open(os.path.join(media_dir, "analysis.json"), "w") as f:
        json.dump(analysis, f, indent=2)
    return analysis, regrid(media_dir, meter=meter)


def _sections(media_dir, body):
    from choppad_structure import sections
    return sections(media_dir, force=bool(body.get("force")))


def _advise(media_dir, body):
    from choppad_advisor import advise
    return advise(media_dir, force=bool(body.get("force")))


def _export(media_dir, body):
    """kind=hydrogen: install the bank as a Hydrogen drumkit (with body.fx baked in via pedalboard);
    kind=sfz: write <id>-bank<N>.sfz next to audio.wav (zero-copy, so no fx)."""
    from choppad_export import export_hydrogen, export_sfz
    bank = int(body.get("bank") or 0)
    sid = os.path.basename(media_dir)
    title = json.load(open(os.path.join(media_dir, "meta.json"))).get("title", sid)
    if body.get("kind") == "hydrogen":
        name = re.sub(r"[^\w -]+", "", f"ChopPad {title} B{bank + 1}")[:60].strip()
        out = os.path.expanduser(f"~/.local/share/hydrogen/data/drumkits/{name}")
        export_hydrogen(media_dir, out, bank=bank, kit_name=name)
        note = "Restart Hydrogen or use Drumkits > Rescan to see it."
        fx = body.get("fx")
        if fx:
            from choppad_fx import apply_to_file, is_neutral
            if not is_neutral(fx):
                for wav in sorted(os.listdir(out)):
                    if wav.endswith(".wav"):
                        apply_to_file(os.path.join(out, wav), fx)
                note = "Tone/reverb baked into the samples. " + note
        return {"path": out, "kit": name, "note": note}
    path = export_sfz(media_dir, bank=bank)
    return {"path": os.path.abspath(path) if path else os.path.join(media_dir, f"{sid}-bank{bank}.sfz")}


def make_server(media_root: str, port: int = 8960):
    """App + media + API server (see server.py). Import jobs run the full pipeline in a thread."""
    from server import make_server as _make
    os.makedirs(media_root, exist_ok=True)
    return _make(media_root, port, pipeline=run_pipeline, regrid=regrid, reanalyze=reanalyze,
                 actions={"sections": _sections, "advise": _advise, "export": _export})


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
    parser.add_argument("--advise", action="store_true", help="run OpenRouter advisor on media_dir")
    parser.add_argument("--export-sfz", nargs="?", const="", default=None, help="export SFZ file (optional path)")
    parser.add_argument("--export-hydrogen", default=None, help="export Hydrogen drumkit to directory")

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
        if not subdirs and not args.serve:
            print("No media dirs found in", media_root, file=sys.stderr)
            sys.exit(1)
        media_id = subdirs[-1] if subdirs else None
        media_dir = os.path.join(media_root, media_id) if media_id else None

    if args.advise:
        from choppad_advisor import advise as _advise
        _advise(media_dir, force=args.force)

    if args.export_sfz is not None:
        from choppad_export import export_sfz as _export_sfz
        sfz_out = args.export_sfz if args.export_sfz else None
        _export_sfz(media_dir, out_path=sfz_out)

    if args.export_hydrogen:
        from choppad_export import export_hydrogen as _export_hydrogen
        _export_hydrogen(media_dir, args.export_hydrogen)

    if args.serve:
        print(f"ChopPad: http://127.0.0.1:{args.port}/" + (f"#/song/{media_id}" if media_id else ""))
        srv = make_server(media_root, port=args.port)
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            srv.shutdown()
    else:
        print(json.dumps({"id": media_id, "media_root": media_root}))


if __name__ == "__main__":
    main()
