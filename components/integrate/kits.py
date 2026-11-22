"""kits: 16 pads whose slices may come from different songs (W3). Stored as media/_kits/<kit-id>/kit.json:

  {"schema": "choppad.kit/1", "name": "...", "pads": [ {song_id, start, end, params, stretch?} | null  x16 ], "fx": {...}}

`params` are the pad sound parameters (gainDb, pitch, attack, release, reverse, mode); `stretch` = {ratio, url} names a
rendering made by POST /api/songs/<song_id>/stretch. Plain functions over a media_root; server.py only routes to them.

  GET    /api/kits                 -> [{id, name, pads, songs}]
  POST   /api/kits {name, pads, fx?} -> kit (201)         POST /api/songs/<id>/savekit {bank, name} builds one from a bank
  GET    /api/kits/<id>            -> kit + id
  PUT    /api/kits/<id> {name?, pads?, fx?}
  DELETE /api/kits/<id>
  POST   /api/kits/<id>/export {kind: "hydrogen"|"sfz"}
"""
import json
import os
import re
import shutil
import tempfile

SCHEMA = "choppad.kit/1"
KIT_DIR = "_kits"
ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
PARAM_KEYS = {"gainDb": (int, float), "pitch": (int, float), "attack": (int, float), "release": (int, float),
              "reverse": (bool,), "mode": (str, type(None))}
FX_KEYS = ("tone", "low", "mid", "high", "filter", "reverb", "size")


def kits_root(media_root):
    return os.path.join(media_root, KIT_DIR)


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _song_exists(media_root, sid):
    return isinstance(sid, str) and ID_RE.match(sid) and os.path.isfile(os.path.join(media_root, sid, "meta.json"))


def normalize(doc, media_root, *, partial=None):
    """Validate a kit document (or, with `partial`, merge `doc` into it). Returns the clean document; raises ValueError."""
    base = dict(partial or {})
    base.update({k: v for k, v in (doc or {}).items() if k in ("name", "pads", "fx")})
    name = str(base.get("name") or "").strip()
    if not name or len(name) > 80:
        raise ValueError("kit needs a name (1-80 characters)")
    raw = base.get("pads")
    if not isinstance(raw, list) or len(raw) > 16:
        raise ValueError("pads must be a list of at most 16 entries (null = empty pad)")
    pads = []
    for i, p in enumerate(raw + [None] * (16 - len(raw))):
        if p is None:
            pads.append(None)
            continue
        if not isinstance(p, dict):
            raise ValueError(f"pad {i + 1}: expected an object or null")
        sid = p.get("song_id")
        if not _song_exists(media_root, sid):
            raise ValueError(f"pad {i + 1}: song {sid!r} is not in the library")
        if not (_num(p.get("start")) and _num(p.get("end")) and 0 <= p["start"] < p["end"]):
            raise ValueError(f"pad {i + 1}: needs 0 <= start < end (seconds)")
        out = {"song_id": sid, "start": p["start"], "end": p["end"]}
        params = p.get("params") or {}
        if not isinstance(params, dict):
            raise ValueError(f"pad {i + 1}: params must be an object")
        out["params"] = {k: v for k, v in params.items() if k in PARAM_KEYS and isinstance(v, PARAM_KEYS[k])}
        st = p.get("stretch")
        if st:
            m = re.match(r"^/media/([A-Za-z0-9_-]{1,64})/stretch/[0-9a-f]{16}\.wav$", str(st.get("url") or ""))
            if not m or m.group(1) != sid or not _num(st.get("ratio")) or st["ratio"] <= 0:
                raise ValueError(f"pad {i + 1}: stretch needs a positive ratio and a /media/{sid}/stretch/<hash>.wav url")
            out["stretch"] = {"ratio": st["ratio"], "url": st["url"]}
        pads.append(out)
    fx = base.get("fx") or {}
    if not isinstance(fx, dict):
        raise ValueError("fx must be an object")
    return {"schema": SCHEMA, "name": name, "pads": pads, "fx": {k: v for k, v in fx.items() if k in FX_KEYS and _num(v)}}


def _slug(name):
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40]
    return s or "kit"


def _path(media_root, kid):
    if not ID_RE.match(kid or ""):
        raise KeyError(kid)
    return os.path.join(kits_root(media_root), kid, "kit.json")


def read_kit(media_root, kid):
    path = _path(media_root, kid)
    if not os.path.isfile(path):
        raise KeyError(kid)
    with open(path, encoding="utf-8") as f:
        return {"id": kid, **json.load(f)}


def _write(media_root, kid, doc):
    path = _path(media_root, kid)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".part"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2)
    os.replace(tmp, path)


def list_kits(media_root):
    out = []
    root = kits_root(media_root)
    for kid in sorted(os.listdir(root)) if os.path.isdir(root) else []:
        try:
            k = read_kit(media_root, kid)
        except (KeyError, ValueError, OSError):
            continue
        used = [p for p in k.get("pads", []) if p]
        out.append({"id": kid, "name": k.get("name", kid), "pads": len(used),
                    "songs": sorted({p["song_id"] for p in used}), "modified": os.path.getmtime(_path(media_root, kid))})
    return sorted(out, key=lambda k: -k["modified"])


def create_kit(media_root, doc):
    clean = normalize(doc, media_root)
    base = _slug(clean["name"])
    kid, n = base, 1
    while os.path.exists(os.path.join(kits_root(media_root), kid)):
        n += 1
        kid = f"{base}-{n}"
    _write(media_root, kid, clean)
    return {"id": kid, **clean}


def update_kit(media_root, kid, doc):
    cur = read_kit(media_root, kid)
    cur.pop("id", None)
    clean = normalize(doc, media_root, partial=cur)
    _write(media_root, kid, clean)
    return {"id": kid, **clean}


def delete_kit(media_root, kid):
    read_kit(media_root, kid)
    shutil.rmtree(os.path.dirname(_path(media_root, kid)))


def kit_from_bank(media_root, song_id, bank, name, *, overrides=None, pads=None, fx=None):
    """Build a kit from a song's bank: chops (or custom slices) of `bank`, with the pad sounds and master fx of the
    saved project unless `overrides` / `pads` / `fx` are given (what the browser holds right now)."""
    from choppad_export import bank_pads
    media_dir = os.path.join(media_root, song_id)
    proj = {}
    pj = os.path.join(media_dir, "project.json")
    if os.path.isfile(pj):
        proj = json.load(open(pj))
    ov = proj.get("overrides") or {} if overrides is None else overrides
    sounds = proj.get("pads") or [] if pads is None else pads
    out = [None] * 16
    for p in bank_pads(media_dir, bank, ov):
        o = ov.get(f"{bank}:{p['pad']}") or {}
        entry = {"song_id": o.get("song") or song_id, "start": p["start"], "end": p["end"],
                 "params": sounds[p["pad"]] if p["pad"] < len(sounds) and isinstance(sounds[p["pad"]], dict) else {}}
        if o.get("stretch"):
            entry["stretch"] = o["stretch"]
        out[p["pad"]] = entry
    return {"name": name, "pads": out, "fx": (proj.get("fx") or {}) if fx is None else fx}


def export_kit(media_root, kid, kind, *, hydrogen_root=None):
    """Write a kit as a Hydrogen drumkit or an SFZ. Pads from other songs and stretched pads work in both;
    a kit whose songs are gone fails with a clear ValueError."""
    from choppad_export import kit_pads, write_hydrogen, write_sfz
    kit = read_kit(media_root, kid)
    pads = kit_pads(media_root, kit)
    if not pads:
        raise ValueError("this kit has no pads")
    if kind == "sfz":
        out = os.path.join(kits_root(media_root), kid, f"{kid}.sfz")
        write_sfz(pads, out, default_audio=pads[0]["audio"])
        return {"path": out, "note": f"{len(pads)} pads from {len({p['audio'] for p in pads})} song(s)"}
    if kind == "zip":
        from choppad_export import write_zip
        out = os.path.join(kits_root(media_root), kid, f"{kid}.zip")
        write_zip(pads, out)
        return {"path": out, "note": f"{len(pads)} WAVs"}
    if kind != "hydrogen":
        raise ValueError("kind must be hydrogen, sfz or zip")
    name = re.sub(r"[^\w -]+", "", f"ChopPad kit {kit['name']}")[:60].strip()
    root = hydrogen_root or os.environ.get("CHOPPAD_HYDROGEN_DIR") or os.path.expanduser("~/.local/share/hydrogen/data/drumkits")
    out = os.path.join(root, name)
    write_hydrogen(pads, out, name)
    note = "Restart Hydrogen or use Drumkits > Rescan to see it."
    if kit.get("fx"):
        from choppad_fx import apply_to_file, is_neutral
        if not is_neutral(kit["fx"]):
            for wav in sorted(os.listdir(out)):
                if wav.endswith(".wav"):
                    apply_to_file(os.path.join(out, wav), kit["fx"])
            note = "Tone/reverb baked into the samples. " + note
    return {"path": out, "kit": name, "note": note}


def handle(media_root, method, path, body, *, export=None):
    """Route one request. Returns (status, json-able). `path` has no query string."""
    parts = [p for p in path.split("/") if p]            # ["api", "kits", id?, "export"?]
    try:
        if len(parts) == 2:
            if method == "GET":
                return 200, list_kits(media_root)
            if method == "POST":
                return 201, create_kit(media_root, body)
        elif len(parts) == 3:
            if method == "GET":
                return 200, read_kit(media_root, parts[2])
            if method == "PUT":
                return 200, update_kit(media_root, parts[2], body)
            if method == "DELETE":
                delete_kit(media_root, parts[2])
                return 200, {"ok": True}
        elif len(parts) == 4 and parts[3] == "export" and method == "POST":
            return 200, (export or export_kit)(media_root, parts[2], (body or {}).get("kind") or "hydrogen")
    except KeyError:
        return 404, {"error": "unknown kit"}
    except ValueError as e:
        return 400, {"error": str(e)}
    return 405, {"error": "method not allowed"}
