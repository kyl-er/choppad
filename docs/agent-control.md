---
title: Driving ChopPad from an agent
type: guide
date: 2026-11-22
tags: [choppad, agent, mcp, cli, daw]
---

# Driving ChopPad from an agent (CLI and MCP)

Since CHOP-13 (contracts §15) the thing an agent edits is a DRUM PAD MEGA BANK: SONG -> MEGA BANK -> pad banks. A song feeds a mega bank (its chops show on the pads that have no sound of their own); the mega bank owns the pads, their sounds, the patterns, the fx and the tempo, and keeps the pads you used when the song changes. Every verb below takes a song id (its most recently updated mega bank, as before) or `--mega MID` (exactly that mega bank, whatever song feeds it, or none). Section 6 is the walk-through.

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
| `show ID` | one song (or `--mega MID`): title, bpm, meter, banks, sections, pattern size, the mega bank's name and which pads are kept and from which song | `choppad show --mega MID` |
| `import SRC` | import a URL or a local audio/video file; waits, prints each step on stderr, prints `{"id", "job", "steps"}` on stdout | `choppad import https://youtu.be/XXXX --wait 900` |
| `regrid ID` | chop grid, meter, tempo multiplier, bar-1 offset, or re-detect with `--bpm` | `choppad regrid ID --grid 1/8 --meter 3/4` |
| `assign ID` | custom slice on a pad (`--clear` returns it to the grid chop) | `choppad assign ID --bank 0 --pad 3 --start 41.2 --end 41.7` |
| `pad ID P` | gain (dB), pitch (semitones), attack, release, reverse, mode | `choppad pad ID 3 --gain -4 --pitch 2` |
| `pattern ID set\|add\|clear\|show` | the step pattern; steps are `pad:step,step` with 1/16 steps from the loop start | `choppad pattern ID set --steps "0:0,8 1:4,12" --bars 2 --bank 0` |
| `fx ID` | master effects: a preset and/or single knobs | `choppad fx ID --preset warm --reverb 0.2` |
| `play ID pattern\|song\|stop` | playback in the open browser | `choppad play ID pattern` |
| `hit ID P` | one pad in the open browser | `choppad hit ID 3 --velocity 0.8` |
| `export ID hydrogen\|sfz\|zip` | a bank as a Hydrogen drumkit, an SFZ file or a zip of 16 WAVs (works with kept pads and with no source song) | `choppad export --mega MID zip --bank 0` |
| `daw open\|sync\|play\|stop\|close ID` | DAW bridge (section 5); `close` shuts the bridge this server started | `choppad daw open --mega MID --daw hydrogen --swing 0.2` |
| `station [ID]` | server + DAW in one command (`--mega MID` for a mega bank) | `choppad station --mega MID --daw hydrogen` |
| `mega list` | the mega banks: id, name, source song, used pads, banks | `choppad mega list` |
| `mega show MID` | name, source, tempo, pattern, kept pads and where they came from | `choppad mega show MID` |
| `mega new` | a new empty mega bank, optionally fed by a song | `choppad mega new --source SONG --name "My break"` |
| `mega rename MID NAME` / `mega remove MID` | rename; remove (moved to `media/_mega/.trash/`, never deleted; open tabs are told) | `choppad mega remove MID` |
| `mega source MID SONG\|none` | change the song that feeds it: the pads it uses are kept as their own sounds, the rest show the new song; `none` leaves it with no source | `choppad mega source MID SONG` |
| `mega keep MID --bank B --pad P` / `mega release …` | keep one pad now / give it back to the song's chop | `choppad mega keep MID --bank 0 --pad 3` |
| `mega sweep MID` | move samples no pad uses to the mega bank's `.trash` (also done after a release) | `choppad mega sweep MID` |

`--mega MID` goes anywhere on the line and replaces the song id: `choppad pad 3 --mega MID --gain -3`, `choppad pattern --mega MID set --steps "0:0,8"`. `regrid`, `reanalyze`, sections, key and stems belong to the SOURCE song: on a mega bank with no source they answer "... Pick a song first." (HTTP 409).

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
| `song_summary` | one song (or `mega`) in a few lines: bpm, meter, key if known, banks, sections, pattern size, current bank, the mega bank's name, and a compact `kept` list: which pads are kept and which song they came from |
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
| `daw_close` | shut the DAW bridge this server started for the song or mega bank |
| `export` | write one bank as a Hydrogen drumkit, an SFZ file or a zip of 16 WAVs; returns the path |
| `mega_list`, `mega_show`, `mega_new`, `mega_rename`, `mega_remove` | the mega banks (section 6) |
| `mega_source` | set the song that feeds a mega bank (`song: "none"` = no source); used pads are kept |
| `mega_keep`, `mega_release`, `mega_sweep` | keep one pad now, give it back to the song, tidy the samples |

Every tool above that takes `id` (a song) also takes `mega` (a mega bank id) instead: it then acts on exactly that mega bank. With only `id` it keeps its meaning, the song's most recently updated mega bank.

Results are small on purpose (no peaks, no full chop list). Failures come back as tool results with `isError: true` and a message that says what to change.

## 5. The DAW bridge in one screen

- `daw open` renders the bank's pads to WAVs with the same code the Export button uses (custom slices, slices of other songs, stretch renderings, master FX baked in), writes a Hydrogen kit and a `.h2song`, and starts the bridge's own Hydrogen with a private config (`CHOPPAD_DAW_HEADLESS=1` for no window). Your own Hydrogen and its `~/.hydrogen` are never touched.
- Per-pad gain and pitch go into the kit instruments (`gain`, `pitchOffset`). Pads cut each other off (one mute group) unless you pass `--no-choke`. Swing, which the browser does not save, is baked into the pattern when you pass `--swing 0.2 [--quant 1/16]` (or MCP `swing`, `quant`, `choke`).
- While a bridge is open, hits and pattern play/stop pressed in a browser tab reach it, and so do agent `hit` and `play` commands. `GET /api/songs/<id>` has `daw: {open, daw, bank}`, and tabs get a `daw` event when it changes.
- Ardour: the session is written and renders correctly headless, but its transport cannot be driven without enabling a control surface by hand (see `docs/daw-bridge-spikes.md`). It runs with a private config too, so its first-run wizard shows once.

## 6. Example session: a drum break, then a new song, a bass chop, Hydrogen

"Import a drum break, lay a beat on the pads, bring in a new song and keep the drums, add a bass chop from it, open it all in Hydrogen." A walk-through of the calls and their argument shapes (the same calls are run against a real server by `components/integrate/tests/test_mega_d_agents.py`); ids, times and bar numbers are illustrative.

1. `import_song {source: "https://youtu.be/BREAK"}` -> `{"id":"BREAK",...}`; `import_song {source: "https://youtu.be/LOOP"}` -> `{"id":"LOOP",...}` (the song with the bass and the loop). CLI: `choppad import URL`.
2. Start the mega bank on the break: `mega_new {source: "BREAK", name: "My break"}` -> `{"id":"m1a2b3c4d5e",...}`. CLI: `choppad mega new --source BREAK --name "My break"`. Open tabs list it at once (library event `mega created`). Use that id as `mega` below.
3. Lay the beat: `pattern_set {mega: MID, steps: "0:0,8 1:4,12 2:2,6,10,14", bars: 1, bank: 0}` (kick on 1 and 3, snare on 2 and 4, hats on the off-beats). The first hit gives the pattern its own bar length (4 beats); it keeps it whatever song comes next. `tempo {mega: MID, bpm: 96}` sets YOUR tempo: nothing changes it but you. `pad {mega: MID, pad: 1, gain: -3}` shapes a pad. `pattern_text {mega: MID}` reads it back; `play {mega: MID, what: "pattern"}` plays it if a tab is open.
4. Bring in the new song and keep the drums: `mega_source {mega: MID, song: "LOOP"}` -> `{"kept": 3, "released": 37, ...}`. The pads the pattern uses keep the break's audio and their own sound (they are copied into the mega bank, so they survive the song and any later change of it); every other pad now shows LOOP's chops. Open tabs follow without reloading and the pattern keeps playing. CLI: `choppad mega source MID LOOP`.
5. Add a bass chop: `chop_table {mega: MID, bank: 0}` lists the pads with LOOP's slices; `assign {mega: MID, bank: 0, pad: 5, start: 12.3, end: 12.8}` puts a custom slice of LOOP on pad 6, `pattern_set {mega: MID, steps: "5:0,6,12", add: true}` adds it to the beat. `song_summary {mega: MID}` now says which pads are kept and from where, for example `"kept":[{"from":"Drum break","pads":"0:0-2"}]`, and names the mega bank.
6. Open it in Hydrogen: `daw_open {mega: MID, daw: "hydrogen", bank: 0, swing: 0.15}`. The kit is built from the mega bank (kept pads from their own files at their own gain and sound, the free pads from LOOP), at YOUR tempo, with the pattern's own bar length. `daw_transport {mega: MID, daw: "hydrogen", command: "play"}` plays it; `daw_sync` pushes later edits; `daw_close {mega: MID}` shuts the Hydrogen this server started. `export {mega: MID, kind: "zip", bank: 0}` gives the 16 WAVs. A bank can also be saved as a kit in the browser (or `POST /api/mega/MID/savekit`): kits hold kept pads too, their audio is copied into the kit.
7. Leave the song behind entirely with `mega_source {mega: MID, song: "none"}`: the used pads stay kept, the rest go empty, and everything above (export, DAW, kit) still works. `mega_release {mega: MID, bank: 0, pad: 1}` hands one kept pad back to the song's chop; `mega_sweep` tidies samples nothing uses (moved to `.trash`, never deleted).

Check what an edit did with `song_summary` (counts) and `pattern_text`; the replies of edit tools also say how many browsers were watching (`listeners`), so an agent knows when `hit` and `play` have nobody to play to.

## 7. For people wiring a UI

- `GET /api/events` is the library-level stream: `mega` events `{change: created|renamed|removed|source, id, name?, source?}` when a mega bank is made, renamed, removed or gets another source (refresh the list and the header name), and `job` events `{job, state: queued|running|done|error, step, progress 0..1, song?, error?}` for every import. A new stream is first told the recent jobs, so a job that moved on is not missed. `POST /api/songs {url}` and `POST /api/upload` return `{job}`; match on it.
- `GET /api/mega/<mid>/events?origin=<tab id>` is the mega bank's stream: `actions`, `chops`, `transport`, `daw`, `role`, and `mega {change: "removed", id}` when it is deleted while a tab has it open: the tab should stop writing and leave (the browser session says so with the session event `mega-removed`). `GET /api/songs/<id>/events` is the same stream of the song's mega bank.
- Everything an agent can do to a mega bank has a route under `/api/mega/<mid>/`: `actions`, `transport`, `source`, `keep`, `sweep`, `export`, `savekit`, `loadkit`, `stretch`, `daw` (open, sync, play, stop, close), `resample`, and for the SOURCE song `regrid`, `reanalyze`, `sections`, `key`, `advise`, `stems` (409 with no source). The song-keyed routes are the same code on the song's mega bank.
- `POST /api/songs/<id>/transport` with `local: true` reports what a tab played itself; it is handed to an open DAW bridge only, never rebroadcast.
- Test hook: `CHOPPAD_DAW_FAKE=<log file>` swaps the DAW bridges for ones that only append to that log (used by `tests/e2e/test_bridge.py`); `CHOPPAD_DAW_DIR` moves the bridge's state folder.
