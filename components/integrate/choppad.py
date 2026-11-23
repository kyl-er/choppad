"""choppad CLI: `choppad <url|file> [--grid 1/4] [--meter 4/4] [--bpm N] [--max-duration 420]
   [--cookies-from-browser B] [--serve] [--port 8960] [--clean] [--force]`.

Runs ingest -> rhythm -> chop into media/<id>/, then (with --serve) serves the web UI at
http://127.0.0.1:<port>/?id=<id> with /media/<id>/* static. See contracts/README.md.

Agent verbs (clients of a running server): `choppad songs | show ID | import SRC | regrid ID | assign ID | pad ID P |
pattern ID set|add|clear|show | fx ID | play ID pattern|song|stop | hit ID P | export ID hydrogen|sfz |
daw open|sync|play|stop ID [--daw hydrogen|ardour] | station [ID] [--daw ...]`.
`choppad <verb> --help` for each; `--server URL` or $CHOPPAD_SERVER picks the server (default http://127.0.0.1:8960).
"""

import argparse
import json
import re
import os
import sys
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

# Sibling components are plain modules (no package install); make them importable when run as a CLI.
_COMPONENTS = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
for _c in ("ingest", "rhythm", "chop", "advisor", "export", "control", "daw", "stems"):
    _p = os.path.join(_COMPONENTS, _c)
    if _p not in sys.path:
        sys.path.insert(0, _p)
sys.path.insert(0, os.path.join(_COMPONENTS, "..", "contracts"))

from choppad_ingest import ingest as _ingest
from choppad_rhythm import analyze as _analyze
from choppad_chop import chop as _chop
from choppad_stretch import StretchError as _StretchError, ratio_for_tempo as _ratio_for_tempo, stretch_slice as _stretch_slice
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



def _write_json(path, doc, indent=None):
    """Write JSON through a temp file and a rename, so a request that reads the file while it is being
    rewritten (a regrid racing a pad edit) never sees half of it."""
    import tempfile
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(doc, f, indent=indent)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise

def run_pipeline(source: str, media_root: str, grid: str = "1/4", meter: str | None = None,
                 bpm: float | None = None, on_stage=None, **ingest_kw) -> str:
    stage = on_stage or (lambda name, progress: None)
    stage("download + transcode", 0.05)
    media_dir = _ingest(source, media_root, **ingest_kw)
    stage("tempo + beats", 0.6)

    audio_path = os.path.join(media_dir, "audio.wav")
    analysis = _analyze(audio_path, meter=meter, bpm=bpm)
    analysis_path = os.path.join(media_dir, "analysis.json")
    _write_json(analysis_path, analysis, indent=2)

    stage("chopping", 0.85)
    chops = {**_chop(analysis, grid=grid, meter=meter), "mode": "grid"}
    chops_path = os.path.join(media_dir, "chops.json")
    _write_json(chops_path, chops, indent=2)

    return media_dir


def _onsets(media_dir: str) -> dict:
    """Onset candidates of audio.wav, cached as onsets.json (the sensitivity slider only re-filters them)."""
    audio = os.path.join(media_dir, "audio.wav")
    path = os.path.join(media_dir, "onsets.json")
    if os.path.isfile(path) and os.path.getmtime(path) >= os.path.getmtime(audio):
        return json.load(open(path))
    table = _detect_onsets(audio)
    _write_json(path, table)
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
    _write_json(os.path.join(media_dir, "chops.json"), chops, indent=2)
    return chops


def reanalyze(media_dir: str, *, bpm=None, meter=None) -> tuple[dict, dict]:
    """Re-run rhythm with a forced tempo/meter (e.g. from tap tempo), then chop with defaults."""
    analysis = _analyze(os.path.join(media_dir, "audio.wav"), meter=meter, bpm=bpm)
    _write_json(os.path.join(media_dir, "analysis.json"), analysis, indent=2)
    return analysis, regrid(media_dir, meter=meter)


def _sections(media_dir, body):
    from choppad_structure import sections
    return sections(media_dir, force=bool(body.get("force")))


def _advise(media_dir, body):
    from choppad_advisor import advise
    return advise(media_dir, force=bool(body.get("force")))


# ---- W3: key detection ----
def _key(media_dir, body):
    """POST /api/songs/<id>/key -> {tonic, mode, confidence}; detected once and cached in analysis.json["key"]."""
    path = os.path.join(media_dir, "analysis.json")
    analysis = json.load(open(path))
    if analysis.get("key") and not body.get("force"):
        return analysis["key"]
    from choppad_key import detect_key_file
    analysis["key"] = detect_key_file(os.path.join(media_dir, "audio.wav"))
    _write_json(path, analysis, indent=2)
    return analysis["key"]


# ---- W3b: stems ----
def _stems(media_dir, body):
    """POST /api/songs/<id>/stems {force?} -> {stems: [...], urls: {name: url}, backend}. Needs a stems backend (components/stems:
    CHOPPAD_STEMS_BACKEND=remote and CHOPPAD_STEMS_URL); without one it answers 501 with the steps. Cached in media/<id>/stems/."""
    from choppad_stems import separate
    paths = separate(media_dir, force=bool(body.get("force")))
    sid = os.path.basename(media_dir)
    return {"stems": list(paths), "urls": {n: f"/media/{sid}/stems/{n}.wav" for n in paths},
            "backend": (os.environ.get("CHOPPAD_STEMS_BACKEND") or "none").lower()}


def make_server(media_root: str, port: int = 8960):
    """App + media + API server (see server.py). Import jobs run the full pipeline in a thread."""
    from server import make_server as _make
    os.makedirs(media_root, exist_ok=True)
    # --- W2 DAW bridge: POST /api/songs/<id>/daw, and mirroring of agent-driven transport (components/daw) ---
    from manager import DawManager
    fake_log = os.environ.get("CHOPPAD_DAW_FAKE")  # test hook (tests/e2e/test_bridge.py): bridges that only write a log, no DAW
    if fake_log:
        from fake_bridge import fake_bridges, no_midi
        daw = DawManager(bridges=fake_bridges(fake_log), midi_factory=no_midi)
    else:
        daw = DawManager()

    import megaops
    source_ops = {"sections": _sections, "advise": _advise, "key": _key, "stems": _stems}
    # the project operations (export, savekit, stretch, daw, and the source-song ones) are keyed by mega bank (megaops.py): the
    # song-keyed routes resolve the song's mega bank and call the same functions. The source-song ones also stay song-keyed.
    srv = _make(media_root, port, pipeline=run_pipeline, regrid=regrid, reanalyze=reanalyze, actions=source_ops,
                mega_actions=megaops.make_ops(daw, source_ops), on_transport=daw.on_transport, daw_state=daw.state_for)
    srv.daw = daw
    daw.on_change = lambda sid: srv.announce_daw(sid)  # `daw` events when a bridge opens or closes
    _shutdown = srv.shutdown

    def shutdown():
        daw.close()  # stops the Hydrogen / Ardour this server started and closes the MIDI port
        _shutdown()
    srv.shutdown = shutdown
    return srv


# ---------------------------------------------------------------------------------------------------------------
# Agent verbs: `choppad songs|show|import|regrid|assign|pad|pattern|fx|play|hit|export|daw ...`
# Thin HTTP clients of a running server (`choppad --serve`); the logic lives in components/control/choppad_client.py.
# ---------------------------------------------------------------------------------------------------------------
VERBS = ("songs", "show", "import", "regrid", "assign", "pad", "pattern", "fx", "play", "hit", "export", "daw", "station",
         "tempo", "resample", "song", "stems")


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
    s.add_argument("--decay", type=float, help="envelope decay in seconds (0-10)")
    s.add_argument("--sustain", type=float, help="envelope sustain level 0-1 (1 = no decay)")
    s.add_argument("--filter-type", choices=["off", "lp", "hp", "bp"], help="per-pad filter: off, low-pass, high-pass, band-pass")
    s.add_argument("--cutoff", type=float, help="filter cutoff in Hz (20-20000)")
    s.add_argument("--q", type=float, help="filter resonance (0.1-24, 0.7 = flat)")
    s.add_argument("--mute-group", type=int, help="mute group 0-8: a pad cuts the voices of pads in its group (0 = none)")
    s.add_argument("--reset", action="store_true", help="back to defaults first")
    s = add("pattern", "the step pattern: set | add | clear | show")
    s.add_argument("id")
    s.add_argument("op", choices=["set", "add", "clear", "show"])
    s.add_argument("--steps", help='pad:steps groups, 1/16 steps from the loop start (0-based): "1:0,8 3:4,12"')
    s.add_argument("--bars", type=float, help="loop length in bars")
    s.add_argument("--bank", type=int, help="bank the hits play (default: the bank on screen)")
    s.add_argument("--vel", type=float, default=1.0, help="velocity 0..1 (default 1)")
    s.add_argument("--pitch", type=float, help="semitones added to the pad's pitch for these hits (like 16 levels pitch)")
    s.add_argument("--cutoff", type=float, help="filter cutoff in Hz for these hits (like 16 levels filter; turns a low-pass on)")
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
    # --- W3b sampler core ---
    s = add("tempo", "project tempo: show it, set it (--bpm), go back to the song's tempo (--song), fit pads to it (--fit)")
    s.add_argument("id")
    s.add_argument("--bpm", type=float, help="project tempo in BPM (20-400)")
    s.add_argument("--song", action="store_true", help="use the song's own tempo again (fitted pads go back to plain slices)")
    s.add_argument("--follow", action=argparse.BooleanOptionalAction, default=None,
                   help="pads fitted earlier are re-fitted when the tempo changes (default on)")
    s.add_argument("--fit", choices=["pattern", "bank"], help="stretch the pads of the pattern, or of the bank on screen, to the tempo now")
    s = add("song", "song mode: patterns and the chain that plays them. ops: show | add | select | rename | remove | chain | mode")
    s.add_argument("id")
    s.add_argument("op", choices=["show", "add", "select", "rename", "remove", "chain", "mode"])
    s.add_argument("arg", nargs="*", help="select/remove PATTERN; rename PATTERN NAME; chain 'p1:2 p2'; mode pattern|song")
    s.add_argument("--name", help="add: name of the new pattern")
    s.add_argument("--bars", type=float, help="add: its length in bars (default: the edited pattern's)")
    s.add_argument("--copy", action="store_true", help="add: start from a copy of the edited pattern")
    s = add("stems", "split the song into drums, bass, vocals and other (needs a stems backend on the server), optionally put a stem on a pad")
    s.add_argument("id")
    s.add_argument("--force", action="store_true", help="ask the backend again even when the stems are cached")
    s.add_argument("--pad", type=int, help="pad (0-15) that should play its slice from a stem")
    s.add_argument("--bank", type=int, help="bank of that pad (default: the bank on screen)")
    s.add_argument("--stem", choices=["drums", "bass", "vocals", "other", "mix"], help="the stem for --pad ('mix' = the full song again)")
    s = add("resample", "render the pattern (or record the master bus) into a new sound on a pad; the open browser does the work")
    s.add_argument("id")
    s.add_argument("--pad", type=int, required=True, help="pad (0-15) that gets the new sound")
    s.add_argument("--bars", type=float, help="length in bars at the project tempo")
    s.add_argument("--seconds", type=float, help="length in seconds (instead of --bars)")
    s.add_argument("--source", choices=["pattern", "master"], default="pattern",
                   help="pattern = offline render of the step pattern (default); master = live recording of the master bus")
    s.add_argument("--bank", type=int, help="bank of the pad (default: the bank on screen)")
    s.add_argument("--wait", type=float, default=120, help="seconds to wait for the browser (default 120)")
    # --- W2 DAW bridge (components/daw) ---
    s = add("daw", "DAW bridge: open a song in Hydrogen or Ardour, re-sync it after edits, drive its transport")
    s.add_argument("op", choices=["open", "sync", "play", "stop"])
    s.add_argument("id", nargs="?", help="song id (required)")
    s.add_argument("--daw", choices=["hydrogen", "ardour"], default="hydrogen")
    s.add_argument("--bank", type=int, help="bank to export (default: the bank the pattern plays, else 0)")
    s.add_argument("--swing", type=float, help="0..0.5, bake this swing into the DAW pattern (the browser's swing is not saved)")
    s.add_argument("--quant", choices=["off", "1/8", "1/16"], help="the step swing refers to (default 1/16)")
    s.add_argument("--choke", action=argparse.BooleanOptionalAction, default=None, help="pads cut each other off (default on)")
    s = add("station", "start ChopPad (if it is not running), open a song in the DAW wired to it, print the URL")
    s.add_argument("id", nargs="?", help="song id (default: the newest song in the library)")
    s.add_argument("--daw", choices=["hydrogen", "ardour"], default="hydrogen")
    s.add_argument("--bank", type=int)
    s.add_argument("--port", type=int, default=8960, help="port for the server this command starts (default 8960)")
    return p


def _station(a, c, out, err):
    """`choppad station`: make sure a server answers, open the song in the DAW through it, print where everything is.
    A server this command starts runs until Ctrl-C (it owns the DAW it launched); an existing server keeps its bridge."""
    import threading
    import choppad_client as cc
    base = (a.server or os.environ.get("CHOPPAD_SERVER") or f"http://127.0.0.1:{a.port}").rstrip("/")
    started = None
    try:
        try:
            c.server = base
            c.songs()
        except cc.ControlError:
            if a.server or os.environ.get("CHOPPAD_SERVER"):
                raise  # an explicit server that does not answer is an error, not a reason to start another
            repo = os.environ.get("CHOPPAD_REPO", REPO_DIR)
            started = make_server(os.path.join(repo, "media"), port=a.port)
            threading.Thread(target=started.serve_forever, daemon=True).start()
            err(f"started ChopPad server on {base}")
            import signal
            signal.signal(signal.SIGINT, signal.default_int_handler)   # a backgrounded job inherits SIGINT=ignored
            signal.signal(signal.SIGTERM, signal.default_int_handler)  # so `kill` also closes the DAW cleanly
        sid = a.id or next(iter(c.songs()), {}).get("id")
        if not sid:
            raise cc.ControlError("the library is empty; import a song first (`choppad import <url|file>`)")
        r = c.daw(sid, a.daw, "open", a.bank)
        out(json.dumps({k: r[k] for k in r if k != "detail"}, separators=(",", ":")))
        out(f"ChopPad: {base}/#/song/{sid}")
        if r.get("detail"):
            out(f"{a.daw}: " + json.dumps({k: v for k, v in r["detail"].items() if k in ("song", "osc_port", "headless", "file", "pid", "tracks", "regions")}))
        if r.get("not_implemented"):
            err("note: " + r["not_implemented"])
        if r.get("midi", {}).get("port"):
            out(f"MIDI out: ALSA port '{r['midi']['port']}' ({r['midi'].get('address')}) carries notes, clock and start/stop")
        sys.stdout.flush()
        if started is not None:
            out("station is running; Ctrl-C (or SIGTERM) stops the server and the DAW it started")
            sys.stdout.flush()
            try:
                while True:
                    time.sleep(3600)
            except KeyboardInterrupt:
                pass
        else:
            out("(the server that was already running keeps the DAW bridge; stop it there)")
    finally:
        if started is not None:
            started.shutdown()
            started.server_close()
    return 0


def run_verb(argv, client=None, out=None, err=None):
    """Run one verb; returns the exit code. `client` and the streams are injectable for tests."""
    import choppad_client as cc
    out = out or (lambda s: print(s))
    err = err or (lambda s: print(s, file=sys.stderr))
    a = build_verb_parser().parse_args(argv)
    j = lambda o: out(json.dumps(o, separators=(",", ":")))  # noqa: E731
    if a.verb == "daw" and not a.id:  # explicit on purpose: a DAW should never open the "newest song" by accident
        err("daw: a song id is required (see `choppad songs`); `choppad station` opens the newest song. "
            "DAW bridge: components/daw, workstream W2")
        return 2
    c = client or cc.Client(a.server)
    if a.verb == "station":
        try:
            return _station(a, c, out, err)
        except cc.ControlError as e:
            err(f"choppad: {e}")
            return 1

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
            j(c.import_source(a.source, wait_s=a.wait, progress=lambda ev: err(f"import: {ev.get('step')} {ev.get('progress') or 0:.0%}")))
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
                        reverse=a.reverse, mode=a.mode, decay=a.decay, sustain=a.sustain, filter_type=a.filter_type,
                        cutoff=a.cutoff, q=a.q, mute_group=a.mute_group))
        elif a.verb == "pattern":
            if a.op == "show":
                out(c.pattern_view(a.id, a.bank))
            elif a.op == "clear":
                reply(c.pattern_clear(a.id))
            else:
                reply(c.pattern_set(a.id, a.steps, a.bars, a.bank, a.vel, add=a.op == "add", pitch=a.pitch, cutoff=a.cutoff))
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
        elif a.verb == "stems":
            if a.stem is not None and a.pad is None:
                raise cc.ControlError("--stem needs --pad")
            r = c.stems(a.id, a.force)
            j(r)
            if a.pad is not None:
                bank = a.bank if a.bank is not None else c.current_bank(a.id)
                reply(c.pad_stem(a.id, bank, a.pad, None if a.stem == "mix" else (a.stem or "drums")))
        elif a.verb == "song":
            if a.op == "show":
                j(c.song_show(a.id))
            else:
                one = a.arg[0] if a.arg else None
                reply(c.song_edit(a.id, a.op, pattern=one if a.op in ("select", "rename", "remove") else None,
                                  name=(" ".join(a.arg[1:]) or None) if a.op == "rename" else a.name, bars=a.bars, copy=a.copy,
                                  chain=" ".join(a.arg) if a.op == "chain" else None, mode=one if a.op == "mode" else None))
        elif a.verb == "resample":
            j(c.resample(a.id, a.pad, a.bars, a.seconds, a.source, a.bank, a.wait))
        elif a.verb == "tempo":
            j(c.tempo(a.id, a.bpm, a.song, a.follow, a.fit))
        elif a.verb == "daw":
            r = c.daw(a.id, a.daw, a.op, a.bank, **{k: getattr(a, k) for k in ("swing", "quant", "choke") if getattr(a, k) is not None})
            j(r)
            if r.get("not_implemented"):
                err("note: " + r["not_implemented"])
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
