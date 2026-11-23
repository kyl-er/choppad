"""export: export a chop bank to a zero-copy Ardour SFZ file and a Hydrogen drumkit directory.
See contracts/README.md §7 (v2.1)."""

import io
import json
import math
import os
import re
import struct
import wave
import xml.etree.ElementTree as ET

import choppad_voice as V  # noqa: E402  (W3b: pad filter and envelope)

HYDROGEN_NS = "http://www.hydrogen-music.org/drumkit"


def _read_json(media_dir, name, default=None):
    path = os.path.join(media_dir, name)
    if not os.path.exists(path):
        if default is not None:
            return default
        raise FileNotFoundError(path)
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _bank_indices(chops, bank):
    return chops["banks"][bank]


# ---- W3: what is on a pad -------------------------------------------------------------------------------------
# A pad is a dict {"pad", "start", "end", "audio": path of the song's audio.wav, "stretch": path of a rendered
# time-stretch WAV or None}. Pads come from a song bank (grid/transient chop, custom slice, stretch, slice of another
# song) or from a kit (contracts: media/_kits/<id>/kit.json). Exports work on this list.
_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_STRETCH_URL = re.compile(r"^/media/([A-Za-z0-9_-]{1,64})/stretch/([0-9a-f]{16}\.wav)$")


def _song_audio(media_root, song_id):
    if not _ID.match(str(song_id or "")):
        raise ValueError(f"bad song id {song_id!r}")
    path = os.path.join(media_root, song_id, "audio.wav")
    if not os.path.isfile(path):
        raise ValueError(f"this kit needs song {song_id!r}, which is not in the library")
    return path


_RESAMPLE_URL = re.compile(r"^/media/([A-Za-z0-9_-]{1,64})/resample/([0-9a-f]{16}\.wav)$")
STEMS = ("drums", "bass", "vocals", "other")


def _resample_path(media_root, url):
    """File of a pad's resample (`src: /media/<id>/resample/<hash>.wav`), or, since CHOP-13 step D, a sample a mega bank owns
    (`src: /media/_mega/<mid>/samples/<hash>.wav`: a resample uploaded to the mega bank)."""
    if _MEGA_SAMPLE_URL.match(url or ""):
        return _mega_sample_path(media_root, url)
    m = _RESAMPLE_URL.match(url or "")
    if not m:
        raise ValueError(f"bad resample url {url!r}")
    path = os.path.join(media_root, m.group(1), "resample", m.group(2))
    if not os.path.isfile(path):
        raise ValueError(f"resample {url} is missing; resample it again")
    return path


def _stem_path(media_root, song_id, stem):
    """File of a pad's stem (`stem: "drums"`) of `song_id`."""
    if stem not in STEMS or not _ID.match(str(song_id or "")):
        raise ValueError(f"bad stem {stem!r}")
    path = os.path.join(media_root, song_id, "stems", f"{stem}.wav")
    if not os.path.isfile(path):
        raise ValueError(f"the {stem} stem of {song_id!r} is missing; run the stems step for that song")
    return path


def _sample_rate(path):
    try:
        with wave.open(path, "rb") as w:
            return w.getframerate()
    except (OSError, wave.Error, EOFError):
        return 44100


def _stretch_path(media_root, url):
    m = _STRETCH_URL.match(url or "")
    if not m:
        raise ValueError(f"bad stretch url {url!r}")
    path = os.path.join(media_root, m.group(1), "stretch", m.group(2))
    if not os.path.isfile(path):
        raise ValueError(f"stretched sound {url} is missing; stretch the pad again")
    return path


# ---- CHOP-13: a song's project is the project of its mega bank (contracts §15) ----
_MEGA_SAMPLE_URL = re.compile(r"^/media/_mega/([A-Za-z0-9_-]{1,64})/samples/([0-9a-f]{16}\.wav)$")
MEGA_DIR = "_mega"


class NoSourceSong(ValueError):
    """A pad needs the source song of its mega bank, and there is none (or it is gone)."""


def _mega_sample_path(media_root, url):
    """File of a kept pad's audio (`src: /media/_mega/<mid>/samples/<hash>.wav`). The url pattern allows no path escape."""
    m = _MEGA_SAMPLE_URL.match(url or "")
    if not m:
        raise ValueError(f"bad mega sample url {url!r}")
    path = os.path.join(media_root, MEGA_DIR, m.group(1), "samples", m.group(2))
    if not os.path.isfile(path):
        raise ValueError(f"kept sound {url} is missing")
    return path


def latest_mega_doc(media_root, song_id):
    """The most recently updated mega bank document whose `source` is `song_id`, or None. Read-only (nothing is created or
    migrated here; media/_mega/.trash is ignored). The one definition of the compatibility rule of contracts §15."""
    root = os.path.join(media_root, MEGA_DIR)
    best = None
    for name in sorted(os.listdir(root)) if os.path.isdir(root) else []:
        if not _ID.match(name):
            continue
        try:
            with open(os.path.join(root, name, "mega.json"), encoding="utf-8") as f:
                doc = json.load(f)
        except (OSError, ValueError):
            continue
        if not isinstance(doc, dict) or doc.get("source") != song_id or not isinstance(doc.get("project"), dict):
            continue
        key = tuple(v if _num(v) else 0 for v in (doc.get("updated"), doc.get("created"))) + (name,)
        if best is None or key > best[0]:
            best = (key, doc)
    return best[1] if best else None


def song_project(media_dir):
    """The project of the song's mega bank; without one, the song's old project.json (read only); else {}."""
    media_dir = os.path.abspath(media_dir)
    doc = latest_mega_doc(os.path.dirname(media_dir), os.path.basename(media_dir))
    if doc:
        return doc["project"]
    path = os.path.join(media_dir, "project.json")
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {}


def project_overrides(media_dir):
    return song_project(media_dir).get("overrides") or {}


def project_pad_params(media_dir):
    """The saved project's per-pad sound settings (contracts §8 `pads`), 16 dicts (missing ones empty)."""
    pads = song_project(media_dir).get("pads") or []
    return [p if isinstance(p, dict) else {} for p in (list(pads) + [{}] * 16)[:16]]


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def project_pad_sounds(media_dir):
    """Same as project_pad_params (older name)."""
    return project_pad_params(media_dir)


def bank_pads(media_dir, bank=0, overrides=None, pad_params=None, sounds=None, *, chops=None, media_root=None, only=None):
    """THE definition of "the audio of pad p on bank b, as played": the chop table plus the project's custom slices
    (`overrides`, default: the saved project.json), their stretch renderings and slices from other songs. Empty pads are
    left out. Every pad dict also carries the pad's sound settings that a DAW kit can express: `gain_db` and `pitch`
    (semitones), and `params`, the full sound dict (filter, envelope, mute group) that the exporters bake into the audio
    or write as opcodes; both come from `pad_params` (16 dicts; `sounds` is the same argument under its older name;
    default: the saved project's `pads`).
    The Export button (Hydrogen kit, SFZ, zip) and the DAW bridge both start here.
    CHOP-13: a KEPT pad (override `kept: true`, `src: /media/_mega/<mid>/samples/<hash>.wav`) plays that sample file and its
    `params` are the pad's sound (`override.params` instead of `pad_params[pad]`). `media_dir` may be None for a mega bank without
    a source song (then give `media_root`; `chops` = the table to use instead of reading chops.json; both optional otherwise).
    `only` = a pad number: just that pad is looked at (so another pad's missing file cannot fail it)."""
    if media_dir is None:
        own, own_id = None, None
        chops = chops if chops is not None else {"banks": [], "chops": []}
    else:
        chops = chops if chops is not None else _read_json(media_dir, "chops.json")
        media_root = media_root or os.path.dirname(os.path.abspath(media_dir))
        own, own_id = os.path.join(media_dir, "audio.wav"), os.path.basename(os.path.abspath(media_dir))
        if overrides is None:
            overrides = project_overrides(media_dir)
    overrides = overrides or {}
    indices = chops["banks"][bank] if 0 <= bank < len(chops["banks"]) else []
    if pad_params is None:
        pad_params = sounds
    params = pad_params if pad_params is not None else (project_pad_params(media_dir) if media_dir is not None else [])
    pads = []
    for pad in range(16):
        if only is not None and pad != only:
            continue
        o = overrides.get(f"{bank}:{pad}")
        kept = False
        if isinstance(o, dict) and _num(o.get("start")) and _num(o.get("end")) and o["end"] > o["start"]:
            start, end = o["start"], o["end"]
            if o.get("kept") is True and _MEGA_SAMPLE_URL.match(o.get("src") or ""):
                kept, audio, stretch = True, _mega_sample_path(media_root, o["src"]), None
            else:
                if o.get("src"):                      # W3b: a resample, or a stem of the song, instead of the song's own audio
                    audio = _resample_path(media_root, o["src"])   # (needs no song: a mega bank's own resample)
                elif not o.get("song") or o["song"] == own_id:
                    if own is None:
                        raise NoSourceSong(f"pad {pad + 1} on bank {bank + 1} needs a source song")
                    audio = own
                else:
                    audio = _song_audio(media_root, o["song"])
                if o.get("src"):
                    pass
                elif o.get("stem"):
                    audio = _stem_path(media_root, o.get("song") or own_id, o["stem"])
                stretch = _stretch_path(media_root, o["stretch"]["url"]) if o.get("stretch") else None
        elif pad < len(indices):
            ch = chops["chops"][indices[pad]]
            start, end, audio, stretch = ch["start"], ch["end"], own, None
            if own is None:
                continue
        else:
            continue
        if kept:
            pr = o["params"] if isinstance(o.get("params"), dict) else {}
        else:
            pr = params[pad] if pad < len(params) and isinstance(params[pad], dict) else {}
        # `level`: the multiplier a kept pad played at before it was kept (§15 `gain`); the file stays raw, so exports and the
        # zip are unchanged and only a Hydrogen kit or song carries it (pad_gain_db adds it to the pad's gain)
        level = float(o["gain"]) if kept and _num(o.get("gain")) and 0 < o["gain"] <= 4 else 1.0
        pads.append({"pad": pad, "start": start, "end": end, "audio": audio, "stretch": stretch, "sr": _sample_rate(audio),
                     "kept": kept, "params": pr, "level": level,
                     "gain_db": float(pr["gainDb"]) if _num(pr.get("gainDb")) else 0.0,
                     "pitch": float(pr["pitch"]) if _num(pr.get("pitch")) else 0.0})
    return pads


def kit_pads(media_root, kit):
    """The pads of a kit document (`choppad.kit/1`) for export. Raises ValueError (clear message) when a song is gone."""
    pads = []
    for pad, p in enumerate(kit.get("pads") or []):
        if not p:
            continue
        if "song_id" not in p and p.get("src"):   # CHOP-13 step D: a kept pad: its audio is a file of the kit
            m = re.match(r"^/media/_kits/([A-Za-z0-9_-]{1,64})/samples/([0-9a-f]{16}\.wav)$", p["src"])
            audio = os.path.join(media_root, "_kits", m.group(1), "samples", m.group(2)) if m else ""
            if not os.path.isfile(audio):
                raise ValueError(f"the sound {p['src']} of this kit is missing")
            pr = p.get("params") or {}
            pads.append({"pad": pad, "start": p["start"], "end": p["end"], "audio": audio, "sr": _sample_rate(audio), "stretch": None, "kept": True,
                         "params": pr, "level": float(p["gain"]) if _num(p.get("gain")) and 0 < p["gain"] <= 4 else 1.0,
                         "gain_db": float(pr["gainDb"]) if _num(pr.get("gainDb")) else 0.0,
                         "pitch": float(pr["pitch"]) if _num(pr.get("pitch")) else 0.0})
            continue
        audio = _song_audio(media_root, p["song_id"])
        if p.get("src"):                          # W3b: a resample or a stem of that song
            audio = _resample_path(media_root, p["src"])
        elif p.get("stem"):
            audio = _stem_path(media_root, p["song_id"], p["stem"])
        pr = p.get("params") or {}
        pads.append({"pad": pad, "start": p["start"], "end": p["end"], "audio": audio, "sr": _sample_rate(audio),
                     "stretch": _stretch_path(media_root, p["stretch"]["url"]) if p.get("stretch") else None,
                     "params": pr,
                     "gain_db": float(pr["gainDb"]) if _num(pr.get("gainDb")) else 0.0,
                     "pitch": float(pr["pitch"]) if _num(pr.get("pitch")) else 0.0})
    return pads


def export_sfz(media_dir, out_path=None, *, bank=0, overrides=None, sounds=None):
    """Write a zero-copy .sfz referencing audio.wav with sample-frame offset/end, loop_mode=one_shot,
    group=1, off_by=1, ampeg_attack=0.002, and <region> key=36+pad for each pad of the bank.
    Custom slices follow `overrides` (default: the saved project); a stretched pad points at its rendered WAV
    and a pad from another song at that song's audio.wav (a `sample=` on the region). Returns the .sfz path."""
    meta = _read_json(media_dir, "meta.json", {})
    if out_path is None:
        meta_id = meta.get("id", "choppad")
        out_path = os.path.join(media_dir, f"{meta_id}-bank{bank}.sfz")
    return write_sfz(bank_pads(media_dir, bank, overrides, sounds), out_path, default_audio=os.path.join(media_dir, "audio.wav"))


def mute_group_of(p):
    """A pad's mute group 0..8 (0 = none) from its sound params."""
    g = (p.get("params") or {}).get("muteGroup") or 0
    return int(g) if isinstance(g, (int, float)) and not isinstance(g, bool) and 0 <= g <= 8 else 0


def groups_in_use(pads):
    """True when any pad has a mute group. Without any, exports keep the old behaviour (all pads cut each other)."""
    return any(mute_group_of(p) for p in pads)


def write_sfz(pads, out_path, *, default_audio):
    grouped = groups_in_use(pads)
    here = os.path.dirname(os.path.abspath(out_path))
    rel = lambda p: os.path.relpath(p, here).replace(os.sep, "/")  # noqa: E731
    lines = [
        "// generated by ChopPad export",
        # W3b: with mute groups in use each region names its own group (group=N off_by=N); otherwise the old "one group" choke
        f"<group> sample={rel(default_audio)} loop_mode=one_shot " + ("" if grouped else "group=1 off_by=1 ")
        + "ampeg_attack=0.002 ampeg_release=0.003",
    ]
    for p in pads:
        ops = V.sfz_opcodes(p.get("params"))   # W3b: filter (fil_type, cutoff, resonance) and envelope (ampeg_*)
        if grouped and mute_group_of(p):
            ops += f" group={mute_group_of(p)} off_by={mute_group_of(p)}"
        if p["stretch"]:
            lines.append(f"<region> sample={rel(p['stretch'])} key={36 + p['pad']}{ops}")
            continue
        extra = "" if os.path.abspath(p["audio"]) == os.path.abspath(default_audio) else f" sample={rel(p['audio'])}"
        sr = p.get("sr") or 44100   # the sample rate of the file the region plays (a resample may not be 44.1 kHz)
        lines.append(f"<region>{extra} key={36 + p['pad']} offset={round(p['start'] * sr)} end={round(p['end'] * sr)}{ops}")

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return out_path


def _fade(scale, i, total):
    assert total > 0
    return scale * i / (total - 1)


def _slice_declicked(frames, nch, sampwidth, start, end, sr):
    fade_in = int(0.0025 * sr)
    fade_out = int(0.003 * sr)
    seg = frames[start:end]
    nframes = len(seg) // (nch * sampwidth)
    out = bytearray()
    fmt = "<" + "h" * nch
    for i in range(nframes):
        base = i * nch * sampwidth
        vals = struct.unpack_from(fmt, seg, base)
        if i < fade_in:
            g = i / (fade_in - 1) if fade_in > 1 else 0.0
        elif i >= nframes - fade_out:
            g = (nframes - 1 - i) / (fade_out - 1) if fade_out > 1 else 0.0
        else:
            g = 1.0
        out += struct.pack(fmt, *[max(-32768, min(32767, int(round(v * g)))) for v in vals])
    return bytes(out)


def export_hydrogen(media_dir, out_dir, *, bank=0, kit_name=None, overrides=None, sounds=None, choke=True):
    """Slice audio.wav for each pad in the bank into out_dir/chop_00.wav..chop_NN.wav (s16le 44.1k stereo
    with 2.5 ms fade-in and 3 ms fade-out) and write out_dir/drumkit.xml (muteGroup 0 = choke, or -1 with choke=False;
    midiOutNote=36+pad; per-pad gain and pitch in the instrument's `gain` and `pitchOffset`).
    Custom slices follow `overrides` (default: the saved project); a stretched pad writes its rendering, a pad from
    another song is cut from that song. Returns the written drumkit.xml path."""
    return write_hydrogen(bank_pads(media_dir, bank, overrides, sounds), out_dir, kit_name, choke=choke)


def _read_wav(path, cache):
    if path not in cache:
        with wave.open(path, "rb") as src:
            cache[path] = (src.getframerate(), src.getnchannels(), src.getsampwidth(), src.readframes(src.getnframes()))
    return cache[path]


def pad_pcm(p, cache):
    """(sr, channels, sampwidth, declicked PCM bytes) of one pad: its stretched rendering or its slice of the song."""
    sr, nch, sampwidth, frames = _read_wav(p["stretch"] or p["audio"], cache)
    if p.get("kept") and (not p.get("params") or V.is_neutral(p["params"])):
        return sr, nch, sampwidth, frames   # a kept pad's file already has its declick (made when it was kept): played as it is
    if p["stretch"]:
        start, end = 0, len(frames)
    else:
        start = round(p["start"] * sr) * nch * sampwidth
        end = round(p["end"] * sr) * nch * sampwidth
    if sampwidth == 2 and p.get("params") and not V.is_neutral(p["params"]):
        # W3b: the pad's filter and envelope are baked in (the envelope replaces the plain 2.5 ms / 3 ms declick)
        import numpy as np
        seg = np.frombuffer(frames[start:end], dtype="<i2")
        seg = seg[: len(seg) // nch * nch].reshape(-1, nch).astype(np.float32) / 32768.0
        if len(seg):
            y = np.clip(np.round(V.bake(seg, sr, p["params"]) * 32768.0), -32768, 32767).astype("<i2")
            return sr, nch, sampwidth, y.tobytes()
    return sr, nch, sampwidth, _slice_declicked(frames, nch, sampwidth, start, end, sr)


def pad_wav_bytes(p, cache=None, *, neutral=False):
    """(WAV file bytes, duration in seconds) of one pad as `pad_pcm` renders it. `neutral=True` leaves out the pad's sound
    params (filter, envelope): the audio of the pad as played, which is what a mega bank keeps (contracts §15)."""
    sr, nch, sampwidth, data = pad_pcm({**p, "params": {}} if neutral else p, {} if cache is None else cache)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(nch)
        w.setsampwidth(sampwidth)
        w.setframerate(sr)
        w.writeframes(data)
    return buf.getvalue(), len(data) / (nch * sampwidth) / sr


def export_zip(media_dir, out_path=None, *, bank=0, overrides=None, sounds=None):
    """Zip of the bank's pads as WAV files pad01.wav .. pad16.wav (empty pads left out). Returns the .zip path."""
    return write_zip(bank_pads(media_dir, bank, overrides, sounds), out_path or os.path.join(
        media_dir, f"{_read_json(media_dir, 'meta.json', {}).get('id', 'choppad')}-bank{bank + 1}.zip"))


def write_zip(pads, out_path):
    import zipfile
    cache = {}
    tmp = out_path + ".part"
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for p in pads:
            z.writestr(f"pad{p['pad'] + 1:02d}.wav", pad_wav_bytes(p, cache)[0])
    os.replace(tmp, out_path)
    return out_path


def pad_gain_db(p):
    """The gain a DAW instrument carries for a pad: its own `gain_db` plus, for a kept pad, the level it played at when it was kept."""
    return float(p.get("gain_db") or 0.0) + 20.0 * math.log10(p.get("level") or 1.0)


def hydrogen_gain(gain_db):
    """Hydrogen instrument `gain` (linear multiplier, its knob runs 0..5) for a ChopPad pad gain in dB."""
    return round(max(0.0, min(5.0, 10 ** (float(gain_db or 0.0) / 20.0))), 6)


def hydrogen_pitch(pitch):
    """Hydrogen instrument `pitchOffset` (semitones, resampling like ChopPad's pad pitch; its knob runs -24..24)."""
    return round(max(-24.0, min(24.0, float(pitch or 0.0))), 4)


def _hy_instrument(inst_list, p, wav_name, choke, grouped=False):
    """One <instrument> in the element order drumkit.xsd demands (so `h2cli -c` accepts the kit as current format).
    Per-pad gain and pitch go into the instrument (`gain`, `pitchOffset`), not into the WAV, so the WAVs stay identical
    to the zip export and a Hydrogen user can still change them."""
    pad = p["pad"]
    inst = ET.SubElement(inst_list, "instrument")
    el = lambda tag, v: setattr(ET.SubElement(inst, tag), "text", str(v))  # noqa: E731
    el("id", pad)
    el("name", f"Pad {pad + 1}")
    el("volume", "1.0")
    el("isMuted", "false")
    el("isSoloed", "false")
    el("pan", "0.0")
    el("pitchOffset", hydrogen_pitch(p.get("pitch")))
    el("randomPitchFactor", "0.0")
    el("gain", hydrogen_gain(pad_gain_db(p)))
    el("applyVelocity", "true")
    el("filterActive", "false")
    el("filterCutoff", "1.0")
    el("filterResonance", "0.0")
    el("Attack", "0")
    el("Decay", "0")
    el("Sustain", "1.0")
    el("Release", "1000")
    # Hydrogen: -1 = no mute group, 0 and up = a real group (all instruments of one group cut each other off; the
    # shipped kits use -1). ChopPad's "choke" setting (on by default) is exactly "one pad at a time", i.e. one group.
    # With per-pad mute groups in use, each pad gets its own group (none = -1); otherwise the global choke decides.
    el("muteGroup", (mute_group_of(p) or -1) if grouped else ("0" if choke else "-1"))
    el("midiOutChannel", "-1")
    el("midiOutNote", 36 + pad)
    el("isStopNote", "false")
    el("sampleSelectionAlgo", "VELOCITY")
    el("isHihat", "-1")
    el("lower_cc", "0")
    el("higher_cc", "127")
    for n in range(1, 5):
        el(f"FX{n}Level", "0.0")
    comp = ET.SubElement(inst, "instrumentComponent")
    ET.SubElement(comp, "component_id").text = "0"
    ET.SubElement(comp, "gain").text = "1.0"
    layer = ET.SubElement(comp, "layer")
    ET.SubElement(layer, "filename").text = wav_name
    ET.SubElement(layer, "min").text = "0.0"
    ET.SubElement(layer, "max").text = "1.0"
    ET.SubElement(layer, "gain").text = "1.0"
    ET.SubElement(layer, "pitch").text = "0.0"


def write_hydrogen(pads, out_dir, kit_name=None, *, choke=True):
    """Write the pads as a Hydrogen 1.2 drumkit (current format, valid against drumkit.xsd): chop_NN.wav + drumkit.xml.
    choke=True puts every pad in mute group 0 (one pad at a time, ChopPad's default), False gives -1 (pads overlap)."""
    os.makedirs(out_dir, exist_ok=True)
    cache = {}
    root = ET.Element("drumkit_info", xmlns=HYDROGEN_NS)
    ET.SubElement(root, "name").text = kit_name or "ChopPad"
    ET.SubElement(root, "author").text = "ChopPad"
    ET.SubElement(root, "info").text = "Generated by ChopPad"
    ET.SubElement(root, "license").text = "undefined license"
    ET.SubElement(root, "image").text = ""
    ET.SubElement(root, "imageLicense").text = "undefined license"
    comps = ET.SubElement(root, "componentList")
    comp = ET.SubElement(comps, "drumkitComponent")
    ET.SubElement(comp, "id").text = "0"
    ET.SubElement(comp, "name").text = "Main"
    ET.SubElement(comp, "volume").text = "1.0"
    inst_list = ET.SubElement(root, "instrumentList")
    grouped = groups_in_use(pads)

    for p in pads:
        pad = p["pad"]
        sr, nch, sampwidth, data = pad_pcm(p, cache)
        wav_name = f"chop_{pad:02d}.wav"
        with wave.open(os.path.join(out_dir, wav_name), "wb") as dst:
            dst.setnchannels(nch)
            dst.setsampwidth(sampwidth)
            dst.setframerate(sr)
            dst.writeframes(data)
        _hy_instrument(inst_list, p, wav_name, choke, grouped)

    ET.indent(root, space=" ")
    xml_path = os.path.join(out_dir, "drumkit.xml")
    ET.ElementTree(root).write(xml_path, encoding="utf-8", xml_declaration=True)
    return xml_path


def bake_fx(out_dir, fx):
    """Bake the master fx (tone/reverb ...) into every chop_NN.wav of a kit folder. Returns True when anything changed."""
    if not fx:
        return False
    from choppad_fx import apply_to_file, is_neutral
    if is_neutral(fx):
        return False
    for wav in sorted(os.listdir(out_dir)):
        if wav.endswith(".wav"):
            apply_to_file(os.path.join(out_dir, wav), fx)
    return True


def render_bank_kit(pads, out_dir, kit_name=None, *, fx=None, choke=True):
    """The ONE path from pads (bank_pads / kit_pads) to a Hydrogen kit folder with the master fx baked in. The Export
    button and the DAW bridge both call this, so a pad sounds the same whichever way it leaves ChopPad.
    Returns (drumkit.xml path, fx_baked)."""
    xml = write_hydrogen(pads, out_dir, kit_name, choke=choke)
    return xml, bake_fx(out_dir, fx)
