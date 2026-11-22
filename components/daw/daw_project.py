"""DawProject (contracts §11): everything a bridge needs to put one bank + pattern into a DAW.

`build_project(media_dir, out_dir, ...)` renders the bank's chops to WAV files with the existing exporter
(`choppad_export.export_hydrogen`, master FX baked in with `choppad_fx` as the Export button does), honouring custom
slices (`project.json overrides`), and returns the dataclass. Pure file work; no DAW is started.
"""
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "export"))

PADS = 16


@dataclass
class Chop:
    pad: int           # 0..15
    path: str          # rendered WAV (44.1 kHz stereo s16, FX baked in)
    midi_note: int     # 36 + pad


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


def effective_chops(chops, overrides, bank):
    """chops.json with this bank's custom slices applied: an overridden pad gets a new chop entry with the custom
    start/end, so the exporter (which only reads chops.json) cuts what the pad plays in the browser."""
    out = json.loads(json.dumps(chops))
    row = list(out["banks"][bank])
    for pad in range(len(row)):
        o = (overrides or {}).get(f"{bank}:{pad}")
        if isinstance(o, dict) and isinstance(o.get("start"), (int, float)) and isinstance(o.get("end"), (int, float)) \
                and o["end"] > o["start"]:
            out["chops"].append({"index": len(out["chops"]), "start": float(o["start"]), "end": float(o["end"]),
                                 "bar": 0, "beat": 0})
            row[pad] = len(out["chops"]) - 1
    out["banks"] = list(out["banks"])
    out["banks"][bank] = row
    return out


def content_key(media_dir, chops, bank, fx):
    audio = os.path.join(media_dir, "audio.wav")
    st = os.stat(audio)
    blob = json.dumps([chops["chops"][i] if i < len(chops["chops"]) else None for i in chops["banks"][bank]] +
                      [bank, fx or {}, st.st_size, int(st.st_mtime)], sort_keys=True, default=str)
    return hashlib.sha1(blob.encode()).hexdigest()[:8]


def pads_key(media_dir, pads, bank, fx):
    """Content hash of what the kit sounds like: the exporter's pads (slices, stretch renderings, other songs, filter and
    envelope), the bank, the master fx and the song's audio file. A different sound is a different kit folder."""
    import choppad_voice as V
    st = os.stat(os.path.join(media_dir, "audio.wav"))
    blob = json.dumps([[p["pad"], p["start"], p["end"], p["audio"], p["stretch"], V.params_of(p.get("params"))] for p in pads] +
                      [bank, fx or {}, st.st_size, int(st.st_mtime)], sort_keys=True, default=str)
    return hashlib.sha1(blob.encode()).hexdigest()[:8]


def build_project(media_dir, out_root, *, bank=None, fx="project", project=None):
    """Render the kit into `out_root/<kit name>/` (skipped when that exact content is already there) and describe it.

    fx: "project" = the song's saved master FX, a dict = those settings, None = no FX.
    project: the project.json document (default: read from media_dir).
    """
    import choppad_export as X

    meta = _read(os.path.join(media_dir, "meta.json"), {})
    chops = _read(os.path.join(media_dir, "chops.json"))
    proj = project if project is not None else _read(os.path.join(media_dir, "project.json"), {})
    pattern = proj.get("pattern") or {"bars": 2, "events": []}
    bank = pick_bank(pattern, bank, len(chops["banks"]))
    # W3b: the kit is built from the exporter's pad list, so custom slices, stretched pads, pads from other songs and each
    # pad's filter and envelope (baked into the WAV) all reach the DAW; unusable overrides are skipped
    overrides = {k: o for k, o in (proj.get("overrides") or {}).items() if isinstance(o, dict)
                 and isinstance(o.get("start"), (int, float)) and isinstance(o.get("end"), (int, float)) and o["end"] > o["start"]}
    pads = X.bank_pads(media_dir, bank, overrides, proj.get("pads"))
    if fx == "project":
        fx = proj.get("fx")
    sid = meta.get("id") or os.path.basename(os.path.abspath(media_dir))
    title = meta.get("title") or sid
    key = pads_key(media_dir, pads, bank, fx)
    kit_name = f"ChopPad {clean_name(title)} B{bank + 1} {key}"
    kit_dir = os.path.join(out_root, kit_name)
    if not os.path.isfile(os.path.join(kit_dir, "drumkit.xml")):
        try:
            stage = kit_dir + ".part"
            shutil.rmtree(stage, ignore_errors=True)
            X.write_hydrogen(pads, stage, kit_name)
            if fx:
                from choppad_fx import apply_to_file, is_neutral
                if not is_neutral(fx):
                    for wav in sorted(os.listdir(stage)):
                        if wav.endswith(".wav"):
                            apply_to_file(os.path.join(stage, wav), fx)
            os.makedirs(out_root, exist_ok=True)
            shutil.rmtree(kit_dir, ignore_errors=True)
            os.replace(stage, kit_dir)
        finally:
            shutil.rmtree(kit_dir + ".part", ignore_errors=True)
    n = len(chops["banks"][bank])
    slots = [Chop(p, os.path.join(kit_dir, f"chop_{p:02d}.wav"), 36 + p) if p < n else None for p in range(PADS)]
    # W3b: the project tempo (project.json "tempo": {bpm, follow}) wins over the song's own tempo
    bpm = float(((proj.get("tempo") or {}).get("bpm")) or chops.get("bpm") or 120.0)
    return DawProject(song_id=sid, title=title, bpm=bpm, beats_per_bar=int(chops.get("beats_per_bar") or 4), bank=bank,
                      chops=slots, pattern=pattern, kit_name=kit_name, kit_dir=kit_dir, key=key)


def prune_kits(out_root, keep, keep_n=3):
    """Drop old content-hashed kit folders of the same song+bank, keeping the newest `keep_n` and `keep`."""
    prefix = keep.rsplit(" ", 1)[0] + " "
    mine = sorted((d for d in os.listdir(out_root) if d.startswith(prefix) and os.path.isdir(os.path.join(out_root, d))),
                  key=lambda d: os.path.getmtime(os.path.join(out_root, d)), reverse=True)
    for d in mine[keep_n:]:
        if d != keep:
            shutil.rmtree(os.path.join(out_root, d), ignore_errors=True)
