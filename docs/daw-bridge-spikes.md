---
title: DAW bridge spikes (W2, CHOP-6)
type: research
date: 2026-11-22
tags: [choppad, daw, hydrogen, ardour, midi, osc]
---

# DAW bridge spikes, 2026-11-22

Machine: Arch, Hyprland 0.56.2, PipeWire (JACK emulation), Hydrogen 1.2.7, Ardour 9.8.0, Python 3.13 repo venv. Every
claim below was run unless it says "reasoned" or "not tested". These results replace the "unconfirmed" column of
`daw-bridge-research.md`. Scratch scripts lived in the session scratchpad; the product code that came out of them is in
`components/daw/`, and `components/daw/tests/test_integration.py` (opt-in, `CHOPPAD_DAW=1`) re-runs the key proofs.

## Verdicts

| | Question | Verdict |
|---|---|---|
| S1 | Own Hydrogen with copied config, OSC on its own port | **Proven.** Load song, BPM, play/stop, NOTE_ON, pattern select all work. Focus: a windowed instance steals focus; `QT_QPA_PLATFORM=offscreen` runs the whole app with no window at all. |
| S2 | Generated `.h2song` loads and renders | **Proven.** 48 ticks per quarter, 192 per 4/4 bar, exact to 0.1 ms at 120 and 101.5 BPM; loads over OPEN_SONG; `h2cli` renders it. |
| S3 | Virtual MIDI port from Python 3.13 | **Proven, no dependency.** ctypes on `libasound.so.2` works; `python-rtmidi` also works but builds from source. Clock jitter measured. |
| S4 | Ardour without the Preferences GUI | **Partly proven.** Headless session, tracks, tempo, meter and audio regions from external WAVs work and render correctly. OSC and the MCP HTTP surface could **not** be enabled without GUI (headless Lua does not start surfaces; a fresh GUI shows a first-run wizard). |
| S5 | Ardour following ChopPad's MIDI clock | **Not tested** (needs the GUI configured). Manual steps below. |

## S1 Hydrogen on its own instance

Setup: copy of `~/.hydrogen/hydrogen.conf` with `oscEnabled` true and `oscServerPort` 9731, `--user-data <dir>` for kits
and songs, `-O 9731`, a ChopPad kit exported from Funkorama bank 1 by `choppad_export.export_hydrogen`.

- Start: `hydrogen --child -n --config <copy> --user-data <dir> -O <port> -L <log>`. `--child` avoids the crash-reporter fork,
  so the PID you start is the only process (the owner's runs as `hydrogen` plus `hydrogen --child`).
- Readiness: the log line `Osc server started. Listening on port N` (about 0.2 s after start offscreen).
- Audio: with driver `Auto` it picks JACK (PipeWire's), ports `Hydrogen:out_L/R`. I recorded only my instance by linking its
  ports (found by PID in `pw-dump`) to a `pw-record --target 0` client, so no other audio was captured.
- **OSC meanings on 1.2.7** (from the instance's own log lines and the recordings):
  - `/Hydrogen/NOTE_ON <note> <velocity>`: note may be int or float; `[Map to instrument list position] mapped note [36] to instrument [0]`,
    so **note = 36 + position in the instrument list** (not the instrument id, not the kit's `midiOutNote`). Velocity is a float 0..1
    (0.3 came out at 0.3 of full level on the capture).
  - `/Hydrogen/SELECT_AND_PLAY_PATTERN <int>`: **0-based index in the song's patternList**, and it needs pattern mode
    (`/SONG_MODE_ACTIVATION 0.0`). Verified with a two-pattern song: index 1 played pattern B (8 clicks per bar), index 0 pattern A (1 click).
  - `/Hydrogen/OPEN_SONG <absolute path>`, `/PLAY`, `/STOP`, `/BPM <float>` all work. BPM changes in song mode with the timeline off:
    a bar that lasted 2.0 s at 120 BPM did not repeat at 2.0 s after `/BPM 60`.
  - `/Hydrogen/LOAD_DRUMKIT`: by name only finds kits in the user-data `drumkits/` folder; an absolute path works anywhere.
  - Play-to-sound latency measured at 10 to 20 ms (first click 16 ms after the PLAY datagram).
- **Feedback**: the first message from a new client makes Hydrogen dump about 100 state messages back to its port. The bridge
  config sets `oscFeedbackEnabled` false (set; not separately verified).
- **Security note**: the OSC server (liblo) listens on `0.0.0.0`, not localhost. While the bridge runs, anything on the network
  can send it OSC, including `/QUIT` and `/OPEN_SONG`. There is no option to bind it to loopback; the instance only lives while
  `choppad station` runs. A firewall rule for the OSC port is the mitigation.
- **Focus (Hyprland)**: the windowed instance opens two windows (main and Mixer) on the current workspace and takes focus.
  A `[workspace special:x silent]` exec rule moved only one of the two. Moving each window by address
  (`hyprctl dispatch 'hl.dsp.window.move({ workspace = "special:x", silent = true, window = "address:0x..." })'`, Hyprland 0.56 Lua
  dispatch syntax) works, and focus can be restored with `hl.dsp.focus({ window = "address:..." })`. Simpler: **offscreen mode**
  (`QT_QPA_PLATFORM=offscreen`): no window, no focus change, OSC, audio and OPEN_SONG all work. The bridge uses a window by
  default (the owner wants to see Hydrogen) and offscreen with `CHOPPAD_DAW_HEADLESS=1`.
- **h2cli vs h2player**: `h2cli -s song -o out.wav` renders offline (no device, any speed) and is the right tool for tests and for
  a "render this pattern" feature. `h2player` is keyboard-driven (stdin), has an OSC server but takes its config from
  `$HOME/.hydrogen` (no `--config`, no `-O`), so it would need a fake HOME. Offscreen `hydrogen` does everything it does with
  proper flags, so `h2player` is not used.
- **Gotchas that matter**:
  1. **Every `hydrogen` and `h2cli` run rewrites the config file it is given** (`h2cli -c` on a copy flipped `oscEnabled` false to
     true). The bridge therefore passes its own copy to every call, including `h2cli`. See "Side effects" for what this did to the
     owner's file before I knew.
  2. The owner's config has `jack_transport_mode USE_JACK_TRANSPORT`. Two Hydrogens on one JACK graph can share transport, so
     the bridge copy sets `NO_JACK_TRANSPORT` (precaution; the cross-talk itself was not reproduced).
  3. The existing exporter writes a "legacy format" kit (`h2cli -c`: INVALID, `uses a legacy format`; `h2cli -u` upgrades it).
     Hydrogen loads it fine and song instruments that reference it by name resolve, so no upgrade step is needed. Custom slices
     (`project.json overrides`) were never reaching the exporter; `daw_project.effective_chops` now applies them first.
  4. In the exported kit `muteGroup` is 0, which in Hydrogen is a real group, so all 16 pads choke each other (matches ChopPad's
     default "choke on").

## S2 `.h2song` from a ChopPad pattern

Modelled on `/usr/share/hydrogen/data/demo_songs/GM_kit_demo1.h2song`; writer is `components/daw/h2song.py`.

- Structure: header (`bpm`, `volume`, `loopEnabled`, `mode` song), 16 `<instrument>` elements (`midiOutNote` 36+pad,
  one layer each), one `<pattern>` (`size` in ticks, `denominator` 4, `<note>` per hit: `position`, `velocity`, `instrument`,
  `length` -1), a `patternSequence` with that one pattern.
- **Tick resolution**: a pattern of `size` ticks with denominator 4 has **48 ticks per quarter**: 192 per 4/4 bar, 12 per 1/16 step.
  Proof: click kit, hits at beats 0, 0.25, 1, 2.5, 3.75, 4, 5.0625; `h2cli` render has the clicks at 0.0000, 0.1250, 0.5000,
  1.2500, 1.8750, 2.0000, 2.5312 s at 120 BPM and at 0.0000, 0.1478, 0.5911, 1.4778, 2.2168, 2.3645, 2.9926 s at 101.5 BPM
  (expected 2.2167 for the fifth, so within 0.1 ms).
- **Instrument sample paths**: `<drumkit>` must be non-empty (empty gives warnings and `not a valid drumkit folder` errors in the
  log). With `<drumkit>kit name</drumkit>`, `<drumkitLookup>1</drumkitLookup>` (1 = user data, 2 = system, the demos use 2) and
  a layer `<filename>chop_00.wav</filename>` relative to the kit folder, the song loads clean and renders byte-identical to the
  version with absolute paths. Kit folder must sit in `<user-data>/drumkits/<name>/`.
- **Loads**: `h2cli -s song -o out.wav` renders non-silent audio (peak 0.38 on the Funkorama chops, length 4.098 s for 2 bars at
  120 BPM = 4.0 s plus tail). `OPEN_SONG` on the running instance logs `Session Drumkit [...] loaded`, no errors.
- Hydrogen caches a kit by name, so every push writes a **new kit folder name with a content hash** (`ChopPad <title> B1 <hash>`),
  keeping the last three.

## S3 Virtual MIDI output port

- `python-rtmidi` 1.5.8: no cp313 wheel, `uv pip install` builds the sdist in about 8 s (needs gcc and the ALSA headers, both
  present; this used the network through uv). `open_virtual_port` works; compiled APIs `alsa`, `jack`.
- **ctypes on `libasound.so.2`** (`components/daw/alsa_seq.py`): `snd_seq_open`, `snd_seq_create_simple_port` (READ|SUBS_READ,
  MIDI_GENERIC|SOFTWARE|APPLICATION), raw bytes encoded with `snd_midi_event_encode`, sent with `snd_seq_event_output_direct` to
  the subscribers address. Notes, clock (0xF8), Start/Continue/Stop (0xFA/0xFB/0xFC) and controllers all arrived on an rtmidi
  listener. No compiler, wheel or pip dependency: **this is the default backend**; `python-rtmidi` is not a dependency.
- Visibility: `aconnect -l` shows the client; `pw-link -o` shows `Midi-Bridge:<client>:<port> (capture)`, so PipeWire sees it too.
  **Chrome's Web MIDI** (reasoned, not tested, the owner's Chrome was not opened): Chrome on Linux enumerates ALSA sequencer ports, and
  this port is a readable user-client port, so it should appear as an input named after the client.
- **Clock jitter** (30 s each, absolute deadlines `deadline += period`, receive side timed by the kernel's ALSA event timestamps;
  the machine was busy, load average about 4):

  | BPM | spin before tick | ticks | mean wake error | p99 | max |
  |---|---|---|---|---|---|
  | 101 | none (`time.sleep` only) | 1213 | 0.185 ms | 2.5 ms | 14.8 ms |
  | 136 | none | 1633 | 0.070 ms | 1.0 ms | 3.4 ms |
  | 101 | last 2 ms spun | 1213 | **0.002 ms** | 0.021 ms | **0.54 ms** |
  | 136 | last 2 ms spun | 1633 | **0.005 ms** | 0.023 ms | **1.76 ms** |

  Receive-side interval error matched (mean 0.02 ms, max 0.54 and 1.76 ms with the spin); every tick sent was received (1213, 1633).
  The 2 ms spin costs roughly 8 to 11% of one core while the clock runs (estimate: 2 ms per tick at 40 to 54 ticks a second), so it is the
  default (`CHOPPAD_CLOCK_SPIN_MS` changes it).
- **What Hydrogen does with MIDI** (ChopPad port connected to the instance's `Hydrogen Midi-In` with `aconnect`): note-on triggers
  pads with velocity; **MIDI Start starts and MIDI Stop stops playback**; **MIDI clock is ignored** (bars stayed 2.0 s with the clock at
  90 BPM against a 120 BPM song) and each tick logs `MIDI message of type [TIMING_CLOCK] is not supported` as an error. So ChopPad leads
  Hydrogen over OSC, and the bridge does not subscribe Hydrogen to the MIDI port (live hits go over OSC NOTE_ON).

## S4 Ardour

- `ardour9-new_session [-s rate] <dir> [name]` creates the session in about 3 s (the folder must not exist; the file is
  `<dir>/<name>.ardour`). Templates: none installed. Both Ardour CLIs print `FATAL: exception not rethrown` and abort at exit even when
  they worked (the bridge judges by output, accepts exit 0, 134, -6).
- **Headless Lua** (`ardour9-lua script.lua args`, flush stdout or it is lost on the abort):
  `load_session(dir, name)` opens a session on the dummy engine at 48 kHz (a 44.1 kHz session fails: `sample-rate mismatches`);
  `Session:new_audio_track`, `new_midi_track`, `Temporal.TempoMap.write_copy()` with `set_tempo(Temporal.Tempo(bpm, bpm, 4), pos)` and
  `set_meter(Temporal.Meter(n, 4), pos)`, `set_session_extents`, `save_state("")` all worked.
- **Audio import has no headless path**: `Editor:do_import` is GUI-only, `Session::import_files` is not bound, the OSC surface has no
  import, and the MCP HTTP surface has `tracks/add`, `midi_region/*`, `midi_note/*`, `region/*`, `plugin/*`, `session/save`, `transport/*` but
  no audio import and no tempo tool (read from the shipped `libardour_mcp_http.so`).
- **Workaround that works: write the session XML.** One `<Source type="audio" origin="/abs/chop.wav" channel="n">` per channel, a
  whole-file `<Region>` in `<Regions>`, and one `<Region>` per hit in the track's `<Playlist>`. Ardour 9 time strings: positions
  `a<superclock ticks>`, lengths `a<ticks>@a<position>` (282240000 ticks per second; a first attempt with plain lengths died with
  `basic_string::substr`). Ardour reads the source's real length and corrects a wrong region length with an error line, so the
  length must be exact: files must be at the session rate (48 kHz), so the bridge resamples the chops first (a 44.1 kHz file was
  read as 2000 frames at 48 kHz). Result checked with `ardour9-export` (headless render): clicks at 0.0001, 1.0001, 2.5001 s for
  regions at 0, 1, 2.5 s, and the integration test checks four pattern hits on the beat to 20 ms.
- **OSC and the MCP HTTP surface per session**: the session file has
  `<ControlProtocols><Protocol name="Open Sound Control (OSC)" active="0"/> ... "MCP HTTP Server (Experimental)" active="0"/>`.
  Setting `active="1"` and loading it with `ardour9-lua` (sleeping 12 s) opened **no** listening socket: headless `luasession` does not start
  control surfaces. A GUI Ardour with no config shows the first-run wizard ("Welcome to Ardour", Cancel / Forward) before anything else
  (tried once, in a private Xvfb display with a private `XDG_CONFIG_HOME`, closed again), so I could not test per-session activation
  or whether the MCP surface can bind to localhost only. From the binary's strings it prints `Listen Port:` and links `libwebsockets`; no
  interface option was found (reasoned, not tested). Not claimed: nothing about OSC or MCP transport is wired.
- Global config: every Ardour tool run creates files in `~/.config/ardour9` (`recent`, `session_port.rc`, `plugin_metadata/scan_log`,
  `luahist` for `-i`). The bridge runs all headless tools with a private `XDG_CONFIG_HOME` / `XDG_CACHE_HOME` and the integration test
  asserts the owner's directory is unchanged. See "Side effects" for what my early runs left behind.
- MIDI in Ardour: no SFZ/sampler plugin is installed (PRD decision 4), and MIDI regions cannot be created headless (no source factory in
  Lua), so the pattern goes in as audio regions (one track per used pad, the pattern repeated `loops` times, region gain = velocity).

## S5 Ardour following ChopPad's MIDI clock

Not testable without the GUI configured. The ChopPad side is proven (port, clock, Start/Stop above). The Ardour side needs, once,
by hand: start Ardour and finish the wizard; Window > Preferences > Sync (Transport Masters): add or select "MIDI Clock" and enable
it; connect `ChopPad:ChopPad` to Ardour's "MIDI Clock in" port (Window > MIDI Connections, or `aconnect`/`pw-link`);
optionally Window > Preferences > Control Surfaces > enable "Open Sound Control (OSC)" (port 3819) for `/transport_play`,
`/transport_stop`, and "MCP HTTP Server (Experimental)" for track and MIDI-note tools (it may listen on all interfaces). None of
this is verified here.

## Side effects of the spikes (disclosed)

1. **`~/.hydrogen/hydrogen.conf` was rewritten by my first two `h2cli` calls** (`-c` and `-u`, run without `--config`, before I knew they
   save preferences). Content before and after is identical except **`oscEnabled` went from `false` to `true`** (port 9000). The
   owner's running Hydrogen does not listen on 9000 (checked), so nothing is exposed now, but the next Hydrogen start would open an
   OSC server on all interfaces. I did not edit the file (instructed not to); one-line fix: set `<oscEnabled>false</oscEnabled>` in it.
   Its running Hydrogen will also write its own in-memory value back when the owner quits it.
2. **`~/.config/ardour9`** got `recent` (lists four temp sessions that no longer exist), `session_port.rc`, `luahist` and
   `plugin_metadata/scan_log` from my early headless runs, before I switched to a private `XDG_CONFIG_HOME`. The owner had only
   an empty `sfdb` there. They are harmless caches; to restore the earlier state delete those four.
3. For under a minute a **real Ardour window** (the first-run wizard) opened on workspace 6 and took focus, started by a
   `daw open --daw ardour` in a test script of mine against the live bridge. I closed it by PID and refocused the previous window.
   The default tests never do this (the Ardour launch is faked); the real launch is only reachable by `choppad station --daw ardour`
   or `daw open --daw ardour`.
4. Earlier a windowed Hydrogen of mine (two windows) briefly took focus; windows were moved to a special workspace and focus restored.
5. No audio was played above about 5 to 10% master volume; recordings only captured my instance's own ports.
