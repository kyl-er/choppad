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

PADS = 16
DEFAULT_PAD = {"gainDb": 0, "pitch": 0, "attack": 0.0025, "release": 0.003, "reverse": False, "mode": None}
DEFAULT_FX = {"tone": 0, "low": 0, "mid": 0, "high": 0, "filter": 0, "reverb": 0, "size": 0.5}
DEFAULT_SETTINGS = {"mode": "oneshot", "choke": True, "fullLevel": True, "quant": "1/16", "swing": 0,
                    "metronome": True, "repeat": False, "repeatRate": 0.25, "follow": True}

# Actions that change the saved project. The rest only change what the player looks at (not written to project.json).
PROJECT_ACTIONS = frozenset({
    "pad.set", "pad.reset", "pad.copyToAll", "slice.set", "slice.clear", "slice.setMany", "slice.stretch", "kit.load", "fx.set",
    "pattern.toggleStep", "pattern.addEvent", "pattern.undo", "pattern.clear", "pattern.setBars", "pattern.load"})
VIEW_ACTIONS = frozenset({"pad.select", "bank.set", "chops.replace", "sections.set", "settings.set"})
ACTION_TYPES = PROJECT_ACTIONS | VIEW_ACTIONS


def _is_num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


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
    """JS ===: True is not 1."""
    return a == b and isinstance(a, bool) == isinstance(b, bool)


def _same(a, b):
    return all(k in a and _same_value(a[k], v) for k, v in b.items())


def _step_of(event):
    return math.floor(event["beat"] * 4 + 0.5)  # JS Math.round


step_of = _step_of


def initial_state(project=None, chops=None, sections=None):
    """Starting state for a song. `project` is whatever is on disk (may be partial or None)."""
    project = project if isinstance(project, dict) else {}
    pattern = project.get("pattern") if isinstance(project.get("pattern"), dict) else {}
    pads = project.get("pads") if isinstance(project.get("pads"), list) else []
    overrides = project.get("overrides") if isinstance(project.get("overrides"), dict) else {}
    return {
        "project": {
            "pads": [{**DEFAULT_PAD, **_pick(pads[p] if p < len(pads) else None, DEFAULT_PAD)} for p in range(PADS)],
            "overrides": copy.deepcopy(overrides),
            "pattern": {"bars": pattern.get("bars") if pattern.get("bars") is not None else 2,
                        "events": copy.deepcopy(list(pattern.get("events") or []))},
            "fx": {**DEFAULT_FX, **_pick(project.get("fx"), DEFAULT_FX)},
        },
        "chops": chops,
        "sections": list(sections or []),
        "bank": 0,
        "selected": 0,
        "settings": dict(DEFAULT_SETTINGS),
    }


def project_doc(state):
    """The part of the state that is written to project.json."""
    return state["project"]


def _with_project(state, **patch):
    return {**state, "project": {**state["project"], **patch}}


def _clamp_bank(state, n):
    banks = (state.get("chops") or {}).get("banks")
    top = (len(banks) if banks is not None else 1) - 1
    return max(0, min(top, n))


def reduce(state, action):
    """Pure reducer. Unknown or invalid actions return `state` itself."""
    a = action if isinstance(action, dict) else {}
    t = a.get("type")
    proj = state["project"]
    if t == "pad.set":
        pad = _pad(a.get("pad"))
        params = _pick(a.get("params"), DEFAULT_PAD)
        if pad is None or _same(proj["pads"][pad], params):
            return state
        pads = [{**P, **params} if p == pad else P for p, P in enumerate(proj["pads"])]
        return _with_project(state, pads=pads)
    if t == "pad.reset":
        pad = _pad(a.get("pad"))
        if pad is None:
            return state
        return _with_project(state, pads=[dict(DEFAULT_PAD) if p == pad else P for p, P in enumerate(proj["pads"])])
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
        base = cur
        if not base:
            chops = state.get("chops") or {}
            try:
                ch = chops["chops"][chops["banks"][bank][pad]]
            except (KeyError, IndexError, TypeError):
                return state
            base = {"start": ch["start"], "end": ch["end"]}
        return _with_project(state, overrides={**proj["overrides"], key: {**base, "stretch": {"ratio": ratio, "url": url}}})
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
                o["stretch"] = {"ratio": ks["ratio"], "url": ks["url"]}
            overrides[f"{bank}:{pad}"] = o
            params = _pick(k.get("params"), DEFAULT_PAD)
            if params:
                pads = [{**P, **params} if i == pad else P for i, P in enumerate(pads)]
        return _with_project(state, overrides=overrides, pads=pads)

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
            events = [*pattern["events"], {"beat": step / 4, "pad": pad, "bank": bank, "vel": vel, "pass": -1}]
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
    if t == "sections.set":
        return {**state, "sections": list(a.get("sections") or [])}
    if t == "settings.set":
        params = _pick(a.get("params"), DEFAULT_SETTINGS)
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
            if "song" in v or "stretch" in v:  # a slice with a source or a stretch travels as a one-pad kit.load
                kit = [None] * PADS
                kit[pad] = {"song_id": v.get("song", ""), "start": v["start"], "end": v["end"],
                            **({"stretch": v["stretch"]} if "stretch" in v else {})}
                out.append({"type": "kit.load", "bank": bank, "song": "", "pads": kit})
            else:
                by_bank.setdefault(bank, []).append({"pad": pad, "start": v["start"], "end": v["end"]})
    for bank, slices in by_bank.items():
        out.append({"type": "slice.setMany", "bank": bank, "slices": slices})
    fx = {k: v for k, v in new["fx"].items() if old["fx"].get(k) != v}
    if fx:
        out.append({"type": "fx.set", "params": fx})
    if old["pattern"] != new["pattern"]:
        out.append({"type": "pattern.load", "pattern": new["pattern"]})
    return out
