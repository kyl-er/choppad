"""mega: the drum pad mega bank store (CHOP-13, contracts §15).

A mega bank is what is being made: pads, their sounds, patterns, chain, master fx, tempo. It is the project document of §8/§14
moved out of the song, and it points at the SOURCE song whose chops its empty pads show.

  media/_mega/<mid>/mega.json            {schema: "choppad.mega/1", id, name, source: <song id> | null, created, updated, project}
  media/_mega/<mid>/samples/<16hex>.wav  audio the mega bank owns (kept pads; 16-bit PCM; named by the hash of its content)
  media/_mega/.trash/<mid>-<stamp>/      a deleted mega bank (never removed, only moved; listing ignores it)
  media/_mega/migrated.json              {songs: [song id, ...]}: songs whose old project.json has been used to seed a mega bank

`created` and `updated` are epoch seconds. `updated` changes when the project or the source changes (not on a rename).
Plain functions over a media_root; server.py only routes to them. All JSON is written atomically.

Migration (non-destructive, lazy): `mega_for_song` creates the song's first mega bank from `media/<song>/project.json` the first
time it is needed. That file is only READ, never written, renamed or removed; after that it is no longer used (a song that
already has had its mega bank migrated is never seeded from it again, even if that mega bank is deleted later).
"""
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import threading
import time
import uuid
import wave

_HERE = os.path.dirname(os.path.abspath(__file__))
for _c in ("control", "export"):
    _p = os.path.join(_HERE, "..", _c)
    if _p not in sys.path:
        sys.path.insert(0, _p)
import choppad_actions as A  # noqa: E402  (the reducer: used_pads, bank_count)
import choppad_export as X  # noqa: E402  (bank_pads, pad_wav_bytes: the one path from "pad as played" to audio)

SCHEMA = "choppad.mega/1"
ROOT = "_mega"
TRASH = ".trash"
MIGRATED = "migrated.json"
ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
NAME_MAX = 80
EMPTY_CHOPS = {"schema": "choppad.chops/2", "grid": "1/4", "bpm": None, "beats_per_bar": 4, "chops": [], "banks": []}
_LOCK = threading.RLock()  # creation, migration, deletion (the per-mega edit lock lives in server.py)


def empty_project():
    return {"pads": [{} for _ in range(16)], "overrides": {}}


def mega_root(media_root):
    return os.path.join(media_root, ROOT)


def _dir(media_root, mid):
    if not isinstance(mid, str) or not ID_RE.match(mid):
        raise KeyError(mid)
    return os.path.join(mega_root(media_root), mid)


def write_json_atomic(path, doc):
    """Temp file in the same folder, then rename: a reader (or a crash) never sees a half-written file."""
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(doc, f)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ---- songs (read only) ----
def song_dir(media_root, sid):
    """Folder of an existing song, else None."""
    if not isinstance(sid, str) or not ID_RE.match(sid):
        return None
    d = os.path.join(media_root, sid)
    return d if os.path.isfile(os.path.join(d, "meta.json")) else None


def song_title(media_root, sid):
    d = song_dir(media_root, sid)
    if not d:
        return None
    try:
        return _read_json(os.path.join(d, "meta.json")).get("title") or sid
    except (OSError, ValueError):
        return sid


def chops_of(media_root, doc):
    """The chop table of the mega bank's source song, or an empty table (no source, or the song is gone)."""
    d = song_dir(media_root, doc.get("source"))
    if d:
        try:
            return _read_json(os.path.join(d, "chops.json"))
        except (OSError, ValueError):
            pass
    return dict(EMPTY_CHOPS)


# ---- the store ----
def new_id(media_root):
    while True:
        mid = "m" + uuid.uuid4().hex[:11]
        if not os.path.exists(os.path.join(mega_root(media_root), mid)):
            return mid


def read(media_root, mid):
    """The mega bank document. KeyError when there is none (or the id is not a safe name)."""
    path = os.path.join(_dir(media_root, mid), "mega.json")
    try:
        doc = _read_json(path)
    except (OSError, ValueError):
        raise KeyError(mid) from None
    if not isinstance(doc, dict) or not isinstance(doc.get("project"), dict):
        raise KeyError(mid)
    return doc


def write(media_root, doc):
    """Save a mega bank document (atomic). Does not touch `updated`: call `touch` first when the content changed."""
    d = _dir(media_root, doc["id"])
    os.makedirs(os.path.join(d, "samples"), exist_ok=True)
    write_json_atomic(os.path.join(d, "mega.json"), doc)
    return doc


def touch(doc, now=None):
    doc["updated"] = time.time() if now is None else now
    return doc


def clean_name(name, fallback):
    n = name.strip() if isinstance(name, str) else ""
    if not n:
        return fallback
    if len(n) > NAME_MAX:
        raise ValueError(f"the name must be at most {NAME_MAX} characters")
    return n


def create(media_root, name=None, source=None, project=None):
    """A new mega bank (empty unless `project` is given). `source` must be a song of the library or None (ValueError otherwise)."""
    if source is not None and not song_dir(media_root, source):
        raise ValueError(f"unknown song {source!r}")
    with _LOCK:
        mid = new_id(media_root)
        now = time.time()
        title = song_title(media_root, source) if source else None
        doc = {"schema": SCHEMA, "id": mid, "name": clean_name(name, title or "Drum pad mega bank"), "source": source,
               "created": now, "updated": now, "project": project if isinstance(project, dict) else empty_project()}
        return write(media_root, doc)


def list_docs(media_root):
    """Every mega bank document, most recently updated first (`.trash` and anything that is not a mega bank is ignored)."""
    root = mega_root(media_root)
    out = []
    for mid in sorted(os.listdir(root)) if os.path.isdir(root) else []:
        if mid.startswith(".") or not ID_RE.match(mid) or not os.path.isdir(os.path.join(root, mid)):
            continue
        try:
            out.append(read(media_root, mid))
        except KeyError:
            continue
    return sorted(out, key=lambda d: (-(d.get("updated") or 0), -(d.get("created") or 0), d["id"]))


def rename(media_root, mid, name):
    with _LOCK:
        doc = read(media_root, mid)
        doc["name"] = clean_name(name, None)
        if doc["name"] is None:
            raise ValueError("a mega bank needs a name")
        return write(media_root, doc)


def delete(media_root, mid):
    """Move the folder to media/_mega/.trash/<mid>-<timestamp>/ (user data is never removed). Returns the new path."""
    with _LOCK:
        read(media_root, mid)
        trash = os.path.join(mega_root(media_root), TRASH)
        os.makedirs(trash, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        dest, n = os.path.join(trash, f"{mid}-{stamp}"), 1
        while os.path.exists(dest):
            n += 1
            dest = os.path.join(trash, f"{mid}-{stamp}-{n}")
        os.replace(_dir(media_root, mid), dest)
        return dest


def megas_with_source(media_root, song_id):
    return [d["id"] for d in list_docs(media_root) if d.get("source") == song_id]


def _migrated(media_root):
    try:
        songs = _read_json(os.path.join(mega_root(media_root), MIGRATED)).get("songs")
        return set(songs) if isinstance(songs, list) else set()
    except (OSError, ValueError, AttributeError):
        return set()


def mega_for_song(media_root, song_id):
    """The song's mega bank: the most recently updated one whose `source` is the song. When there is none it is created: from
    the song's project.json if that has never been migrated (the file is only read), else empty. KeyError for an unknown song."""
    if not song_dir(media_root, song_id):
        raise KeyError(song_id)
    with _LOCK:
        doc = X.latest_mega_doc(media_root, song_id)
        if doc:
            return doc
        seed, done = None, _migrated(media_root)
        if song_id not in done:
            path = os.path.join(media_root, song_id, "project.json")
            if os.path.isfile(path):
                try:
                    seed = _read_json(path)
                except (OSError, ValueError):
                    seed = None
            os.makedirs(mega_root(media_root), exist_ok=True)
            write_json_atomic(os.path.join(mega_root(media_root), MIGRATED), {"songs": sorted(done | {song_id})})
        return create(media_root, None, song_id, seed if isinstance(seed, dict) else None)


def used_pads(project):
    """Every (bank, pad) with an override or hit by an event of the pattern or of any song pattern (contracts §15)."""
    return A.used_pads(project)


def summary(media_root, doc):
    """A mega bank without its project, for lists and for GET /api/mega/<mid>."""
    project = doc["project"]
    return {"id": doc["id"], "name": doc.get("name"), "source": doc.get("source"),
            "source_title": song_title(media_root, doc.get("source")) if doc.get("source") else None,
            "created": doc.get("created"), "updated": doc.get("updated"),
            "used_pads": len(used_pads(project)), "banks": A.bank_count(project, chops_of(media_root, doc))}


# ---- keeping a pad ----
class EmptyPad(ValueError):
    """The pad has no audio at all (nothing to keep)."""


def render_keep(media_root, mid, source, project, chops, bank, pad):
    """Render pad `pad` of `bank` as played (its slice, other-song source, stretch rendering, resample or stem) WITHOUT its sound
    params and WITHOUT the master fx into `samples/<hash>.wav` (named by the content, so equal audio is stored once).
    Returns {src, start, end, from?} for a `slice.keep` action; raises ValueError (a reason) when the audio cannot be produced."""
    media_dir = song_dir(media_root, source) if source else None
    try:
        pads = X.bank_pads(media_dir, bank, project.get("overrides") or {}, [], chops=chops, media_root=media_root, only=pad)
        if not pads:
            if source and not media_dir:
                raise ValueError(f"the song {source!r} is gone")
            raise EmptyPad("the pad is empty")
        p = pads[0]
        data, duration = X.pad_wav_bytes(p, neutral=True)
    except X.NoSourceSong:
        raise ValueError(f"the song {source!r} is gone" if source else "the mega bank has no source song") from None
    except (OSError, wave.Error, EOFError) as e:
        raise ValueError(f"audio not readable: {e}") from None
    if duration <= 0:
        raise ValueError("the pad has no audio")
    h = hashlib.sha1(data).hexdigest()[:16]
    samples = os.path.join(_dir(media_root, mid), "samples")
    path = os.path.join(samples, f"{h}.wav")
    if not os.path.isfile(path):
        os.makedirs(samples, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=samples, prefix=".tmp-", suffix=".part")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    o = (project.get("overrides") or {}).get(f"{bank}:{pad}") or {}
    out = {"src": f"/media/{ROOT}/{mid}/samples/{h}.wav", "start": 0, "end": duration}
    frm_song = o.get("song") or source
    if frm_song and not o.get("src"):  # a resample has no slice of a song to point back to
        out["from"] = {"song": frm_song, "start": p["start"], "end": p["end"]}
    return out
