---
title: ChopPad — PRD v4 (ChopPad Station)
type: prd
version: 4
status: draft-for-signoff
owner: neo
date: 2026-11-21
supersedes: docs/history/PRD-v3.md
tags: [choppad, prd]
---

# ChopPad v4 — ChopPad Station

**One line:** turn the browser MPC into a self-contained sampling workstation: a deeper sampler core, a new MPC 2000-inspired interface with live visuals, one launcher that brings up the DAW already wired, and a control surface an AI agent can drive.

## 1. Why (neo, 2026-11-21)

> "it's actually pretty dope, I didn't realize there was an import into hydrogen button that's cool. the choppad app just needs some work … i just think the whole process can be updated into a more professional self contained and daw connected work station that can be controlled with AI"

Follow-up answers the same day:
- Try **both Hydrogen and Ardour**; research their remote control before fixing the bridge design.
- Self-contained = **one launcher** that starts ChopPad and the DAW wired together, **and** more of ChopPad's own core capability.
- **No demucs in v4**, but design the stems step so it can later run on a cloud GPU.
- **Complete UX/UI overhaul**: "live sound three.js animation, a particle vox spectrogram live view, and an Akai MPC 2000 emulator kind of design", palette like the futuristic parkour city-running game, "cyberpunk meets a white and black line palette with orange and blue highlighter accents", "modern sleek engineering design juxtaposed with old elements like vinyl, old synth gear and the MPC 2000".

## 2. Where v3 stands (verified 2026-11-21)

Works: URL and file import, tempo/meter/downbeat detection, 1/4–1/16 regrid, 4×4 pads with banks, waveform slice editing, per-pad gain/pitch/attack/release/reverse/mode, master FX with presets, step sequencer with record/quantize/swing/undo, exports (WAV, MIDI, SFZ, Hydrogen kit). All suites green (55 Python, 46 Node).

Gaps carried into v4:
| Gap | Detail |
|---|---|
| PRD v3 items never built | transient auto-chop, phone pad mode, chops zip export |
| AI advisor dead | all OpenRouter keys return 401 |
| No DAW link | exports write files only; no MIDI out, clock, transport sync or remote control; Hydrogen is not launched |
| Not agent-drivable | HTTP API is partial, no CLI verbs, no MCP, API edits do not show in an open browser |
| UI is one file | `components/ui/ui.js` (746 lines) mixes state, actions and DOM; the split planned in v3 §3 never happened |
| No browser e2e in the repo | v3 required one per milestone |
| Phone acceptance | never run |

## 3. Workstreams

Each workstream is one Kaneo epic on board `CHOP`, one git worktree branched from `v4`, one builder agent. Folders listed are the only ones that builder writes.

### W0 Foundation (orchestrator, serial, before anything else)
- [x] Commit the 2026-10-27 work; branch `v4`.
- [x] Kaneo project `CHOP`; `_atlas.md` bound; old dotpm board retired; key docs copied to `docs/history/`.
- [x] Fix stale vault paths (`AGENTS.md`, `deps.json`, `opencode.json`).
- [ ] **Seam split** of `ui.js` into `store.js` (project document + observable state), `actions.js` (every mutation, pure and unit-tested) and view modules. No behaviour change; existing suites plus a new action suite stay green. This is what lets W1, W3 and W4 work in parallel without colliding.
- [ ] **Contracts v4**: action list (name, payload, effect), project.json schema v4, event stream format, DAW bridge interface, stems backend interface.
- [ ] First Playwright smoke test (import a library song, hit a pad, reload keeps state) so every later stream has a browser gate.

### W1 Agent control — `components/control/`, `components/integrate/server.py`
Goal: anything a hand can do in the UI, an agent can do, and the open browser shows it happening.
- Server becomes the owner of the project document; every action in the contract is callable as `POST /api/songs/<id>/actions`.
- Live push to browsers (server-sent events): pad assignments, patterns, FX, transport and import progress update without reload.
- `choppad` CLI verbs over the same API: `import`, `songs`, `show`, `regrid`, `assign`, `pad set`, `pattern set|clear`, `fx`, `export`, `daw open|sync`.
- MCP server (stdio) exposing the same verbs plus read tools (song summary, chop table, sections, pattern as text) sized for an LLM.
- Advisor rerouted from OpenRouter to the local LiteLLM router (:4000); model chosen from that router only.
- Acceptance: with the song page open, an agent session imports a song, builds a 2-bar pattern from text ("kick on 1 and 3, snare chop on 2 and 4"), changes FX, and exports; every step appears in the browser within 200 ms, with no reload.

### W2 DAW bridge — `components/daw/`
Goal: `choppad station` starts ChopPad plus the chosen DAW, connected and in time.
- Virtual MIDI output port from the server: pad hits, sequencer notes and MIDI clock, visible to Hydrogen, Ardour and anything else on PipeWire.
- **Hydrogen:** install kit, write a song file containing the current pattern and tempo, launch or attach, remote transport and tempo.
- **Ardour:** create or update a session with the chops as an SFZ instrument track plus the pattern as a MIDI region, or the chops as audio regions on a track; remote transport and tempo.
- Transport and tempo follow in at least one direction (ChopPad leads).
- The exact mechanism for each DAW (OSC, Lua, file write, CLI) is fixed in `docs/daw-bridge-research.md` after the research pass; see §6.
- Acceptance: one command, no manual steps: Hydrogen opens with the kit and pattern loaded and plays in sync with ChopPad's transport; the same for an Ardour session. Checked by ear and by a MIDI-clock jitter log.

### W3 Sampler core — `components/chop/`, `components/rhythm/`, `components/export/`, `components/ui/engine.js`, `components/stems/`
Goal: grow what the instrument itself can do.
- **Transient auto-chop** (onset-based slices, sensitivity control) next to the grid chop.
- **Key detection** per song; shown in the library and LCD.
- **Time-stretch** with rubberband (already installed): fit a chop or loop to the project tempo, independent of pitch.
- **Kits across songs:** a kit can hold chops from several songs; kits saved, listed and loaded from the sidebar.
- **Per-pad filter** (LP/HP with resonance), full ADSR, mute groups, and **16 levels** (one chop across 16 pads by pitch or velocity).
- **Resample:** record the master output into a new chop.
- **Song mode:** chain patterns into an arrangement; render to WAV and MIDI.
- **Chops zip export** (owed from v3).
- **Stems interface, no local model:** `separate(song) -> {drums,bass,vocals,other}.wav` behind a backend setting (`none` by default; `remote` posts the audio to a configurable endpoint). Chops can then be cut from a stem. A cloud-GPU backend can be added later without touching the rest.
- Acceptance: each feature has unit tests and a browser check on a real song; a 72 BPM loop stretched to 101 BPM stays in time for 8 bars.

### W4 UX/UI overhaul — `components/ui/` views and styles, `docs/design/`
Goal: a complete redesign. Presentation only; it talks to the store and actions from W0 and never owns state.
- **Form:** an original hardware-style workstation that borrows the MPC 2000's layout language: monochrome LCD with six soft keys beneath it, data wheel, 4×4 pads with bank A–D, note-variation slider, transport cluster, numeric keypad. Own name and markings; no Akai logos or trade dress copied.
- **Palette and line:** white and black technical line work, with orange and blue used like highlighter for state (playing, armed, selected). Reference is the parkour game with the white city; **assumed to be Mirror's Edge — neo to confirm.**
- **Juxtaposition:** clean engineering drawing surfaces next to aged physical elements: a vinyl platter that turns with the source song and shows the playhead, VU needles, bakelite-style knobs, the LCD.
- **Live visuals (three.js, vendored as an ES module, no build step):**
  - particle spectrogram ("vox" view): the master bus drawn as a moving particle field, frequency across, time in depth, energy as height and brightness;
  - pad hits and sequencer steps drive the scene (pulses, colour by section).
- **Performance rule:** visuals read from an AnalyserNode tap and render off the audio path; a quality switch (off / lite / full) and automatic drop to lite if frames fall under 50 fps. Pad-to-sound latency must not get worse than today.
- **Process:** (1) design spec with three static directions as HTML mock-ups in `docs/design/`, (2) neo picks one, (3) build the shell, (4) port every existing control, (5) visuals.
- Acceptance: every v3 control reachable in the new UI (checklist), Playwright suite passes unchanged, latency measured before and after.

### W5 Quality and phone — `tests/e2e/`, `docs/`
- Playwright suite covering the user stories of every workstream, on the three library songs.
- Phone pad mode (`?mode=pad`), owed from v3, restyled by W4.
- Phone acceptance run with the Pixel over USB-MIDI (needs neo).

## 4. Order

| Phase | What | Who |
|---|---|---|
| A | W0 remainder: seam split, contracts v4, Playwright smoke | orchestrator |
| B (parallel) | W1 agent control · W2 DAW bridge · W3 sampler core · W4 steps 1–2 (design directions) | one Sonnet builder per worktree |
| C | W4 steps 3–5 on top of merged B; W5 e2e grows with each merge | Sonnet builder; `canvas-designer` agent for the three.js views |
| D | Phone acceptance, launcher, docs | orchestrator with neo |

Merges into `v4` happen one stream at a time, each after the orchestrator has run it on a real song in a real browser.

## 5. Model routing

| Job | Model |
|---|---|
| File and web lookups, log reading, doc gathering | Haiku |
| Building inside one worktree against a contract | Sonnet |
| Contracts, seam split, review, merge, real-song verification | orchestrator (this session) |
| Design directions and three.js scenes | Sonnet via the designer agent, orchestrator review |

At most three builders at once (memory and CPU on this machine are tight).

## 6. DAW bridge decision

From `docs/daw-bridge-research.md` (desk research; five short spikes confirm it before W2 builds).

| | Hydrogen 1.2.7 | Ardour 9.8.0 |
|---|---|---|
| Role | drum machine: kit + patterns, fastest path from pads to a beat | arrangement: chops and patterns on a timeline, mixing, final render |
| Remote control | **OSC** (built in): transport, BPM, load kit, open/save song, trigger notes | **MCP HTTP surface** for tracks, MIDI regions and notes; OSC only for transport and markers |
| Getting material in | write the kit (exists today) plus a generated `.h2song` holding the pattern and tempo, then `OPEN_SONG` | `ardour9-new_session`, then chops as WAV files and the pattern as MIDI through the MCP surface; audio import path to be settled by spike |
| Tempo and transport | ChopPad sends BPM and play/stop over OSC (Hydrogen does not appear to follow MIDI clock) | Ardour follows ChopPad's MIDI clock as its transport master |
| Launch | own instance with a copied config and its own OSC port, so the user's normal Hydrogen settings are untouched | `ardour9 <session>`; surfaces enabled once in Preferences |
| Main risk | hand-written song file must load in 1.2.7 | MCP surface is experimental and binds to all interfaces; no SFZ plugin installed, so chops go in as audio or `sfizz` gets installed |

Build order inside W2: virtual MIDI port and clock, then Hydrogen (lower risk, already half there), then Ardour.
Ardour's own MCP surface also means an agent can work in ChopPad and Ardour in one session, which fits W1.

## 7. Non-goals for v4

Local stem separation models, hosting or accounts, a native (non-browser) rewrite, plugin hosting inside ChopPad, mobile layout beyond the phone pad mode.

## 8. Open questions for neo

1. Is the palette reference **Mirror's Edge**?
2. W3 is a long list. Is there a must-have order? Proposed: transient chop, time-stretch, kits across songs, then the rest.
3. Name: keep "ChopPad", or does the workstation get a new name on the faceplate?
