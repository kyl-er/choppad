---
title: ChopPad v4 W4 build notes (CHOP-8)
type: notes
tags: [choppad, w4, ux]
date: 2026-11-22
---

# W4 build notes

Direction A "Blueprint" is the interface; direction C "Night shift" is its dark theme (same layout, same components, one token set).

## Layout of the code
- `components/ui/ui.js` opens the session and composes `components/ui/views/*` (each `mount(root, session, env) -> {destroy}`, own store subscription). `ui-classic.js` + `style-classic.css` are the previous screen, reachable with `?ui=classic`. `?mode=pad` is the phone pad screen.
- `views/`: `shell` (sheet, header, theme, env) · `lcd` + `lcdpages` (control surface) · `pads` · `transport` · `stepgrid` · `wavestrip` · `platter` · `monitor` · `vu` · `fxdeck` (knobs) · `vox` · `librarypane` · `phone`.
- `env` holds only non-song view state: shared ticker, theme, wave zoom, master volume, note variation, toast. Song state lives in `session.store`.
- three.js r170 is vendored at `components/ui/vendor/three.module.min.js` (the mock-ups keep working through a relative symlink in `docs/design/vendor/`).

## Adding a control to the LCD
Append a field to a sub-page in `views/lcdpages.js` (kinds: `num`, `inp`, `bool`, `en`, `act`, `txt`, `blank`; at most six per sub-page, one per soft key), or append a sub-page to a page. No other file changes. Examples: pad filter / ADSR / mute groups go on PAD, project tempo on GRID, song mode on SEQ.

## Behaviour choices
- Note variation slider = velocity of mouse and keyboard hits (MIDI keeps its own velocity). It matters when FULL LVL is off.
- Enter plays/pauses the song everywhere except in text/number inputs (as in the classic screen), so Enter on a focused button does not activate it; Space (native) does. In the step grid Enter toggles the cursor cell.
- The VU pair is "VU" (RMS ballistics) and "PK" (peak): the master analyser is a mono sum, so L/R would be invented.
- Vox ripple colour: orange if the pad's chop lies in a chorus-like section, blue otherwise (custom slices blue); before sections load, bars 3-4 of the bank are orange.
- On a lost WebGL context the view waits 2.5 s for a restore, then switches to the 2D canvas.

## Deviations from the mock-up
- VU: "VU" and "PK" instead of "L" and "R", with a numeric dBFS readout between them (fills the gap at 1080p).
- Title block shows song id and bank count instead of "DRAWN NEO"; callouts and key can be hidden with the KEY button.
- Vox quality switch lives inside the vox figure, not in the header (the module is self-contained); theme and KEY buttons are in the header.
- LCD fields are real controls (range / checkbox / buttons); enum fields show as segmented buttons.
- Extra rows the mock did not have: QUANT and BARS keys under the transport, SWING as a fifth knob, KEY on the MAIN page, PAD page 4 (time-stretch), GRID page 3 (transient chop + sensitivity), EXPORT page 2 (kit save), zip export.
- Dark theme keeps the A layout: no full-width skyline, no glass panels. Particles are additive and glow; pads and selection glow.

## Known limits
- Video is black in headless Chromium (no H.264); not checked with a real video frame in the shots.
- Step-grid row numbers are 8.5 px at 1500x1000 (rows are about 8 px); they are readable but at the floor. At 1920x1080 rows are 12 px.
- Phone mode in portrait leaves empty bands above and below the square pad grid.

# CHOP-12: the second sampler-core slice in the station (2026-11-22)

Every feature of contracts §14 now has a home; presentation only (state, actions, session methods). Tests: `tests/e2e/test_station2.py` (one test per feature, operated through the screen). Screenshots: `docs/design/shots/built2-{light,dark}-<page>.png` (`CHOPPAD_SHOTS=1` rewrites them).

## Where things are
| Feature | Place |
|---|---|
| Project tempo, fit to tempo | GRID page 4 "project tempo" (tempo, song tempo, follow, refit all, fit pattern / bank pads, result list with skipped pads and reasons). Header and MAIN status line show `92.0 BPM (song 101.0)`. Pads: `FIT` tag + `.fitted` for `stretch.fit`, `≈` + `.handstretched` for hand stretches |
| Envelope | PAD 5 (attack, decay, sustain, release + drawn ADSR curve) |
| Filter | PAD 6 (type, cutoff on a log range typed in Hz, Q on a log range) |
| Mute group | PAD 7 (group 0-8, choke toggle, one-line explanation that follows the choke setting); `M<n>` badge on the pad |
| 16 levels | `16 LEVELS` key under the pads (source = selected pad, mode = last mode used) and PAD 8 (mode, source). While on, the pads show the level (vel %, semitones, cutoff Hz); the LCD MAIN line and PAD 8 name the source pad |
| Resample | EXPORT 3 "sample" (source, unit, length, target pad, GO, progress bar, result); `RS` tag + `.resampled` on the pad |
| Song mode | SEQ 3 "song" (mode, new, copy, name, edit, remove, pattern list) and SEQ 4 "chain" (entries with pattern, repeats, reorder, remove; whole-song .wav / .mid). While a chain plays: the entry is marked in the list and named on the LCD (SEQ pages and MAIN line); the PLAY key reads "Chain" in song mode |
| Stems | AI 2 "stems" (separate; plain explanation of `CHOPPAD_STEMS_BACKEND=remote` + `CHOPPAD_STEMS_URL` on 501; per-pad mix/drums/bass/vocals/other); stem tag on the pad |
| Import progress | `library.js`: rows driven by `GET /api/events` `job` events (step, bar, percent); polling `/api/jobs/<id>` only when a job gets no news for 3 s; opens the song when done (only for imports this tab started) |
| DAW bridge | header chip `-> Hydrogen (bank A)` and `display only` for a non-primary tab; EXPORT 4 "DAW" (`> Hydrogen`, Sync, Stop) |

## Decisions
- New sub-pages are appended after the old ones so the numbers tests rely on (PAD 4 stretch, GRID 3, EXPORT 1-2) did not move. The LCD head now also names the sub-page (`5/8 ENV`).
- The LCD mechanism got three small extensions: `extra` (an element under the rows: curve, lists, results), `scale` on `num` fields (log ranges that still take real units from the keypad and wheel), and the `cols2` page class (two columns, leaving room for an extra). Documented at the top of `lcdpages.js`.
- The 16 LEVELS key sits on the bank-label line (not a new row) so the step grid keeps 143 px at 1500x1000.
- PLAY key: shows pause bars and "Playing" while the loop runs, so it no longer reads like STOP. It keeps the text "■" in a visually hidden span (older tests in test_smoke assert it and may not be edited).
- Enter: on a control the keyboard focused (Tab) it activates the control; after a mouse click, on the body and on the song it is still play/pause. Decided by a flag set by Tab / pointerdown, not by `:focus-visible` (which also matches after a key press). No test edits were needed.
- DAW: the server has no `close` command (only open, sync, play, stop), so the EXPORT 4 page has Stop (the bridge's transport) and says to quit Hydrogen itself.
- `session.js` got two thin additions: the DAW name in `remote.daw` (`{open, daw, bank}`, it was dropped from the stream event) and `dawCommand(name, command, {bank})`.

## Known limits
- The LCD is 125 px tall inside: the chain editor and result lists scroll inside their own box (mouse wheel over them scrolls when they overflow; the data wheel works as usual elsewhere).
- Pads are 57 px wide at 1500x1000: the `M<n>` badge and the FIT / RS / stem tag share the bottom edge, and a tagged pad moves its label up a little.
- SONG key (`.play`) still plays the original recording; the chain plays on the PLAY key in song mode.

# Drum pad mega banks, step C (CHOP-13): the interface

Owner's words: three levels, SONG / DRUM PAD MEGA BANKS / then drum pad banks; "load up a drum break ... then load up a new song and play the loop and bass and keep my drum loops"; "i dont want it to completely refresh everytime we load up a song"; tempo "set by user and should stay" but "the chop should be tempo of the song". Tests: `tests/e2e/test_mega_ui.py` (one test per point below, driven through the screen). Screenshots in `shots/mega-*.png` (light and dark, made with `MEGA_SHOTS=1`).

## Where things are
| What | Place |
|---|---|
| SONGS and DRUM PAD MEGA BANKS lists | `library.js` (the classic screen gets no mega list). Rows are buttons (keyboard). The open bank has an `OPEN` tag, not only a colour. Rename: pencil, inline, Enter / Esc. Remove: `×` asks in the row ("Move to trash?", focus on "Keep it"), then says "moved to trash". `+ New mega bank` makes an empty bank on the current song and opens it. |
| Path SONG ▸ DRUM PAD MEGA BANK ▸ DRUM PAD BANK | header (`shell.js`): three buttons, each opens its picker (SONGS list, mega list, bank keys). Names are the owner's. |
| The choice on a song change | `views/songchoice.js`: a non-modal panel over the right-hand column (never the pads); A / B / Esc, remember-my-choice tick, focus goes into the panel and back. It asks only when `usedPads(project)` is not empty and the song is one of the library; otherwise the source changes at once. (B) = `session.setSource`, (A) = `POST /api/mega {source}` + open. |
| What happened | one status line (`role=status`) under the panel's place: "Kept 4 pads from X; 12 free pads now play Y." plus any `failed` pad with its reason; "Ask me again" when a choice is remembered. |
| Kept pads | grid: heavier inner frame, `KEPT` tag, the source song's short name as the label, the song and time in the title / aria-label. LCD PAD 9 "kept": kept or not, source song, where in it, level; KEEP and RELEASE. `KEEP` key beside 16 LEVELS. RELEASE asks first when the sequence plays the pad (same panel). |
| Bank overview | LCD MAIN 2 "mega banks": per drum pad bank, KEPT / USED (not yet kept) / FREE, rows jump to the bank. |
| No source song | `session.source` is null; kept pads and the sequence play; video, waveform and LCD say "Pick a song to chop" with a button to the SONGS list. Tempo 120 until a song or the user gives one. |
| Tempo | header `TEMPO 101.0 · yours` / `TEMPO 101.0 · following <song>`; LCD big number `YOUR TEMPO` / `SONG TEMPO`; the song's tempo is the "chop tempo" on the GRID page. Logic unchanged. |

## Decisions
- The header keeps the older long form of the tempo (`USER 93.0 BPM (song 101.0)` / `SONG 101.0 BPM`) in a visually hidden span: screen readers get it, and the station tests that read it still hold.
- A kept pad's label is the song it came from, not the bar.beat of a chop it no longer is (the time shown is its start in that song).
- "Free pads" in the result line counts the pads of the bank on screen that play the new song.
- A remembered choice lives in `sessionStorage` (this tab); "A" remembered means every later song change makes a new bank.
- A bank removed while open: the screen closes it first (pending edits flushed), then opens the next most recent bank, or the welcome screen.
- Second tab whose load of the new song fails: the status line turns to an error ("Couldn't load X ... Still on Y") with Retry (`session.retrySource()`); it stays until the retry works.

## Known limits
- The pattern's bar length is kept through a source change only for the life of the session (`session.patternBeatsPerBar`); after a reload it is the open song's again. Saving it needs `pattern.beatsPerBar` in both reducers (see the report of step C).
- A mega bank that lost its song cannot be set back to "no song" by a failed switch: the server has no call that clears `source`.
- The classic screen (`?ui=classic`) still remounts on a song change and always keeps the used pads (option B); it has no mega list and no panel.
