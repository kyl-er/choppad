"""choppad_client: thin HTTP client for a running ChopPad server, plus the text views an agent reads.

Shared by the CLI verbs (components/integrate/choppad.py) and the MCP server (choppad_mcp.py). Stdlib only.
Everything here talks to the endpoints in contracts §10 and the existing /api routes; nothing touches media/ directly.
"""
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_SERVER = "http://127.0.0.1:8960"
ORIGIN = "agent"  # X-ChopPad-Origin of writes made from here; browser tabs have their own random ids
PADS = 16
STEPS_PER_GRID = {"1/4": 1, "1/8": 2, "1/16": 4}
PRESETS = {  # twin of components/ui/fx.js PRESETS
    "dry": {"tone": 0, "low": 0, "mid": 0, "high": 0, "filter": 0, "reverb": 0, "size": 0.5},
    "warm": {"tone": -0.35, "low": 2, "mid": 0, "high": 0, "filter": 0, "reverb": 0.18, "size": 0.3},
    "room": {"tone": 0, "low": 0, "mid": 0, "high": 0, "filter": 0, "reverb": 0.25, "size": 0.35},
    "hall": {"tone": 0.1, "low": 0, "mid": 0, "high": 0, "filter": 0, "reverb": 0.4, "size": 0.85},
    "lofi": {"tone": -0.5, "low": 0, "mid": 0, "high": -4, "filter": -0.35, "reverb": 0.15, "size": 0.4},
    "radio": {"tone": 0, "low": -8, "mid": 6, "high": -6, "filter": 0.45, "reverb": 0, "size": 0.5},
}
FX_KEYS = ("tone", "low", "mid", "high", "filter", "reverb", "size")
PAD_KEYS = ("gainDb", "pitch", "attack", "decay", "sustain", "release", "reverse", "mode", "filter", "muteGroup")
FILTER_TYPES = ("off", "lp", "hp", "bp")


class ControlError(Exception):
    """A request failed in a way worth telling the agent about (message is the whole story)."""


class Client:
    def __init__(self, server=None, origin=ORIGIN, timeout=30):
        self.server = (server or os.environ.get("CHOPPAD_SERVER") or DEFAULT_SERVER).rstrip("/")
        self.origin = origin
        self.timeout = timeout

    # ---- transport ----
    def _call(self, method, path, body=None, headers=None, raw=None, timeout=None):
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        h = {"X-ChopPad-Origin": self.origin, **(headers or {})}
        if raw is None and data is not None:
            h["Content-Type"] = "application/json"
        req = urllib.request.Request(self.server + path, data=data, method=method, headers=h)
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as r:
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            try:
                msg = json.loads(e.read()).get("error") or e.reason
            except (ValueError, AttributeError):
                msg = e.reason
            raise ControlError(f"{method} {path}: HTTP {e.code}: {msg}") from None
        except urllib.error.URLError as e:
            raise ControlError(f"cannot reach the ChopPad server at {self.server} ({e.reason}); "
                               "start it with `choppad --serve` or pass --server / set CHOPPAD_SERVER") from None

    def get(self, path):
        return self._call("GET", path)

    def post(self, path, body=None, timeout=None):
        return self._call("POST", path, body if body is not None else {}, timeout=timeout)

    # ---- the API ----
    def songs(self):
        return self.get("/api/songs")

    def song(self, sid):
        return self.get(f"/api/songs/{_sid(sid)}")

    def actions(self, sid, actions):
        """Apply §8 actions on the server; open browsers show them live. Returns the server's answer."""
        return self.post(f"/api/songs/{_sid(sid)}/actions", {"actions": actions})

    def transport(self, sid, command, **kw):
        return self.post(f"/api/songs/{_sid(sid)}/transport", {"command": command, **kw})

    # --- W2 DAW bridge (contracts §11) ---
    def daw(self, sid, daw, command, bank=None):
        """open | sync | play | stop on a DAW bridge (hydrogen or ardour) held by the server. Opening can take a while
        (Ardour builds a session), hence the long timeout."""
        body = {"daw": daw, "command": command}
        if bank is not None:
            body["bank"] = bank
        return self.post(f"/api/songs/{_sid(sid)}/daw", body, timeout=300)

    def regrid_to(self, sid, **change):
        """change: grid, meter, bpm_mult, beat_offset (absolute) and/or mult, shift (relative). Returns the new chops."""
        chops = self.song(sid)["chops"]
        grid = change.get("grid") or chops["grid"]
        mult = change.get("bpm_mult", chops.get("bpm_mult", 1)) * change.get("mult", 1)
        offset = change.get("beat_offset", chops.get("beat_offset", 0)) + change.get("shift", 0)
        body = {"grid": grid, "meter": change.get("meter"), "beat_offset": int(offset), "bpm_mult": mult}
        return self.post(f"/api/songs/{_sid(sid)}/regrid", body)

    def reanalyze(self, sid, bpm, meter=None):
        return self.post(f"/api/songs/{_sid(sid)}/reanalyze", {"bpm": bpm, "meter": meter}, timeout=300)["chops"]

    def export(self, sid, kind, bank=0):
        fx = self.song(sid)["project"].get("fx")
        return self.post(f"/api/songs/{_sid(sid)}/export", {"kind": kind, "bank": bank, "fx": fx}, timeout=300)

    def import_source(self, source, wait_s=900, progress=None):
        """URL -> import job; an existing file path -> upload. Waits for the job; returns {id}."""
        if os.path.isfile(source):
            size = os.path.getsize(source)
            with open(source, "rb") as f:
                job = self._call("POST", "/api/upload", raw=f, timeout=600, headers={
                    "X-Filename": urllib.parse.quote(os.path.basename(source)), "Content-Length": str(size)})["job"]
        else:
            job = self.post("/api/songs", {"url": source})["job"]
        end = time.time() + wait_s
        while time.time() < end:
            j = self.get(f"/api/jobs/{job}")
            if progress:
                progress(j)
            if j.get("state") == "done":
                return {"id": j["id"]}
            if j.get("state") == "error":
                raise ControlError(f"import failed: {j.get('error')}")
            time.sleep(1)
        raise ControlError(f"import still running after {wait_s}s (job {job}); check `choppad songs` later")

    # ---- verbs (one call each; CLI and MCP both use these) ----
    def assign(self, sid, bank, pad, start=None, end=None, clear=False):
        if clear:
            return self.actions(sid, [{"type": "slice.clear", "bank": bank, "pad": pad}])
        if start is None or end is None or not end > start:
            raise ControlError("assign needs start and end with end > start (seconds)")
        return self.actions(sid, [{"type": "slice.set", "bank": bank, "pad": pad, "start": start, "end": end}])

    def pad(self, sid, pad, reset=False, **params):
        acts = [{"type": "pad.reset", "pad": pad}] if reset else []
        p = pad_params(**params)
        if p:
            acts.append({"type": "pad.set", "pad": pad, "params": p})
        if not acts:
            raise ControlError("nothing to change; give --gain/--pitch/--attack/--decay/--sustain/--release/--reverse/--mode/"
                               "--filter-type/--cutoff/--q or --reset")
        return self.actions(sid, acts)

    def pattern_set(self, sid, steps=None, bars=None, bank=None, vel=1.0, add=False, pitch=None, cutoff=None):
        """Replace the pattern with `steps` ("1:0,8 3:4,12"), or with add=True merge them in. Only `bars`: change loop length."""
        d = self.song(sid)
        cur = (d.get("project") or {}).get("pattern") or {"bars": 2, "events": []}
        bpb = d["chops"]["beats_per_bar"]
        if steps is None:
            if bars is None:
                raise ControlError("give --steps and/or --bars")
            return self.actions(sid, [{"type": "pattern.setBars", "bars": bars, "beatsPerBar": bpb}])
        bank = bank if bank is not None else (d.get("view") or {}).get("bank") or 0
        use_bars = bars if bars is not None else cur.get("bars", 2)
        events = pattern_events(parse_steps(steps, use_bars, bpb), bank, vel, pitch, cutoff)
        if add:
            have = {(e["pad"], round(e["beat"] * 4)) for e in cur.get("events", [])}
            events = cur.get("events", []) + [e for e in events if (e["pad"], round(e["beat"] * 4)) not in have]
            if bars is None:
                use_bars = cur.get("bars", 2)
        return self.actions(sid, [{"type": "pattern.load", "pattern": {"bars": use_bars, "events": events}}])

    # ---- W3b: project tempo ----
    def _slice_of(self, d, bank, pad):
        """(source song id, start, end) of the slice on (bank, pad), or None when the pad is empty."""
        o = ((d.get("project") or {}).get("overrides") or {}).get(f"{bank}:{pad}")
        if o and o.get("end", 0) > o.get("start", 0):
            return (o.get("song") or d["meta"]["id"], o["start"], o["end"])
        chops = d["chops"]
        try:
            ch = chops["chops"][chops["banks"][bank][pad]]
        except (IndexError, TypeError):
            return None
        return (d["meta"]["id"], ch["start"], ch["end"])

    def fit_tempo(self, sid, scope="pattern", refit=False):
        """Stretch the pads of `scope` ("pattern": the pads the pattern plays; "bank": the bank on screen) to the project
        tempo with the server's stretch endpoint, mark them `fit`, and save it as slice.stretch actions. `refit`: instead
        every pad that was fitted before. Pads stretched by hand and resample pads are left alone.
        Returns {tempo, fitted: [{bank, pad, ratio}], skipped: [{bank, pad, reason}]}."""
        d = self.song(sid)
        proj = d.get("project") or {}
        T = (proj.get("tempo") or {}).get("bpm")
        ov = proj.get("overrides") or {}
        if refit:
            keys = [k for k, o in ov.items() if (o.get("stretch") or {}).get("fit") is not None]
        elif scope == "bank":
            bank = (d.get("view") or {}).get("bank") or 0
            keys = [f"{bank}:{p}" for p in range(PADS)]
        elif scope == "pattern":
            keys = sorted({f"{e.get('bank', 0)}:{e['pad']}" for e in (proj.get("pattern") or {}).get("events", [])})
        else:
            raise ControlError("scope must be pattern or bank")
        if not refit and T is None:
            raise ControlError("set a project tempo first (choppad tempo ID --bpm N)")
        acts, fitted, skipped = [], [], []
        for k in keys:
            bank, pad = (int(x) for x in k.split(":"))
            o = ov.get(k) or {}
            st = o.get("stretch")
            if T is None:
                if st and st.get("fit") is not None:
                    acts.append({"type": "slice.stretch", "bank": bank, "pad": pad, "stretch": None})
                    fitted.append({"bank": bank, "pad": pad, "ratio": 1})
                continue
            if st and st.get("fit") is None:
                skipped.append({"bank": bank, "pad": pad, "reason": "stretched by hand"})
                continue
            if o.get("src"):
                skipped.append({"bank": bank, "pad": pad, "reason": "resample (already at the project tempo)"})
                continue
            sl = self._slice_of(d, bank, pad)
            if not sl:
                continue
            if st and st.get("fit") == T:
                skipped.append({"bank": bank, "pad": pad, "reason": "already fitted"})
                continue
            body = {"bank": bank, "pad": pad, "start": sl[1], "end": sl[2], "target_bpm": T}
            if o.get("stem"):  # a pad that plays a stem is stretched from that stem
                body["stem"] = o["stem"]
            r = self.post(f"/api/songs/{_sid(sl[0])}/stretch", body, timeout=300)
            if abs(r["ratio"] - 1) < 0.002:
                if st:
                    acts.append({"type": "slice.stretch", "bank": bank, "pad": pad, "stretch": None})
            else:
                acts.append({"type": "slice.stretch", "bank": bank, "pad": pad,
                             "stretch": {"ratio": r["ratio"], "url": r["url"], "fit": T}})
            fitted.append({"bank": bank, "pad": pad, "ratio": round(r["ratio"], 4)})
        if acts:
            self.actions(sid, acts)
        return {"tempo": T, "fitted": fitted, "skipped": skipped}

    def tempo(self, sid, bpm=None, song=False, follow=None, fit=None):
        """Show or change the project tempo. `bpm` sets it, `song=True` goes back to the song's own tempo, `follow`
        (bool) = fitted pads follow tempo changes, `fit` = "pattern" | "bank" fits those pads now. When follow is on,
        pads fitted earlier are re-fitted to a new tempo."""
        if bpm is not None and not 20 <= bpm <= 400:
            raise ControlError("tempo must be between 20 and 400 BPM")
        d = self.song(sid)
        cur = (d.get("project") or {}).get("tempo") or {"bpm": None, "follow": True}
        act = {"type": "tempo.set"}
        if song:
            act["bpm"] = None
        elif bpm is not None:
            act["bpm"] = bpm
        if follow is not None:
            act["follow"] = follow
        out = {"tempo": cur["bpm"], "follow": cur["follow"], "song_bpm": round(d["chops"]["bpm"], 3)}
        if len(act) > 1:
            r = self.actions(sid, [act])
            out.update(applied=r.get("applied"), listeners=r.get("listeners"))
            t = r["project"].get("tempo") or {"bpm": None, "follow": True}
            out.update(tempo=t["bpm"], follow=t["follow"])
            if t["follow"] and (bpm is not None or song):
                fit_r = self.fit_tempo(sid, refit=True)
                if fit_r["fitted"]:
                    out["refitted"] = len(fit_r["fitted"])
        if fit:
            out["fit"] = self.fit_tempo(sid, fit)
        return out

    # ---- W3b: song mode ----
    def song_show(self, sid):
        """The song-mode state: {mode, editing, patterns: [{id, name, bars, hits}], chain: [{pattern, repeats}], bars}."""
        d = self.song(sid)
        proj = d.get("project") or {}
        s = proj.get("song") or {"patterns": [], "chain": [], "mode": "pattern", "current": None}
        cur = proj.get("pattern") or {"bars": 2, "events": []}
        pats = [{"id": p["id"], "name": p["name"], "bars": p["bars"], "hits": len(p["events"])} for p in s["patterns"]]
        if not pats:  # an old project: the one pattern it has
            pats = [{"id": None, "name": "(the pattern, not yet in a song)", "bars": cur.get("bars", 2), "hits": len(cur.get("events", []))}]
        sizes = {p["id"]: p["bars"] for p in s["patterns"]}
        return {"mode": s["mode"], "editing": s["current"], "patterns": pats, "chain": s["chain"],
                "chain_bars": sum(sizes.get(c["pattern"], 0) * c["repeats"] for c in s["chain"])}

    def song_edit(self, sid, op, pattern=None, name=None, bars=None, copy=False, chain=None, mode=None):
        """Song-mode edits: op = add | select | rename | remove | chain | mode. `chain` is "p1:2 p2" (pattern:repeats) or a list
        of {pattern, repeats}. Browsers show the change live."""
        def need(v, what):
            if v is None:
                raise ControlError(f"{op} needs {what}")
            return v
        if op == "add":
            bpb = self.song(sid)["chops"]["beats_per_bar"]
            act = {"type": "song.addPattern", "beatsPerBar": bpb, **({"name": name} if name else {}),
                   **({"bars": bars} if bars is not None else {}), **({"copy": True} if copy else {})}
        elif op == "select":
            act = {"type": "song.selectPattern", "id": need(pattern, "a pattern id")}
        elif op == "rename":
            act = {"type": "song.renamePattern", "id": need(pattern, "a pattern id"), "name": need(name, "a name")}
        elif op == "remove":
            act = {"type": "song.removePattern", "id": need(pattern, "a pattern id")}
        elif op == "chain":
            act = {"type": "song.setChain", "chain": parse_chain(chain) if isinstance(chain, str) else need(chain, "a chain like 'p1:2 p2'")}
        elif op == "mode":
            if mode not in ("pattern", "song"):
                raise ControlError("mode must be pattern or song")
            act = {"type": "song.setMode", "mode": mode}
        else:
            raise ControlError("song op must be add, select, rename, remove, chain or mode")
        return self.actions(sid, [act])

    # ---- W3b: stems ----
    def stems(self, sid, force=False):
        """Split the song into stems with the server's stems backend (CHOPPAD_STEMS_BACKEND=remote + CHOPPAD_STEMS_URL on the
        server). Can take minutes. Returns {stems: [...], urls: {...}}; without a backend the server's message is the error."""
        return self.post(f"/api/songs/{_sid(sid)}/stems", {"force": True} if force else {}, timeout=1800)

    def pad_stem(self, sid, bank, pad, stem):
        """Make a pad play its slice from a stem ("drums", "bass", "vocals", "other"), or from the full mix again with stem=None."""
        if stem is not None and stem not in ("drums", "bass", "vocals", "other"):
            raise ControlError("stem must be drums, bass, vocals or other")
        return self.actions(sid, [{"type": "slice.stem", "bank": bank, "pad": pad, "stem": stem}])

    # ---- W3b: resample ----
    def resample(self, sid, pad, bars=None, seconds=None, source="pattern", bank=None, wait_s=120):
        """Ask the browser that has the song open to resample into a new sound on `pad` (the browser renders or records, so
        one must be open). source "pattern" = an offline render of the step pattern at the project tempo, "master" = a
        live recording of the master bus (real time). Waits until the pad plays the new file; returns {bank, pad, src, start, end}."""
        if source not in ("pattern", "master"):
            raise ControlError("source must be pattern or master")
        if (bars is None) == (seconds is None):
            raise ControlError("give bars or seconds (one of them)")
        if not (isinstance(pad, int) and 0 <= pad < PADS):
            raise ControlError("pad must be 0..15")
        bank = bank if bank is not None else self.current_bank(sid)
        key = f"{bank}:{pad}"
        old = (((self.song(sid).get("project") or {}).get("overrides") or {}).get(key) or {}).get("src")
        body = {"source": source, "pad": pad, "bank": bank, **({"bars": bars} if bars is not None else {"seconds": seconds})}
        r = self.transport(sid, "resample", **body)
        if r.get("listeners") == 0:
            raise ControlError("no browser has this song open; resampling is done by the browser (open the song, then retry)")
        end = time.time() + wait_s
        while time.time() < end:
            o = (((self.song(sid).get("project") or {}).get("overrides") or {}).get(key) or {})
            if o.get("src") and o["src"] != old:
                return {"bank": bank, "pad": pad, "src": o["src"], "start": o["start"], "end": o["end"]}
            time.sleep(0.5)
        raise ControlError(f"the browser did not finish the resample within {wait_s}s (is audio enabled in that tab?)")

    def pattern_clear(self, sid):
        return self.actions(sid, [{"type": "pattern.clear"}])

    def fx(self, sid, preset=None, **params):
        return self.actions(sid, [{"type": "fx.set", "params": fx_params(preset, **params)}])

    def play(self, sid, what, start=None):
        cmd = {"pattern": "play-pattern", "song": "play-song", "stop": "stop"}.get(what)
        if not cmd:
            raise ControlError("play takes pattern, song or stop")
        return self.transport(sid, cmd, **({"from": start} if start is not None and what == "song" else {}))

    def hit(self, sid, pad, velocity=1.0, bank=None):
        return self.transport(sid, "hit", pad=pad, velocity=velocity, **({"bank": bank} if bank is not None else {}))

    def pattern_view(self, sid, bank=None):
        d = self.song(sid)
        return pattern_text((d.get("project") or {}).get("pattern") or {"bars": 2, "events": []}, d["chops"]["beats_per_bar"], bank)

    def chop_rows(self, sid, bank=None):
        d = self.song(sid)
        return chop_table(d, bank if bank is not None else (d.get("view") or {}).get("bank") or 0)

    # ---- views sized for a person or an LLM ----
    def current_bank(self, sid):
        return (self.song(sid).get("view") or {}).get("bank") or 0

    def summary(self, sid):
        d = self.song(sid)
        out = song_summary(d)
        try:
            secs = self.post(f"/api/songs/{_sid(sid)}/sections").get("sections", [])
            out["sections"] = [{"label": s["label"], "bars": f"{s['start_bar']}-{s['end_bar']}"} for s in secs]
        except ControlError:
            out["sections"] = []
        return out


def _sid(sid):
    sid = str(sid)
    if not sid or "/" in sid:
        raise ControlError(f"bad song id {sid!r}")
    return urllib.parse.quote(sid, safe="")


def song_summary(d):
    """Small description of a song from GET /api/songs/<id> (no peaks, no chop list)."""
    meta, chops, ana, proj = d["meta"], d["chops"], d.get("analysis") or {}, d.get("project") or {}
    out = {"id": meta.get("id"), "title": meta.get("title"), "duration_s": round(meta.get("duration_s") or 0, 2),
           "bpm": round(chops["bpm"], 2), "meter": f"{chops['beats_per_bar']}/4", "grid": chops["grid"],
           "banks": len(chops["banks"]), "chops": len(chops["chops"]),
           "pattern": {"bars": (proj.get("pattern") or {}).get("bars", 2),
                       "hits": len((proj.get("pattern") or {}).get("events", []))},
           "custom_slices": len(proj.get("overrides") or {})}
    key = ana.get("key") or meta.get("key")
    if key:
        out["key"] = key
    if (proj.get("tempo") or {}).get("bpm"):  # W3b: the project tempo, when one is set
        out["project_tempo"] = proj["tempo"]["bpm"]
    if proj.get("song"):  # W3b: song mode, when the project has patterns or a chain
        out["song"] = {"mode": proj["song"]["mode"], "patterns": len(proj["song"]["patterns"]), "chain_entries": len(proj["song"]["chain"])}
    bank = (d.get("view") or {}).get("bank")
    if bank is not None:
        out["current_bank"] = bank
    return out


def chop_label(chop, grid):
    n = STEPS_PER_GRID.get(grid, 1)
    return f"{chop['bar'] + 1}.{chop['beat'] // n + 1}" + (f".{chop['beat'] % n + 1}" if n > 1 else "")


def chop_table(d, bank):
    """One bank as rows {pad, label, start, end, custom}. Pads past the end of a short last bank are left out."""
    chops, over = d["chops"], (d.get("project") or {}).get("overrides") or {}
    if not 0 <= bank < len(chops["banks"]):
        raise ControlError(f"bank must be 0..{len(chops['banks']) - 1}")
    rows = []
    for pad in range(PADS):
        sl = over.get(f"{bank}:{pad}")
        idx = chops["banks"][bank][pad] if pad < len(chops["banks"][bank]) else None
        c = chops["chops"][idx] if idx is not None else None
        if not sl and not c:
            continue
        rows.append({"pad": pad, "label": chop_label(c, chops["grid"]) if c else "-",
                     "start": round(sl["start"] if sl else c["start"], 3), "end": round(sl["end"] if sl else c["end"], 3),
                     "custom": bool(sl)})
    return rows


def format_table(rows, cols):
    """Plain aligned text table from a list of dicts."""
    if not rows:
        return "(none)"
    cells = [[str(r.get(c, "")) for c in cols] for r in rows]
    w = [max(len(c), *(len(x[i]) for x in cells)) for i, c in enumerate(cols)]
    line = lambda xs: "  ".join(x.ljust(w[i]) for i, x in enumerate(xs)).rstrip()  # noqa: E731
    return "\n".join([line(cols), *map(line, cells)])


def pattern_text(pattern, beats_per_bar=4, bank=None):
    """The pattern as a 16-row step grid (pads as rows, 1/16 steps as columns, beats and bars separated).
    `bank` shows only that bank's hits; None merges every bank. Hits off the 1/16 grid are counted, not drawn."""
    bars = pattern.get("bars", 2)
    per_bar = int(beats_per_bar) * 4
    total = int(bars * per_bar)
    hits, off, banks = {}, 0, set()
    for e in pattern.get("events", []):
        if bank is not None and e.get("bank") != bank:
            continue
        step = round(e["beat"] * 4)
        if abs(e["beat"] * 4 - step) > 0.01 or not 0 <= step < total:
            off += 1
            continue
        hits[(e["pad"], step)] = e.get("vel", 1)
        banks.add(e.get("bank"))
    head = "".join(("|" if s and s % per_bar == 0 else " " if s and s % 4 == 0 else "") + (str(s % per_bar // 4 + 1) if s % 4 == 0 else " ")
                   for s in range(total))
    lines = [f"pattern: {bars} bars of {beats_per_bar}/4, {total} steps (1/16)" + (f", bank {bank}" if bank is not None else ""),
             "     " + head]
    for pad in range(PADS):
        row = "".join(("|" if s and s % per_bar == 0 else " " if s and s % 4 == 0 else "") +
                      (("X" if hits[(pad, s)] >= 0.7 else "x") if (pad, s) in hits else ".") for s in range(total))
        lines.append(f"p{pad:02d}  {row}")
    lines.append(f"{len(hits)} hits (x soft, X full); banks used: {sorted(b for b in banks if b is not None) or '-'}"
                 + (f"; {off} hits off the grid not shown" if off else ""))
    return "\n".join(lines)


def parse_steps(text, bars=None, beats_per_bar=4):
    """`"1:0,8 3:4,12"` -> {1: [0, 8], 3: [4, 12]}: pad:steps (1/16 steps from the loop start, 0-based), groups split on
    whitespace or `;`. With `bars`, steps past the loop end are an error."""
    out = {}
    for group in str(text).replace(";", " ").split():
        pad_s, sep, steps_s = group.partition(":")
        try:
            if not sep:
                raise ValueError
            pad = int(pad_s)
            steps = [int(x) for x in steps_s.split(",") if x != ""]
        except ValueError:
            raise ControlError(f"bad steps group {group!r}; expected PAD:STEP,STEP like 1:0,8") from None
        if not steps:
            raise ControlError(f"no steps for pad {pad} in {group!r}")
        if not 0 <= pad < PADS:
            raise ControlError(f"pad {pad} is not 0..15")
        limit = None if bars is None else int(bars * beats_per_bar * 4)
        for s in steps:
            if s < 0 or (limit is not None and s >= limit):
                raise ControlError(f"step {s} is outside the loop (0..{limit - 1 if limit else '?'})")
        out.setdefault(pad, []).extend(steps)
    if not out:
        raise ControlError("no steps given")
    return out


def parse_chain(text):
    """`"p1:2 p2 p1:3"` -> [{pattern: "p1", repeats: 2}, {pattern: "p2", repeats: 1}, ...] (groups split on whitespace, `;` or `,`)."""
    out = []
    for group in str(text).replace(";", " ").replace(",", " ").split():
        pid, sep, reps = group.partition(":")
        try:
            n = int(reps) if sep else 1
        except ValueError:
            raise ControlError(f"bad chain entry {group!r}; expected PATTERN or PATTERN:REPEATS like p1:2") from None
        if not pid or not 1 <= n <= 64:
            raise ControlError(f"bad chain entry {group!r}; repeats are 1-64")
        out.append({"pattern": pid, "repeats": n})
    if not out:
        raise ControlError("the chain is empty; give entries like 'p1:2 p2'")
    return out


def pattern_events(steps_by_pad, bank, vel=1.0, pitch=None, cutoff=None):
    """Events for `pad:steps` groups. pitch (semitones) and cutoff (Hz) are optional per-hit offsets (W3b, as recorded in 16 levels)."""
    extra = {**({"pitch": pitch} if pitch is not None else {}), **({"cutoff": cutoff} if cutoff is not None else {})}
    return [{"beat": s / 4, "pad": pad, "bank": bank, "vel": vel, "pass": -1, **extra}
            for pad, steps in sorted(steps_by_pad.items()) for s in sorted(set(steps))]


def pad_params(gain=None, pitch=None, attack=None, release=None, reverse=None, mode=None,
               decay=None, sustain=None, filter_type=None, cutoff=None, q=None, mute_group=None):
    p = {}
    for k, v in (("gainDb", gain), ("pitch", pitch), ("attack", attack), ("decay", decay), ("sustain", sustain),
                 ("release", release), ("reverse", reverse)):
        if v is not None:
            p[k] = v
    if sustain is not None and not 0 <= sustain <= 1:
        raise ControlError("sustain must be between 0 and 1")
    if mute_group is not None:
        if not (isinstance(mute_group, int) and 0 <= mute_group <= 8):
            raise ControlError("mute group must be a whole number from 0 (none) to 8")
        p["muteGroup"] = mute_group
    if filter_type is not None and filter_type not in FILTER_TYPES:
        raise ControlError("filter type must be off, lp, hp or bp")
    f = {k: v for k, v in (("type", filter_type), ("cutoff", cutoff), ("q", q)) if v is not None}
    if f:  # only the given fields change: the server merges a partial filter onto the pad's own
        p["filter"] = f
    if mode is not None:
        if mode not in ("oneshot", "gate", "playthrough", "default"):
            raise ControlError("mode must be oneshot, gate, playthrough or default")
        p["mode"] = None if mode == "default" else mode
    return p


def fx_params(preset=None, **kw):
    p = {}
    if preset:
        if preset not in PRESETS:
            raise ControlError(f"unknown preset {preset!r}; one of {', '.join(PRESETS)}")
        p.update(PRESETS[preset])
    p.update({k: v for k, v in kw.items() if v is not None and k in FX_KEYS})
    if not p:
        raise ControlError("give --preset or at least one of --" + ", --".join(FX_KEYS))
    return p
