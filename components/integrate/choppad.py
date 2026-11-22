"""choppad CLI: `choppad <url|file> [--grid 1/4] [--meter 4/4] [--bpm N] [--max-duration 420]
   [--cookies-from-browser B] [--serve] [--port 8960] [--clean] [--force]`.

Runs ingest -> rhythm -> chop into media/<id>/, then (with --serve) serves the web UI at
http://127.0.0.1:<port>/?id=<id> with /media/<id>/* static. See contracts/README.md.

Agent verbs (clients of a running server): `choppad songs | show ID | import SRC | regrid ID | assign ID | pad ID P |
pattern ID set|add|clear|show | fx ID | play ID pattern|song|stop | hit ID P | export ID hydrogen|sfz | daw open|sync`.
`choppad <verb> --help` for each; `--server URL` or $CHOPPAD_SERVER picks the server (default http://127.0.0.1:8960).
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
for _c in ("ingest", "rhythm", "chop", "advisor", "export", "control"):
    _p = os.path.join(_COMPONENTS, _c)
    if _p not in sys.path:
        sys.path.insert(0, _p)
sys.path.insert(0, os.path.join(_COMPONENTS, "..", "contracts"))

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
    chops = _chop(analysis, grid=grid, meter=meter)
    chops_path = os.path.join(media_dir, "chops.json")
    with open(chops_path, "w") as f:
        json.dump(chops, f, indent=2)

    return media_dir


def regrid(media_dir: str, *, grid="1/4", meter=None, beat_offset=0, bpm_mult=1) -> dict:
    """Re-run chop on media_dir/analysis.json, write chops.json, return it."""
    analysis = json.load(open(os.path.join(media_dir, "analysis.json")))
    chops = _chop(analysis, grid=grid, meter=meter, beat_offset=beat_offset, bpm_mult=bpm_mult)
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


# ---------------------------------------------------------------------------------------------------------------
# Agent verbs: `choppad songs|show|import|regrid|assign|pad|pattern|fx|play|hit|export|daw ...`
# Thin HTTP clients of a running server (`choppad --serve`); the logic lives in components/control/choppad_client.py.
# ---------------------------------------------------------------------------------------------------------------
VERBS = ("songs", "show", "import", "regrid", "assign", "pad", "pattern", "fx", "play", "hit", "export", "daw")


def build_verb_parser():
    import choppad_client as cc
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--server", default=None, help="server URL (default $CHOPPAD_SERVER or http://127.0.0.1:8960)")
    p = argparse.ArgumentParser(prog="choppad", description="Drive a running ChopPad server (start it with `choppad <url> --serve`).")
    sub = p.add_subparsers(dest="verb", required=True)
    add = lambda name, help_: sub.add_parser(name, parents=[common], help=help_, description=help_)  # noqa: E731

    add("songs", "list the library")
    s = add("show", "one song: title, bpm, meter, banks, sections, pattern size")
    s.add_argument("id")
    s = add("import", "import a URL or a local audio/video file (waits for the job)")
    s.add_argument("source")
    s.add_argument("--wait", type=float, default=900, help="seconds to wait for the job (default 900)")
    s = add("regrid", "change the chop grid / meter / tempo multiplier / bar-1 offset, or re-run tempo with --bpm")
    s.add_argument("id")
    s.add_argument("--grid", choices=["1/4", "1/8", "1/16"])
    s.add_argument("--meter", help="e.g. 3/4")
    s.add_argument("--mult", type=float, choices=[0.5, 2], help="halve or double the tempo grid (relative)")
    s.add_argument("--bpm-mult", type=float, choices=[0.5, 1, 2], help="set the tempo multiplier (absolute)")
    s.add_argument("--shift", type=int, help="move bar 1 by N beats (relative)")
    s.add_argument("--beat-offset", type=int, help="set the bar-1 offset in beats (absolute)")
    s.add_argument("--bpm", type=float, help="re-analyse with this tempo (like tap tempo)")
    s = add("assign", "put a custom slice on a pad (seconds from the start of the song)")
    s.add_argument("id")
    s.add_argument("--bank", type=int, required=True)
    s.add_argument("--pad", type=int, required=True)
    s.add_argument("--start", type=float)
    s.add_argument("--end", type=float)
    s.add_argument("--clear", action="store_true", help="back to the grid chop")
    s = add("pad", "sound settings of one pad")
    s.add_argument("id")
    s.add_argument("pad", type=int)
    s.add_argument("--gain", type=float, help="dB")
    s.add_argument("--pitch", type=float, help="semitones")
    s.add_argument("--attack", type=float, help="seconds")
    s.add_argument("--release", type=float, help="seconds")
    s.add_argument("--reverse", action=argparse.BooleanOptionalAction, default=None)
    s.add_argument("--mode", choices=["oneshot", "gate", "playthrough", "default"])
    s.add_argument("--reset", action="store_true", help="back to defaults first")
    s = add("pattern", "the step pattern: set | add | clear | show")
    s.add_argument("id")
    s.add_argument("op", choices=["set", "add", "clear", "show"])
    s.add_argument("--steps", help='pad:steps groups, 1/16 steps from the loop start (0-based): "1:0,8 3:4,12"')
    s.add_argument("--bars", type=float, help="loop length in bars")
    s.add_argument("--bank", type=int, help="bank the hits play (default: the bank on screen)")
    s.add_argument("--vel", type=float, default=1.0, help="velocity 0..1 (default 1)")
    s = add("fx", "master effects: a preset and/or single knobs")
    s.add_argument("id")
    s.add_argument("--preset", choices=list(cc.PRESETS))
    for k in cc.FX_KEYS:
        s.add_argument(f"--{k}", type=float)
    s = add("play", "pattern | song | stop in the open browser")
    s.add_argument("id")
    s.add_argument("what", choices=["pattern", "song", "stop"])
    s.add_argument("--from", dest="start", type=float, help="song start time in seconds")
    s = add("hit", "play one pad in the open browser")
    s.add_argument("id")
    s.add_argument("pad", type=int)
    s.add_argument("--velocity", type=float, default=1.0)
    s.add_argument("--bank", type=int)
    s = add("export", "export a bank as a Hydrogen kit or an SFZ file")
    s.add_argument("id")
    s.add_argument("kind", choices=["hydrogen", "sfz"])
    s.add_argument("--bank", type=int, default=0)
    s = add("daw", "DAW bridge (not built yet)")
    s.add_argument("op", choices=["open", "sync"])
    s.add_argument("id", nargs="?")
    return p


def run_verb(argv, client=None, out=None, err=None):
    """Run one verb; returns the exit code. `client` and the streams are injectable for tests."""
    import choppad_client as cc
    out = out or (lambda s: print(s))
    err = err or (lambda s: print(s, file=sys.stderr))
    a = build_verb_parser().parse_args(argv)
    j = lambda o: out(json.dumps(o, separators=(",", ":")))  # noqa: E731
    if a.verb == "daw":
        # TODO(W2, components/daw): `choppad daw open|sync <id>` -> POST /api/songs/<id>/daw (contracts §11). W1 only reserves the verb.
        err("daw: not implemented yet (owned by workstream W2)")
        return 2
    c = client or cc.Client(a.server)

    def reply(r):  # what an edit answered: how much changed and whether anyone is watching
        j({k: r[k] for k in ("applied", "ignored", "listeners") if k in r})
        if r.get("listeners") == 0:
            err("note: no browser has this song open; the change is saved and will show when one does")

    try:
        if a.verb == "songs":
            rows = [{"id": s["id"], "bpm": round(s["bpm"], 1), "meter": s["meter"], "banks": s["banks"],
                     "dur": f"{s['duration_s']:.0f}s", "title": s["title"]} for s in c.songs()]
            out(cc.format_table(rows, ["id", "bpm", "meter", "banks", "dur", "title"]))
        elif a.verb == "show":
            j(c.summary(a.id))
        elif a.verb == "import":
            j(c.import_source(a.source, wait_s=a.wait, progress=lambda job: err(f"{job.get('stage')} {job.get('progress', 0):.0%}")))
        elif a.verb == "regrid":
            if a.bpm is not None:
                chops = c.reanalyze(a.id, a.bpm, a.meter)
            else:
                ch = {k: v for k, v in (("grid", a.grid), ("meter", a.meter), ("mult", a.mult), ("bpm_mult", a.bpm_mult),
                                        ("shift", a.shift), ("beat_offset", a.beat_offset)) if v is not None}
                if not ch:
                    raise cc.ControlError("nothing to change; give --grid/--meter/--mult/--bpm-mult/--shift/--beat-offset/--bpm")
                chops = c.regrid_to(a.id, **ch)
            j({"grid": chops["grid"], "bpm": round(chops["bpm"], 2), "meter": f"{chops['beats_per_bar']}/4",
               "chops": len(chops["chops"]), "banks": len(chops["banks"])})
        elif a.verb == "assign":
            reply(c.assign(a.id, a.bank, a.pad, a.start, a.end, a.clear))
        elif a.verb == "pad":
            reply(c.pad(a.id, a.pad, a.reset, gain=a.gain, pitch=a.pitch, attack=a.attack, release=a.release,
                        reverse=a.reverse, mode=a.mode))
        elif a.verb == "pattern":
            if a.op == "show":
                out(c.pattern_view(a.id, a.bank))
            elif a.op == "clear":
                reply(c.pattern_clear(a.id))
            else:
                reply(c.pattern_set(a.id, a.steps, a.bars, a.bank, a.vel, add=a.op == "add"))
        elif a.verb == "fx":
            reply(c.fx(a.id, a.preset, **{k: getattr(a, k) for k in cc.FX_KEYS}))
        elif a.verb == "play":
            r = c.play(a.id, a.what, a.start)
            j(r)
            if r.get("listeners") == 0:
                err("note: no browser has this song open, so nothing is playing")
        elif a.verb == "hit":
            r = c.hit(a.id, a.pad, a.velocity, a.bank)
            j(r)
            if r.get("listeners") == 0:
                err("note: no browser has this song open, so nothing is playing")
        elif a.verb == "export":
            j(c.export(a.id, a.kind, a.bank))
    except cc.ControlError as e:
        err(f"choppad: {e}")
        return 1
    return 0


def main():
    if len(sys.argv) > 1 and sys.argv[1] in VERBS:
        sys.exit(run_verb(sys.argv[1:]))
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
