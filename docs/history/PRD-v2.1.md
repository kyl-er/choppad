---
title: ChopPad — PRD v2.1
type: prd
version: 2.1
status: approved-for-build
owner: neo
date: 2026-10-25
sources: "[[Planning/PRD-v1]] + [[Planning/DEBATE-SUMMARY]] + [[Planning/RESEARCH]] + [[Planning/CONSENSUS]]"
tags: [choppad, prd, music, midi, v2.1]
---

# ChopPad — Product Requirements (v2.1, research & consensus merged)

> Take a YouTube video, detect its tempo and meter, slice it into quarter-note chops, map the chops to a
> 4×4 MPC-style pad grid in a local web app, and finger-drum new beats from the song with a MIDI
> controller (Android phone over USB) while a muted video jumps to whatever chop is playing — plus optional
> AI chop/section curation and 1-click export to Ardour (SFZ) and Hydrogen drumkits.

Code: `~/03-Projects/choppad` (git). Board: [[ChopPad]] (dotpm). Agent rules: [[AGENT-BRIEF]].
Messaging: `Inbox/`. Live test status: [[STATUS]]. Data contracts: `~/03-Projects/choppad/contracts/README.md`.
Tags: `[v1]` = original, `[AG1]` / `[AG2]` = debate rounds, `[R]` = research (`Planning/RESEARCH.md`), `[C]` = 3–0 consensus (`Planning/CONSENSUS.md`).

## 1. Goals
1. **G1 — Under 60 s from URL to playable pads** for a ≤ 7 min video (excluding download time). Ingest transcode ≈ 6–9 s, analysis ≈ 0.5–5 s.
2. **G2 — Playable latency.** Pad-hit → audible ≤ 20–25 ms. Automated proxy: `AudioContext({latencyHint:"interactive"})` (or `latencyHint: 0`) reports `baseLatency + outputLatency ≤ 25 ms` on PipeWire quantum 256 (~16–24 ms audio output + 1–2.5 ms ALSA `snd_seq` MIDI input); plus a human listening check. `[AG2][R]` Video: pad-hit → visible frame ≈ 8–22 ms (`-g 6 -bf 0 -an` + Blob URL); video is never the timing source.
3. **G3 — Musically correct, correctable grid.** BPM within ±1% and beats within ±20 ms on steady-tempo material; banks always start on downbeats; **one-click correction in the browser** (÷2, ×2, beat −1/+1, set bar 1, grid, meter) without re-download or re-analysis. `[AG1][AG2]`
4. **G4 — Any class-compliant MIDI controller**, primarily neo's **Pixel 11 Pro XL in USB-MIDI mode** (already verified as ALSA client `"Pixel 11 Pro XL MIDI 1"`), with **mandatory MIDI-learn** and **Full Level** mode; plus layout-independent keyboard (`KeyboardEvent.code`) and mouse/touch pads. `[AG1][AG2][R]`

## 2. Non-goals (v1 / v2.1)
- No built-in sequencer or multi-track recorder inside the web app — instead, **`export`** writes a zero-copy `.sfz` for **Ardour** and a `drumkit.xml` kit for **Hydrogen** (both already installed on this machine). `[C]`
- Phone browser touch-pad mode (`?mode=pad` over `adb reverse`) is **deferred to v1.1** because native USB-MIDI on the Pixel 11 Pro XL is already working with 1–2.5 ms latency. `[C]`
- No stem separation, no time-stretch (pitch control, if any, is MPC-style resample via `playbackRate`), no rubato tracking, no per-pad FX.
- No hosting, no auth, no database. Server binds `127.0.0.1` only.
- **No build step and no heavy deps.** Frontend = plain ES modules (no Vite/TS/node_modules; optional `wavesurfer.js@8.0.3` via CDN ESM for timeline view only). Backend = stdlib HTTP server. Python 3.14 prebuilt wheels only (`numpy`, `scipy`, `soundfile`, `yt-dlp`; optional `onnxruntime` for `Beat This!`; never `torch`, `madmom`, or `aubio`). `[v1][R][C]`
- `beats_per_bar` limited to **{2, 3, 4}** in v1 (6/8, 7/8 deferred). `[AG1][AG2]`

## 3. Users, live system facts & flow
Single user (neo), Linux/Omarchy (Hyprland, PipeWire quantum 256, `realtime` + `audio` groups active), Chrome, ~3.4 GB free disk, Python 3.14, Node 26 (tests only), **Ardour** and **Hydrogen** installed, **Pixel 11 Pro XL** confirmed working as ALSA USB-MIDI client `"Pixel 11 Pro XL MIDI 1"`.
```
choppad "https://youtu.be/…" --serve           # or a local file path
  → media/<id>/video.mp4 (480p, -g 6 -bf 0 -an), audio.wav, meta.json   (ingest)
  → media/<id>/analysis.json                                            (rhythm)
  → media/<id>/chops.json                                               (chop)
  → media/<id>/advisor.json (optional, on-demand via OpenRouter)        (advisor)
  → media/<id>/<id>-bank0.sfz / ~/.hydrogen/data/drumkits/<id>/         (export)
  → http://127.0.0.1:8960/?id=<id>                                      (integrate)
Browser (MPC layout): muted video monitor · LCD (title, BPM, meter, confidence, active chop) ·
4×4 pads (pad 1 bottom-left) · bank A/B/C… · grid/meter/÷2/×2/beat±1/set-bar-1 ·
MIDI device list + Learn/Reset · One-shot/Gate/Play-through · Choke · Full Level ·
"click to enable audio" banner.
```

## 4. Architecture
| Component | Lang | Input → Output | Owner |
|---|---|---|---|
| **contracts** | py+js | schemas, `validate.py`, goldens, module/entry-point signatures | orchestrator |
| **ingest** | py | URL/file → `video.mp4`, `audio.wav`, `meta.json`; media cache cap | agent-ingest |
| **rhythm** | py | `audio.wav` (+meta) → `analysis.json` (4-part NumPy/SciPy DSP + optional `Beat This!` ONNX) | agent-rhythm |
| **chop** | py | `analysis.json` + `{grid, meter, beat_offset, bpm_mult}` → `chops.json` (pure function) | agent-chop |
| **midi** | js | Web MIDI + keyboard + learn → pad/bank/device events (`midi.js`) | agent-midi |
| **ui** | js | `engine.js` (audio + video follow, oneshot/gate/playthrough), `layout.js`, `ui.js` (`mountUI`) | agent-ui |
| **integrate** | py+js | CLI, `ThreadingHTTPServer` (Range 206, `/api/regrid`), `main.js`, e2e | agent-integrator |
| **advisor** | py | `media/<id>/` → `advisor.json` (OpenRouter audio-LLM section map & best-16 bank, mocked in tests) | agent-advisor |
| **export** | py | `media/<id>/` → Ardour zero-copy `.sfz` and Hydrogen `drumkit.xml` + 16 WAVs | agent-export |
| **phone-midi** | doc | Android USB-MIDI setup, Pixel 11 Pro XL proof, app note map, Stay-awake & 3-finger gesture tips | agent-research + neo |

Dependency graph (enforced by `tools/status.py`):
- Critical path: `contracts → {ingest, rhythm, chop, midi, ui} → integrate → acceptance ← phone-midi`
- Off-critical-path v2.1 components: `contracts → {advisor, export}`

## 5. Functional requirements

### Ingest
- **FR-ingest-1** YouTube URL via `yt-dlp` (cookieless by default; `--cookies-from-browser chrome` **opt-in only**; pass `--js-runtimes node` when needed for 2026 `yt-dlp-ejs` challenges) or a local media file. `[AG1][R]` Check duration from metadata **before** downloading and reject > `--max-duration 420` s. Media id = YouTube id, else `local-<sha1(abspath)[:10]>`.
- **FR-ingest-2** One ffmpeg pass from the same source, then delete the raw download: `[all]`
  - `audio.wav`: `pcm_s16le`, 44100 Hz, stereo.
  - `video.mp4`: `-an -c:v libx264 -profile:v baseline -vf "scale=-2:480,fps=30" -g 6 -keyint_min 1 -sc_threshold 0 -bf 0 -preset veryfast -tune fastdecode -crf 25`, plus `-avoid_negative_ts make_zero` so t=0 matches `audio.wav`.
- **FR-ingest-3** Disk guard: total `media/` ≤ 500 MB using LRU eviction of the oldest `<id>` directories. `choppad --clean` wipes the cache. Re-runs are idempotent unless `--force`. `[AG1][AG2]`

### Rhythm
- **FR-rhythm-1** Baseline DSP is **numpy + scipy + soundfile** (`cp314` binary wheels, ~55 MB total): `[AG1][AG2][R][C]`
  - onset envelope from multi-band log spectral flux (hop ≈ 10–11.6 ms)
  - tempo from autocorrelation + comb harmonic sum with a log-Gaussian prior around 110–120 BPM ($\sigma_\tau \approx 0.9$ octaves)
  - beats from Ellis dynamic-programming tracking, then a ±35 ms snap to the nearest local onset peak
  - optional progressive enhancement: if `onnxruntime` and `beat_this_small.onnx` + `mel_spectrogram.onnx` (`danigb/beat-this-rs`, MIT, ~10.3 MB) are present on disk and `bpm` is not forced, use `Beat This!` (`method: "beat-this-small-onnx"`).
- **FR-rhythm-2** BPM in [30, 300], folded into [70, 180] unless `--bpm` overrides. When folding, decimate or interpolate `beats[]` to match. `bpm` is **derived** as `60 / median(diff(beats))`, so the contract invariant holds by construction. Also output `bpm_candidates` (including `bpm`, `½×`, `2×`). `[AG2][AG1]`
- **FR-rhythm-3** Meter from {2, 3, 4} and downbeat phase using the **4-part bar template** after skipping any quiet/noise intro (RMS/onset gate): `[R][C]`
  1. Sub-bass/low-band kick energy (`<130–150 Hz`) on beat 1 (and accented clicks at ≥1000 Hz for click tracks)
  2. Backbeat snare/mid-high energy (`200–4000 Hz`) on beats 2 & 4 in 4/4
  3. 2-beat-windowed chroma / spectral change on beat 1
  4. Tie-breaker bonus for the phase aligning with the first active high-energy bar after the intro
  - Prefer `4/4` unless `3/4` is clearly stronger (>20% relative margin). Output `confidence` and `meter_confidence`; `--meter` overrides.

### Chop
- **FR-chop-1** Pure function of `analysis.json` and its parameters (no audio access), so `chops.json` stays contiguous and runs in < 50 ms. `[AG1 conceded]` Grids: `1/4` (default), `1/8`, `1/16`. Parameters:
  - `beat_offset` (−3…+3): shifts bar 1
  - `bpm_mult` (½ | 1 | 2): re-grids from the beat list by merging or splitting beats
  - `meter`: overrides `beats_per_bar`
  The pickup before the (shifted) first downbeat is dropped, as is the trailing beat with no end.
- **FR-chop-2** Banks: `steps_per_bar = beats_per_bar × steps`. Bank size is `(16 // steps_per_bar) × steps_per_bar` when `steps_per_bar ≤ 16`, otherwise 16. `banks` is `number[][]`, with each bank holding 1–16 chop indices and starting on `beat == 0`. `[AG2][AG1]`

### MIDI / input
- **FR-midi-1** Note-on/off on **any channel**; note-on with velocity 0 counts as note-off. Default map is notes 36–51 → pads 0–15. Inputs are picked up when plugged in after page load (`statechange`). `[v1]`
- **FR-midi-2** **MIDI-learn is mandatory**: tap pads 0→15 in sequence. The map persists in `localStorage["choppad.midiMap"]`, with Reset available. `createInput()` exposes `getDevices()`, `startLearn()`, `cancelLearn()`, `resetMap()`, `mapping`, and `destroy()`, and emits `devices` and `learn-state` events. `[AG1][AG2]`
- **FR-midi-3** Keyboard uses **`KeyboardEvent.code`** so it works on any layout:
  - pads: `KeyZ KeyX KeyC KeyV` / `KeyA…KeyF` / `KeyQ…KeyR` / `Digit1…Digit4` (bottom row first)
  - banks: `BracketLeft`/`BracketRight`
  - panic stop: `Space`
  Ignore `event.repeat` and keys typed into form fields (`INPUT`, `TEXTAREA`, `SELECT`). `[AG1][AG2]`

### Engine / UI
- **FR-ui-1** Voice = `BufferSource → Gain`:
  - Ramps: **2.5 ms fade-in** (`FADE_IN`) and **3 ms fade-out** (`FADE_OUT`) on start, choke, gate release and chop end.
  - Modes: **`"oneshot"`** (default, plays `[chop.start, chop.end]`), **`"gate"`** (plays from `chop.start` until `release(pad)`), and **`"playthrough"`** (plays from `chop.start` to the end of the song until choked or stopped). `[C]`
  - Choke: one global mute group across all banks, on by default (`choke: true`). `[C]`
  - **Full Level** on by default (velocity forced to 1.0); with it off, `gain = v^1.4`.
  - The decoded buffer is peak-normalized to −1 dBFS (`0.891`), capped at 4× gain. `[AG1]`
- **FR-ui-2** Video is a visual follow only: `<video muted playsinline>`, preloaded as a Blob URL (`URL.createObjectURL`) with server Range 206 support. `[all][R]`
  - On trigger, seek to `chop.start` through a **`video.seeking` coalescing guard** (the latest target wins on `"seeked"`) and call `play().catch(()=>{})`.
  - Pause at chop end (one-shot) or on release (gate).
  - A rAF drift check re-seeks if the video is more than 45 ms off the audio playhead. `[all]`
- **FR-ui-3** Show an **audio-unlock banner** while `ctx.state !== "running"`, and `resume()` on the first pointer or key event, since Web MIDI doesn't count as a user gesture in Chrome. `[AG1][AG2]`
- **FR-ui-4** MPC page:
  - pads rendered top row `12–15` down to bottom row `0–3`, clickable or touchable via pointer events, flashing on hit
  - LCD readout (title, BPM, meter, confidence, active chop)
  - bank selector
  - grid and meter selectors, ÷2 / ×2 / beat −1 / +1 / set-bar-1, all calling `/api/regrid` and hot-swapping chops via `engine.setChops()`
  - MIDI device list with Learn/Reset
  - mode toggles (`oneshot` / `gate` / `playthrough`, `choke`, `fullLevel`)
  `mountUI(rootEl, {meta, analysis, chops, engine, input, onRegrid}) → {updateChops, destroy}`. `[AG2]`

### Integrate
- **FR-int-1** CLI: `choppad <src> [--grid] [--meter] [--bpm] [--max-duration] [--cookies-from-browser B] [--serve] [--port 8960] [--clean] [--force]`. `[v1]`
- **FR-int-2** Server: `ThreadingHTTPServer` on 127.0.0.1.
  - `/` serves the ui, and `/midi.js` serves the midi module.
  - `/media/<id>/*` supports **Range 206** with `Accept-Ranges` and suppresses BrokenPipe/ConnectionReset.
  - `POST /api/regrid {id, grid, meter, beat_offset, bpm_mult}` re-runs chop in < 50 ms, writes `chops.json` and returns it. `[v1][AG1][AG2]`

### Advisor (optional, off critical path) `[R][C]`
- **FR-adv-1** `advise(media_dir, *, client=None, model="qwen/qwen3.8-omni-flash", max_cost_usd=0.05, force=False) -> dict` in `components/advisor/choppad_advisor.py`.
- **FR-adv-2** Uses the **Hybrid Bar/Chop-Index Pattern**: builds a prompt listing local bar/chop timestamps from `analysis.json` and `chops.json`, transcodes `audio.wav` to a 16 kHz mono 32 kbps MP3 (~960 KB for 4 min), checks that estimated token cost ≤ `max_cost_usd` ($0.05 cap; default `qwen/qwen3.8-omni-flash` costs ~$0.0006/song), and requests structured JSON (`sections[]`, `suggested_bank[16]`, `pad_labels[16]`, `bpm_meter_check`).
- **FR-adv-3** Validates against `choppad.advisor/2`, caches to `media/<id>/advisor.json`, and accepts an injectable `client(prompt, audio_bytes, model)` callable so unit tests run 100% offline.

### Export (DAW hand-off to Ardour & Hydrogen, off critical path) `[R][C]`
- **FR-exp-1** `export_sfz(media_dir, out_path=None, *, bank=0) -> str` in `components/export/choppad_export.py`: writes a zero-copy `.sfz` file referencing `audio.wav` with `<group>` (`loop_mode=one_shot`, `group=1`, `off_by=1`, `ampeg_attack=0.002`) and 1–16 `<region>` entries (`key=36..51`, sample-frame `offset` and `end`), ready to load into Ardour (`sfizz` / `a-fluidsynth`).
- **FR-exp-2** `export_hydrogen(media_dir, out_dir, *, bank=0, kit_name=None) -> str`: slices the bank's chops from `audio.wav` into `chop_00.wav`..`chop_15.wav` (44.1 kHz stereo 16-bit PCM with 2.5 ms fade-in and 3 ms fade-out) and writes `drumkit.xml` (`xmlns="http://www.hydrogen-music.org/drumkit"`, `<muteGroup>0</muteGroup>`, `<midiOutNote>36..51</midiOutNote>`) so the kit drops straight into `~/.hydrogen/data/drumkits/<kit_name>/`.

## 6. Acceptance (neo)
1. `tools/status.py` all GREEN (including `advisor` and `export`).
2. A real YouTube song is playable on the pads within 60 s of download finishing, with BPM within 1% of tap tempo. If the grid is wrong, one click fixes it.
3. The Pixel 11 Pro XL in USB-MIDI mode (`Pixel 11 Pro XL MIDI 1`) shows up in `aconnect -l` and in the UI device list. MIDI-learn works, and hits produce no clicks, no doubled audio, no audio silenced by the browser's autoplay block, and smooth video jumps.

## 7. Risks
| Risk | Mitigation |
|---|---|
| Downbeat lands on the backbeat, or half/double tempo | 4-part bar template (`<130 Hz` kick + beats 2&4 snare + chroma + entry anchor), prefer 4/4 (>20% margin for 3/4), `bpm_candidates`, browser regrid (÷2/×2/±1/set-bar-1), optional `Beat This!` ONNX |
| Long-GOP YouTube video stalls on seek or doubles the audio | required `-g 6 -bf 0 -an` transcode, muted video, Blob URL preload + Range 206, seek coalescing |
| Disk fills up (~3.4 GB free) | 7 min cap checked before download, 500 MB LRU, raw file deleted, `--clean`, zero-copy SFZ export, no node_modules |
| Phone app sends odd notes/channels or erratic velocity | omni channel, mandatory MIDI-learn, Full Level default |
| Android drops touches or sleeps | `docs/phone-midi.md`: Developer options → Stay awake; disable 3-finger screenshot gesture |
| Chrome autoplay keeps audio suspended on MIDI-first hits | unlock banner + resume on first gesture |
| Audio LLM hallucinates raw timestamps or overspends budget | Hybrid bar/chop-index prompt + hard $0.05/song cap + cached `advisor.json` |

## 8. Contracts v2.1 — summary
1. §§0–5 from v2 (landed in commit `f1688aa`), plus `"playthrough"` mode in `engine.js`.
2. §6 (new): `choppad.advisor/2` (`advisor.json`) validated by `validate_advisor(adv, chops=None)` and produced by `components/advisor/choppad_advisor.py`.
3. §7 (new): `export_sfz` and `export_hydrogen` in `components/export/choppad_export.py`.

## 9. Resolved questions (from [[Planning/CONSENSUS]], 3–0 unanimous)
1. **Phone web pad over `adb reverse`:** Deferred to v1.1 (Pixel 11 Pro XL USB-MIDI already works).
2. **Choke across banks:** Yes, single global MPC mute group across all banks (`choke: true` default).
3. **Play-through mode:** Yes, included as `"playthrough"` alongside `"oneshot"` and `"gate"`.
4. **Audio-LLM advisor:** Yes, optional component `advisor` (off critical path, ≤$0.05/song cap, cached, mocked in tests).
5. **DAW export (Hydrogen & SFZ):** Yes, component `export` (zero-copy `.sfz` for Ardour + `drumkit.xml` kit for Hydrogen).
6. **DSP engine:** 4-part NumPy/SciPy baseline for deterministic <1s tests + optional `Beat This! (small)` ONNX when weights exist.
