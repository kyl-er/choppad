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
- `export_hydrogen(media_dir, out_dir, *, bank=0, kit_name=None) -> xml_path`: writes `chop_00.wav`..`chop_NN.wav` (44.1 kHz stereo s16le with 2.5 ms fade-in and 3 ms fade-out) and `drumkit.xml` (`xmlns="http://www.hydrogen-music.org/drumkit"`, `<muteGroup>0</muteGroup>`, `<midiOutNote>36+pad</midiOutNote>`) into `out_dir`.

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
- `GET /api/songs/<id>/events?origin=<id>` → `text/event-stream`. Messages: `actions` (`{ origin, actions }`), `chops` (`{ origin, chops }` after regrid or reanalyze), `transport` (`{ origin, command, pad?, velocity?, bank?, from? }`). Import `job` events are not emitted yet.
- `POST /api/songs/<id>/transport` body `{ command: "play-pattern"|"stop"|"play-song"|"hit", pad?, velocity?, bank?, from? }` → publishes the `transport` event, returns `{ ok, listeners }`. Every open tab executes it.
- `PUT /api/songs/<id>/project` still works; the server diffs old and new and broadcasts the difference as an ordinary `actions` batch.
- Each tab sends a random origin id in `X-ChopPad-Origin` on every write and ignores messages carrying it. Messages from elsewhere are dispatched with `{ remote: true, save: false }`. After a dropped stream the tab re-reads the song and applies the difference.
- Browser edits are sent as action batches about every 80 ms (runs of the same edit collapse); if that POST fails the whole project is PUT.
- CLI (`choppad songs|show|import|regrid|assign|pad|pattern|fx|play|hit|export`, server from `--server` or `CHOPPAD_SERVER`) and the MCP server (`components/control/choppad_mcp.py`, stdio, stdlib JSON-RPC) are thin clients of these endpoints. `choppad <url> --serve` is unchanged.
- Advisor endpoint and model: `CHOPPAD_LLM_URL`, `CHOPPAD_LLM_MODEL`, `CHOPPAD_LLM_KEY` (fallback `OPENROUTER_API_KEY`); `advise(model=None)` reads them.
- The server keeps binding to `127.0.0.1` only.

## 11. DAW bridge (W2) — `components/daw/`
```python
class Bridge(Protocol):                       # one per DAW: HydrogenBridge, ArdourBridge
    name: str
    def available(self) -> bool               # binary present
    def launch(self, project: DawProject) -> None      # start or attach, load kit/session
    def push(self, project: DawProject) -> None        # send the current kit + pattern + tempo again
    def transport(self, command: Literal["play", "stop"], *, bpm: float | None = None) -> None
    def close(self) -> None

@dataclass
class DawProject: song_id: str; title: str; bpm: float; beats_per_bar: int; bank: int
                  chops: list[Chop]           # 16 slots, None = empty: path to a rendered WAV, pad, midi_note (36 + pad)
                  pattern: dict               # §8 pattern
```
- `components/daw/midi_port.py`: `open_port(name="ChopPad") -> Port` with `note_on(note, vel)`, `note_off(note)`, `clock(bpm)` (24 pulses per quarter note, absolute-deadline scheduling), `start()`, `stop()`, `close()`.
- Server: `POST /api/songs/<id>/daw` body `{ daw: "hydrogen"|"ardour", command: "open"|"sync"|"play"|"stop" }`. CLI: `choppad station [--daw hydrogen|ardour] [song]`.
- A bridge never edits the user's own DAW config in place: Hydrogen runs with a copied config (`--config`) and its own OSC port.
- Mechanism per DAW: PRD §6 and `docs/daw-bridge-research.md`. The five spikes listed there come first.

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
- Open: there is no project tempo yet (the sequencer runs at `chops.bpm`), so a stretched pattern cannot play at a new tempo.
