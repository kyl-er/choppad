---
title: DAW bridge research — Hydrogen 1.2.7 and Ardour 9.8.0
type: research
date: 2026-11-21
tags: [choppad, daw, osc]
---

# DAW bridge research (2026-11-21)

Desk research by a Haiku agent against source and manuals; nothing was launched or tested. "Unconfirmed" items must be proven by a spike before W2 builds on them.

## Hydrogen 1.2.7 (`hydrogen`, `h2cli`, `h2player`)

| Capability | Status | How |
|---|---|---|
| OSC server | built in, off by default | `~/.hydrogen/hydrogen.conf`: `oscEnabled` false, `oscServerPort` 9000; port override `-O`; alternate config `--config` |
| Transport | yes | `/Hydrogen/PLAY`, `/STOP`, `/PAUSE`, `/RELOCATE` |
| Tempo | yes | `/Hydrogen/BPM` (float); ignored in song mode with Timeline on |
| Load kit | yes | `/Hydrogen/LOAD_DRUMKIT` (kit name) |
| Open / save song | yes | `/Hydrogen/OPEN_SONG`, `/SAVE_SONG_AS`, `/SAVE_SONG` |
| Pattern select | partly | `/SELECT_AND_PLAY_PATTERN`, `/OPEN_PATTERN`; index meaning unconfirmed |
| Trigger note | yes | `/Hydrogen/NOTE_ON` (two floats, meaning unconfirmed); MIDI notes 36+ map to instruments in order |
| Song/pattern mode | yes | `/Hydrogen/SONG_MODE_ACTIVATION` |
| CLI | yes | `hydrogen -s song -k kit -d driver -O port --config file`; `h2cli -s song -o out.wav` renders headless; `h2cli -c` validates a kit |
| Song file | XML, no schema shipped | `.h2song`: `bpm`, `instrumentList`, `patternList/pattern/noteList`, `patternSequence`; 192 ticks per 4/4 bar, so a 1/16 step is probably 12 ticks (inferred from demo songs) |
| MIDI clock follow | no evidence of support | — |
| JACK transport | supported | under `pw-jack`: unconfirmed |

Sources: `src/core/OscServer.cpp` and `Preferences.cpp` on the `master` branch (not the 1.2.7 tag), local `--help` output, shipped XSDs and demo songs.

## Ardour 9.8.0 (`ardour9`, `ardour9-lua`, `ardour9-new_session`, `ardour9-export`)

| Capability | Status | How |
|---|---|---|
| OSC surface | yes, port 3819, enabled in Preferences | transport (`/transport_play`, `/transport_stop`), markers, strip gain. No session load, no track creation, no file import, tempo not found |
| MCP HTTP surface | yes, experimental | Control Surfaces > "MCP HTTP Server", `http://127.0.0.1:4820/mcp`; tools include `tracks_add`, `midi_region_add_bbt`, `midi_note_import_json_into_region`, `transport_locate_bbt`. No audio import or tempo tool documented. Listens on all interfaces, no auth seen |
| Headless session tools | yes | `ardour9-new_session <dir> [name]` (templates), `ardour9-export` (render) |
| Headless Lua (`ardour9-lua`) | present | `Session:new_audio_track` confirmed; import, tempo and MIDI-region bindings unconfirmed; `Editor:do_import` is GUI-only |
| MIDI clock | follows and sends | MIDI Beat Clock as transport master; connect the port in Ardour's MIDI connections |
| SFZ playback | no plugin installed | no `sfizz`; bundled synths are ACE Fluid Synth (SF2) and Reasonable Synth |

Sources: manual.ardour.org (OSC control, MCP HTTP, transport masters), `libs/surfaces/osc/osc.cc`, local binaries and bundled Lua scripts.

## Python side
- `python-osc` and `mido` are pure Python. `python-rtmidi` (virtual ALSA port) may need a source build on newer Pythons; check against the repo venv first.
- MIDI clock is 24 pulses per quarter note; schedule against absolute deadlines, not relative sleeps, and measure jitter.

## Spikes W2 must run first (each under an hour)
1. Start a second Hydrogen with a copied config (`--config`, OSC on, own port): load a ChopPad kit, set BPM, play, over OSC.
2. Write a `.h2song` by cloning a demo song and replacing the pattern parts; confirm it loads and `h2cli` renders it.
3. Open a virtual MIDI port from the venv; confirm Hydrogen, Ardour and Chrome all see it; log clock jitter.
4. Ardour: enable the MCP HTTP surface, add a MIDI track and notes through it; check whether it can be bound to localhost only.
5. Ardour following ChopPad's MIDI clock under PipeWire.
