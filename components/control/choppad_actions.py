"""choppad_actions: the contracts §8 reducer in Python (twin of components/ui/actions.js).

State of one open song (plain dicts, same shape as the JS):
  project  {pads[16], overrides{"bank:pad": {start, end}}, pattern{bars, events[]}, fx{}}   saved per song
  chops    the chop table (contracts §3)
  sections [{label, letter, start_s, end_s, start_bar, end_bar}]
  bank, selected, settings                                                                  not saved

`reduce(state, action)` is pure: it never mutates `state`, and returns the *same object* when the action is
unknown, invalid or changes nothing. contracts/fixtures/actions.json holds the cases both implementations pass.
"""
import copy
import math
import re

PADS = 16
DEFAULT_TEMPO = {"bpm": None, "follow": True}
TEMPO_MIN, TEMPO_MAX = 20, 400
STEMS = ("drums", "bass", "vocals", "other")
DEFAULT_SONG = {"patterns": [], "chain": [], "mode": "pattern", "current": None}
MAX_PATTERNS, MAX_CHAIN, MAX_REPEATS = 64, 256, 64
_PATTERN_ID = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
RESAMPLE_URL = re.compile(r"^/media/[A-Za-z0-9_-]{1,64}/resample/[0-9a-f]{16}\.wav$")
MEGA_SAMPLE_URL = re.compile(r"^/media/_mega/[A-Za-z0-9_-]{1,64}/samples/[0-9a-f]{16}\.wav$")  # CHOP-13: audio a mega bank owns
_OVERRIDE_KEY = re.compile(r"^(\d+):(\d+)$")
FILTER_TYPES = ("off", "lp", "hp", "bp")
DEFAULT_FILTER = {"type": "off", "cutoff": 8000, "q": 0.7}
DEFAULT_PAD = {"gainDb": 0, "pitch": 0, "attack": 0.0025, "decay": 0, "sustain": 1, "release": 0.003, "reverse": False,
               "mode": None, "filter": DEFAULT_FILTER, "muteGroup": 0}
DEFAULT_FX = {"tone": 0, "low": 0, "mid": 0, "high": 0, "filter": 0, "reverb": 0, "size": 0.5}
SIXTEEN_MODES = ("velocity", "pitch", "filter")
DEFAULT_SETTINGS = {"mode": "oneshot", "choke": True, "fullLevel": True, "quant": "1/16", "swing": 0, "sixteen": None,
                    "metronome": True, "repeat": False, "repeatRate": 0.25, "follow": True}

# Actions that change the saved project. The rest only change what the player looks at (not written to project.json).
PROJECT_ACTIONS = frozenset({
    "pad.set", "pad.reset", "pad.copyToAll", "slice.set", "slice.clear", "slice.setMany", "slice.stretch", "kit.load", "fx.set",
    "tempo.set", "slice.src", "slice.stem", "slice.keep",
    "song.addPattern", "song.selectPattern", "song.renamePattern", "song.removePattern", "song.setChain", "song.setMode", "song.load",
    "pattern.toggleStep", "pattern.addEvent", "pattern.undo", "pattern.clear", "pattern.setBars", "pattern.load"})
VIEW_ACTIONS = frozenset({"pad.select", "bank.set", "chops.replace", "source.set", "sections.set", "settings.set"})
ACTION_TYPES = PROJECT_ACTIONS | VIEW_ACTIONS


def _is_num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def project_bpm(state):
    """Tempo the sequencer and every render use: the project tempo when set, else the song's own."""
    t = state["project"].get("tempo")
    if t and t.get("bpm") is not None:
        return t["bpm"]
    return (state.get("chops") or {}).get("bpm")


def _clean_tempo(t):
    """A clean {bpm, follow} from a saved or sent tempo, or None when it is not one."""
    if not isinstance(t, dict):
        return None
    bpm = t.get("bpm")
    if bpm is not None and not (_is_num(bpm) and TEMPO_MIN <= bpm <= TEMPO_MAX):
        return None
    return {"bpm": bpm, "follow": t["follow"] if isinstance(t.get("follow"), bool) else True}


def _clean_stretch(st):
    """{ratio, url[, fit]}: `fit` = the project tempo this rendering was made for (absent = stretched by hand)."""
    out = {"ratio": st["ratio"], "url": st["url"]}
    if _is_num(st.get("fit")) and st["fit"] > 0:
        out["fit"] = st["fit"]
    return out


def _pad(p):
    """Pad index as int, or None. (JS Number.isInteger accepts 3.0, so does this.)"""
    if _is_num(p) and float(p).is_integer() and 0 <= p < PADS:
        return int(p)
    return None


def _bank_key(b):
    return int(b) if _is_num(b) and float(b).is_integer() else None


def _pick(obj, keys):
    return {k: obj[k] for k in keys if isinstance(obj, dict) and k in obj}


def _same_value(a, b):
    """JS ===, extended to JSON objects (pad filters): True is not 1."""
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_same_value(a[k], b[k]) for k in a)
    return a == b and isinstance(a, bool) == isinstance(b, bool)


def _clamp(v, lo, hi):
    return min(hi, max(lo, v))


def _clean_filter(f, base):
    """A filter {type, cutoff, q} made from `f` on top of `base`; fields that are not valid are ignored. None when `f` is no object."""
    if not isinstance(f, dict):
        return None
    o = dict(base)
    if f.get("type") in FILTER_TYPES:
        o["type"] = f["type"]
    if _is_num(f.get("cutoff")):
        o["cutoff"] = _clamp(f["cutoff"], 20, 20000)
    if _is_num(f.get("q")):
        o["q"] = _clamp(f["q"], 0.1, 24)
    return o


def clean_pad(raw, cur=None):
    """The valid part of pad params (twin of cleanPad in actions.js): gainDb/pitch/attack/release/reverse/mode pass through;
    decay is 0..10 s, sustain 0..1, filter is merged onto the pad's current filter."""
    out = _pick(raw, DEFAULT_PAD)
    if "decay" in out:
        if _is_num(out["decay"]):
            out["decay"] = _clamp(out["decay"], 0, 10)
        else:
            del out["decay"]
    if "muteGroup" in out:  # 0..8, an integer (3.0 counts, like Number.isInteger)
        g = out["muteGroup"]
        if _is_num(g) and float(g).is_integer() and 0 <= g <= 8:
            out["muteGroup"] = int(g)
        else:
            del out["muteGroup"]
    if "sustain" in out:
        if _is_num(out["sustain"]):
            out["sustain"] = _clamp(out["sustain"], 0, 1)
        else:
            del out["sustain"]
    if "filter" in out:
        f = _clean_filter(out["filter"], (cur or DEFAULT_PAD).get("filter") or DEFAULT_FILTER)
        if f is not None:
            out["filter"] = f
        else:
            del out["filter"]
    return out


def _default_pad():
    return copy.deepcopy(DEFAULT_PAD)


def _same(a, b):
    return all(k in a and _same_value(a[k], v) for k, v in b.items())


def _override_key(key):
    """(bank, pad) of an overrides key "b:p", or None."""
    m = _OVERRIDE_KEY.match(key) if isinstance(key, str) else None
    return (int(m.group(1)), int(m.group(2))) if m and int(m.group(2)) < PADS else None


def used_pads(project):
    """CHOP-13: every (bank, pad) that has an override or is hit by an event of `project.pattern` or of any
    `project.song.patterns[*]`, sorted. Works on a raw saved project as well as on a clean one."""
    out = set()
    for key in (project.get("overrides") or {}):
        kp = _override_key(key)
        if kp:
            out.add(kp)
    song = project.get("song") if isinstance(project.get("song"), dict) else {}
    lists = [(project.get("pattern") or {}).get("events")] + [p.get("events") for p in (song.get("patterns") or []) if isinstance(p, dict)]
    for events in lists:
        for e in events if isinstance(events, list) else []:
            if isinstance(e, dict) and _pad(e.get("pad")) is not None:
                b = _bank_key(e.get("bank"))
                out.add((b if b is not None and b >= 0 else 0, _pad(e["pad"])))
    return sorted(out)


def bank_count(project, chops):
    """CHOP-13: number of banks = max(banks of the source's chop table, 1 + highest bank index in overrides), at least 1."""
    banks = (chops or {}).get("banks")
    top = len(banks) if isinstance(banks, list) else 0
    for key in (project.get("overrides") or {}):
        kp = _override_key(key)
        if kp:
            top = max(top, kp[0] + 1)
    return max(top, 1)


def _step_of(event):
    return math.floor(event["beat"] * 4 + 0.5)  # JS Math.round


step_of = _step_of


# ---- W3b song mode: project.song = {patterns: [{id, name, bars, events}], chain: [{pattern, repeats}], mode, current} ----
# `project.pattern` stays the pattern being edited; `current` names the entry of `patterns` it belongs to (None = none yet, an
# old project). Every pattern.* action is mirrored into that entry. The key is absent until used.
def _next_pattern_id(patterns):
    n = max([0] + [int(m.group(1)) for p in patterns for m in [re.match(r"^p(\d+)$", p["id"])] if m])
    return f"p{n + 1}"


def _clean_name(n, fallback):
    return n.strip() if isinstance(n, str) and n.strip() and len(n.strip()) <= 40 else fallback


def _clean_events(events):
    return [dict(e) for e in (events if isinstance(events, list) else [])
            if isinstance(e, dict) and _pad(e.get("pad")) is not None and _is_num(e.get("beat"))]


def _clean_chain(chain, patterns):
    out = []
    if not isinstance(chain, list):
        return out
    for c in chain[:MAX_CHAIN]:
        if not isinstance(c, dict) or not isinstance(c.get("pattern"), str) or not any(p["id"] == c["pattern"] for p in patterns):
            continue
        if "repeats" in c and not _is_num(c["repeats"]):
            continue
        out.append({"pattern": c["pattern"], "repeats": int(_clamp(math.floor((c["repeats"] if "repeats" in c else 1) + 0.5), 1, MAX_REPEATS))})
    return out


def clean_song(raw, pattern=None):
    """A clean song from a saved or sent one, or None when it is just the default (nothing to save). `pattern` = the edited
    pattern, which wins over the copy in patterns[current]."""
    if not isinstance(raw, dict):
        return None
    seen, patterns = set(), []
    for p in (raw.get("patterns") if isinstance(raw.get("patterns"), list) else [])[:MAX_PATTERNS]:
        if not isinstance(p, dict) or not isinstance(p.get("id"), str) or not _PATTERN_ID.match(p["id"]) or p["id"] in seen:
            continue
        seen.add(p["id"])
        patterns.append({"id": p["id"], "name": _clean_name(p.get("name"), f"Pattern {len(patterns) + 1}"),
                         "bars": p["bars"] if _is_num(p.get("bars")) and p["bars"] >= 1 else 2, "events": _clean_events(p.get("events"))})
    chain = _clean_chain(raw.get("chain"), patterns)
    mode = "song" if raw.get("mode") == "song" else "pattern"
    current = raw.get("current") if any(p["id"] == raw.get("current") for p in patterns) else None
    if current is not None and pattern:
        patterns = [{**p, "bars": pattern["bars"], "events": pattern["events"]} if p["id"] == current else p for p in patterns]
    if not patterns and not chain and mode == "pattern":
        return None
    return {"patterns": patterns, "chain": chain, "mode": mode, "current": current}


def song_timeline(project, beats_per_bar=4, force=False):
    """The playing order as {total, chain, segments: [{start, len, id, events}]} in beats from 0 (twin of songTimeline)."""
    s = project.get("song")

    def single():
        ln = project["pattern"]["bars"] * beats_per_bar
        return {"total": ln, "chain": False, "segments": [{"start": 0, "len": ln, "id": (s or {}).get("current"), "events": project["pattern"]["events"]}]}
    if not s or not s["chain"] or not (s["mode"] == "song" or force):
        return single()
    segments, cursor = [], 0
    for c in s["chain"]:
        p = next((x for x in s["patterns"] if x["id"] == c["pattern"]), None)
        if not p:
            continue
        ln = p["bars"] * beats_per_bar
        events = [e for e in p["events"] if e["beat"] < ln]
        for _ in range(c["repeats"]):
            segments.append({"start": cursor, "len": ln, "id": p["id"], "events": events})
            cursor += ln
    return {"total": cursor, "chain": True, "segments": segments} if segments else single()


def timeline_events(tl):
    """The timeline's events with their beats made absolute."""
    return [{**e, "beat": e["beat"] + seg["start"]} for seg in tl["segments"] for e in seg["events"]]


def _with_song(state, song):
    """A project with `song` set; a song that is just the default is not kept."""
    if not song["patterns"] and not song["chain"] and song["mode"] == "pattern" and song["current"] is None:
        return {**state, "project": {k: v for k, v in state["project"].items() if k != "song"}}
    return _with_project(state, song=song)


def _mirror_current(state):
    song = state["project"].get("song")
    if not song or song["current"] is None:
        return state
    pat = state["project"]["pattern"]
    i = next((k for k, p in enumerate(song["patterns"]) if p["id"] == song["current"]), -1)
    if i < 0 or (song["patterns"][i]["bars"] == pat["bars"] and song["patterns"][i]["events"] == pat["events"]):
        return state
    patterns = [{**p, "bars": pat["bars"], "events": pat["events"]} if k == i else p for k, p in enumerate(song["patterns"])]
    return _with_project(state, song={**song, "patterns": patterns})


def initial_state(project=None, chops=None, sections=None, source=None, mega=None):
    """Starting state. `project` is whatever is on disk (may be partial or None); `source` = id of the song being chopped
    and `mega` = id of the mega bank (CHOP-13), both optional."""
    project = project if isinstance(project, dict) else {}
    pattern = project.get("pattern") if isinstance(project.get("pattern"), dict) else {}
    pads = project.get("pads") if isinstance(project.get("pads"), list) else []
    pat = {"bars": pattern.get("bars") if pattern.get("bars") is not None else 2, "events": copy.deepcopy(list(pattern.get("events") or []))}
    song = clean_song(project.get("song"), pat)
    overrides = project.get("overrides") if isinstance(project.get("overrides"), dict) else {}
    return {
        "project": {
            "pads": [{**_default_pad(), **clean_pad(pads[p] if p < len(pads) else None)} for p in range(PADS)],
            "overrides": copy.deepcopy(overrides),
            "pattern": pat,
            "fx": {**DEFAULT_FX, **_pick(project.get("fx"), DEFAULT_FX)},
            # optional keys: absent in old projects (and until first used), which then mean the defaults
            **({"tempo": _clean_tempo(project.get("tempo"))} if _clean_tempo(project.get("tempo")) else {}),
            **({"song": song} if song else {}),
        },
        "chops": chops,
        "source": source,
        "mega": mega,
        "sections": list(sections or []),
        "bank": 0,
        "selected": 0,
        "settings": dict(DEFAULT_SETTINGS),
    }


def project_doc(state):
    """The part of the state that is saved: the project (the mega bank's `project`, contracts §15)."""
    return state["project"]


def _with_project(state, **patch):
    return {**state, "project": {**state["project"], **patch}}


def _clamp_bank(state, n):
    return max(0, min(bank_count(state["project"], state.get("chops")) - 1, n))


def _target_bank(a, state):
    """Bank an action about a pad refers to: `bank` when sent, else the current one. None when `bank` is no valid bank."""
    if a.get("bank") is None:
        return state["bank"]
    b = _bank_key(a["bank"])
    return b if b is not None and b >= 0 else None


def _kept(proj, bank, pad):
    o = proj["overrides"].get(f"{bank}:{pad}")
    return o if isinstance(o, dict) and o.get("kept") is True else None


def reduce(state, action):
    """Pure reducer. Unknown or invalid actions return `state` itself."""
    nxt = _reduce_core(state, action)
    t = action.get("type") if isinstance(action, dict) else None
    if nxt is not state and isinstance(t, str) and t.startswith("pattern."):
        return _mirror_current(nxt)  # W3b: the edited pattern is copied into its entry of project.song.patterns
    return nxt


def _reduce_core(state, action):
    a = action if isinstance(action, dict) else {}
    t = a.get("type")
    proj = state["project"]
    if t == "pad.set":
        pad = _pad(a.get("pad"))
        if pad is None:
            return state
        bank = _target_bank(a, state)
        if bank is None:
            return state
        o = _kept(proj, bank, pad)
        if o:  # CHOP-13: a kept pad's sound lives in its override
            cur = {**_default_pad(), **(o.get("params") or {})}
            params = clean_pad(a.get("params"), cur)
            if _same(cur, params):
                return state
            return _with_project(state, overrides={**proj["overrides"], f"{bank}:{pad}": {**o, "params": {**cur, **params}}})
        params = clean_pad(a.get("params"), proj["pads"][pad])
        if _same(proj["pads"][pad], params):
            return state
        pads = [{**P, **params} if p == pad else P for p, P in enumerate(proj["pads"])]
        return _with_project(state, pads=pads)
    if t == "pad.reset":
        pad = _pad(a.get("pad"))
        if pad is None:
            return state
        bank = _target_bank(a, state)
        if bank is None:
            return state
        o = _kept(proj, bank, pad)
        if o:
            if _same_value(o.get("params"), _default_pad()):
                return state
            return _with_project(state, overrides={**proj["overrides"], f"{bank}:{pad}": {**o, "params": _default_pad()}})
        return _with_project(state, pads=[_default_pad() if p == pad else P for p, P in enumerate(proj["pads"])])
    if t == "pad.copyToAll":
        pad = _pad(a.get("pad"))
        if pad is None:
            return state
        return _with_project(state, pads=[dict(proj["pads"][pad]) for _ in range(PADS)])
    if t == "pad.select":
        pad = _pad(a.get("pad"))
        return {**state, "selected": pad} if pad is not None and pad != state["selected"] else state

    if t == "slice.set":
        pad, bank = _pad(a.get("pad")), _bank_key(a.get("bank"))
        s, e = a.get("start"), a.get("end")
        if pad is None or bank is None or not (_is_num(s) and _is_num(e) and e > s):
            return state
        return _with_project(state, overrides={**proj["overrides"], f"{bank}:{pad}": {"start": s, "end": e}})
    if t == "slice.clear":
        bank, pad = _bank_key(a.get("bank")), _bank_key(a.get("pad"))
        key = f"{bank}:{pad}"
        if bank is None or pad is None or key not in proj["overrides"]:
            return state
        return _with_project(state, overrides={k: v for k, v in proj["overrides"].items() if k != key})
    if t == "slice.setMany":
        bank = _bank_key(a.get("bank"))
        if bank is None:
            return state
        overrides = dict(proj["overrides"])
        for s in a.get("slices") or []:
            if not isinstance(s, dict):
                continue
            pad = _pad(s.get("pad"))
            if pad is not None and _is_num(s.get("start")) and _is_num(s.get("end")) and s["end"] > s["start"]:
                overrides[f"{bank}:{pad}"] = {"start": s["start"], "end": s["end"]}
        return _with_project(state, overrides=overrides)
    if t == "slice.stretch":  # attach (or, with stretch null, remove) a time-stretched rendering of the pad's slice
        pad, bank = _pad(a.get("pad")), _bank_key(a.get("bank"))
        if pad is None or bank is None:
            return state
        key = f"{bank}:{pad}"
        cur = proj["overrides"].get(key)
        st = a.get("stretch")
        if not st:
            if not cur or not cur.get("stretch"):
                return state
            return _with_project(state, overrides={**proj["overrides"], key: {k: v for k, v in cur.items() if k != "stretch"}})
        ratio, url = (st.get("ratio"), st.get("url")) if isinstance(st, dict) else (None, None)
        if not (_is_num(ratio) and ratio > 0) or not isinstance(url, str) or not url:
            return state
        if cur and cur.get("src"):  # a resample is already at the project tempo and has no slice of the song to stretch
            return state
        base = cur
        if not base:
            chops = state.get("chops") or {}
            try:
                ch = chops["chops"][chops["banks"][bank][pad]]
            except (KeyError, IndexError, TypeError):
                return state
            base = {"start": ch["start"], "end": ch["end"]}
        return _with_project(state, overrides={**proj["overrides"], key: {**base, "stretch": _clean_stretch(st)}})
    if t == "slice.src":  # W3b: put a rendered file of the song (a resample) on a pad; src null removes it again
        pad, bank = _pad(a.get("pad")), _bank_key(a.get("bank"))
        if pad is None or bank is None or bank < 0:
            return state
        key = f"{bank}:{pad}"
        cur = proj["overrides"].get(key)
        src = a.get("src")
        if "src" in a and src is None:
            if not cur or not cur.get("src"):
                return state
            return _with_project(state, overrides={k: v for k, v in proj["overrides"].items() if k != key})
        s_, e_ = a.get("start"), a.get("end")
        if not isinstance(src, str) or not RESAMPLE_URL.match(src) or not (_is_num(s_) and _is_num(e_) and s_ >= 0 and e_ > s_):
            return state
        return _with_project(state, overrides={**proj["overrides"], key: {"start": s_, "end": e_, "src": src}})
    if t == "slice.stem":  # W3b: the pad plays its slice from a stem of the song; null = the full mix again
        pad, bank = _pad(a.get("pad")), _bank_key(a.get("bank"))
        if pad is None or bank is None or bank < 0:
            return state
        key = f"{bank}:{pad}"
        cur = proj["overrides"].get(key)
        stem = a.get("stem")
        if "stem" in a and stem is None:
            if not cur or not cur.get("stem"):
                return state
            return _with_project(state, overrides={**proj["overrides"], key: {k: v for k, v in cur.items() if k not in ("stem", "stretch")}})
        if stem not in STEMS or (cur and cur.get("src")) or (cur and cur.get("stem") == stem):
            return state
        base = cur
        if not base:
            chops = state.get("chops") or {}
            try:
                ch = chops["chops"][chops["banks"][bank][pad]]
            except (KeyError, IndexError, TypeError):
                return state
            base = {"start": ch["start"], "end": ch["end"]}
        return _with_project(state, overrides={**proj["overrides"], key: {**{k: v for k, v in base.items() if k != "stretch"}, "stem": stem}})
    if t == "slice.keep":  # CHOP-13: the pad owns its audio (a file of the mega bank) and its sound
        pad, bank = _pad(a.get("pad")), _bank_key(a.get("bank"))
        src, s_, e_ = a.get("src"), a.get("start"), a.get("end")
        if pad is None or bank is None or bank < 0 or not isinstance(src, str) or not MEGA_SAMPLE_URL.match(src):
            return state
        if not (_is_num(s_) and _is_num(e_) and s_ >= 0 and e_ > s_):
            return state
        if a.get("params") is not None and not isinstance(a["params"], dict):
            return state
        fr = a.get("from")
        if fr is not None and not (isinstance(fr, dict) and isinstance(fr.get("song"), str) and fr["song"]
                                   and _is_num(fr.get("start")) and _is_num(fr.get("end"))):
            return state
        params = {**_default_pad(), **clean_pad(a["params"] if a.get("params") is not None else proj["pads"][pad])}
        gain = a.get("gain")  # the level the pad played at before it was kept, so keeping does not change loudness
        if gain is not None and not (_is_num(gain) and 0 < gain <= 4):
            return state
        o = {"start": s_, "end": e_, "src": src, "kept": True, "params": params}
        if fr is not None:
            o["from"] = {"song": fr["song"], "start": fr["start"], "end": fr["end"]}
        if gain is not None and gain != 1:
            o["gain"] = gain
        key = f"{bank}:{pad}"
        if _same_value(proj["overrides"].get(key), o):
            return state
        return _with_project(state, overrides={**proj["overrides"], key: o})

    if t == "kit.load":  # put a kit's pads (16 entries or null) on a bank; null = leave the pad alone
        bank = a.get("bank")
        if not (isinstance(bank, int) and not isinstance(bank, bool) and bank >= 0) or not isinstance(a.get("pads"), list):
            return state
        overrides = dict(proj["overrides"])
        pads = proj["pads"]
        for pad, k in enumerate(a["pads"][:PADS]):
            if not isinstance(k, dict) or not isinstance(k.get("song_id"), str):
                continue
            s_, e_ = k.get("start"), k.get("end")
            if not (_is_num(s_) and _is_num(e_) and e_ > s_ and s_ >= 0):
                continue
            o = {"start": s_, "end": e_}
            if k["song_id"] != a.get("song"):
                o["song"] = k["song_id"]
            ks = k.get("stretch")
            if isinstance(ks, dict) and ks.get("url") and _is_num(ks.get("ratio")) and ks["ratio"] > 0:
                o["stretch"] = _clean_stretch(ks)
            if isinstance(k.get("src"), str) and RESAMPLE_URL.match(k["src"]):
                o["src"] = k["src"]
            elif k.get("stem") in STEMS:
                o["stem"] = k["stem"]
            overrides[f"{bank}:{pad}"] = o
            params = clean_pad(k.get("params"), pads[pad])
            if params:
                pads = [{**P, **params} if i == pad else P for i, P in enumerate(pads)]
        return _with_project(state, overrides=overrides, pads=pads)

    if t == "tempo.set":  # W3b: project tempo; bpm null = the song's own tempo
        cur = proj.get("tempo") or DEFAULT_TEMPO
        bpm = a["bpm"] if "bpm" in a else cur["bpm"]  # bpm omitted = keep it (set only `follow`)
        if bpm is not None and not (_is_num(bpm) and TEMPO_MIN <= bpm <= TEMPO_MAX):
            return state
        follow = a["follow"] if isinstance(a.get("follow"), bool) else cur["follow"]
        if bpm == cur["bpm"] and follow == cur["follow"] and (bpm is None) == (cur["bpm"] is None):
            return state
        return _with_project(state, tempo={"bpm": bpm, "follow": follow})

    if t == "fx.set":
        params = _pick(a.get("params"), DEFAULT_FX)
        if _same(proj["fx"], params):
            return state
        return _with_project(state, fx={**proj["fx"], **params})

    if t == "pattern.toggleStep":
        pad, step = _pad(a.get("pad")), a.get("step")
        if pad is None or not (_is_num(step) and step >= 0):
            return state
        pattern = proj["pattern"]
        i = next((k for k, e in enumerate(pattern["events"]) if e.get("pad") == pad and _step_of(e) == step), -1)
        if i >= 0:
            events = [e for k, e in enumerate(pattern["events"]) if k != i]
        else:
            bank = a.get("bank") if a.get("bank") is not None else state["bank"]
            vel = a.get("vel") if a.get("vel") is not None else 1
            # pass -1: drawn by hand, not recorded, so "undo last take" leaves it alone
            extra = {k: a[k] for k in ("pitch", "cutoff") if _is_num(a.get(k))}  # W3b: a hit's own pitch offset and cutoff
            events = [*pattern["events"], {"beat": step / 4, "pad": pad, "bank": bank, "vel": vel, "pass": -1, **extra}]
        return _with_project(state, pattern={**pattern, "events": events})
    if t == "pattern.addEvent":
        e = a.get("event")
        if not isinstance(e, dict) or _pad(e.get("pad")) is None:
            return state
        pattern = proj["pattern"]
        return _with_project(state, pattern={**pattern, "events": [*pattern["events"], dict(e)]})
    if t == "pattern.undo":
        pattern = proj["pattern"]
        last = max([0, *[e.get("pass", 0) for e in pattern["events"]]])
        events = [e for e in pattern["events"] if e.get("pass") != last]
        return state if len(events) == len(pattern["events"]) else _with_project(state, pattern={**pattern, "events": events})
    if t == "pattern.clear":
        pattern = proj["pattern"]
        return _with_project(state, pattern={**pattern, "events": []}) if pattern["events"] else state
    if t == "pattern.setBars":
        try:
            bars = float(a.get("bars"))
        except (TypeError, ValueError):
            return state
        if not bars >= 1:  # also rejects NaN
            return state
        bars = int(bars) if bars.is_integer() else bars
        bpb = a.get("beatsPerBar")
        if bpb is None:
            bpb = (state.get("chops") or {}).get("beats_per_bar")
        if bpb is None:
            bpb = 4
        events = [e for e in proj["pattern"]["events"] if e["beat"] < bars * bpb]
        return _with_project(state, pattern={"bars": bars, "events": events})
    if t == "pattern.load":
        p = a.get("pattern") if isinstance(a.get("pattern"), dict) else {}
        bars = p.get("bars") if p.get("bars") is not None else proj["pattern"]["bars"]
        return _with_project(state, pattern={"bars": bars, "events": copy.deepcopy(list(p.get("events") or []))})

    if t == "song.addPattern":  # W3b: a new pattern, which becomes the one being edited ({name?, bars?, copy?: bool})
        song = proj.get("song") or DEFAULT_SONG
        pat = proj["pattern"]
        patterns, current = song["patterns"], song["current"]
        if len(patterns) >= MAX_PATTERNS - 1:
            return state
        if current is None:  # the pattern being edited so far becomes "Pattern 1"
            current = _next_pattern_id(patterns)
            patterns = [*patterns, {"id": current, "name": f"Pattern {len(patterns) + 1}", "bars": pat["bars"], "events": pat["events"]}]
        pid = _next_pattern_id(patterns)
        bars = a["bars"] if _is_num(a.get("bars")) and a["bars"] >= 1 else pat["bars"]
        bpb = a.get("beatsPerBar") if a.get("beatsPerBar") is not None else (state.get("chops") or {}).get("beats_per_bar")
        bpb = bpb if bpb is not None else 4
        events = [dict(e) for e in pat["events"] if e["beat"] < bars * bpb] if a.get("copy") is True else []
        patterns = [*patterns, {"id": pid, "name": _clean_name(a.get("name"), f"Pattern {len(patterns) + 1}"), "bars": bars, "events": events}]
        st = {**state, "project": {**proj, "pattern": {"bars": bars, "events": events}}}
        return _with_song(st, {**song, "patterns": patterns, "current": pid})
    if t == "song.selectPattern":  # the pattern to edit
        song = proj.get("song")
        p = next((x for x in song["patterns"] if x["id"] == a.get("id")), None) if song else None
        if not p or song["current"] == p["id"]:
            return state
        st = {**state, "project": {**proj, "pattern": {"bars": p["bars"], "events": [dict(e) for e in p["events"]]}}}
        return _with_song(st, {**song, "current": p["id"]})
    if t == "song.renamePattern":
        song = proj.get("song")
        i = next((k for k, x in enumerate(song["patterns"]) if x["id"] == a.get("id")), -1) if song else -1
        name = _clean_name(a.get("name"), None)
        if i < 0 or name is None or song["patterns"][i]["name"] == name:
            return state
        return _with_song(state, {**song, "patterns": [{**p, "name": name} if k == i else p for k, p in enumerate(song["patterns"])]})
    if t == "song.removePattern":  # also leaves the chain; removing the edited pattern selects the first one left
        song = proj.get("song")
        if not song or not any(x["id"] == a.get("id") for x in song["patterns"]):
            return state
        patterns = [p for p in song["patterns"] if p["id"] != a["id"]]
        chain = [c for c in song["chain"] if c["pattern"] != a["id"]]
        nxt, st = {**song, "patterns": patterns, "chain": chain}, state
        if song["current"] == a["id"]:
            first = patterns[0] if patterns else None
            nxt = {**nxt, "current": first["id"] if first else None}
            if first:
                st = {**state, "project": {**proj, "pattern": {"bars": first["bars"], "events": [dict(e) for e in first["events"]]}}}
        return _with_song(st, nxt)
    if t == "song.setChain":  # [{pattern: id, repeats: 1..64}]; entries naming a pattern that does not exist are dropped
        song = proj.get("song") or DEFAULT_SONG
        chain = _clean_chain(a.get("chain"), song["patterns"])
        if chain == song["chain"]:
            return state
        return _with_song(state, {**song, "chain": chain})
    if t == "song.setMode":  # "pattern" loops the edited pattern, "song" plays the chain
        if a.get("mode") not in ("pattern", "song"):
            return state
        song = proj.get("song") or DEFAULT_SONG
        if song["mode"] == a["mode"]:
            return state
        return _with_song(state, {**song, "mode": a["mode"]})
    if t == "song.load":  # replace the whole song document (used to sync tabs and by agents); null clears it
        if "song" not in a or (a["song"] is not None and not a["song"]):
            return state
        song = None if a["song"] is None else clean_song(a["song"], proj["pattern"])
        if song == proj.get("song"):
            return state
        return _with_project(state, song=song) if song else _with_song(state, dict(DEFAULT_SONG))

    if t == "bank.set":
        try:
            n = float(a.get("bank"))
        except (TypeError, ValueError):
            return state
        if not math.isfinite(n):
            return state
        bank = _clamp_bank(state, int(n))
        return state if bank == state["bank"] else {**state, "bank": bank}
    if t == "chops.replace":
        return {**state, "chops": a["chops"], "bank": 0} if a.get("chops") else state
    if t == "source.set":  # CHOP-13: the song being chopped changes; the project is not touched
        song, chops = a.get("song"), a.get("chops")
        if not isinstance(song, str) or not song or not isinstance(chops, dict) or not isinstance(chops.get("banks"), list):
            return state
        if state.get("source") == song and state.get("chops") == chops:
            return state
        nxt = {**state, "source": song, "chops": chops}
        return {**nxt, "bank": _clamp_bank(nxt, state["bank"])}
    if t == "sections.set":
        return {**state, "sections": list(a.get("sections") or [])}
    if t == "settings.set":
        params = _pick(a.get("params"), DEFAULT_SETTINGS)
        if "sixteen" in params:  # W3b 16 levels: None, or a source pad and a mode; anything else is ignored
            v = params["sixteen"]
            if v is not None:
                if isinstance(v, dict) and _pad(v.get("pad")) is not None and v.get("mode") in SIXTEEN_MODES:
                    params["sixteen"] = {"pad": _pad(v["pad"]), "mode": v["mode"]}
                else:
                    del params["sixteen"]
        if _same(state["settings"], params):
            return state
        return {**state, "settings": {**state["settings"], **params}}
    return state


def diff_project(old, new):
    """Actions that turn project `old` into project `new` (both full project docs). Used to broadcast a
    whole-document save (PUT /project) to other tabs as ordinary §8 actions."""
    out = []
    for p in range(PADS):
        changed = {k: v for k, v in new["pads"][p].items() if not _same_value(old["pads"][p].get(k), v)}
        if changed:
            out.append({"type": "pad.set", "pad": p, "params": changed})
    for k in old["overrides"]:
        if k not in new["overrides"]:
            bank, pad = k.split(":")
            out.append({"type": "slice.clear", "bank": int(bank), "pad": int(pad)})
    by_bank = {}
    for k, v in new["overrides"].items():
        if old["overrides"].get(k) != v:
            bank, pad = (int(x) for x in k.split(":"))
            if v.get("kept") is True:  # CHOP-13: a kept pad travels whole (src, params, from)
                out.append({"type": "slice.keep", "bank": bank, "pad": pad, "src": v.get("src"), "start": v["start"], "end": v["end"],
                            "params": v.get("params"), **({"from": v["from"]} if "from" in v else {}),
                            **({"gain": v["gain"]} if "gain" in v else {})})
            elif "song" in v or "stretch" in v or "src" in v or "stem" in v:  # a slice with a source or a stretch travels as a one-pad kit.load
                kit = [None] * PADS
                kit[pad] = {"song_id": v.get("song", ""), "start": v["start"], "end": v["end"],
                            **({"stretch": v["stretch"]} if "stretch" in v else {}),
                            **({"src": v["src"]} if "src" in v else {}), **({"stem": v["stem"]} if "stem" in v else {})}
                out.append({"type": "kit.load", "bank": bank, "song": "", "pads": kit})
            else:
                by_bank.setdefault(bank, []).append({"pad": pad, "start": v["start"], "end": v["end"]})
    for bank, slices in by_bank.items():
        out.append({"type": "slice.setMany", "bank": bank, "slices": slices})
    fx = {k: v for k, v in new["fx"].items() if old["fx"].get(k) != v}
    if fx:
        out.append({"type": "fx.set", "params": fx})
    if old.get("tempo") != new.get("tempo"):
        t = new.get("tempo") or DEFAULT_TEMPO
        out.append({"type": "tempo.set", "bpm": t["bpm"], "follow": t["follow"]})
    if old["pattern"] != new["pattern"]:
        out.append({"type": "pattern.load", "pattern": new["pattern"]})
    if old.get("song") != new.get("song"):  # W3b: song mode (patterns, chain, mode, which pattern is edited)
        out.append({"type": "song.load", "song": new.get("song")})
    return out
