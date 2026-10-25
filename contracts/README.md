# ChopPad contracts (v1)

Owner: orchestrator. Components MUST NOT edit this folder; request changes via the vault Inbox
(type `contract-change`). `validate.py` is the executable definition — if prose and code disagree, code wins.

All times are seconds (float) from the start of `audio.wav`. Pipeline per source lives in `media/<id>/`.

## 1. `meta.json` — produced by **ingest**
```json
{ "schema": "choppad.meta/1", "id": "dQw4w9WgXcQ", "source": "https://youtu.be/…",
  "title": "…", "duration_s": 212.3,
  "video": "video.mp4", "audio": "audio.wav", "audio_sr": 44100, "audio_channels": 2 }
```
- `video`/`audio` are paths relative to the media dir and must exist.
- `audio.wav`: PCM s16le, 44100 Hz, stereo, sample-aligned with the video's audio track (t=0 identical).

## 2. `analysis.json` — produced by **rhythm**
```json
{ "schema": "choppad.analysis/1", "bpm": 120.0,
  "meter": { "beats_per_bar": 4, "beat_unit": 4 },
  "beats": [0.5, 1.0, 1.5, …], "downbeats": [0.5, 2.5, …],
  "confidence": 0.83, "method": "librosa-0.11/beat_track+onset-accent" }
```
- `beats` strictly increasing, all in `[0, duration_s]`.
- `downbeats` ⊆ `beats`; consecutive downbeats are exactly `beats_per_bar` beats apart.
- `60 / median(diff(beats))` within 2% of `bpm`. `confidence` in `[0,1]`.
- `beats_per_bar` ∈ {2,3,4,6,7}; default 4 when unsure (confidence reflects it). CLI flag `--meter 3/4` overrides.

## 3. `chops.json` — produced by **chop**
```json
{ "schema": "choppad.chops/1", "grid": "1/4", "bpm": 120.0, "beats_per_bar": 4,
  "pads_per_bank": 16,
  "chops": [ { "index": 0, "start": 0.5, "end": 1.0, "bar": 0, "beat": 0 }, … ],
  "banks": [ [0,1,…,15], [16,…,31], … ] }
```
- Chops are contiguous: `chops[i].end == chops[i+1].start`; `start < end`.
- `grid` ∈ {"1/4","1/8","1/16"}; for "1/4" one chop per beat, finer grids subdivide beats evenly.
- `bar`/`beat` locate the chop's start (beat = grid step within bar, 0-based).
- Every bank starts on a downbeat chop (`beat == 0`). Last bank may be short; pads beyond it are empty.
- Audio before the first downbeat (pickup) is not chopped.

## 4. Pad events (JS, **midi** → **ui**)
```js
{ type: "pad-on",  pad: 0..15, velocity: 0..1, time: <performance.now() ms>, source: "midi"|"keyboard" }
{ type: "pad-off", pad: 0..15, time, source }
{ type: "bank", delta: +1|-1, time, source }
```
- Emitted as `CustomEvent(type, { detail: event })` on the EventTarget returned by `createInput()`.
- Default MIDI map: note 36..51 → pad 0..15 (MPC/GM layout, pad 0 = bottom-left). Note-on vel 0 ≡ pad-off.
- Default keyboard map (4×4, bottom row first): `z x c v` / `a s d f` / `q w e r` / `1 2 3 4`; `[` `]` = bank.

## 5. Engine API (JS, **ui** → **integrate**)
```js
createEngine({ ctx, buffer, chops, video?, mode: "oneshot"|"gate", choke: true })
  .trigger(pad, velocity) .release(pad) .setBank(n) .bank -> n
resolveChop(chops, bank, pad) -> chop | null      // pure, unit-tested
```
- Audio plays from the decoded buffer (sample-accurate); video is *visual follow only*:
  on trigger, `video.currentTime = chop.start` and plays; on release (gate) or chop end (oneshot) it pauses.
