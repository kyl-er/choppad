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
    slice, the stretch rendering, gain, pitch and the sound params baked into the WAV (filter, envelope, mute group);
    plus bank, master fx and choke."""
    def stat(path):
        st = os.stat(path) if path else None
        return [path, st.st_size, int(st.st_mtime)] if st else None
    blob = json.dumps([KIT_FORMAT, bank, fx or {}, bool(choke)] +
                      [[p["pad"], stat(p["audio"]), p["start"], p["end"], stat(p["stretch"]), p.get("gain_db"), p.get("level", 1.0), p.get("pitch"), p.get("params")]
                       for p in pads], sort_keys=True, default=str)
    return hashlib.sha1(blob.encode()).hexdigest()[:8]


def project_beats_per_bar(proj, chops):
    """Beats per bar of the pattern: its own (`project.pattern.beatsPerBar`, contracts §8 step D), else the chop table's, else 4."""
    own = ((proj.get("pattern") or {}).get("beatsPerBar"))
    if isinstance(own, int) and not isinstance(own, bool) and 1 <= own <= 16:
        return own
    return int((chops or {}).get("beats_per_bar") or 4)


def build_project(media_dir, out_root, *, bank=None, fx="project", project=None, swing=None, quant=None, choke=None):
    """Render the kit into `out_root/<kit name>/` (skipped when that exact content is already there) and describe it.

    The audio of every pad is whatever `choppad_export.bank_pads` says it is as played in the browser (custom slice,
    slice of another song, stretch rendering) and the kit is written by `choppad_export.render_bank_kit`, the same
    two calls the Export button makes. There is no slice logic in this module.

    fx: "project" = the song's saved master FX, a dict = those settings, None = no FX.
    project: the project document (default: the project of the song's mega bank, else its old project.json).
    swing, quant, choke: the player settings that are not saved with the project (contracts section 8 `settings`);
    swing is baked into note positions by the bridges, choke decides the Hydrogen mute group. Defaults: 0, "1/16", True.
    """
    import choppad_export as X

    meta = _read(os.path.join(media_dir, "meta.json"), {})
    chops = _read(os.path.join(media_dir, "chops.json"))
    proj = project if project is not None else X.song_project(media_dir)  # CHOP-13: the song's mega bank (X.song_project)
    sid = meta.get("id") or os.path.basename(os.path.abspath(media_dir))
    return _assemble(sid, meta.get("title") or sid, proj, chops, media_dir, os.path.dirname(os.path.abspath(media_dir)), out_root,
                     bank=bank, fx=fx, swing=swing, quant=quant, choke=choke, saved_params=False)


def build_mega_project(media_root, doc, chops, media_dir, out_root, *, bank=None, fx="project", swing=None, quant=None, choke=None):
    """The same for a drum pad MEGA BANK (CHOP-13 step D): `doc` is its document, `chops` the chop table of its source (an empty
    table without one), `media_dir` the source song's folder or None. Kept pads play their sample files at their own `gain`
    and with their own params, the other pads are the source song's; the tempo is the mega bank's and the bar length the
    pattern's own. The bridge's song and kit are named after the mega bank (its id keys the song file)."""
    return _assemble(doc["id"], doc.get("name") or doc["id"], doc["project"], chops, media_dir, media_root, out_root,
                     bank=bank, fx=fx, swing=swing, quant=quant, choke=choke, saved_params=True)


def _assemble(sid, title, proj, chops, media_dir, media_root, out_root, *, bank, fx, swing, quant, choke, saved_params):
    import choppad_export as X

    n_banks = max(len(chops.get("banks") or []), 1 + max([0] + [int(k.split(":")[0]) for k in (proj.get("overrides") or {})
                                                                  if re.match(r"^\d+:\d+$", k)]))
    pattern = proj.get("pattern") or {"bars": 2, "events": []}
    bank = pick_bank(pattern, bank, n_banks)
    if fx == "project":
        fx = proj.get("fx")
    swing = max(0.0, min(0.5, float(swing))) if isinstance(swing, (int, float)) and not isinstance(swing, bool) else 0.0
    quant = quant if quant in QUANT_BEATS else "1/16"
    choke = True if choke is None else bool(choke)
    if saved_params:   # a mega bank: nothing is read from "the song's latest mega bank"; everything comes from `proj`
        pads = X.bank_pads(media_dir, bank, proj.get("overrides") or {}, proj.get("pads") or [], chops=chops, media_root=media_root)
    else:
        pads = X.bank_pads(media_dir, bank, proj.get("overrides") or {}, proj.get("pads") or [])
    key = content_key(pads, bank, fx, choke)
    kit_name = f"ChopPad {clean_name(title)} B{bank + 1} {key}"
    kit_dir = os.path.join(out_root, kit_name)
    if not os.path.isfile(os.path.join(kit_dir, "drumkit.xml")):
        stage = kit_dir + ".part"
        try:
            shutil.rmtree(stage, ignore_errors=True)
            X.render_bank_kit(pads, stage, kit_name, fx=fx, choke=choke)
            os.makedirs(out_root, exist_ok=True)
            shutil.rmtree(kit_dir, ignore_errors=True)
            os.replace(stage, kit_dir)
        finally:
            shutil.rmtree(stage, ignore_errors=True)
    by_pad = {p["pad"]: p for p in pads}
    slots = [Chop(p, os.path.join(kit_dir, f"chop_{p:02d}.wav"), 36 + p, X.pad_gain_db(by_pad[p]), by_pad[p]["pitch"])
             if p in by_pad else None for p in range(PADS)]
    # W3b: the project tempo (project "tempo": {bpm, follow}) wins over the song's own tempo
    bpm = float(((proj.get("tempo") or {}).get("bpm")) or chops.get("bpm") or 120.0)
    groups = [X.mute_group_of(by_pad[i]) if i in by_pad else 0 for i in range(PADS)]
    if any(groups):  # W3b: mute groups travel inside the pattern dict (see h2song.build_song)
        pattern = {**pattern, "mute_groups": groups}
    song = proj.get("song")
    if isinstance(song, dict) and song.get("mode") == "song" and song.get("chain") and song.get("patterns"):
        pattern = {**pattern, "song": song}  # W3b: song mode: one Hydrogen pattern per ChopPad pattern, the chain as the sequence
    return DawProject(song_id=sid, title=title, bpm=bpm, beats_per_bar=project_beats_per_bar(proj, chops), bank=bank,
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
