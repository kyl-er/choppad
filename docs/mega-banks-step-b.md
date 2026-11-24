# Drum pad mega banks, step B (browser): a session that outlives the song

CHOP-13, contracts §15. Owner, 2026-11-22: "i dont want it to completely refresh everytime we load up a song".

## What a person sees

Open a song, lay down a break on the pads, start the pattern. Click another song in the library. The pattern does not stop, the pads you
used keep their sound (they are marked `KEPT`), the other pads now cut the new song, and the title, video, waveform, platter and LCD show
the new song. The page is not rebuilt: nothing flashes, nothing is created again.

## How the switch is sequenced (`session.setSource(song)`, `components/ui/session.js`)

1. **Load the new song while the old one keeps working.** Its `meta.json`, `analysis.json`, `chops.json` (and `peaks.json`, computed in the
   browser when the server has not cached it) are read from its media folder and `audio.wav` is decoded. These are plain file reads, so
   asking creates nothing on the server (the song endpoint would make an empty mega bank for the song). Any failure here (unknown song,
   missing file, undecodable audio) ends the switch: nothing was touched, an error is reported (`source-error` event, a toast, `session.lastSourceError`).
2. **Tell the server.** Pending edits are flushed first (a hit recorded 50 ms ago must count as "used"), then `POST /api/mega/<mid>/source`.
   The server renders every used pad into the mega bank's `samples/`, sets the tempo if none is set, and returns the batch of actions
   (`tempo.set`, `slice.keep`..., `source.set`). That POST is the only moment the server changes. If a later step fails, the tab posts the old song
   back as the source, so the server never keeps a source the tab could not open.
3. **Decode the kept pads' sample files.** Still the old song; still playing.
4. **One synchronous step, no `await` inside** (`commitSwap`): stop the original-audio transport, swap the engine's main buffer
   (`engine.setBuffer`), swap the video `src`, replace the content of `meta` / `analysis` / `peaks` in place, apply the server's actions to the
   store, clear the sections, drop buffers nothing refers to, emit `source {song, meta}`, start loading the sections.
   The audio thread can only schedule a hit between tasks, never inside this step, so there is no window in which a pattern hit on a kept
   pad plays the old song's slice, the new song's slice or nothing: before the step the pad is a grid chop of the old song (the same audio the kept
   sample holds); after it, it is bound to its own decoded sample. A kept pad whose sample is not loaded yet plays silence, never the song
   (`engine.trigger` returns for it), which only happens when a stream or a reload brings a kept pad in before its file has arrived.
   Unkept pads change from the old song's chop to the new song's chop in the same step.

Another tab on the same mega bank gets the server's batch on its event stream; it runs the same steps 1, 3, 4 (`setSource(song, {actions})`).
A batch that arrives while a switch is loading waits behind it, so actions are applied in the order the server made them. After a dropped
stream `resync()` follows a source change too (kept overrides are applied as `slice.keep`).

## What is the same object / node, and what is made again

Same for the whole life of the open mega bank: `AudioContext`, master gain, analyser, fx; the session; the engine; the sequencer (and its pattern,
position and tempo clock); the store; the event stream; the `<video>` element (only its `src` changes); `session.meta`, `session.analysis`,
`session.peaks` (content replaced in place, so views holding them see the new song); every DOM node of the page: header, library pane and
its host, pad grid, bank keys, LCD, keypad, data wheel, transport, step grid, vox view, VU, knobs, monitor, the waveform's container, the platter's host.

Made again on a source change, and why: the **wavesurfer instance** inside the waveform container (the widget cannot take new peaks), the
**platter's SVG** (its ticks are one per bar, its label is the song's), the **bank strip and section strip** buttons (the new song's banks and sections),
the engine's **decoded buffer** (new song). Text nodes of the header and the LCD main page are rewritten in place.

Memory: at most the source song plus the songs still referenced by an override stay decoded. `commitSwap` calls `collectBuffers()`, which keeps only
the open song, the buffers of overrides that name a url (stretch, resample, another song, kept sample) and drops the rest; the engine's reversed copy
of the old song is reset in `setBuffer`. Checked by `test_9` (below).

## Routing (`components/ui/main.js`)

`#/mega/<mid>` and `#/song/<id>` (the song's most recently updated mega bank). After an open or a switch the address is written as
`#/song/<source>/mega/<mid>` with `history.replaceState`: it names the song and the bank, causes no navigation and no `hashchange`
(and a hashchange equal to what the script wrote is ignored). Library clicks call `setSource` on the live session. A hash change is tested
against the open mega bank: same bank, other song -> `setSource`; another mega bank -> the screen is made again (step C adds the choice).
The classic screen (`?ui=classic`) still remounts on every song change; it works on the mega-keyed session.

## Tempo (§15)

The sequencer runs at `project.tempo.bpm` when set. The server writes the old source's tempo on the first switch when none was set; the browser applies
that batch before `source.set`, so the sequencer's bpm never changes (`seq.setBpm` of the same value does nothing). The header reads `USER 93.0 BPM (song 101.0)`
when the user's tempo is set and `SONG 101.0 BPM` when it is not; the LCD's big number is labelled `USER BPM` / `SONG BPM`. The chop table
(`chops.bpm`, used by the grid, regrid and tap tools) is always the open song's.

## Kept pads in the engine and views

- `engine.trigger` takes `override.params ?? pads[pad]` per (bank, pad): one `Map.get`, merged with the defaults when the override is set, nothing asynchronous.
- A kept pad plays its sample through the existing `src` path (whole file, gain 1, the video does not follow it).
- The pad grid shows a `KEPT` tag. The waveform strip draws no region for a kept pad (it is not a slice of the open song), so it cannot be dragged or
  re-sliced; the LCD "Grab" key is disabled for it. The inspector (`pad.set`, `pad.reset`) sends the bank, so it edits the kept pad's own params.
- `stretchPad`, `fitToTempo`, `refitAll`, `stretchBank`, `stretchPattern` skip kept pads (the server answers 400 for them).
  `saveBankAsKit` on a bank with kept pads asks the server and shows its 400 text.
- Bank pickers, bank strip, LCD and phone view use `bankCount(project, chops)`.

## How each acceptance point is tested (`tests/e2e/test_mega.py`, temporary media root, free port, two library songs)

| # | test | what it proves |
|---|---|---|
| 1 | `test_1_choosing_another_song_does_not_rebuild_the_page` | Tags the AudioContext, session, engine, sequencer, store, video and 18 DOM nodes (library pane and host, pad grid, bank keys, LCD, keypad, wheel, transport, step grid, vox canvas, VU, knobs, monitor video, waveform container, platter) with `===` identity and JS expandos; a pattern is playing. Click song B in the library: all identities hold, all nodes are still in the document with their expandos, `performance.timeOrigin` and the navigation entry count are unchanged, `window.__survive` is still set, `choppad.stats.mounts` did not move and `destroys` is 0, and the header, LCD title, video src, bank strip, waveform duration and URL show song B. Only one mega bank exists (no stray bank for B). |
| 2 | `test_2_the_pattern_keeps_playing_across_the_switch` | Samples every 100 ms from 1.5 s before the click until 2.3 s after song B is ready (song B's audio is delayed 1.5 s so the load is long): `seq.state.playing` never false, context running, the longest gap between scheduled hits is far below one loop, hits were scheduled during the load, the master analyser has signal during the load and after. |
| 3 | `test_3_kept_pads_are_the_drum_break_...` | The four pattern pads are kept overrides with `src` under `/media/_mega/` and `from.song` = A; the free pads resolve to song B's chops; A's audio is not decoded any more, B's is; the offline render of the pattern correlates with song A's audio at the four slices (> 0.9) and with song B's audio at the same slices (negative control, < 0.5), at the 6 ms limiter lag. |
| 4 | `test_4_a_kept_pad_edits_its_own_sound_...` | Pitch set through the LCD PAD page on a kept pad: `override.params.pitch` is 3, `pads[0].pitch` is 0, the same pad number on another bank (also kept) is 0, the engine plays the kept pad with pitch 3, the server has the same, and after a page reload all of it is still so; the grid tags the pad `KEPT`. |
| 5 | `test_5_the_users_tempo_stays_through_a_switch` | A and B have different tempos. No user tempo before; after the switch `project.tempo.bpm` is A's tempo, the sequencer's bpm is unchanged, `chops.bpm` is B's; header and LCD say USER / SONG correctly; a tempo set by the user survives switching back to A. |
| 6 | `test_6_a_second_tab_follows_the_switch_without_reloading` | A second tab on the same mega bank follows the switch from the event stream: same marked objects, not reloaded, shows song B, has the kept pads bound to their samples. |
| 7 | `test_7_a_failed_load_leaves_everything_on_the_old_song` | Switching (through the router and through `setSource`) to a song that does not exist: source, title, video and address stay A's, an error toast shows, nothing is kept or changed on the server, no remount, the pattern keeps playing. |
| 8 | `test_8_a_reload_comes_back_...` | After the switch a reload opens the same mega bank on song B with the kept pads bound; `#/song/B` and `#/mega/<mid>` open it too. |
| 9 | `test_9_the_old_songs_decoded_audio_is_released` | A `WeakRef` to song A's `AudioBuffer`, taken before the switch: after the switch the session holds only B's audio plus the four kept samples, and a forced garbage collection through CDP (`HeapProfiler.collectGarbage`) reclaims the buffer (`deref()` is `undefined`). |
| 10 | `test_10_kept_pads_are_skipped_...` | Stretch, fit and bank stretch leave kept pads out and make no request for them; `saveBankAsKit` shows the server's 400 text. |

## Known limits (for step C)

- A mega bank with no source song cannot be opened yet (`openMega` throws a plain message); step C adds choosing one.
- Choosing a song always means option (b) (keep used pads). The (a)/(b) question and the library's MEGA BANKS list are step C.
- A kept pad plays its sample as it is (gain 1, the `src` path); the old grid chop played at the song's normalising gain, so a kept pad can be a little quieter.
- `beats_per_bar` follows the open song's chop table; a switch to a song in another meter changes the loop length of the pattern (the pattern is stored in beats).
- The DAW and export endpoints are still keyed by song (compatibility rule); step D moves them to the mega bank.
