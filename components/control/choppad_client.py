"""choppad_client: thin HTTP client for a running ChopPad server, plus the text views an agent reads.

Shared by the CLI verbs (components/integrate/choppad.py) and the MCP server (choppad_mcp.py). Stdlib only.
Everything here talks to the endpoints in contracts §10 and the existing /api routes; nothing touches media/ directly.
"""
import http.client
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
PAD_KEYS = ("gainDb", "pitch", "attack", "release", "reverse", "mode")


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
        """URL -> import job; an existing file path -> upload. Waits for the job and returns {id, job, steps}: the new
        song id, the job id and the names of the steps it went through. `progress(event)` is called on every change with
        {job, state, step, progress, song?, error?}, the same shape as the server's `job` events (GET /api/events)."""
        if os.path.isfile(source):
            size = os.path.getsize(source)
            with open(source, "rb") as f:
                job = self._call("POST", "/api/upload", raw=f, timeout=600, headers={
                    "X-Filename": urllib.parse.quote(os.path.basename(source)), "Content-Length": str(size)})["job"]
        else:
            job = self.post("/api/songs", {"url": source})["job"]
        return self.wait_job(job, wait_s, progress)

    def wait_job(self, job, wait_s=900, progress=None):
        """Follow import job `job` on the server's event stream (`GET /api/events`); falls back to polling `/api/jobs/<job>`
        when the stream cannot be used. Returns {id, job, steps}; raises ControlError when the job fails or times out."""
        end = time.time() + wait_s
        steps, seen = [], [None]

        def see(ev):
            """One job event: report changes once, finish on done or error."""
            key = (ev.get("state"), ev.get("step"), round(float(ev.get("progress") or 0), 2))
            if key != seen[0]:
                seen[0] = key
                if ev.get("step") and (not steps or steps[-1] != ev["step"]):
                    steps.append(ev["step"])
                if progress:
                    progress(ev)
            if ev.get("state") == "done":
                return {"id": ev["song"], "job": job, "steps": steps}
            if ev.get("state") == "error":
                raise ControlError(f"import failed: {ev.get('error')}")
            return None

        try:
            done = self._follow_stream(job, end, see)
            if done:
                return done
        except (OSError, http.client.HTTPException):
            pass  # no stream (older server, proxy, dropped): poll below, nothing is lost, the job lives on the server
        while time.time() < end:
            j = self.get(f"/api/jobs/{job}")
            done = see({"job": job, "state": j.get("state"), "step": j.get("stage"), "progress": j.get("progress", 0.0),
                        "song": j.get("id"), "error": j.get("error")})
            if done:
                return done
            time.sleep(1)
        raise ControlError(f"import still running after {wait_s}s (job {job}); check `choppad songs` later")

    def _follow_stream(self, job, end, see):
        """Read `job` events from /api/events until `see` returns a result or time is up. None = ran out of time."""
        u = urllib.parse.urlparse(self.server)
        conn = http.client.HTTPConnection(u.hostname, u.port or 80, timeout=35)  # the server pings every 10 s
        try:
            conn.request("GET", "/api/events", headers={"Accept": "text/event-stream", "X-ChopPad-Origin": self.origin})
            r = conn.getresponse()
            if r.status != 200:
                raise OSError(f"events stream answered HTTP {r.status}")
            name, data = None, []
            while time.time() < end:
                line = r.readline()
                if not line:
                    raise OSError("events stream closed")
                line = line.decode().rstrip("\r\n")
                if line.startswith("event:"):
                    name = line[6:].strip()
                elif line.startswith("data:"):
                    data.append(line[5:].strip())
                elif line == "":
                    if name == "job" and data:
                        try:
                            ev = json.loads("".join(data))
                        except ValueError:
                            ev = {}
                        if ev.get("job") == job:
                            done = see(ev)
                            if done:
                                return done
                    name, data = None, []
            return None
        finally:
            conn.close()

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
            raise ControlError("nothing to change; give --gain/--pitch/--attack/--release/--reverse/--mode or --reset")
        return self.actions(sid, acts)

    def pattern_set(self, sid, steps=None, bars=None, bank=None, vel=1.0, add=False):
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
        events = pattern_events(parse_steps(steps, use_bars, bpb), bank, vel)
        if add:
            have = {(e["pad"], round(e["beat"] * 4)) for e in cur.get("events", [])}
            events = cur.get("events", []) + [e for e in events if (e["pad"], round(e["beat"] * 4)) not in have]
            if bars is None:
                use_bars = cur.get("bars", 2)
        return self.actions(sid, [{"type": "pattern.load", "pattern": {"bars": use_bars, "events": events}}])

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


def pattern_events(steps_by_pad, bank, vel=1.0):
    return [{"beat": s / 4, "pad": pad, "bank": bank, "vel": vel, "pass": -1}
            for pad, steps in sorted(steps_by_pad.items()) for s in sorted(set(steps))]


def pad_params(gain=None, pitch=None, attack=None, release=None, reverse=None, mode=None):
    p = {}
    for k, v in (("gainDb", gain), ("pitch", pitch), ("attack", attack), ("release", release), ("reverse", reverse)):
        if v is not None:
            p[k] = v
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
