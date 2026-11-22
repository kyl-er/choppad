"""DawProject (contracts §11): everything a bridge needs to put one bank + pattern into a DAW.

`build_project(media_dir, out_dir, ...)` renders the bank's pads to WAV files with the exporter the Export button uses
(`choppad_export.bank_pads` + `render_bank_kit`: custom slices, slices of other songs, stretch renderings, master FX
baked in), and returns the dataclass. Pure file work; no DAW is started.
"""
import hashlib
import json
import os
import re
import shutil
import sys
from dataclasses import dataclass

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "export"))

PADS = 16


@dataclass
class Chop:
    pad: int           # 0..15
    path: str          # rendered WAV (44.1 kHz stereo s16, FX baked in)
    midi_note: int     # 36 + pad
    gain_db: float = 0.0   # the pad's gain; the kit and song instruments carry it (Hydrogen `gain`), the WAV does not
    pitch: float = 0.0     # the pad's pitch in semitones (Hydrogen `pitchOffset`)


@dataclass
class DawProject:
    song_id: str
    title: str
    bpm: float
    beats_per_bar: int
    bank: int
    chops: list        # 16 slots, Chop or None for an empty pad
    pattern: dict      # contracts §8 {bars, events:[{beat, pad, bank, vel, pass}]}
    kit_name: str = "ChopPad"
    kit_dir: str = ""  # folder holding chop_00.wav.. and drumkit.xml
    key: str = ""      # content hash: changes when the sound changes (kit name, Hydrogen cache busting)
    swing: float = 0.0     # 0..0.5, delays odd quantize steps (see swung_beat); baked into note positions by the bridges
    quant: str = "1/16"    # the step swing refers to: "off" | "1/8" | "1/16"
    choke: bool = True     # all pads in one Hydrogen mute group (ChopPad's choke setting)

    def wavs(self):
        return [c.path if c else None for c in self.chops]


def _read(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        if default is not None:
            return default
        raise


def clean_name(text, limit=40):
    return re.sub(r"[^\w -]+", "", text or "").strip()[:limit].strip() or "Song"


def pick_bank(pattern, requested=None, n_banks=1):
    """The bank to export: the requested one, else the single bank the pattern plays, else 0."""
    if requested is not None:
        return max(0, min(int(requested), n_banks - 1))
    banks = {int(e.get("bank", 0)) for e in (pattern or {}).get("events", []) if isinstance(e, dict)}
    return banks.pop() if len(banks) == 1 and 0 <= next(iter(banks)) < n_banks else 0


KIT_FORMAT = "2"  # bump when the kit's files change shape, so Hydrogen never reuses a cached older kit of the same name

QUANT_BEATS = {"off": 0.0, "1/8": 0.5, "1/16": 0.25}


def swung_beat(beat, quant, swing):
    """The browser sequencer's swing (components/ui/sequencer.js `swungBeat`): every odd multiple of the quantize step
    `quant` (beats; 0.25 for 1/16) is delayed by `swing` * `quant` beats. Off-grid hits are left alone."""
    if not quant or not swing:
        return beat
    step = round(beat / quant)
    return beat + swing * quant if abs(beat - step * quant) < 1e-6 and step % 2 == 1 else beat


def content_key(pads, bank, fx, choke):
    """Hash of everything that decides what the kit sounds like: for every pad the audio file (path, size, mtime), the
    slice, the stretch rendering, gain and pitch; plus bank, master fx and choke."""
    def stat(path):
        st = os.stat(path) if path else None
        return [path, st.st_size, int(st.st_mtime)] if st else None
    blob = json.dumps([KIT_FORMAT, bank, fx or {}, bool(choke)] +
                      [[p["pad"], stat(p["audio"]), p["start"], p["end"], stat(p["stretch"]), p.get("gain_db"), p.get("pitch")]
                       for p in pads], sort_keys=True, default=str)
    return hashlib.sha1(blob.encode()).hexdigest()[:8]


def build_project(media_dir, out_root, *, bank=None, fx="project", project=None, swing=None, quant=None, choke=None):
    """Render the kit into `out_root/<kit name>/` (skipped when that exact content is already there) and describe it.

    The audio of every pad is whatever `choppad_export.bank_pads` says it is as played in the browser (custom slice,
    slice of another song, stretch rendering) and the kit is written by `choppad_export.render_bank_kit`, the same
    two calls the Export button makes. There is no slice logic in this module.

    fx: "project" = the song's saved master FX, a dict = those settings, None = no FX.
    project: the project.json document (default: read from media_dir).
    swing, quant, choke: the player settings that are not saved with the project (contracts section 8 `settings`);
    swing is baked into note positions by the bridges, choke decides the Hydrogen mute group. Defaults: 0, "1/16", True.
    """
    import choppad_export as X

    meta = _read(os.path.join(media_dir, "meta.json"), {})
    chops = _read(os.path.join(media_dir, "chops.json"))
    proj = project if project is not None else _read(os.path.join(media_dir, "project.json"), {})
    pattern = proj.get("pattern") or {"bars": 2, "events": []}
    bank = pick_bank(pattern, bank, len(chops["banks"]))
    if fx == "project":
        fx = proj.get("fx")
    swing = max(0.0, min(0.5, float(swing))) if isinstance(swing, (int, float)) and not isinstance(swing, bool) else 0.0
    quant = quant if quant in QUANT_BEATS else "1/16"
    choke = True if choke is None else bool(choke)
    pads = X.bank_pads(media_dir, bank, proj.get("overrides") or {}, proj.get("pads") or [])
    sid = meta.get("id") or os.path.basename(os.path.abspath(media_dir))
    title = meta.get("title") or sid
    key = content_key(pads, bank, fx, choke)
    kit_name = f"ChopPad {clean_name(title)} B{bank + 1} {key}"
    kit_dir = os.path.join(out_root, kit_name)
    if not os.path.isfile(os.path.join(kit_dir, "drumkit.xml")):
        stage = kit_dir + ".part"
        shutil.rmtree(stage, ignore_errors=True)
        X.render_bank_kit(pads, stage, kit_name, fx=fx, choke=choke)
        os.makedirs(out_root, exist_ok=True)
        shutil.rmtree(kit_dir, ignore_errors=True)
        os.replace(stage, kit_dir)
    by_pad = {p["pad"]: p for p in pads}
    slots = [Chop(p, os.path.join(kit_dir, f"chop_{p:02d}.wav"), 36 + p, by_pad[p]["gain_db"], by_pad[p]["pitch"])
             if p in by_pad else None for p in range(PADS)]
    bpm = float(chops.get("bpm") or 120.0)
    return DawProject(song_id=sid, title=title, bpm=bpm, beats_per_bar=int(chops.get("beats_per_bar") or 4), bank=bank,
                      chops=slots, pattern=pattern, kit_name=kit_name, kit_dir=kit_dir, key=key,
                      swing=swing, quant=quant, choke=choke)


def prune_kits(out_root, keep, keep_n=3):
    """Drop old content-hashed kit folders of the same song+bank, keeping the newest `keep_n` and `keep`."""
    prefix = keep.rsplit(" ", 1)[0] + " "
    mine = sorted((d for d in os.listdir(out_root) if d.startswith(prefix) and os.path.isdir(os.path.join(out_root, d))),
                  key=lambda d: os.path.getmtime(os.path.join(out_root, d)), reverse=True)
    for d in mine[keep_n:]:
        if d != keep:
            shutil.rmtree(os.path.join(out_root, d), ignore_errors=True)
