"""megaops: the per-project operations of CHOP-13 step D, keyed by DRUM PAD MEGA BANK (contracts §15).

Every function takes a `mega.Target` (the mega bank as read at the start of the request) and the request body and acts on exactly
that mega bank: export (Hydrogen kit, SFZ, zip), savekit, stretch, DAW bridge, and the operations that belong to the SOURCE song
(sections, key, advise, stems), which raise `mega.NoSource` (HTTP 409, a plain sentence) when the mega bank has none.
`/api/mega/<mid>/<name>` calls them directly; the song-keyed `/api/songs/<id>/<name>` routes resolve the song's mega bank and call
the same functions. server.py only routes; choppad.make_server wires them (`make_ops`).
"""
import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
for _c in ("control", "export", "chop", "daw"):
    _p = os.path.join(_HERE, "..", _c)
    if _p not in sys.path:
        sys.path.insert(0, _p)
import choppad_export as X  # noqa: E402
import kits  # noqa: E402
import mega as M  # noqa: E402

KEPT_STRETCH_MSG = "pad {pad} on bank {bank} is kept (it owns its audio) and has no slice of the song to stretch"


def _bank(body):
    try:
        return int(body.get("bank") or 0)
    except (TypeError, ValueError):
        raise ValueError("bank must be a number") from None


def pads_of(t, bank, body):
    """The pads of `bank` of THIS mega bank as played: its overrides and pad sounds (or those the request carries: what the browser
    holds right now), its source's chops, kept pads from their sample files. Works with no source song."""
    ov = body.get("overrides") if isinstance(body.get("overrides"), dict) else (t.project.get("overrides") or {})
    sounds = body.get("pads") if isinstance(body.get("pads"), list) else (t.project.get("pads") or [])
    return X.bank_pads(t.media_dir, bank, ov, sounds, chops=t.chops, media_root=t.media_root)


def _fx(t, body):
    return body.get("fx") if "fx" in body else t.project.get("fx")


def export(t, body):
    """kind hydrogen | sfz | zip for one bank of the mega bank. Needs no source song (kept pads and other-song pads are enough)."""
    from choppad_export import render_bank_kit, write_sfz, write_zip
    bank = _bank(body)
    kind = body.get("kind")
    if kind not in ("hydrogen", "sfz", "zip"):
        raise ValueError("kind must be hydrogen, sfz or zip")
    pads = pads_of(t, bank, body)
    if not pads:
        raise ValueError(f"bank {bank + 1} has no pads to export")
    if kind == "hydrogen":
        name = re.sub(r"[^\w -]+", "", f"ChopPad {t.title} B{bank + 1}")[:60].strip() or f"ChopPad {t.mid}"
        root = os.environ.get("CHOPPAD_HYDROGEN_DIR") or os.path.expanduser("~/.local/share/hydrogen/data/drumkits")
        out = os.path.join(root, name)
        _, baked = render_bank_kit(pads, out, name, fx=_fx(t, body), choke=body.get("choke", True) is not False)
        note = "Restart Hydrogen or use Drumkits > Rescan to see it."
        return {"path": out, "kit": name, "note": ("Tone/reverb baked into the samples. " if baked else "") + note, "mega": t.mid}
    if kind == "zip":
        note = "16 WAVs (one per pad, 2.5 ms fade-in / 3 ms fade-out by default); each pad's filter and envelope are baked in, pitch, gain and master fx are not."
        if t.media_dir:   # next to the song, as before (served as /media/<song>/<file>); the mega id keeps two mega banks apart
            path = os.path.join(t.media_dir, f"{os.path.basename(t.media_dir)}-{t.mid}-bank{bank + 1}.zip")
            write_zip(pads, path)
            return {"path": os.path.abspath(path), "url": f"/media/{os.path.basename(t.media_dir)}/{os.path.basename(path)}", "mega": t.mid, "note": note}
        path = os.path.join(t.exports_dir(), f"{t.mid}-bank{bank + 1}.zip")
        write_zip(pads, path)
        return {"path": os.path.abspath(path), "url": f"/media/{M.ROOT}/{t.mid}/exports/{os.path.basename(path)}", "mega": t.mid, "note": note}
    if t.media_dir:   # next to the song's audio.wav, as before: its regions name audio.wav, resample/ and stems/ relative to it
        path = os.path.join(t.media_dir, f"{os.path.basename(t.media_dir)}-{t.mid}-bank{bank}.sfz")
        write_sfz(pads, path, default_audio=os.path.join(t.media_dir, "audio.wav"))
        return {"path": os.path.abspath(path), "mega": t.mid}
    path = os.path.join(t.exports_dir(), f"{t.mid}-bank{bank}.sfz")
    write_sfz(pads, path, default_audio=pads[0]["audio"])
    return {"path": os.path.abspath(path), "url": f"/media/{M.ROOT}/{t.mid}/exports/{os.path.basename(path)}", "mega": t.mid}


def savekit(t, body):
    """POST …/savekit {bank, name [, overrides, pads, fx]} -> the new kit (what the bank holds now), kept pads copied into it."""
    bank = _bank(body)
    name = str(body.get("name") or "").strip() or f"{t.title} bank {bank + 1}"
    doc = kits.kit_from_mega(t, bank, name, overrides=body.get("overrides"), pads=body.get("pads"), fx=body.get("fx"))
    return kits.create_kit(t.media_root, doc)


def _pad_slice(t, bank, pad):
    """(start, end) of the slice on (bank, pad): the custom slice if the project has one, else the grid/transient chop."""
    ov = (t.project.get("overrides") or {}).get(f"{bank}:{pad}")
    if ov and ov.get("end", 0) > ov.get("start", 0) and not ov.get("song"):
        return float(ov["start"]), float(ov["end"])
    try:
        ch = t.chops["chops"][t.chops["banks"][bank][pad]]
    except (IndexError, TypeError, KeyError):
        raise ValueError(f"pad {pad} on bank {bank} is empty") from None
    return ch["start"], ch["end"]


def stretch(t, body):
    """POST …/stretch {bank, pad, ratio | target_bpm [, start, end, source_bpm, stem, song]}: renders the pad's slice at `ratio` x its
    length (same pitch) into the SOURCE song's folder media/<id>/stretch/<hash>.wav (cached), or into the folder of `song` when the
    pad's slice comes from another song. A kept pad has no slice of the song: 400 with the plain reason."""
    from choppad_stretch import StretchError, ratio_for_tempo, stretch_slice
    song = body.get("song")
    if song is not None:
        media_dir = M.song_dir(t.media_root, song)
        if not media_dir:
            raise ValueError(f"unknown song {song!r}")
    else:
        media_dir = t.need_song("stretch")
    sid = os.path.basename(media_dir)
    bank = _bank(body)
    try:
        if body.get("pad") is not None:
            pad = int(body["pad"])
            if ((t.project.get("overrides") or {}).get(f"{bank}:{pad}") or {}).get("kept"):
                raise ValueError(KEPT_STRETCH_MSG.format(pad=pad + 1, bank=bank + 1))
        if body.get("start") is not None and body.get("end") is not None:
            start, end = float(body["start"]), float(body["end"])
        else:
            start, end = _pad_slice(t, bank, int(body.get("pad") or 0))
        if body.get("ratio") is not None:
            ratio = float(body["ratio"])
        elif body.get("target_bpm") is not None:
            with open(os.path.join(media_dir, "chops.json")) as f:
                own_bpm = json.load(f)["bpm"]
            ratio = ratio_for_tempo(float(body.get("source_bpm") or own_bpm), float(body["target_bpm"]))
        else:
            raise StretchError("ratio or target_bpm required")
        stem = body.get("stem")
        if stem is not None and stem not in ("drums", "bass", "vocals", "other"):
            raise StretchError("stem must be drums, bass, vocals or other")
        r = stretch_slice(media_dir, start, end, ratio, f"stems/{stem}.wav" if stem else "audio.wav")
    except (StretchError, ValueError) as e:
        raise ValueError(str(e))
    return {"url": f"/media/{sid}/stretch/{r['file']}", "duration": r["duration"], "ratio": r["ratio"], "hash": r["hash"],
            "start": start, "end": end}


def make_ops(daw, source_ops):
    """The operation table `name -> fn(Target, body)` for server.make_server(mega_actions=...). `daw` = the DawManager;
    `source_ops` = {name: fn(media_dir, body)} for what belongs to the source song (sections, key, advise, stems)."""
    import daw_project

    def source_op(name, what):
        fn = source_ops[name]
        return lambda t, body: fn(t.need_song(what), body)

    def daw_op(t, body):
        def build(out_root, bank, **settings):
            return daw_project.build_mega_project(t.media_root, t.doc, t.chops, t.media_dir, out_root, bank=bank, **settings)
        return daw.command_mega(t.mid, build, {**body, "view_settings": t.view_settings}, extra={"song": t.source})

    return {"export": export, "savekit": savekit, "stretch": stretch, "daw": daw_op,
            "sections": source_op("sections", "analyse"), "key": source_op("key", "detect a key for"),
            "advise": source_op("advise", "advise on"), "stems": source_op("stems", "separate into stems")}
