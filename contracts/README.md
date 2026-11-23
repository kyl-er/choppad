# ChopPad contracts (v4)

Owner: orchestrator. Builders MUST NOT edit this folder; ask for a change in a comment on your Kaneo task (`CHOP-n`).
`validate.py` and `fixtures/actions.json` are the executable definitions: if prose and code disagree, code wins.
§0–§7 are unchanged from v2.1 and describe what is built. §8–§12 are new in v4 (PRD `docs/PRD-v4.md`): §8–§9 are
implemented in the browser; §10–§12 are interfaces the v4 workstreams build to.
All times are seconds (float) from the start of `audio.wav`. Pipeline per source lives in `media/<id>/`.

## 0. Entry points (module boundaries)
| Component | File | Signature |
|---|---|---|
| ingest | `components/ingest/choppad_ingest.py` | `ingest(source, media_root, *, max_duration_s=420, cookies_from_browser=None, force=False, cap_bytes=500 MiB) -> media_dir` · `enforce_cap(media_root, cap_bytes, keep=None) -> [removed]` |
| rhythm | `components/rhythm/choppad_rhythm.py` | `analyze(audio_path, meter=None, bpm=None) -> analysis` |
| chop | `components/chop/choppad_chop.py` | `chop(analysis, grid="1/4", *, meter=None, beat_offset=0, bpm_mult=1) -> chops` (pure, < 50 ms) |
| midi | `components/midi/midi.js` | `parseMidiMessage(data, mapping?, time?)`, `createInput({midiAccess, keyboardTarget, mapping, storage})`, `defaultMapping`, `STORAGE_KEY` |
| ui | `components/ui/engine.js` | `resolveChop`, `normalizationGain`, `createEngine`, `FADE_IN`, `FADE_OUT` |
| ui | `components/ui/layout.js` | `padToCell(pad)`, `cellToPad(row, col)` |
| ui | `components/ui/actions.js` | `initialState({project, chops, sections})`, `reduce(state, action)`, `projectDoc(state)`, `stepOf(event)`, `DEFAULT_PAD`, `DEFAULT_FX`, `DEFAULT_SETTINGS` (pure; §8) |
| ui | `components/ui/store.js` | `createStore(reduce, initial) -> { get(), dispatch(action, meta?), subscribe(fn) }` |
| ui | `components/ui/session.js` | `openSong({id, audio, onProgress?}) -> session` (§9) |
| ui | `components/ui/ui.js` | `mountSong(rootEl, {id, audio, input}) -> {session, state, destroy}` (view only; covered by `tests/e2e/`) |
| advisor | `components/advisor/choppad_advisor.py` | `advise(media_dir, *, client=None, model="qwen/qwen3.8-omni-flash", max_cost_usd=0.05, force=False) -> advisor` · `build_prompt(analysis, chops) -> str` · `estimate_cost_usd(duration_s, model=...) -> float` |
| export | `components/export/choppad_export.py` | `export_sfz(media_dir, out_path=None, *, bank=0) -> sfz_path` · `export_hydrogen(media_dir, out_dir, *, bank=0, kit_name=None) -> xml_path` |
| integrate | `components/integrate/choppad.py` | `run_pipeline(source, media_root, grid, meter, bpm, **ingest_kw)`, `make_server(media_root, port)`; `components/ui/main.js` wiring |

## 1. `meta.json` — **ingest**
```json
{ "schema": "choppad.meta/2", "id": "dQw4w9WgXcQ", "source": "https://youtu.be/…", "title": "…",
  "duration_s": 212.3, "video": "video.mp4", "audio": "audio.wav", "audio_sr": 44100, "audio_channels": 2,
  "video_info": { "codec": "h264", "height": 480, "gop": 6, "b_frames": 0, "has_audio": false } }
```
- `audio.wav`: PCM s16le, 44100 Hz, stereo; `video.mp4`: H.264 baseline ≤ 480p, max keyframe distance ≤ 6 frames, no B-frames, **no audio stream**. Both from the same source so t=0 aligns. Raw download deleted.
- `video_info` is measured with ffprobe, not copied from flags.
- Sources longer than `max_duration_s` raise `ValueError` before download. `media/` total ≤ `cap_bytes` (LRU by dir mtime).
- Cookies are **opt-in** (`cookies_from_browser`), never default (locked-keyring hang on this machine).

## 2. `analysis.json` — **rhythm**
```json
{ "schema": "choppad.analysis/2", "duration_s": 212.3, "bpm": 120.0, "bpm_candidates": [120.0, 60.0, 240.0],
  "meter": { "beats_per_bar": 4, "beat_unit": 4 }, "beats": [0.5, 1.0, …], "downbeats": [1.5, 3.5, …],
  "confidence": 0.83, "meter_confidence": 0.6, "method": "scipy-ellis-dp-v1" }
```
- `beats` strictly increasing within `[0, duration_s]`, at least `beats_per_bar + 1` of them.
- `bpm` is **derived**: within 0.5% of `60 / median(diff(beats))`. `bpm_candidates` includes `bpm` (and should include ½× and 2×).
- `downbeats ⊆ beats`, exactly `beats_per_bar` beats apart, last downbeat is not the final beat. Quiet/spoken intros are skipped.
- `beats_per_bar ∈ {2,3,4}`, `beat_unit = 4` (6/8, 7/8 deferred). `method` is any non-empty string.
- v4: optional `"key": {"tonic": "C".."B" (sharps), "mode": "major"|"minor", "confidence": 0..1}`. `POST /api/songs/<id>/key` detects it once and caches it in `analysis.json`.

## 3. `chops.json` — **chop**
```json
{ "schema": "choppad.chops/2", "grid": "1/4", "bpm": 120.0, "beats_per_bar": 4, "bpm_mult": 1, "beat_offset": 0,
  "pads_per_bank": 16, "bank_size": 16,
  "chops": [ { "index": 0, "start": 1.5, "end": 2.0, "bar": 0, "beat": 0 }, … ], "banks": [[0,…,15], [16,…], …] }
```
- Pure function of `analysis` + params. Pipeline: (1) `bpm_mult` re-grids the beat list from the first downbeat (2 = insert midpoints, 0.5 = keep every other beat); (2) bar 1 = first downbeat moved by `beat_offset` beats; (3) `meter` overrides `beats_per_bar`; (4) slice each beat interval into `steps` (1/4→1, 1/8→2, 1/16→4).
- Chops contiguous (`end[i] == start[i+1]`), `bar = i // steps_per_bar`, `beat = i % steps_per_bar`. Pickup before bar 1 and the trailing beat with no end are dropped.
- `bank_size = (16 // steps_per_bar) × steps_per_bar` if `steps_per_bar ≤ 16` else 16. Every bank starts on `beat == 0`; only the last bank may be short.
- `resolveChop(chops, bank, pad) = chops.chops[chops.banks[bank]?.[pad]] ?? null`.
- v4: optional `"mode": "grid"|"transient"` (absent = grid) and, in transient mode, `"sensitivity": 0..1`. A transient table keeps `schema`, `bpm`, `beats_per_bar`, `bpm_mult`, `beat_offset`, `pads_per_bank`, `bank_size = 16`; `grid` is `"1/16"`; chops are contiguous and banks hold 16 chops, but `bar`/`beat` are the nearest 1/16 position of the analysis grid, so a bank need not start on `beat == 0` and `bar == index // steps_per_bar` need not hold. `POST …/regrid` accepts `{mode, sensitivity}`. Only the server tags `"mode"`; `chop()` stays pure and the golden sample is unchanged.

## 4. Pad events (JS, **midi** → **ui**/**integrate**)
```js
{ type: "pad-on", pad: 0..15, velocity: 0..1, time, source: "midi"|"keyboard" }   { type: "pad-off", pad, time, source }
{ type: "bank", delta: ±1, time, source }   { type: "panic", time, source }
{ type: "devices", devices: [{ id, name, state }] }   { type: "learn-state", active: bool, nextPad: 0..15|null }
```
- `CustomEvent(type, { detail })` on the EventTarget from `createInput()`, which also has `getDevices() startLearn() cancelLearn() resetMap() mapping destroy()`.
- MIDI: note on/off on **any channel**, vel 0 ≡ off; default notes 36–51 → pads 0–15; hot-plug via `statechange` emits `devices`.
- MIDI learn: next 16 note-ons → pads 0..15 (no pad events while learning); saved as JSON `{note: pad}` in `storage[STORAGE_KEY]`, loaded on create; `resetMap()` clears it.
- Keyboard by **`KeyboardEvent.code`**: `KeyZ KeyX KeyC KeyV` (pads 0–3) / `KeyA KeyS KeyD KeyF` / `KeyQ KeyW KeyE KeyR` / `Digit1–4` (pads 12–15); `BracketLeft/Right` = bank; `Space` = panic. Ignore `event.repeat` and events from `INPUT`/`TEXTAREA`/`SELECT`.

## 5. Engine (JS, **ui** → **integrate**)
```js
createEngine({ ctx, buffer, chops, video?, mode: "oneshot"|"gate"|"playthrough", choke: true, fullLevel: true })
  .trigger(pad, velocity) .release(pad) .setBank(n) .bank .setChops(c) .setMode(m) .setChoke(b) .setFullLevel(b) .stopAll()
```
- Voice = `BufferSource → Gain → destination`. Gain: set 0 at start, ramp to level by `+FADE_IN` (2.5 ms), ramp to ~0 at chop end (`oneshot`); in `gate` mode release fades out over `FADE_OUT`; in `playthrough` mode the voice plays from `chop.start` past `chop.end` (to the end of the buffer) until choked or `stopAll()`. Choke/release/stop: ramp to ~0 over `FADE_OUT` (3 ms) then `stop()`.
- Level = `normalizationGain(buffer)` (peak → −1 dBFS, ≤ 4×) × (`fullLevel` ? 1 : `velocity^1.4`). Choke = one mute group across all banks.
- Video: `video.muted = true`; on trigger seek to `chop.start` + `play().catch(()=>{})`; while `video.seeking`, keep only the latest target and apply it on `"seeked"`; pause at chop end (`oneshot`) / release (`gate`). Audio is the clock.
- Page shows an "enable audio" banner while `ctx.state !== "running"` and resumes on the first pointer/key event (Web MIDI is not a user gesture).

## 6. `advisor.json` — **advisor** (optional, off critical path)
```json
{ "schema": "choppad.advisor/2", "model": "qwen/qwen3.8-omni-flash",
  "sections": [{ "label": "intro", "start_bar": 0, "end_bar": 4, "start_s": 1.5, "end_s": 9.5 }],
  "suggested_bank": [0, 2, 4, 6, 8, 10, 12, 14, 16, 18, 20, 22, 24, 26, 28, 30],
  "pad_labels": ["Kick 1", "Snare 1", "… (same length as suggested_bank)"],
  "bpm_meter_check": { "felt_bpm": 120.0, "bpm_mult_hint": 1, "meter_hint": "4/4", "notes": "steady 4/4" } }
```
- Uses the hybrid bar/chop-index prompt (`build_prompt(analysis, chops)`): passes the local bar/chop table + 16 kHz mono 32 kbps MP3 so the LLM selects valid chop indices rather than inventing raw timestamps.
- `estimate_cost_usd(duration_s, model)` checks the request against `max_cost_usd` (default $0.05 cap) before any network call.
- Cached at `<media_dir>/advisor.json` unless `force=True`. Accepts injectable `client(prompt, audio_mp3_bytes, model) -> dict|str` for offline tests.

## 7. DAW Export — **export** (Ardour SFZ & Hydrogen drumkit)
- `export_sfz(media_dir, out_path=None, *, bank=0) -> sfz_path`: writes a zero-copy `.sfz` text file (default `<media_dir>/<id>-bank<bank>.sfz`) referencing `audio.wav` with `<group>` (`loop_mode=one_shot`, `group=1`, `off_by=1`, `ampeg_attack=0.002`) and one `<region>` per pad in `chops["banks"][bank]` (`key=36+pad`, `offset=<round(start*sr)>`, `end=<round(end*sr)>`).
- `export_hydrogen(media_dir, out_dir, *, bank=0, kit_name=None) -> xml_path`: writes `chop_00.wav`..`chop_NN.wav` (44.1 kHz stereo s16le with 2.5 ms fade-in and 3 ms fade-out) and `drumkit.xml` (`xmlns="http://www.hydrogen-music.org/drumkit"`, valid against Hydrogen's `drumkit.xsd`, `<midiOutNote>36+pad</midiOutNote>`) into `out_dir`. `muteGroup` is 0 when choke is on (the default: in Hydrogen 0 is a real group, so the pads cut each other) and -1 when off. Per-pad gain and pitch go into the instrument's `gain` and `pitchOffset`, not into the WAVs.
- v4: `bank_pads` + `render_bank_kit` in `choppad_export.py` are the one path from "pad p on bank b as played" to audio (custom slice, other-song source, stretch rendering, FX bake). The Export button, kit export and the DAW bridge all use it.

## 8. Actions and state (JS today; W1 adds the same reducer on the server)
State of one open song. Only `project` is saved (`media/<id>/project.json`, `PUT /api/songs/<id>/project`).
```js
{ project: { pads: [16 × { gainDb, pitch, attack, release, reverse, mode|null }],
             overrides: { "<bank>:<pad>": { start, end, song?, stretch? } },   // custom slices; absent = grid chop
                                         // song: id of another song the slice comes from (absent = this song)
                                         // stretch: { ratio, url }: a rendering the pad plays instead (ratio = new length / old)
             pattern: { bars, events: [{ beat, pad, bank, vel, pass }] },   // beat = quarter notes from loop start; pass -1 = drawn
             fx: { tone, low, mid, high, filter, reverb, size } },
  chops,                       // §3, replaced on regrid
  sections: [{ label, letter, start_s, end_s, start_bar, end_bar }],
  bank, selected,
  settings: { mode, choke, fullLevel, quant: "off"|"1/8"|"1/16", swing, metronome, repeat, repeatRate, follow } }   // not saved
```
Every change is an action `{ type, ... }` applied by a pure reducer. Invalid or unknown actions leave the state untouched.

| type | payload | effect |
|---|---|---|
| `pad.set` | `pad, params` | merge sound params into a pad |
| `pad.reset` | `pad` | pad back to defaults |
| `pad.copyToAll` | `pad` | copy this pad's sound to all 16 |
| `pad.select` | `pad` | pad shown in the inspector |
| `slice.set` | `bank, pad, start, end` | custom slice on a pad (`end > start`) |
| `slice.clear` | `bank, pad` | back to the grid chop |
| `slice.setMany` | `bank, slices: [{pad, start, end}]` | several custom slices at once |
| `slice.stretch` | `bank, pad, stretch: {ratio, url} \| null` | attach or remove a stretch rendering; a grid pad first becomes an override with its chop's start/end |
| `kit.load` | `bank, song?, pads: [kitPad \| null × 16]` | per non-null `{song_id, start, end, params?, stretch?}`: set the override (`song` omitted when equal to `action.song`) and merge `params`; null pads are left alone |
| `fx.set` | `params` | merge master fx params |
| `pattern.toggleStep` | `pad, step, bank?, vel?` | add or remove a drawn hit at 1/16 step `step` |
| `pattern.addEvent` | `event` | append a recorded hit |
| `pattern.undo` | — | drop the latest recorded take (highest `pass`) |
| `pattern.clear` | — | empty the pattern |
| `pattern.setBars` | `bars, beatsPerBar?` | loop length; hits past the end are dropped |
| `pattern.load` | `pattern` | replace the pattern |
| `bank.set` | `bank` | current bank (clamped) |
| `chops.replace` | `chops` | new chop table, bank back to 0 |
| `sections.set` | `sections` | replace the section list |
| `settings.set` | `params` | merge player settings |

- `fixtures/actions.json` holds the cases; `components/ui/test/actions.test.js` runs them. A second implementation passes the same file.
- `slice.set` replaces the whole override, so re-slicing a pad drops its `song` and `stretch`.
- New state or actions (W3 second slice: filter, ADSR, mute groups, 16 levels, song mode, project tempo) are added here first, with fixture cases, by the orchestrator.

## 9. Session (JS): the seam between sound and screen
`openSong()` returns the only object a view needs. A view never creates an engine, sequencer or fetch call of its own.
```js
session.store                       // §8: get(), dispatch(action, meta), subscribe((state, prev, action, meta) => …)
session.meta .analysis .peaks .buffer .video .engine .seq
session.hit(pad, velocity) .release(pad) .startRepeat(pad, velocity) .stopRepeat(pad) .panic()
session.setBank(n, seekVideo) .followTo(t) .chopLabel(chop) .position() .playSong(from?) .stopSong() .songPlaying .lastChop
session.regrid({grid}|{meter}|{mult}|{shift}|{bar1}) .reanalyze(bpm)             // -> Promise<bool>
session.toggleRecord() .togglePattern() .patternMidi() .renderPatternWav()
session.loadSections() .advise() .loadAiBank() .exportBank("hydrogen"|"sfz")
session.on(type, fn) .off(type, fn) .destroy()
```
- Events: `hit {pad, velocity}` (a pad was played live), `flash {pad}` (a sequenced hit sounds now on the visible bank), `song {playing}`, `seq {}` (record/play state changed; read `session.seq.state`).
- The session applies state to the engine, sequencer and fx, and saves `project` 300 ms after the last change. Dispatch with `meta.save === false` to skip the save (for changes that came from the server).
- `meta.from === "wave"` marks a slice change made by dragging a waveform region, so the view does not redraw the regions.
- `window.choppad.song.session` exposes the open session for tests and debugging.

## 10. Agent control (W1, built) — `components/control/`
- `components/control/choppad_actions.py`: `initial_state`, `reduce`, `project_doc`, `diff_project` — the §8 reducer on the server; passes `fixtures/actions.json`.
- `POST /api/songs/<id>/actions` body `{ actions: [action, …] }` → applies §8 actions with the server reducer, writes `project.json` atomically, returns `{ project, applied, ignored, view: { bank, selected }, listeners }`. `chops.replace` is ignored here: chops change only through `/regrid` and `/reanalyze`.
- The server keeps per-song view state in memory only (bank, selected, settings, sections). Browsers forward `bank.set` so "the current bank" means the one on screen; `pad.select`, `settings.set` and `sections.set` stay per tab. `GET /api/songs/<id>` also returns `view`.
- `GET /api/songs/<id>/events?origin=<id>` → `text/event-stream`. Messages: `actions` (`{ origin, actions }`), `chops` (`{ origin, chops }` after regrid or reanalyze), `transport` (`{ origin, command, pad?, velocity?, bank?, from? }`), `daw` (`{ open, daw, bank }`, also in `GET /api/songs/<id>`), and, on a stream opened with `&role=1`, `role` (`{ primary, tabs }`): the first tab to connect is primary and is the only one that executes `transport`; the next oldest is promoted when it leaves.
- `GET /api/events` → library-level stream of import `job` events `{ job, state: "queued"|"running"|"done"|"error", step, progress, song?, error? }`. `choppad import` and the MCP `import_song` tool wait on it and return the new song id.
- `POST /api/songs/<id>/transport` body `{ command: "play-pattern"|"stop"|"play-song"|"hit", pad?, velocity?, bank?, from? }` → publishes the `transport` event, returns `{ ok, listeners }`. With `local: true` (a tab reporting its own pad hit or pattern play/stop) it is handed to an open DAW bridge only, rate limited, never rebroadcast.
- `PUT /api/songs/<id>/project` still works; the server diffs old and new and broadcasts the difference as an ordinary `actions` batch.
- Each tab sends a random origin id in `X-ChopPad-Origin` on every write and ignores messages carrying it. Messages from elsewhere are dispatched with `{ remote: true, save: false }`. After a dropped stream the tab re-reads the song and applies the difference.
- Browser edits are sent as action batches about every 80 ms (runs of the same edit collapse); if that POST fails the whole project is PUT.
- CLI (`choppad songs|show|import|regrid|assign|pad|pattern|fx|play|hit|export`, server from `--server` or `CHOPPAD_SERVER`) and the MCP server (`components/control/choppad_mcp.py`, stdio, stdlib JSON-RPC) are thin clients of these endpoints. `choppad <url> --serve` is unchanged.
- Advisor endpoint and model: `CHOPPAD_LLM_URL`, `CHOPPAD_LLM_MODEL`, `CHOPPAD_LLM_KEY` (fallback `OPENROUTER_API_KEY`); `advise(model=None)` reads them.
- The server keeps binding to `127.0.0.1` only.

## 11. DAW bridge (W2, built for Hydrogen; Ardour partial) — `components/daw/`
Proof and measurements: `docs/daw-bridge-spikes.md`.
```python
class Bridge(Protocol):                       # HydrogenBridge, ArdourBridge
    name: str
    def available(self) -> bool
    def launch(self, project: DawProject) -> dict      # start or attach, load kit/session
    def push(self, project: DawProject) -> dict        # send the current kit + pattern + tempo again
    def transport(self, command: Literal["play", "stop"], *, bpm: float | None = None) -> None   # may raise NotImplementedError with the manual steps
    def close(self) -> None

@dataclass
class DawProject: song_id; title; bpm; beats_per_bar; bank; chops: list[Chop | None]   # Chop(pad, path, midi_note = 36 + pad)
                  pattern                     # §8 pattern
                  kit_name=None; kit_dir=None; key=None
```
- `midi_port.py`: `open_port(name="ChopPad")` (ALSA sequencer through ctypes, no dependency) with `note_on`, `note_off`, `clock(bpm | None)` (24 pulses per quarter, absolute deadlines plus a short spin: max error 0.5–1.8 ms), `start(bpm)`, `stop()`, `close()`.
- `h2song.py`: writes a Hydrogen song from the §8 pattern; 48 ticks per quarter note, 192 per 4/4 bar.
- **Hydrogen** (proven): the bridge runs its own instance with a copied config and its own OSC port (`QT_QPA_PLATFORM=offscreen` when `CHOPPAD_DAW_HEADLESS=1`), loads the kit and song (`/Hydrogen/OPEN_SONG`), and leads tempo and transport over OSC (`/Hydrogen/BPM`, `/PLAY`, `/STOP`). `/Hydrogen/NOTE_ON` = note 36 + instrument position, velocity 0–1. Hydrogen follows MIDI Start/Stop but not MIDI clock. `h2cli -s song -o out.wav` is the offline renderer. Every `hydrogen`/`h2cli` call MUST pass `--config <copy>`: without it Hydrogen rewrites the owner's `hydrogen.conf`.
- **Ardour** (partial): the session is written directly (sources and regions at superclock positions) and renders correctly with `ardour9-export`; Ardour has no headless audio import. Transport and clock follow are NOT wired: OSC and the MCP surface can only be enabled in Ardour's Preferences. Headless Ardour tools MUST run with a private `XDG_CONFIG_HOME`.
- Server: `POST /api/songs/<id>/daw {daw: "hydrogen"|"ardour", command: "open"|"sync"|"play"|"stop", bank?}` → `{ok, daw, command, song, detail, midi, bpm, bank, kit}`; `ok: false` with `not_implemented` where Ardour cannot do it. Status: 400 bad input, 409 not open, 501 not installed, 502 bridge failure. Agent-driven `transport` commands and pad hits and pattern play/stop made in the browser are mirrored to an open bridge (`CHOPPAD_DAW_FAKE=<log>` swaps in logging bridges for tests).
- CLI: `choppad daw open|sync|play|stop`, `choppad station [--daw hydrogen|ardour] [song-id]`. MCP: `daw_open`, `daw_sync`, `daw_transport`.
- Limits: one kit holds one bank (hits on other banks are dropped); gain, pitch, swing and choke are carried into the kit and song, envelope and reverse are not; swing and quantize are not saved with the project, so the bridge takes them from the request (`swing`, `quant`) or the server's view settings; the Ardour GUI, like its tools, runs with the bridge's private config (its first-run wizard shows once there); Hydrogen's OSC server listens on all interfaces while the bridge runs.

## 12. Stems (W3) — `components/stems/`
```python
def separate(media_dir: str, *, backend: str | None = None) -> dict[str, str]   # {"drums": path, "bass": …, "vocals": …, "other": …}
```
- Backends: `none` (default; raises `StemsUnavailable`), `remote` (POSTs `audio.wav` to `CHOPPAD_STEMS_URL`, expects a zip of the four WAVs; same length and sample rate as `audio.wav`). No local model in v4.
- Results cached in `media/<id>/stems/`. `meta.json` gains optional `"stems": ["drums", …]`. A chop may name its source: `overrides["b:p"] = { start, end, stem? }`.

## 13. Sampler core (W3 first slice, built)
- Kit: `media/_kits/<kit-id>/kit.json` = `{schema: "choppad.kit/1", name, pads: [{song_id, start, end, params, stretch?} | null × 16], fx}`; `params` use the §8 pad keys; `stretch.url` is `/media/<song_id>/stretch/<16 hex>.wav`. Folders under `media/` whose name starts with `_` are never evicted by the cache cap.
- HTTP: `GET/POST /api/kits`, `GET/PUT/DELETE /api/kits/<id>`, `POST /api/kits/<id>/export {kind: "hydrogen"|"sfz"|"zip"}`, `POST /api/songs/<id>/savekit {bank, name}`, `POST /api/songs/<id>/stretch {bank, pad, ratio | target_bpm [, start, end, source_bpm]} -> {url, duration, ratio, hash, start, end}` (rubberband, cached in `media/<id>/stretch/`), `POST /api/songs/<id>/export {kind: "zip"} -> {path, url}`, `GET /media/<id>/stretch/<hash>.wav`.
- Exports (Hydrogen, SFZ, zip) follow the pads as played: custom slices, other-song sources and stretch renderings, read from the request body or the saved `project.json`.
- Session: `stretchPad(bank, pad, {ratio|target_bpm})`, `stretchBank`, `stretchPattern`, `clearStretch`, `saveBankAsKit(name)`, `loadKit(kitOrId)`, `detectKey()`; regrid change `{mode: "grid"|"transient", sensitivity?}`; event `key`.
- Engine: `setPadBuffer(bank, pad, buffer|null, {whole, gain})`, `getPadBuffer(bank, pad)`; nothing asynchronous on the trigger path.
- Kit pads may carry `src`, `stem` and `stretch.fit` (§14).

## 14. Sampler core (W3 second slice, built)
Saved project, new optional keys (absent = defaults, so old `project.json` files load unchanged):
```
project.tempo: { bpm: number | null, follow: bool }   // null = the song's own tempo; follow = fitted pads are re-fitted when it changes
project.song:  { patterns: [{id, name, bars, events}], chain: [{pattern: id, repeats: 1..64}], mode: "pattern"|"song", current: id|null }
pads[i] gains: decay 0..10 s (0), sustain 0..1 (1), filter {type: off|lp|hp|bp, cutoff 20..20000 Hz (8000), q 0.1..24 (0.7)}, muteGroup 0..8 (0)
overrides["b:p"] gains optional src: "/media/<id>/resample/<16hex>.wav" (a resample) and stem: "drums"|"bass"|"vocals"|"other"
stretch: { ratio, url, fit? }   // fit = the project tempo this rendering was made for; absent = stretched by hand
pattern events gain optional pitch (semitones) and cutoff (Hz)
settings gains sixteen: null | {pad, mode: "velocity"|"pitch"|"filter"}   // view setting, not saved
```
- `project.song.current` names the entry of `patterns` that `project.pattern` is; every `pattern.*` action is mirrored into it; `project.pattern` stays authoritative on load.
- Actions (all with fixture cases, in both reducers): `tempo.set {bpm|null, follow?}`, `slice.src {bank, pad, src|null, start, end}`, `slice.stem {bank, pad, stem|null}`, `song.addPattern {name?, bars?, copy?, beatsPerBar?}`, `song.selectPattern {id}`, `song.renamePattern {id, name}`, `song.removePattern {id}`, `song.setChain {chain}`, `song.setMode {mode}`, `song.load {song|null}`. `kit.load` pads may carry `src` or `stem`. `slice.stretch` is refused on a pad that has `src`.
- The global `settings.choke` (default on) still means every pad cuts every pad; per-pad mute groups matter once it is off.
- Session: `setTempo(bpm|null, {follow})`, `fitToTempo({scope: "pattern"|"bank"})`, `refitAll()`, `whenFitted()`, `tempoBpm`, `setSixteenLevels({pad, mode})`, `sixteen`, `resample({bars|seconds, source: "pattern"|"master", pad, bank?})`, `songMidi()`, `renderSongWav()`, `songPosition()`, `separateStems({force})`, `setStem(bank, pad, stem|null)`; event `stems`; `hit(pad, vel, {raw})` (raw skips the 16-levels mapping; used for remote hits).
- Tempo: the sequencer, metronome, note repeat, MIDI export, offline render and the DAW bridge use the project tempo when set. A pad is "fitted" when its `stretch.fit` is set; hand-stretched pads are never re-fitted.
- Resample: pattern source renders offline (deterministic); master source records the bus through an AudioWorklet (`GET /capture-worklet.js`). `POST /api/songs/<id>/resample` (raw 16-bit WAV body) → `{url, hash, duration, sample_rate, channels}`; `GET /media/<id>/resample/<hash>.wav`. Transport command `resample {source, bars|seconds, pad, bank?}` asks the open browser to do it.
- Song mode: the sequencer plays the chain bar-accurately; recording is off while a chain plays; the Hydrogen song gets one pattern per ChopPad pattern with the chain as its sequence, plus velocity and pitch per note.
- Exports: the pad's filter and envelope are baked into Hydrogen-kit and zip WAVs; SFZ gets `fil_type`, `cutoff`, `resonance`, `ampeg_*`. With pad mute groups in use each pad carries its own group (Hydrogen `muteGroup`, SFZ `group`/`off_by`); otherwise the global choke decides (§7).
- Stems (§12 as built): `separate(media_dir, *, backend=None, force=False)`; backend from `CHOPPAD_STEMS_BACKEND` (default `none` → `StemsUnavailable`); `remote` uses `CHOPPAD_STEMS_URL`, optional `CHOPPAD_STEMS_KEY` (Bearer), `CHOPPAD_STEMS_TIMEOUT`: raw POST of `audio.wav`, zip of four WAVs back, validated and cached in `media/<id>/stems/`. `POST /api/songs/<id>/stems {force?}` → 501 or 502 on failure; `GET /media/<id>/stems/<name>.wav`; `POST …/stretch` accepts `stem`.
- CLI: `tempo`, `song`, `resample`, `stems`; `pad` gains `--filter-type --cutoff --q --decay --sustain --mute-group`; `pattern` gains `--pitch --cutoff`. MCP: `tempo`, `song`, `resample`, `stems`.
- Known limits: the master limiter adds a constant ~6 ms to live audio and renders; MIDI export ignores per-event pitch; per-hit cutoff does not reach Hydrogen or SFZ; reverse is not carried into exports.
- UI (station screen; the classic screen does not have these): TEMPO on GRID 4, envelope/filter/mute group/16 levels on PAD 5–8, resample on EXPORT 3, DAW on EXPORT 4, patterns and chain on SEQ 3–4, stems on AI 2. Session additions for the view: `remote.dawName`, `dawCommand(name, command, {bank})`. The library pane follows `GET /api/events` and polls `/api/jobs/<id>` only while that stream is disconnected.

## 15. Drum pad mega banks (CHOP-13; steps A–D, interface fixed here before the build)
neo, 2026-11-22: "It should keep the sounds and sequence you recorded from pads when you select a new song it shouldnt change
the whole sequence and pad. There should be: SONG / DRUM PAD MEGA BANKS / THEN drum pad banks. So i can load up a drum break,
start laying down a break beat, then load up a new song and play the loop and bass and keep my drum loops. […] the option when
switching to either: (a) create new mega bank, or (b) keep current drum pad: only keep the pads you used and import the new
song into current pad session."

**Model.** Three levels: SONG (the source being chopped: audio, video, chop table, sections) → DRUM PAD MEGA BANK (what is being
made: pads, their sounds, patterns and chain, master fx, tempo; several exist, one is open) → DRUM PAD BANKS inside it (16 pads each).
A mega bank is today's project document (§8, §14) moved out of the song:
```
media/_mega/<mid>/mega.json = { schema: "choppad.mega/1", id, name, source: <song id> | null, created, updated,
                                project: { pads, overrides, pattern, fx, tempo?, song? } }      // project exactly as §8/§14
media/_mega/<mid>/samples/<16hex>.wav     audio the mega bank owns (16-bit PCM)
```
- A pad with no override shows the SOURCE song's chop for that (bank, pad), as today. A pad with an override is the mega bank's own.
- **Used pads** = every (bank, pad) that has an override, or is hit by an event of `project.pattern` or of any `project.song.patterns[*]`.
  `usedPads(project)` (JS) / `used_pads(project)` (Python) return them.
- **Kept pad** = an override that owns its audio and its sound:
  `overrides["b:p"] = { start: 0, end: <duration>, src: "/media/_mega/<mid>/samples/<hash>.wav", kept: true, params: {<pad params §8/§14>}, from?: { song, start, end } }`.
  A kept override may also carry `gain` (0 < gain ≤ 4, absent = 1): the level the chop played at before it was kept (the browser plays
  a song's chops at that song's normalising gain), so keeping a pad does not change how loud it is. The sample file itself stays the raw
  audio, so exports are unchanged. `slice.keep` takes the same optional `gain`.
  `params` is the pad's sound frozen at keep time; from then on the pad's sound lives in the override, not in `pads[p]` (which other
  banks share). The engine and the exporters use `override.params` when present, else `pads[p]`.
- Number of banks = `max(len(chops.banks), 1 + highest bank index in overrides)`; a pad beyond the source's banks without an override is empty.
- State (§8) gains `source: <song id> | null` and `mega: <mid>`.

**Actions (both reducers, with fixture cases).**
| type | payload | effect |
|---|---|---|
| `slice.keep` | `bank, pad, src, start, end, params?, from?` | set the kept override (replaces any other override on that pad); invalid input leaves the state untouched |
| `source.set` | `song, chops` | new source: `state.source`, `state.chops`; the current bank is kept if it still exists (clamped otherwise); the project is not touched |
| `pad.set` | `pad, bank?, params` | as before, but when the pad at (`bank` ?? current bank, `pad`) is kept, the params merge into `override.params` |
| `pad.reset` | `pad, bank?` | as before; on a kept pad it resets `override.params` |
`bank.set` clamps to the bank count above. `chops.replace` (regrid of the source) keeps its meaning.

**Server.**
- `GET /api/mega` → `[{id, name, source, source_title, updated, used_pads, banks}]`; `POST /api/mega {name?, source?}` → new empty mega bank;
  `GET /api/mega/<mid>` → `{mega, project, chops (of the source, or an empty table), view}`; `PUT /api/mega/<mid> {name}`; `DELETE /api/mega/<mid>`.
- `POST /api/mega/<mid>/actions`, `GET /api/mega/<mid>/events`, `POST /api/mega/<mid>/transport`: as §10, keyed by mega bank.
- `POST /api/mega/<mid>/source {song}` — option (b) "keep current drum pad": for every USED pad whose audio is not yet owned, render the pad's
  audio as played (slice, other-song source, stretch rendering, resample or stem; NOT the pad's filter/envelope/gain/pitch and NOT the master fx)
  into `samples/`, turn it into a kept override with its params frozen, then set the source. Unused pads are simply released (they show the new
  song's chops). One `actions` batch (`slice.keep`…, `source.set`) is saved and broadcast, so open tabs follow without reloading. Returns
  `{mega, kept: [{bank, pad}], released: n}`. Idempotent; switching to the current source is a no-op.
- `POST /api/mega/<mid>/keep {bank, pad}` — keep one pad now (same rendering), for the KEEP key.
- Option (a) "create new mega bank" is `POST /api/mega {source: <new song>}`.
- **Migration, non-destructive:** a song's existing `project.json` becomes a mega bank named after the song with `source` = that song the first
  time it is needed; `project.json` is left on disk untouched and is no longer written.
- **Compatibility:** every `/api/songs/<id>/…` endpoint of §10/§13/§14 keeps working and acts on "the song's mega bank" = the most recently
  updated mega bank whose `source` is that song (created empty if there is none). `GET /api/songs/<id>` also returns `mega: <mid>`.
- As built (step A): `/source` and `/keep` answer with the broadcast batch as `actions: [...]` (the calling tab's stream does not receive
  it) and with `failed: [{bank, pad, reason}]`; `/source` also returns `view: {bank}`, `/keep` also `already: [{bank, pad}]`. A pad hit by
  the pattern that has no audio at all is neither kept nor reported. `GET /api/mega` gives `used_pads` as a count; `GET /api/mega/<mid>`
  also returns `daw`; `PUT /api/mega/<mid>/project` is the whole-document save. Migration happens at most once per song
  (`media/_mega/migrated.json`), so deleting a migrated mega bank does not seed a new one from `project.json`; a deleted mega bank is moved
  to `media/_mega/.trash/`. `source.set` and `chops.replace` sent to `/actions` are ignored. `slice.keep`: `src` must match
  `/media/_mega/<mid>/samples/<16hex>.wav`; when `params` is omitted the pad's current sound is frozen. A kept pad cannot be stretched
  (400), and `savekit` of a bank with kept pads answers 400 until step D. `GET /media/_mega/<mid>/samples/<hash>.wav` is the only thing
  served from `_mega`.
- **Cache eviction** never removes a song that is the `source` of a mega bank with used pads, or that an override still refers to
  (`song`, or a `stretch.url` / `src` under `/media/<song>/`). `media/_mega` is never evicted (§13: `_` folders).

**Browser (step B).** The engine, sequencer and master bus live as long as a mega bank is open, not as long as a song: `session.setSource(song)`
loads the next song's audio, video, peaks and analysis while the pattern and kept pads keep playing; kept pads play from their sample files.
Routes: `#/mega/<mid>`; `#/song/<id>` opens that song's mega bank.
**Hard requirement (neo, 2026-11-22: "i dont want it to completely refresh everytime we load up a song"):** choosing another song must not
rebuild the page. No navigation or reload, the AudioContext, engine, sequencer and master bus objects stay the same instances, and the DOM
nodes of everything that does not depend on the source (library, pads, LCD, transport, step grid, meters, vox view) are the same nodes
before and after; only the source-dependent parts (video, waveform, section and bank strips, source figures in the LCD and header) are
updated. A browser test must prove this by object and node identity, with a pattern playing across the switch.

**UI (step C).** Library shows SONGS and DRUM PAD MEGA BANKS; the header names song → mega bank → drum pad bank. Choosing another song while the
open mega bank has used pads asks: (a) new mega bank for that song, or (b) keep current drum pads and bring the song in. Kept pads are marked.

**Agents, DAW, exports (step D).** CLI verbs and MCP tools take a mega bank (or a song, through the compatibility rule); exports and the DAW
bridge read the mega bank; a kit (§13) is 16 pads saved from or loaded into a mega bank.

**Tempo (neo, 2026-11-22: "tempo is set by user and should stay once they set it" / "but the chop should be tempo of the song").**
The mega bank's tempo (`project.tempo.bpm`) is the user's: once set, nothing but the user changes it; loading or switching a song never
does. A song's detected tempo (`chops.bpm`) is used only to cut that song's chops on its own grid. While the user has set no tempo
(`project.tempo.bpm` null) the sequencer runs at the tempo of the source the mega bank was started on; on the first source switch that
value is written into `project.tempo.bpm` (follow: false), so it stays. New chops are fitted to the mega bank's tempo only on request.

**Assumption not yet confirmed by neo (cheap to change):** the video follows only the open song (a kept pad from another song moves nothing).
