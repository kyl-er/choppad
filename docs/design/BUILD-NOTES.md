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
