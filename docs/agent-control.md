---
title: Driving ChopPad from an agent
type: guide
date: 2026-11-22
tags: [choppad, agent, mcp, cli, daw]
---

# Driving ChopPad from an agent (CLI and MCP)

ChopPad has one HTTP server. Browsers, the `choppad` CLI verbs and the MCP server are all clients of it, so whatever an agent does shows live in every open tab (`contracts/README.md` §10), and what a tab does can be read back. Nothing here changes an agent's own config: registering the MCP server is a command you run, shown below, not something ChopPad does for you.

Paths below are relative to the repo (`~/03-Projects/media/choppad`). `choppad` is a launcher on `PATH` (`~/.local/bin/choppad`) for `.venv/bin/python components/integrate/choppad.py`; use that form if the launcher is missing.

## 1. Start the server

```
choppad --serve                       # http://127.0.0.1:8960 , library = media/
choppad --serve --port 8961           # another port
CHOPPAD_REPO=/tmp/copy choppad --serve --port 8961    # media/ taken from /tmp/copy/media (use this for experiments)
```

- It binds to `127.0.0.1` only. Stop it with Ctrl-C, or find it with `ss -ltnp | grep 8960` and kill that PID.
- Clients find it through `--server URL` or `CHOPPAD_SERVER` (default `http://127.0.0.1:8960`).
- `choppad station [ID] [--daw hydrogen|ardour]` starts a server if none answers, opens the song in the DAW through it, and keeps both running until Ctrl-C.
- Hits and play/stop need a browser tab with the song open (`http://127.0.0.1:8960/#/song/<id>`); edits, imports, exports and DAW commands do not.

## 2. CLI verbs

Every verb takes `--server`. `choppad <verb> --help` lists the flags. Pads are 0..15; banks are 0-based; times are seconds from the start of the song.

| Verb | What it does | Example |
|---|---|---|
| `songs` | the library | `choppad songs` |
| `show ID` | one song: title, bpm, meter, banks, sections, pattern size | `choppad show ID` |
| `import SRC` | import a URL or a local audio/video file; waits, prints each step on stderr, prints `{"id", "job", "steps"}` on stdout | `choppad import https://youtu.be/XXXX --wait 900` |
| `regrid ID` | chop grid, meter, tempo multiplier, bar-1 offset, or re-detect with `--bpm` | `choppad regrid ID --grid 1/8 --meter 3/4` |
| `assign ID` | custom slice on a pad (`--clear` returns it to the grid chop) | `choppad assign ID --bank 0 --pad 3 --start 41.2 --end 41.7` |
| `pad ID P` | gain (dB), pitch (semitones), attack, release, reverse, mode | `choppad pad ID 3 --gain -4 --pitch 2` |
| `pattern ID set\|add\|clear\|show` | the step pattern; steps are `pad:step,step` with 1/16 steps from the loop start | `choppad pattern ID set --steps "0:0,8 1:4,12" --bars 2 --bank 0` |
| `fx ID` | master effects: a preset and/or single knobs | `choppad fx ID --preset warm --reverb 0.2` |
| `play ID pattern\|song\|stop` | playback in the open browser | `choppad play ID pattern` |
| `hit ID P` | one pad in the open browser | `choppad hit ID 3 --velocity 0.8` |
| `export ID hydrogen\|sfz` | a bank as a Hydrogen drumkit or an SFZ file | `choppad export ID hydrogen --bank 0` |
| `daw open\|sync\|play\|stop ID` | DAW bridge (section 5) | `choppad daw open ID --daw hydrogen --swing 0.2` |
| `station [ID]` | server + DAW in one command | `choppad station ID --daw hydrogen` |

Exit code 0 means done; 1 means the server said no or could not be reached (the message says why); 2 means bad arguments.

## 3. Register the MCP server

`components/control/choppad_mcp.py` is a stdio MCP server written against the standard library only (JSON-RPC 2.0, newline-delimited; protocol revisions 2024-11-05, 2025-03-26, 2025-06-18). It talks to a running ChopPad server, so start that first (section 1). It needs no virtualenv; any `python3` 3.12 or newer works.

**Claude Code** (run this yourself; it writes Claude Code's config):

```
claude mcp add choppad -- python3 ~/03-Projects/media/choppad/components/control/choppad_mcp.py
claude mcp add choppad -e CHOPPAD_SERVER=http://127.0.0.1:8961 -- python3 ~/03-Projects/media/choppad/components/control/choppad_mcp.py   # another server
claude mcp list        # choppad should show as connected
```

Add `-s user` to make it available in every project (default scope is the current project only).

**Any other stdio MCP client**: launch this command and speak MCP over its stdin/stdout:

```json
{ "mcpServers": { "choppad": {
    "command": "python3",
    "args": ["/home/<you>/03-Projects/media/choppad/components/control/choppad_mcp.py"],
    "env": { "CHOPPAD_SERVER": "http://127.0.0.1:8960" } } } }
```

Smoke test without a client (prints the tool list):

```
printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18"}}' \
               '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' | python3 components/control/choppad_mcp.py
```

If the client sends a `progressToken` with `tools/call`, `import_song` answers with `notifications/progress` while it waits (one per step, `progress` 0..1), then returns `{id, job, steps}`.

## 4. Tools

| Tool | What it does |
|---|---|
| `library` | list the songs (id, title, bpm, meter, banks, duration) |
| `song_summary` | one song in a few lines: bpm, meter, key if known, banks, sections, pattern size, current bank |
| `chop_table` | the 16 pads of one bank: bar.beat label, start/end seconds, custom or grid slice |
| `pattern_text` | the step pattern as a 16-row text grid |
| `import_song` | import a URL or file as a new song; waits until it is chopped; returns the new song id |
| `regrid` | change grid, meter, tempo multiplier, bar-1 offset, or re-detect tempo |
| `assign` | custom slice on a pad, or clear it back to the grid chop |
| `pad` | gain, pitch, attack, release, reverse, mode of one pad |
| `pattern_set` | write the step pattern (`steps`, `bars`, `bank`, `vel`, `add`) |
| `pattern_clear` | empty the pattern |
| `fx` | master effects: a preset and/or tone, low, mid, high, filter, reverb, size |
| `play` | start or stop pattern or song playback in the open browser |
| `hit` | play one pad once in the open browser |
| `daw_open` | open the song in Hydrogen or Ardour (kit + pattern + tempo); also opens the MIDI port `ChopPad` |
| `daw_sync` | push the current kit, pattern and tempo to the open DAW again after edits |
| `daw_transport` | play or stop the DAW (and MIDI start/clock/stop); Ardour reports "not implemented" for its own transport |
| `export` | write one bank as a Hydrogen drumkit or an SFZ file; returns the path |

Results are small on purpose (no peaks, no full chop list). Failures come back as tool results with `isError: true` and a message that says what to change.

## 5. The DAW bridge in one screen

- `daw open` renders the bank's pads to WAVs with the same code the Export button uses (custom slices, slices of other songs, stretch renderings, master FX baked in), writes a Hydrogen kit and a `.h2song`, and starts the bridge's own Hydrogen with a private config (`CHOPPAD_DAW_HEADLESS=1` for no window). Your own Hydrogen and its `~/.hydrogen` are never touched.
- Per-pad gain and pitch go into the kit instruments (`gain`, `pitchOffset`). Pads cut each other off (one mute group) unless you pass `--no-choke`. Swing, which the browser does not save, is baked into the pattern when you pass `--swing 0.2 [--quant 1/16]` (or MCP `swing`, `quant`, `choke`).
- While a bridge is open, hits and pattern play/stop pressed in a browser tab reach it, and so do agent `hit` and `play` commands. `GET /api/songs/<id>` has `daw: {open, daw, bank}`, and tabs get a `daw` event when it changes.
- Ardour: the session is written and renders correctly headless, but its transport cannot be driven without enabling a control surface by hand (see `docs/daw-bridge-spikes.md`). It runs with a private config too, so its first-run wizard shows once.

## 6. Example session

"Import a song, find the chorus, put 16 chops on bank A, write a 2-bar pattern, open it in Hydrogen." This is a walk-through of the calls and their argument shapes, not a recorded run; ids, times and bar numbers are illustrative.

1. `import_song {source: "https://youtu.be/XXXX"}` returns `{"id":"XXXX","job":"…","steps":["download + transcode","tempo + beats","chopping","waveform","done"]}`. CLI: `choppad import https://youtu.be/XXXX`.
2. `song_summary {id: "XXXX"}` returns bpm, meter, bank count and `sections`, for example `[{"label":"verse","bars":"0-8"},{"label":"chorus","bars":"8-16"},…]`. The chorus is bars 8 to 16; at 96 bpm in 4/4 a bar lasts 2.5 s, so it spans 20.0 s to 40.0 s from the start of the grid.
3. At the default 1/4 grid one bank holds 16 beats, which is 4 bars, so the chorus (bars 8 to 16) is banks 2 and 3 (0-based). `chop_table {id: "XXXX", bank: 2}` lists that bank's 16 slices with their start and end seconds. (`regrid` first if a different grid or meter suits the song better.)
4. Put those 16 slices on bank A (bank 0): one `assign {id: "XXXX", bank: 0, pad: n, start: <s>, end: <e>}` per pad, with the `start`/`end` seconds from the table rows. CLI: `choppad assign XXXX --bank 0 --pad 0 --start 20.0 --end 20.625`, and so on for pads 0 to 15.
5. Write the pattern: `pattern_set {id: "XXXX", steps: "0:0,8 3:4,12 5:2,6,10,14", bars: 2, bank: 0}` (kick on 1 and 3, snare on 2 and 4, hats on the off-beats, as 1/16 steps from the loop start), then `pattern_text {id: "XXXX", bank: 0}` to read it back, and `play {id: "XXXX", what: "pattern"}` if a tab is open.
6. `daw_open {id: "XXXX", daw: "hydrogen", bank: 0, swing: 0.15}` starts Hydrogen with the kit and the pattern at the song's tempo. `daw_transport {id: "XXXX", daw: "hydrogen", command: "play"}` plays it. After more edits, `daw_sync` pushes them without restarting Hydrogen.

Check what an edit did with `song_summary` (counts) and `pattern_text`; the replies of edit tools also say how many browsers were watching (`listeners`), so an agent knows when `hit` and `play` have nobody to play to.

## 7. For people wiring a UI

- `GET /api/events` is the library-level stream: `job` events `{job, state: queued|running|done|error, step, progress 0..1, song?, error?}` for every import. A new stream is first told the recent jobs, so a job that moved on is not missed. `POST /api/songs {url}` and `POST /api/upload` return `{job}`; match on it.
- `GET /api/songs/<id>/events?origin=<tab id>` is the song stream: `actions`, `chops`, `transport`, `daw`.
- `POST /api/songs/<id>/transport` with `local: true` reports what a tab played itself; it is handed to an open DAW bridge only, never rebroadcast.
- Test hook: `CHOPPAD_DAW_FAKE=<log file>` swaps the DAW bridges for ones that only append to that log (used by `tests/e2e/test_bridge.py`); `CHOPPAD_DAW_DIR` moves the bridge's state folder.
