---
title: ChopPad — Consensus on Open Questions (v2.1)
type: decision-log
date: 2026-10-25
participants: [claude-opus-5-5, ag1 (gemini-3.8-flash-medium), ag2 (gemini-3.8-flash-medium)]
sources: "[[Planning/RESEARCH]] · [[ORCHESTRATOR-HANDOFF]] · `Planning/debate/11-ag1-reply.md` · `Planning/debate/12-ag2-reply.md`"
tags: [choppad, consensus, prd, v2.1]
---

# Consensus Round (R4) — Open Questions for ChopPad v2.1

Following the completion of [[Planning/RESEARCH]] (`debate/08-researcher-reply.md`), sessions **AG1** and **AG2** voted on the 6 open questions alongside **Claude's** recorded votes in [[ORCHESTRATOR-HANDOFF]]. All 6 questions reached **3–0 unanimous agreement**, leaving no unresolved splits blocking implementation.

## Vote Matrix

| # | Question | Claude | AG1 | AG2 | Outcome (3–0 Unanimous) |
|---|---|---|---|---|---|
| **Q1** | Phone web pad (`?mode=pad` over `adb reverse`) | Yes, v1.1 (low prio) | Defer to v1.1 | Defer to v1.1 | **Deferred to v1.1.** Neo's Pixel 11 Pro XL already works natively via USB-MIDI (`ALSA "Pixel 11 Pro XL MIDI 1"`, 1–2.5 ms latency). |
| **Q2** | Choke across banks | Yes (single mute group) | Yes (v1) | Yes (v1, default on) | **Included in v1/v2.1.** Single global MPC mute group across all banks (`choke: true` default, toggleable in UI). |
| **Q3** | Play-through mode (`"playthrough"`) | Yes (3rd mode) | Yes (v1) | Yes (v1) | **Included in v2.1.** Third engine mode (`"oneshot" \| "gate" \| "playthrough"`): plays from `chop.start` to end of buffer until choked/stopped. |
| **Q4** | Audio-LLM advisor (`advisor` component) | Yes (optional, off critical path) | Yes (v2.1, off critical path) | Yes (off critical path) | **Included in v2.1 (`components/advisor/`).** Hybrid bar/chop-index prompt to OpenRouter (`qwen/qwen3.8-omni-flash` or `google/gemini-3.5-flash-lite`, fallback `thinkingmachines/inkling-small:free`), $\le \$0.05$/song cap, cached as `media/<id>/advisor.json`, mocked in unit tests. |
| **Q5** | Export to Hydrogen & SFZ (`export` component) | Yes (v1.1 / v2.1) | Yes (v2.1) | Yes (SFZ + Hydrogen) | **Included in v2.1 (`components/export/`).** Exports any bank to a zero-copy `<id>.sfz` for Ardour (`offset`/`end`, `group=1`, `off_by=1`) and a Hydrogen drumkit dir (`drumkit.xml` + 16 de-clicked WAVs with `<muteGroup>0</muteGroup>`). |
| **Q6** | DSP engine in `rhythm` | NumPy/SciPy baseline (+ small CPU model if <150 MB) | Yes (4-part NumPy/SciPy + optional `Beat This!` ONNX) | Yes (4-part NumPy/SciPy + optional `Beat This!` ONNX) | **Included in v2.1.** Upgraded NumPy/SciPy Ellis DP + 4-part bar template (`<130 Hz` kick, `200–4000 Hz` snare backbeat, 2-beat chroma novelty, first-bar anchor, >20% 3/4 margin) as deterministic offline baseline, plus optional `Beat This! (small)` (`onnxruntime`) auto-upgrade when weights are present. |

## Key Technical Additions Folded into PRD v2.1 & Contracts v2.1
1. **Engine `"playthrough"` mode (Q3):** `createEngine({ mode: "oneshot" | "gate" | "playthrough" })` — in `"playthrough"`, `source.start(now, chop.start)` runs without a duration limit (fading out at `buffer.duration - chop.start` or when choked/stopped).
2. **`advisor` component (`components/advisor/choppad_advisor.py`, Q4):**
   - Signature: `advise(media_dir, *, client=None, model="qwen/qwen3.8-omni-flash", max_cost_usd=0.05, force=False) -> dict`
   - Contract `choppad.advisor/2` (`media/<id>/advisor.json`): `{ schema, model, sections: [{ label, start_bar, end_bar, start_s, end_s }], suggested_bank: [16 chop indices], pad_labels: [16 strings], bpm_meter_check: { felt_bpm,bpm_mult_hint, meter_hint, notes } }`.
   - Off critical path; `client` injectable so unit tests run 100% offline with zero network calls.
3. **`export` component (`components/export/choppad_export.py`, Q5):**
   - Signatures: `export_sfz(media_dir, out_path=None, *, bank=0) -> str` and `export_hydrogen(media_dir, out_dir, *, bank=0, kit_name=None) -> str`.
   - SFZ references `audio.wav` with sample-frame `offset`/`end`, `loop_mode=one_shot`, `group=1`, `off_by=1`, `ampeg_attack=0.002` (zero extra audio disk usage).
   - Hydrogen writes `drumkit.xml` (`xmlns="http://www.hydrogen-music.org/drumkit"`, `<muteGroup>0</muteGroup>`, `<midiOutNote>36..51</midiOutNote>`) + `chop_00.wav`..`chop_15.wav` (16-bit PCM with 2.5 ms fade-in / 3 ms fade-out).
4. **Reusable UI & System Facts:**
   - Optional `wavesurfer.js@8.0.3` + `regions.esm.js` CDN ES module for timeline visualization only (never for audio playback).
   - `yt-dlp` uses `--js-runtimes node` when available (`yt-dlp-ejs` 2026 requirement).
   - Confirmed live system state: Pixel 11 Pro XL works over USB-MIDI (`Pixel 11 Pro XL MIDI 1`), `neo` is in `realtime` + `audio` groups, PipeWire quantum 256 (~16–24 ms Chrome output latency), Ardour and Hydrogen installed.
