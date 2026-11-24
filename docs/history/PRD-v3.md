---
title: ChopPad — PRD v3 (rescope)
type: prd
version: 3
status: built-m1-m4 (2026-10-25)
owner: neo
date: 2026-10-25
supersedes: "[[PRD]] (v2.1 = engine MVP)"
tags: [choppad, prd, rescope]
---

# ChopPad v3 — a browser MPC for YouTube songs

**Verdict on v2.1 (neo, 2026-10-25):** "MVP at best": no reliable video, no waveform or sound controls, song navigation broken, buggy, CLI-only.
What v2.1 got right, and what we keep: **ingest** (yt-dlp, ffmpeg, short-GOP video), **rhythm** (tempo, beats, meter; verified on real songs), **chop** (pure regrid), **export** (SFZ and Hydrogen), **midi.js** parsing and learn, and the **contracts plus status tooling**.
**Rework:** the whole UI, the server, and the app flow. The UI is designed as a product rather than as a test harness.

## 1. What neo asked for (traceability)
| # | Ask (source) | v2.1 | v3 |
|---|---|---|---|
| A1 | Paste a YouTube link and get pads *in the app* | CLI only | **Library screen**: URL box with live progress |
| A2 | The video plays and jumps as you hit chops | flaky / missing | **Big, always-visible video**, reliable Range server, no caching bugs |
| A3 | Tempo and time signature detection | ✅ | keep; plus **tap tempo** and visible grid correction |
| A4 | Chop into quarter notes, mapped to a grid | ✅ | keep; plus **any chop on any pad** (drag/assign), **transient auto-chop** mode |
| A5 | WAV/spectrum chopper | ✗ | **Waveform of the whole song** with the chop grid, playhead, zoom, click-to-audition, drag chop edges |
| A6 | Sound controls | ✗ | **Master volume and meter**; per-pad **gain, pitch (semitones), attack/release, reverse**, and one-shot/gate/playthrough per pad |
| A7 | Switch through the song | broken | **Song transport** (play/pause/scrub the original), **section strip** (intro/verse/chorus…) that jumps the pads to that part, bank = position in the song |
| A8 | Sampler + drum pads + MIDI input | partial | pads + MIDI learn + phone (Pixel USB-MIDI), keyboard, mouse/touch; **note repeat** |
| A9 | Simple sequencer, record what you play | ✗ (non-goal) | **Pattern sequencer**: record pads live over a metronome, quantize (1/8, 1/16, off), loop 1–4 bars, overdub, undo, clear, BPM, swing |
| A10 | Sound-bank browser on the left | ✗ | **Left sidebar**: song library (thumbnails, BPM, key) plus saved kits/patterns |
| A11 | AI with audio understanding to help chop | mocked only | **"Analyze with AI"** button: sections, a suggested 16-chop bank and pad names (OpenRouter audio model, ≤ $0.05/song, cached) |
| A12 | Reuse components from other apps | none | **wavesurfer.js** v7 (waveform, regions, timeline, zoom), vendored as an ES module with no build step; the export bridge to Hydrogen/Ardour (already working) |
| A13 | MPC feel | basic grid | dark MPC layout, pad colours per section, velocity-lit pads, LCD |
| A14 | Export | ✅ CLI | **in-app** export: chops WAV zip, SFZ, Hydrogen kit, pattern → WAV render and MIDI file |

## 2. Screens
```
┌ Sidebar ───────┐┌ Main ────────────────────────────────────────────────┐
│ [paste URL ][+]││ LCD: title · 101.0 BPM · 4/4 · bar 12.3 · master ▮▮▮▯ │
│ ▸ Funkorama 101││ ┌ Video (16:9, large) ┐ ┌ Pad 4×4 (labels/colours) ┐ │
│ ▸ Faster… 136  ││ │                     │ │ 13 14 15 16              │ │
│ ▸ Mellowtron 72││ │                     │ │  9 10 11 12              │ │
│ ── Kits ────── ││ └─────────────────────┘ │  5  6  7  8   Bank A ◂ ▸ │ │
│ ▸ Funk A       ││ Sections: [intro][verse][chorus][verse][bridge]…    │ │
│ ── Patterns ── ││ Waveform ════════|════|════|════ playhead, chops    │ │
│ ▸ beat 1       ││ Transport ▶ ■ ● rec  loop[2 bars] quantize[1/16]    │ │
└────────────────┘│ Pad inspector: gain · pitch · A/R · reverse · mode   │ │
                  │ Grid: 1/4 1/8 1/16 · meter · ÷2 ×2 · ±beat · tap     │ │
                  └──────────────────────────────────────────────────────┘
```
- **Library** (`/`): sidebar with the URL box. Import shows the steps (download → transcode → analyze → chop) with progress, then opens the song.
- **Song** (`/#/song/<id>`): everything above. The keyboard and MIDI work everywhere.

## 3. Architecture changes
- **Server:** keep the stdlib `ThreadingHTTPServer` but rewrite the handler.
  - Correct Range/206 handling, with BrokenPipe and ConnectionReset silenced.
  - `Cache-Control: no-store` on app files.
  - JSON errors.
  - Background **job API**: `POST /api/songs {url}` → job id; `GET /api/jobs/<id>` returns progress; `GET /api/songs` lists the library; `POST /api/songs/<id>/regrid`, `/advise`, `/export`.
  - Projects (pad assignments, per-pad params, patterns) are stored as `media/<id>/project.json`.
- **Front end:** plain ES modules, split by concern: `app.js` (router and state store), `library.js`, `song.js`, `waveform.js` (wavesurfer adapter), `pads.js`, `inspector.js`, `transport.js`, `sequencer.js` (lookahead scheduler on `AudioContext.currentTime`), and `audio.js` (the v2 engine plus master bus, per-pad params and reverse buffers). `midi.js` stays.
- **State:** one observable store and a project document. Every UI change goes through actions, which are unit-testable.

## 4. Quality bar (the v2.1 lesson)
Green unit tests hid real bugs. **Each milestone is done only when:**
1. Its unit suites are GREEN, **and**
2. a **scripted real-browser e2e** (Playwright against Chromium on the live server, using a real song from the library: Funkorama, "Faster Does It", Mellowtron) passes for that milestone's user stories, **and**
3. the orchestrator has opened it and looked at it (screenshot in `Planning/screens/`).

## 5. Milestones
| M | Scope | Acceptance (e2e) |
|---|---|---|
| **M1 Solid core** | new server + job API; library and import flow; song screen layout; reliable video; waveform with chop overlay and playhead; pads with bar.beat labels and section colours; master volume and meter; bank ◂ ▸ and section strip that actually move through the song; song transport | paste a URL, the song appears and opens; hitting pad 1 plays audio, moves the video and flashes the pad; bank ▸ changes the chops (waveform highlight moves); the volume slider changes the output gain; reloading keeps the state |
| **M2 Sound + chopping** | pad inspector (gain, pitch, A/R, reverse, mode); assign any chop to any pad (click a waveform region, then a pad); drag chop edges; transient auto-chop mode; tap tempo; per-song project saved | edits persist across reload; reverse plays reversed; pitch +12 doubles the rate; a drag-edited chop plays the new range |
| **M3 Sequencer** | metronome, record, quantize, loop 1–4 bars, overdub, undo/clear, swing, note repeat; pattern → WAV render and MIDI export | record 4 hits, then playback triggers the same pads at quantized times (±5 ms, scheduler log); the exported .mid has 4 notes |
| **M4 AI + polish** | "Analyze with AI" (sections, suggested bank, pad names) live on OpenRouter; in-app exports (SFZ/Hydrogen/zip); phone web-pad mode `?mode=pad` | the advisor fills section names on a real song (one live call, cost logged); the exports download |

## 6. Non-goals (still)
Stem separation, time-stretching, multi-track arrangement, hosting/auth, mobile layout beyond the phone pad mode.
