#!/usr/bin/env python3
"""ChopPad MCP server (stdio). Tools drive a running ChopPad server; open browsers show every change live.

    claude mcp add choppad -- python3 /path/to/components/control/choppad_mcp.py
    CHOPPAD_SERVER=http://127.0.0.1:8960 python3 components/control/choppad_mcp.py     # default server

Hand-written JSON-RPC 2.0 over newline-delimited stdio (MCP 2024-11 .. 2025-06 revisions): `initialize`, `ping`,
`tools/list`, `tools/call`. No dependency beyond the standard library. Results are small on purpose: no peaks, no full
chop list (ask for one bank with `chop_table`).
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import choppad_client as cc  # noqa: E402

SERVER_INFO = {"name": "choppad", "version": "0.1.0"}
LATEST = "2025-06-18"
SUPPORTED = ("2025-06-18", "2025-03-26", "2024-11-05")

_S = lambda desc, **kw: {"type": "string", "description": desc, **kw}  # noqa: E731
_N = lambda desc, **kw: {"type": "number", "description": desc, **kw}  # noqa: E731
_I = lambda desc, **kw: {"type": "integer", "description": desc, **kw}  # noqa: E731
_ID = _S("song id from `library`")
_PAD = _I("pad 0..15 (the 4x4 grid, row-major from the bottom-left)", minimum=0, maximum=15)
_BANK = _I("bank number (0-based)", minimum=0)


def _tool(name, description, props, required, fn):
    return {"name": name, "description": description,
            "inputSchema": {"type": "object", "properties": props, "required": required}, "_fn": fn}


def _watch(r):
    """Compact answer of an edit; says when nobody is watching."""
    out = {k: r[k] for k in ("applied", "ignored", "listeners") if k in r}
    if r.get("listeners") == 0:
        out["note"] = "no browser has this song open; saved, and it will show when one opens it"
    return out


def build_tools(client):
    c = client

    def regrid(a):
        if a.get("bpm") is not None:
            chops = c.reanalyze(a["id"], a["bpm"], a.get("meter"))
        else:
            ch = {k: a[k] for k in ("grid", "meter", "mult", "bpm_mult", "shift", "beat_offset") if a.get(k) is not None}
            if not ch:
                raise cc.ControlError("nothing to change; give grid, meter, mult, bpm_mult, shift, beat_offset or bpm")
            chops = c.regrid_to(a["id"], **ch)
        return {"grid": chops["grid"], "bpm": round(chops["bpm"], 2), "meter": f"{chops['beats_per_bar']}/4",
                "chops": len(chops["chops"]), "banks": len(chops["banks"])}

    def hit_text(r):
        return {"listeners": r.get("listeners"), **({"note": "no browser has this song open, so nothing is playing"}
                                                    if r.get("listeners") == 0 else {})}

    T = [
        _tool("library", "List the songs in the library (id, title, bpm, meter, banks, duration).", {}, [],
              lambda a: [{"id": s["id"], "title": s["title"], "bpm": round(s["bpm"], 1), "meter": s["meter"],
                          "banks": s["banks"], "duration_s": round(s["duration_s"] or 0)} for s in c.songs()]),
        _tool("song_summary", "One song in a few lines: title, bpm, meter, key if known, duration, bank count, sections, "
              "pattern size, current bank.", {"id": _ID}, ["id"], lambda a: c.summary(a["id"])),
        _tool("chop_table", "The 16 pads of one bank: bar.beat label, start/end seconds, and whether the slice is custom. "
              "Default bank = the one on screen.", {"id": _ID, "bank": _BANK}, ["id"],
              lambda a: c.chop_rows(a["id"], a.get("bank"))),
        _tool("pattern_text", "The step pattern as a 16-row text grid (pads as rows, 1/16 steps as columns).",
              {"id": _ID, "bank": _BANK}, ["id"], lambda a: c.pattern_view(a["id"], a.get("bank"))),
        _tool("import_song", "Import a YouTube/URL or a local audio/video file path as a new song; waits until it is chopped.",
              {"source": _S("URL or absolute file path")}, ["source"], lambda a: c.import_source(a["source"], wait_s=600)),
        _tool("regrid", "Change the chop grid. Give any of: grid (1/4, 1/8, 1/16), meter (e.g. 3/4), mult (0.5 or 2, relative "
              "tempo), bpm_mult (absolute), shift (beats to move bar 1), beat_offset, or bpm (re-detect with this tempo).",
              {"id": _ID, "grid": _S("1/4, 1/8 or 1/16", enum=["1/4", "1/8", "1/16"]), "meter": _S("e.g. 3/4"),
               "mult": _N("0.5 or 2", enum=[0.5, 2]), "bpm_mult": _N("0.5, 1 or 2", enum=[0.5, 1, 2]), "shift": _I("beats"),
               "beat_offset": _I("beats"), "bpm": _N("force this tempo")}, ["id"], regrid),
        _tool("assign", "Give a pad a custom slice (seconds from the start of the song) or clear it back to the grid chop.",
              {"id": _ID, "bank": _BANK, "pad": _PAD, "start": _N("seconds"), "end": _N("seconds, > start"),
               "clear": {"type": "boolean"}}, ["id", "bank", "pad"],
              lambda a: _watch(c.assign(a["id"], a["bank"], a["pad"], a.get("start"), a.get("end"), bool(a.get("clear"))))),
        _tool("pad", "Sound settings of one pad: gain (dB), pitch (semitones), ADSR envelope (attack, decay, release in s, "
              "sustain 0-1), a filter (filter_type off/lp/hp/bp, cutoff Hz, q resonance), reverse, mode (oneshot, gate, "
              "playthrough, default). Only the given fields change.",
              {"id": _ID, "pad": _PAD, "gain": _N("dB"), "pitch": _N("semitones"), "attack": _N("s"), "release": _N("s"),
               "decay": _N("s, 0-10", minimum=0, maximum=10), "sustain": _N("0-1, 1 = hold the level", minimum=0, maximum=1),
               "filter_type": _S("off, lp, hp or bp", enum=["off", "lp", "hp", "bp"]),
               "cutoff": _N("filter cutoff, Hz (20-20000)", minimum=20, maximum=20000),
               "q": _N("filter resonance (0.1-24; 0.7 is flat)", minimum=0.1, maximum=24),
               "mute_group": _I("0 = none; pads in the same group 1-8 cut each other's voices", minimum=0, maximum=8),
               "reverse": {"type": "boolean"}, "mode": _S("oneshot, gate, playthrough or default"),
               "reset": {"type": "boolean", "description": "defaults first"}}, ["id", "pad"],
              lambda a: _watch(c.pad(a["id"], a["pad"], bool(a.get("reset")), gain=a.get("gain"), pitch=a.get("pitch"),
                                     attack=a.get("attack"), release=a.get("release"), reverse=a.get("reverse"),
                                     mode=a.get("mode"), decay=a.get("decay"), sustain=a.get("sustain"),
                                     filter_type=a.get("filter_type"), cutoff=a.get("cutoff"), q=a.get("q"),
                                     mute_group=a.get("mute_group")))),
        _tool("pattern_set", "Set the step pattern. `steps` is 'pad:step,step pad:step' with 1/16 steps from the loop start "
              "(0-based), e.g. '0:0,8 1:4,12' = kick on 1 and 3, snare on 2 and 4 in a bar of 4/4. Replaces the pattern "
              "(add=true merges). With only `bars` it just changes the loop length.",
              {"id": _ID, "steps": _S("e.g. '0:0,8 1:4,12'"), "bars": _N("loop length in bars"), "bank": _BANK,
               "vel": _N("0..1", minimum=0, maximum=1), "add": {"type": "boolean"}}, ["id"],
              lambda a: _watch(c.pattern_set(a["id"], a.get("steps"), a.get("bars"), a.get("bank"), a.get("vel", 1.0),
                                             add=bool(a.get("add"))))),
        _tool("pattern_clear", "Empty the pattern.", {"id": _ID}, ["id"], lambda a: _watch(c.pattern_clear(a["id"]))),
        _tool("fx", "Master effects. A preset (dry, warm, room, hall, lofi, radio) and/or tone, low, mid, high, filter, "
              "reverb, size. Only the given fields change.",
              {"id": _ID, "preset": _S("preset name", enum=list(cc.PRESETS)), **{k: _N(k) for k in cc.FX_KEYS}}, ["id"],
              lambda a: _watch(c.fx(a["id"], a.get("preset"), **{k: a.get(k) for k in cc.FX_KEYS}))),
        _tool("tempo", "Project tempo: the sequencer, metronome, MIDI export and renders play at it instead of the song's own "
              "tempo. Give bpm to set it, song=true to go back to the song's tempo, follow=false to stop fitted pads "
              "re-fitting on tempo changes, fit='pattern' or 'bank' to stretch those pads to it now (rubberband, pitch kept; "
              "pads stretched by hand are never touched). With nothing given it just reports.",
              {"id": _ID, "bpm": _N("20-400", minimum=20, maximum=400), "song": {"type": "boolean"},
               "follow": {"type": "boolean"}, "fit": _S("pattern or bank", enum=["pattern", "bank"])}, ["id"],
              lambda a: c.tempo(a["id"], a.get("bpm"), bool(a.get("song")), a.get("follow"), a.get("fit"))),
        _tool("play", "Start or stop playback in the open browser: pattern, song or stop.",
              {"id": _ID, "what": _S("pattern, song or stop", enum=["pattern", "song", "stop"]), "from": _N("song start, seconds")},
              ["id", "what"], lambda a: hit_text(c.play(a["id"], a["what"], a.get("from")))),
        _tool("hit", "Play one pad once in the open browser.",
              {"id": _ID, "pad": _PAD, "velocity": _N("0..1", minimum=0, maximum=1), "bank": _BANK}, ["id", "pad"],
              lambda a: hit_text(c.hit(a["id"], a["pad"], a.get("velocity", 1.0), a.get("bank")))),
        _tool("daw_open", "Open a song in a DAW wired to ChopPad: hydrogen (kit + pattern + tempo, its own instance, no window "
              "focus stolen if headless) or ardour (a session with the chops as audio regions). Also opens the virtual MIDI "
              "port 'ChopPad'. Returns what was started.",
              {"id": _ID, "daw": _S("hydrogen or ardour", enum=["hydrogen", "ardour"]), "bank": _BANK}, ["id", "daw"],
              lambda a: c.daw(a["id"], a["daw"], "open", a.get("bank"))),
        _tool("daw_sync", "Push the song's current kit, pattern and tempo to the open DAW again after edits.",
              {"id": _ID, "daw": _S("hydrogen or ardour", enum=["hydrogen", "ardour"]), "bank": _BANK}, ["id", "daw"],
              lambda a: c.daw(a["id"], a["daw"], "sync", a.get("bank"))),
        _tool("daw_transport", "Start or stop the DAW transport (and MIDI start/clock/stop on the ChopPad MIDI port). "
              "Ardour reports 'not implemented' for its own transport until its control surface is enabled by hand.",
              {"id": _ID, "daw": _S("hydrogen or ardour", enum=["hydrogen", "ardour"]),
               "command": _S("play or stop", enum=["play", "stop"])}, ["id", "daw", "command"],
              lambda a: c.daw(a["id"], a["daw"], a["command"])),
        _tool("export", "Export one bank as a Hydrogen drum kit or an SFZ file; returns where it was written.",
              {"id": _ID, "kind": _S("hydrogen or sfz", enum=["hydrogen", "sfz"]), "bank": _BANK}, ["id", "kind"],
              lambda a: c.export(a["id"], a["kind"], a.get("bank", 0))),
    ]
    return {t["name"]: t for t in T}


class McpServer:
    def __init__(self, client=None):
        self.tools = build_tools(client or cc.Client())

    def tools_list(self):
        return [{k: v for k, v in t.items() if k != "_fn"} for t in self.tools.values()]

    def call(self, name, args):
        """Run a tool; returns an MCP tool result. Failures are results with isError, not protocol errors."""
        t = self.tools.get(name)
        if not t:
            return {"content": [{"type": "text", "text": f"unknown tool {name!r}"}], "isError": True}
        args = args or {}
        missing = [k for k in t["inputSchema"]["required"] if args.get(k) is None]
        if missing:
            return {"content": [{"type": "text", "text": f"missing argument(s): {', '.join(missing)}"}], "isError": True}
        try:
            out = t["_fn"](args)
        except cc.ControlError as e:
            return {"content": [{"type": "text", "text": str(e)}], "isError": True}
        except Exception as e:  # noqa: BLE001 - the agent should see it, not a dead server
            return {"content": [{"type": "text", "text": f"{type(e).__name__}: {e}"}], "isError": True}
        text = out if isinstance(out, str) else json.dumps(out, separators=(",", ":"))
        return {"content": [{"type": "text", "text": text}], "isError": False}

    def handle(self, msg):
        """One JSON-RPC message in, the response out (None for notifications)."""
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or "method" not in msg:
            return {"jsonrpc": "2.0", "id": msg.get("id") if isinstance(msg, dict) else None,
                    "error": {"code": -32600, "message": "invalid request"}}
        mid, method, params = msg.get("id"), msg["method"], msg.get("params") or {}
        if "id" not in msg:  # notification (notifications/initialized, cancelled, ...)
            return None
        ok = lambda result: {"jsonrpc": "2.0", "id": mid, "result": result}  # noqa: E731
        if method == "initialize":
            want = params.get("protocolVersion")
            return ok({"protocolVersion": want if want in SUPPORTED else LATEST, "capabilities": {"tools": {"listChanged": False}},
                       "serverInfo": SERVER_INFO,
                       "instructions": "Drive ChopPad (browser MPC sampler). Start with `library`, then `song_summary`. "
                                       "Edits show live in any open browser tab; `hit`/`play` need a tab open."})
        if method == "ping":
            return ok({})
        if method == "tools/list":
            return ok({"tools": self.tools_list()})
        if method == "tools/call":
            return ok(self.call(params.get("name"), params.get("arguments")))
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"method not found: {method}"}}


def serve(stdin=sys.stdin, stdout=sys.stdout, client=None):
    server = McpServer(client)
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            resp = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
        else:
            resp = [r for r in (server.handle(m) for m in msg) if r] if isinstance(msg, list) else server.handle(msg)
        if resp:
            stdout.write(json.dumps(resp, separators=(",", ":")) + "\n")
            stdout.flush()


if __name__ == "__main__":
    serve()
