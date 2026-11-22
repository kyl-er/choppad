"""export: export a chop bank to a zero-copy Ardour SFZ file and a Hydrogen drumkit directory.
See contracts/README.md §7 (v2.1)."""

import json
import os
import re
import struct
import wave
import xml.etree.ElementTree as ET

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


def _stretch_path(media_root, url):
    m = _STRETCH_URL.match(url or "")
    if not m:
        raise ValueError(f"bad stretch url {url!r}")
    path = os.path.join(media_root, m.group(1), "stretch", m.group(2))
    if not os.path.isfile(path):
        raise ValueError(f"stretched sound {url} is missing; stretch the pad again")
    return path


def project_overrides(media_dir):
    path = os.path.join(media_dir, "project.json")
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f).get("overrides") or {}
    return {}


def project_pad_params(media_dir):
    """The saved project's per-pad sound settings (contracts §8 `pads`), 16 dicts (missing ones empty)."""
    path = os.path.join(media_dir, "project.json")
    pads = []
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            pads = json.load(f).get("pads") or []
    return [p if isinstance(p, dict) else {} for p in (list(pads) + [{}] * 16)[:16]]


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def bank_pads(media_dir, bank=0, overrides=None, pad_params=None):
    """THE definition of "the audio of pad p on bank b, as played": the chop table plus the project's custom slices
    (`overrides`, default: the saved project.json), their stretch renderings and slices from other songs. Empty pads are
    left out. Every pad dict also carries the pad's sound settings that a DAW kit can express: `gain_db` and `pitch`
    (semitones), from `pad_params` (16 dicts, default: the saved project's `pads`).
    The Export button (Hydrogen kit, SFZ, zip) and the DAW bridge both start here."""
    chops = _read_json(media_dir, "chops.json")
    media_root = os.path.dirname(os.path.abspath(media_dir))
    own = os.path.join(media_dir, "audio.wav")
    if overrides is None:
        overrides = project_overrides(media_dir)
    indices = chops["banks"][bank] if 0 <= bank < len(chops["banks"]) else []
    params = pad_params if pad_params is not None else project_pad_params(media_dir)
    pads = []
    for pad in range(16):
        o = overrides.get(f"{bank}:{pad}")
        if isinstance(o, dict) and _num(o.get("start")) and _num(o.get("end")) and o["end"] > o["start"]:
            start, end = o["start"], o["end"]
            audio = own if not o.get("song") or o["song"] == os.path.basename(os.path.abspath(media_dir)) else _song_audio(media_root, o["song"])
            stretch = _stretch_path(media_root, o["stretch"]["url"]) if o.get("stretch") else None
        elif pad < len(indices):
            ch = chops["chops"][indices[pad]]
            start, end, audio, stretch = ch["start"], ch["end"], own, None
        else:
            continue
        pr = params[pad] if pad < len(params) and isinstance(params[pad], dict) else {}
        pads.append({"pad": pad, "start": start, "end": end, "audio": audio, "stretch": stretch,
                     "gain_db": float(pr["gainDb"]) if _num(pr.get("gainDb")) else 0.0,
                     "pitch": float(pr["pitch"]) if _num(pr.get("pitch")) else 0.0})
    return pads


def kit_pads(media_root, kit):
    """The pads of a kit document (`choppad.kit/1`) for export. Raises ValueError (clear message) when a song is gone."""
    pads = []
    for pad, p in enumerate(kit.get("pads") or []):
        if not p:
            continue
        audio = _song_audio(media_root, p["song_id"])
        pr = p.get("params") or {}
        pads.append({"pad": pad, "start": p["start"], "end": p["end"], "audio": audio,
                     "stretch": _stretch_path(media_root, p["stretch"]["url"]) if p.get("stretch") else None,
                     "gain_db": float(pr["gainDb"]) if _num(pr.get("gainDb")) else 0.0,
                     "pitch": float(pr["pitch"]) if _num(pr.get("pitch")) else 0.0})
    return pads


def export_sfz(media_dir, out_path=None, *, bank=0, overrides=None):
    """Write a zero-copy .sfz referencing audio.wav with sample-frame offset/end, loop_mode=one_shot,
    group=1, off_by=1, ampeg_attack=0.002, and <region> key=36+pad for each pad of the bank.
    Custom slices follow `overrides` (default: the saved project); a stretched pad points at its rendered WAV
    and a pad from another song at that song's audio.wav (a `sample=` on the region). Returns the .sfz path."""
    meta = _read_json(media_dir, "meta.json", {})
    if out_path is None:
        meta_id = meta.get("id", "choppad")
        out_path = os.path.join(media_dir, f"{meta_id}-bank{bank}.sfz")
    return write_sfz(bank_pads(media_dir, bank, overrides), out_path, default_audio=os.path.join(media_dir, "audio.wav"))


def write_sfz(pads, out_path, *, default_audio):
    sr = 44100
    here = os.path.dirname(os.path.abspath(out_path))
    rel = lambda p: os.path.relpath(p, here).replace(os.sep, "/")  # noqa: E731
    lines = [
        "// generated by ChopPad export",
        f"<group> sample={rel(default_audio)} loop_mode=one_shot group=1 off_by=1 ampeg_attack=0.002 ampeg_release=0.003",
    ]
    for p in pads:
        if p["stretch"]:
            lines.append(f"<region> sample={rel(p['stretch'])} key={36 + p['pad']}")
            continue
        extra = "" if os.path.abspath(p["audio"]) == os.path.abspath(default_audio) else f" sample={rel(p['audio'])}"
        lines.append(f"<region>{extra} key={36 + p['pad']} offset={round(p['start'] * sr)} end={round(p['end'] * sr)}")

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


def export_hydrogen(media_dir, out_dir, *, bank=0, kit_name=None, overrides=None, choke=True):
    """Slice audio.wav for each pad in the bank into out_dir/chop_00.wav..chop_NN.wav (s16le 44.1k stereo
    with 2.5 ms fade-in and 3 ms fade-out) and write out_dir/drumkit.xml (muteGroup 0 = choke, or -1 with choke=False;
    midiOutNote=36+pad; per-pad gain and pitch in the instrument's `gain` and `pitchOffset`).
    Custom slices follow `overrides` (default: the saved project); a stretched pad writes its rendering, a pad from
    another song is cut from that song. Returns the written drumkit.xml path."""
    return write_hydrogen(bank_pads(media_dir, bank, overrides), out_dir, kit_name, choke=choke)


def _read_wav(path, cache):
    if path not in cache:
        with wave.open(path, "rb") as src:
            cache[path] = (src.getframerate(), src.getnchannels(), src.getsampwidth(), src.readframes(src.getnframes()))
    return cache[path]


def pad_pcm(p, cache):
    """(sr, channels, sampwidth, declicked PCM bytes) of one pad: its stretched rendering or its slice of the song."""
    sr, nch, sampwidth, frames = _read_wav(p["stretch"] or p["audio"], cache)
    if p["stretch"]:
        start, end = 0, len(frames)
    else:
        start = round(p["start"] * sr) * nch * sampwidth
        end = round(p["end"] * sr) * nch * sampwidth
    return sr, nch, sampwidth, _slice_declicked(frames, nch, sampwidth, start, end, sr)


def export_zip(media_dir, out_path=None, *, bank=0, overrides=None):
    """Zip of the bank's pads as WAV files pad01.wav .. pad16.wav (empty pads left out). Returns the .zip path."""
    return write_zip(bank_pads(media_dir, bank, overrides), out_path or os.path.join(
        media_dir, f"{_read_json(media_dir, 'meta.json', {}).get('id', 'choppad')}-bank{bank + 1}.zip"))


def write_zip(pads, out_path):
    import io
    import zipfile
    cache = {}
    tmp = out_path + ".part"
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for p in pads:
            sr, nch, sampwidth, data = pad_pcm(p, cache)
            buf = io.BytesIO()
            with wave.open(buf, "wb") as w:
                w.setnchannels(nch)
                w.setsampwidth(sampwidth)
                w.setframerate(sr)
                w.writeframes(data)
            z.writestr(f"pad{p['pad'] + 1:02d}.wav", buf.getvalue())
    os.replace(tmp, out_path)
    return out_path


def hydrogen_gain(gain_db):
    """Hydrogen instrument `gain` (linear multiplier, its knob runs 0..5) for a ChopPad pad gain in dB."""
    return round(max(0.0, min(5.0, 10 ** (float(gain_db or 0.0) / 20.0))), 6)


def hydrogen_pitch(pitch):
    """Hydrogen instrument `pitchOffset` (semitones, resampling like ChopPad's pad pitch; its knob runs -24..24)."""
    return round(max(-24.0, min(24.0, float(pitch or 0.0))), 4)


def _hy_instrument(inst_list, p, wav_name, choke):
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
    el("gain", hydrogen_gain(p.get("gain_db")))
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
    el("muteGroup", "0" if choke else "-1")
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

    for p in pads:
        pad = p["pad"]
        sr, nch, sampwidth, data = pad_pcm(p, cache)
        wav_name = f"chop_{pad:02d}.wav"
        with wave.open(os.path.join(out_dir, wav_name), "wb") as dst:
            dst.setnchannels(nch)
            dst.setsampwidth(sampwidth)
            dst.setframerate(sr)
            dst.writeframes(data)
        _hy_instrument(inst_list, p, wav_name, choke)

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
