// The LCD pages, as data. lcd.js renders and drives them; to add a control, append a field to a sub-page (or a
// sub-page to a page) here. Nothing in this file touches the DOM except the MAIN page body and the download helper.
//
// Field kinds (all take `label`, optional `name` (the input's name attribute and data-out key) and `cls`):
//   num   { get, set, min, max, step, fmt, deferred }  value on a range input; the wheel steps it, the keypad types it.
//                                                      `deferred`: commit 300 ms after the last change (server round trips)
//   inp   { get, set, min, max, step, fmt, deferred }  free numeric input (no snapping); same keypad/wheel behaviour
//   bool  { get, set }                                 checkbox
//   en    { get, set, opts, optCls, optAttr }          opts: values or {v, l}; drawn as a segmented row of buttons
//   act   { fn, val, disabled }                        a button; `label`, `val`, `disabled` may be functions
//   txt   { get, set }                                 text input (typed on the real keyboard)
//   blank {}                                           an empty soft key
//   num/inp also take `scale: { fwd, inv, round? }` (a curved range: Hz, Q ...) and `wheel` (range positions per wheel click, of 1000)
// A sub-page is { name, cls?, fields: (ctx) => field[], info?: { cls, html: (ctx) => string }, body?: (ctx) => { el, update() },
//   extra?: (ctx) => { el, update(), destroy?() } }. `body` replaces the rows (MAIN); `extra` sits under them (lists, curves, results);
//   cls "cols2" lays the rows out in two columns, leaving room for an extra. The new sub-pages of CHOP-12 are in lcdpages2.js.
// `ctx` = { session, st(), dispatch, setting(params), fxSet(params), toast, env, out, refresh(), tapState }.
import { PRESETS, DEFAULT_FX } from "/fx.js";
import { bankName, esc, fmt } from "./util.js";
import { bankCount } from "/actions.js";
import { moreSubpages } from "./lcdpages2.js";
import { megaSubpages } from "./megapages.js";

export const num = (label, o) => ({ t: "n", label, ...o });
export const inp = (label, o) => ({ t: "i", label, ...o });
export const bool = (label, o) => ({ t: "b", label, ...o });
export const en = (label, o) => ({ t: "e", label, ...o });
export const act = (label, o) => ({ t: "a", label, ...o });
export const txt = (label, o) => ({ t: "t", label, ...o });
export const blank = () => ({ t: "x", label: "" });

const sgn = (v, u = "") => `${v > 0 ? "+" : ""}${v}${u}`;
const db = (v) => `${v >= 0 ? "+" : ""}${v.toFixed(1)} dB`;
export const download = (bytes, name, type) => {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([bytes], { type }));
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 5000);
};

export const PAGE_ORDER = ["MAIN", "PAD", "FX", "SEQ", "GRID", "EXPORT", "AI", "MIDI"];

export function buildPages(ctx) {
  const { session, st, dispatch, setting, env, out } = ctx;
  const { meta, engine, seq } = session;
  const base = () => meta.title.replace(/[^\w-]+/g, "_").slice(0, 40);
  // a kept pad's sound is its own (contracts §15): read and write it with the bank, so the reducer reaches the override
  const P = () => { const { project, bank, selected } = st(); const o = project.overrides[`${bank}:${selected}`]; return o?.kept && o.params ? o.params : project.pads[selected]; };
  const padSet = (params) => dispatch({ type: "pad.set", pad: st().selected, bank: st().bank, params });
  const fxNum = (k, label, min, max, step, f) => num(label, { name: k, min, max, step, get: () => st().project.fx[k], set: (v) => ctx.fxSet({ [k]: v }),
    fmt: f ?? ((v) => (v < 0 ? "" : "") + sgn(+v.toFixed(2))) });
  const fxText = {
    tone: (v) => (Math.abs(v) < 0.02 ? "neutral" : v < 0 ? `darker ${Math.round(-v * 100)}%` : `brighter ${Math.round(v * 100)}%`),
    reverb: (v) => (v < 0.01 ? "off" : `${Math.round(v * 100)}%`),
    size: (v) => `${v < 0.33 ? "room" : v < 0.66 ? "studio" : "hall"} ${(0.4 + v * 5.6).toFixed(1)}s`,
    filter: (v) => (Math.abs(v) < 0.02 ? "off" : v < 0 ? `LP ${Math.round(-v * 100)}%` : `HP ${Math.round(v * 100)}%`),
  };
  const stBpm = () => Number(ctx.stretchBpm ?? st().chops.bpm);
  const stretchDone = (r) => {
    const list = [].concat(r), bad = list.filter((x) => !x.ok);
    out.st = bad.length ? `<span class="error">${esc(bad[0].error)}</span>` : `✓ ${list.length} pad${list.length === 1 ? "" : "s"} fitted to ${stBpm().toFixed(1)} BPM`;
    ctx.refresh();
  };
  const stretchWith = (fn) => async () => { out.st = "stretching…"; ctx.refresh(); stretchDone(await fn()); };
  const doExport = (kind) => async () => {
    out.exp = "exporting…"; ctx.refresh();
    const r = await session.exportBank(kind);
    out.exp = r.ok ? `✓ ${esc(r.path)}${r.url ? ` <a class="exp-download" href="${esc(r.url)}" download>download</a>` : ""}${r.note ? " " + esc(r.note) : ""}` : `<span class="error">${esc(r.error)}</span>`;
    ctx.refresh();
  };
  const tap = () => {
    const T = ctx.tapState, now = performance.now();
    if (T.taps.length && now - T.taps.at(-1) > 2000) T.taps = [];
    T.taps.push(now);
    if (T.taps.length > 8) T.taps.shift();
    if (T.taps.length < 3) { T.text = `tap ${4 - T.taps.length} more`; ctx.refresh(); return; }
    const iv = T.taps.slice(1).map((t, i) => t - T.taps[i]).sort((a, b) => a - b);
    T.bpm = Number((60000 / iv[Math.floor(iv.length / 2)]).toFixed(1));
    T.text = `${T.bpm} BPM`;
    ctx.refresh();
  };
  ctx.tap = tap;
  const runAI = async () => {
    out.ai = "listening to the song with an audio model…"; ctx.refresh();
    try {
      const ai = await session.advise();
      const chk = ai.bpm_meter_check || {};
      out.ai = `<b>${esc(ai.model)}</b>: ${ai.sections.length} sections · felt ${esc(chk.felt_bpm)} BPM (${esc(chk.meter_hint)})` +
        (chk.bpm_mult_hint && chk.bpm_mult_hint !== 1 ? ` — suggests ${chk.bpm_mult_hint === 2 ? "×2" : "÷2"}` : "") + ` ${esc(chk.notes || "")}`;
      ctx.aiDone = true;
    } catch (err) {
      const msg = String(err.message || err);
      out.ai = `<span class="error">${esc(/401|User not found/.test(msg) ? "The audio model endpoint rejected the API key (401). Set CHOPPAD_LLM_KEY and restart choppad." : msg)}</span>`;
    }
    ctx.refresh();
  };
  const padInfo = () => {
    const { bank, selected } = st();
    const c = engine.getSlice(bank, selected);
    return `PAD ${selected + 1} · ${c ? `${c.custom ? "CUSTOM SLICE" : "BAR " + session.chopLabel(c)} · ${fmt(c.start)}–${fmt(c.end)}${c.stretch ? ` · STRETCH ×${c.stretch.ratio.toFixed(2)}` : ""}${c.song ? " · KIT PAD" : ""}${c.kept ? " · KEPT (OWN SOUND)" : ""}` : "EMPTY"}`;
  };
  const keyText = (k) => (k ? `${k.tonic} ${k.mode === "minor" ? "min" : "maj"}` : "—");

  const pages = {
    MAIN: { title: "SONG", cls: "page-main", subs: [{ name: "song", body: mainBody, fields: () => [
      act(() => (session.songPlaying ? "Pause" : "Play"), { cls: "lcd-play", fn: () => (session.songPlaying ? session.stopSong() : session.playSong()) }),
      act("Stop", { fn: () => session.panic() }),
      bool("Follow", { name: "follow", get: () => st().settings.follow, set: (v) => setting({ follow: v }) }),
      num("Zoom", { name: "zoom", min: 0, max: 400, step: 10, get: () => env.zoom.get(), set: (v) => env.zoom.set(v), fmt: (v) => (v ? `${v}%` : "FIT") }),
      act("Bank −", { fn: () => session.setBank(st().bank - 1, true) }),
      act("Bank +", { fn: () => session.setBank(st().bank + 1, true) }),
    ] }] },

    PAD: { title: "PAD", cls: "inspector", subs: [
      { name: "sound", info: { cls: "insp-title", html: padInfo }, fields: () => [
        num("Gain", { name: "gainDb", min: -24, max: 12, step: 0.5, get: () => P().gainDb, set: (v) => padSet({ gainDb: v }), fmt: db }),
        num("Pitch", { name: "pitch", min: -12, max: 12, step: 1, get: () => P().pitch, set: (v) => padSet({ pitch: v }), fmt: (v) => sgn(v, " st") }),
        num("Attack", { name: "attack", min: 0, max: 200, step: 1, get: () => Math.round(P().attack * 1000), set: (v) => padSet({ attack: v / 1000 }), fmt: (v) => `${v} ms` }),
        num("Release", { name: "release", min: 3, max: 800, step: 1, get: () => Math.round(P().release * 1000), set: (v) => padSet({ release: v / 1000 }), fmt: (v) => `${v} ms` }),
        bool("Reverse", { name: "reverse", get: () => P().reverse, set: (v) => padSet({ reverse: v }) }),
        en("Mode", { name: "padmode", get: () => P().mode ?? "", set: (v) => padSet({ mode: v || null }),
          opts: [{ v: "", l: "global" }, { v: "oneshot", l: "1-shot" }, { v: "gate", l: "gate" }, { v: "playthrough", l: "thru" }] }),
      ] },
      { name: "slice", info: { cls: "insp-title", html: padInfo }, fields: () => [
        act("Grab", { cls: "grab", val: "playhead", disabled: () => !!engine.getSlice(st().bank, st().selected)?.kept, fn: () => {
          const { bank, selected, chops } = st();
          const t = session.position(), c = engine.getSlice(bank, selected);
          const len = c ? c.end - c.start : 60 / chops.bpm;
          dispatch({ type: "slice.set", bank, pad: selected, start: t, end: Math.min(meta.duration_s, t + len) });
        } }),
        act("Reset slice", { cls: "unslice", fn: () => dispatch({ type: "slice.clear", bank: st().bank, pad: st().selected }) }),
        act("Copy all", { cls: "all", val: "sound → 16", fn: () => dispatch({ type: "pad.copyToAll", pad: st().selected }) }),
        act("Reset snd", { cls: "resetpad", fn: () => dispatch({ type: "pad.reset", pad: st().selected, bank: st().bank }) }),
        blank(), blank(),
      ] },
      { name: "global", info: { html: () => "APPLIES TO ALL PADS UNLESS A PAD HAS ITS OWN MODE" }, fields: () => [
        en("Mode", { name: "mode", cls: "mode-global", get: () => st().settings.mode, set: (v) => setting({ mode: v }),
          opts: [{ v: "oneshot", l: "one-shot" }, { v: "gate", l: "gate" }, { v: "playthrough", l: "play-thru" }] }),
        bool("Choke", { name: "choke", get: () => st().settings.choke, set: (v) => setting({ choke: v }) }),
        bool("Full lvl", { name: "full", get: () => st().settings.fullLevel, set: (v) => setting({ fullLevel: v }) }),
        blank(), blank(), blank(),
      ] },
      { name: "stretch", cls: "stretch", info: { cls: "st-out", html: () => ctx.out.st || "SAME PITCH, NEW LENGTH (RUBBERBAND)" }, fields: () => [
        inp("Fit to BPM", { name: "stbpm", cls: "st-bpm", min: 30, max: 300, step: 0.1, get: () => stBpm(), set: (v) => { ctx.stretchBpm = v; }, fmt: (v) => v.toFixed(1) }),
        act("Pad", { cls: "st-pad", fn: stretchWith(() => session.stretchPad(st().bank, st().selected, { target_bpm: stBpm() })) }),
        act("Bank", { cls: "st-bank", fn: stretchWith(() => session.stretchBank({ target_bpm: stBpm() })) }),
        act("Pattern", { cls: "st-pattern", fn: stretchWith(() => session.stretchPattern({ target_bpm: stBpm() })) }),
        act("Reset pad", { cls: "st-clear", fn: () => session.clearStretch(st().bank, st().selected) }),
        blank(),
      ] },
    ] },

    FX: { title: "MASTER FX", cls: "fx", subs: [
      { name: "tone", fields: () => [
        num("Tone", { name: "tone", min: -1, max: 1, step: 0.01, get: () => st().project.fx.tone, set: (v) => ctx.fxSet({ tone: v }), fmt: fxText.tone }),
        num("Reverb", { name: "reverb", min: 0, max: 1, step: 0.01, get: () => st().project.fx.reverb, set: (v) => ctx.fxSet({ reverb: v }), fmt: fxText.reverb }),
        num("Size", { name: "size", min: 0, max: 1, step: 0.01, get: () => st().project.fx.size, set: (v) => ctx.fxSet({ size: v }), fmt: fxText.size }),
        num("Filter", { name: "filter", min: -1, max: 1, step: 0.01, get: () => st().project.fx.filter, set: (v) => ctx.fxSet({ filter: v }), fmt: fxText.filter }),
        en("Preset", { name: "preset", cls: "fx-presets", optAttr: "data-preset", opts: Object.keys(PRESETS),
          get: () => Object.entries(PRESETS).find(([, pr]) => Object.keys(DEFAULT_FX).every((k) => Math.abs(pr[k] - st().project.fx[k]) < 1e-6))?.[0] ?? null,
          set: (v) => ctx.fxSet(PRESETS[v]) }),
        act("Reset", { fn: () => ctx.fxSet(PRESETS.dry) }),
      ] },
      { name: "eq", info: { html: () => "THREE-BAND EQ · EFFECTS SHAPE EVERYTHING YOU HEAR, THE .WAV RENDER INCLUDED" }, fields: () => [
        fxNum("low", "Low", -12, 12, 0.5, (v) => sgn(v, " dB")), fxNum("mid", "Mid", -12, 12, 0.5, (v) => sgn(v, " dB")), fxNum("high", "High", -12, 12, 0.5, (v) => sgn(v, " dB")),
        blank(), blank(), blank(),
      ] },
    ] },

    SEQ: { title: "SEQUENCER", cls: "seq", subs: [
      { name: "loop", fields: () => [
        bool("Rec", { name: "rec", get: () => seq.state.recording, set: () => session.toggleRecord() }),
        bool("Pattern", { name: "pattern", get: () => seq.state.playing, set: () => session.togglePattern() }),
        bool("Click", { name: "metro", cls: "metro", get: () => st().settings.metronome, set: (v) => setting({ metronome: v }) }),
        en("Bars", { name: "bars", cls: "seq-bars", opts: [1, 2, 4], get: () => st().project.pattern.bars,
          set: (v) => dispatch({ type: "pattern.setBars", bars: Number(v), beatsPerBar: session.patternBeatsPerBar }) }),
        en("Quant", { name: "quant", cls: "seq-q", opts: ["off", "1/8", "1/16"], get: () => st().settings.quant, set: (v) => setting({ quant: v }) }),
        num("Swing", { name: "swing", cls: "seq-swing", min: 0, max: 0.5, step: 0.05, get: () => st().settings.swing, set: (v) => setting({ swing: v }), fmt: (v) => `${Math.round(v * 100)}%` }),
      ] },
      { name: "tools", info: { html: () => `${st().project.pattern.events.length} HITS · ${session.tempoBpm.toFixed(1)} BPM · REPEAT RETRIGGERS A HELD PAD` }, fields: () => [
        bool("Repeat", { name: "repeat", cls: "repeat", get: () => st().settings.repeat, set: (v) => setting({ repeat: v }) }),
        en("Rate", { name: "rate", cls: "repeat-rate", opts: [{ v: 0.5, l: "1/8" }, { v: 0.25, l: "1/16" }], get: () => st().settings.repeatRate, set: (v) => setting({ repeatRate: Number(v) }) }),
        act("Undo", { fn: () => dispatch({ type: "pattern.undo" }) }),
        act("Clear", { fn: () => dispatch({ type: "pattern.clear" }) }),
        act(".MID", { cls: "seq-midi", fn: () => download(session.patternMidi(), `${base()}-pattern.mid`, "audio/midi") }),
        act(".WAV", { cls: "seq-wav", fn: async () => {
          if (!st().project.pattern.events.length) return;
          out.exp = "rendering…"; ctx.refresh();
          download(await session.renderPatternWav(), `${base()}-pattern.wav`, "audio/wav");
          out.exp = "✓ rendered"; ctx.refresh();
        } }),
      ] },
    ] },

    GRID: { title: "GRID / TEMPO", cls: "grid", subs: [
      { name: "grid", info: { html: () => { const c = st().chops; return c.mode === "transient" ? `TRANSIENT CHOP · ${c.chops.length} SLICES` : `GRID ${c.grid} · ${c.beats_per_bar}/4 · CHOP TEMPO ${c.bpm.toFixed(1)} BPM (THE SONG'S OWN)`; } }, fields: () => [
        en("Grid", { name: "grid", cls: "grid-btns", optClsPrefix: "grid-", opts: ["1/4", "1/8", "1/16"], get: () => (st().chops.mode === "transient" ? null : st().chops.grid), set: (v) => session.regrid({ grid: v }) }),
        en("Meter", { name: "meter", cls: "meter-btns", optClsPrefix: "meter-", opts: ["2/4", "3/4", "4/4"], get: () => `${st().chops.beats_per_bar}/4`, set: (v) => session.regrid({ meter: v }) }),
        act("Tempo ÷2", { fn: () => session.regrid({ mult: 0.5 }) }),
        act("Tempo ×2", { fn: () => session.regrid({ mult: 2 }) }),
        act("Beat −1", { fn: () => session.regrid({ shift: -1 }) }),
        act("Beat +1", { fn: () => session.regrid({ shift: 1 }) }),
      ] },
      { name: "tempo", info: { cls: "tap-info", html: () => (ctx.tapState.text ? `TAPPED: ${esc(ctx.tapState.text)}` : "CHOP TEMPO = THE SONG'S, FOR ITS GRID. TAP FOUR TIMES, THEN APPLY TO RE-ANALYZE THE SONG; YOUR TEMPO IS SET ON GRID 4") }, fields: () => [
        act("Bar1 = last", { fn: () => session.regrid({ bar1: true }) }),
        act("Tap", { cls: "tap", val: () => ctx.tapState.bpm ? `${ctx.tapState.bpm}` : "T", fn: () => tap() }),
        act("Apply", { cls: "tap-apply", disabled: () => !ctx.tapState.bpm, fn: async () => {
          const bpm = ctx.tapState.bpm;
          if (!bpm) return;
          ctx.tapState.text = "re-analyzing…"; ctx.refresh();
          ctx.tapState.text = (await session.reanalyze(bpm)) ? `now ${st().chops.bpm.toFixed(1)} BPM` : "failed";
          ctx.refresh();
        } }),
        inp("Chop tempo", { name: "tempo", min: 40, max: 240, step: 0.1, deferred: true, get: () => st().chops.bpm, set: (v) => session.reanalyze(v), fmt: (v) => v.toFixed(1) }),
        blank(), blank(),
      ] },
      { name: "chop", info: { cls: "sens-out", html: () => (st().chops.mode === "transient" ? `${st().chops.chops.length} SLICES` : "GRID: SLICES FOLLOW THE BEAT · TRANSIENTS: THEY FOLLOW THE HITS") }, fields: () => [
        en("Chop by", { name: "chopmode", cls: "chop-mode", optClsPrefix: "mode-", opts: ["grid", "transient"], get: () => st().chops.mode ?? "grid", set: (v) => session.regrid({ mode: v }) }),
        num("Sens", { name: "sens", cls: "sens", min: 0, max: 1, step: 0.05, deferred: true, get: () => st().chops.sensitivity ?? 0.5, set: (v) => session.regrid({ mode: "transient", sensitivity: v }), fmt: (v) => `${Math.round(v * 100)}%` }),
        blank(), blank(), blank(), blank(),
      ] },
    ] },

    EXPORT: { title: "EXPORT", cls: "export", subs: [
      { name: "bank", info: { cls: "exp-out", html: () => out.exp || "CURRENT BANK, 16 PADS · THE HYDROGEN KIT HAS TONE + REVERB BAKED IN · SFZ STAYS DRY" }, fields: () => [
        act("> Hydrogen", { cls: "exp-h2", fn: doExport("hydrogen") }),
        act("> SFZ", { cls: "exp-sfz", fn: doExport("sfz") }),
        act("> Zip", { cls: "exp-zip", val: "16 WAVs", fn: doExport("zip") }),
        act(".MID", { cls: "seq-midi", fn: () => download(session.patternMidi(), `${base()}-pattern.mid`, "audio/midi") }),
        act(".WAV", { cls: "seq-wav", fn: async () => {
          if (!st().project.pattern.events.length) { out.exp = "pattern is empty"; ctx.refresh(); return; }
          out.exp = "rendering…"; ctx.refresh();
          download(await session.renderPatternWav(), `${base()}-pattern.wav`, "audio/wav");
          out.exp = "✓ rendered"; ctx.refresh();
        } }),
        blank(),
      ] },
      { name: "kit", info: { cls: "kit-out", html: () => out.kit || "SAVES THE CURRENT BANK AS A KIT (ANY SONGS); KITS ARE LISTED IN THE LIBRARY" }, fields: () => [
        txt("Kit name", { name: "kitname", cls: "kit-name", get: () => ctx.kitName ?? "", set: (v) => { ctx.kitName = v; }, placeholder: "kit name" }),
        act("Save bank", { cls: "kit-save", fn: async () => {
          const r = await session.saveBankAsKit((ctx.kitName ?? "").trim());
          out.kit = r.ok ? `✓ saved kit “${esc(r.kit.name)}” (${r.kit.pads.filter(Boolean).length} pads)` : `<span class="error">${esc(r.error)}</span>`;
          ctx.refresh();
        } }),
        blank(), blank(), blank(), blank(),
      ] },
    ] },

    AI: { title: "AI ASSIST", cls: "ai", subs: [
      { name: "ai", info: { cls: "ai-out", html: () => out.ai || "SENDS A 32 KBPS MONO COPY TO AN AUDIO MODEL (≤ $0.05 PER SONG, CACHED) · SECTIONS WORK OFFLINE" }, fields: () => [
        act("Analyze", { cls: "ai-run", fn: runAI }),
        act("Load bank", { cls: "ai-bank", disabled: () => !ctx.aiDone, fn: () => {
          const labels = session.loadAiBank();
          if (labels) { out.ai = `${out.ai || ""} Loaded on bank ${st().bank + 1}: ${labels.map(esc).join(", ")}`; ctx.refresh(); }
        } }),
        blank(), blank(), blank(), blank(),
      ] },
    ] },

    MIDI: { title: "MIDI", cls: "midi", subs: [
      { name: "midi", info: { cls: "midi-out", html: () => `${out.devices || ""}<br>${out.learn || ""}` }, fields: () => [
        act("Learn", { cls: "learn", fn: () => env.input.startLearn() }),
        act("Reset map", { cls: "reset", fn: () => { env.input.resetMap(); out.learn = "DEFAULT MAP (NOTES 36–51)"; ctx.refresh(); } }),
        blank(), blank(), blank(), blank(),
      ] },
    ] },
  };

  // CHOP-12: the second sampler-core slice appends its sub-pages (after the existing ones, whose numbers tests and habits rely on)
  for (const [key, subs] of Object.entries(moreSubpages(ctx))) pages[key].subs.push(...subs);
  // CHOP-13 step C: the drum pad mega banks (MAIN 2: the three names and the banks, PAD 9: kept pad)
  for (const [key, subs] of Object.entries(megaSubpages(ctx))) pages[key].subs.push(...subs);
  return pages;

  // MAIN: title, the big numbers, the bank line and three mini pattern rows. update() is called ~12x/s and on state changes.
  function mainBody() {
    const el = document.createElement("div");
    el.className = "lcd-main";
    const analysis = session.analysis;
    el.innerHTML = `<div class="lcd-title"></div>
      <div class="lcd-line mega-line"></div>
      <div class="lcd-big"><div><small>BPM</small><b class="bpm"></b></div><div><small>METER</small><b class="meter"></b></div>
        <div><small>KEY</small><b class="key"></b></div><div><small>BAR</small><b class="pos">1.1</b></div><div><small>TIME</small><b class="clock">0:00.0</b></div></div>
      <div class="lcd-line bankline"></div>
      <div class="lcd-line statusline"></div>
      <div class="lcd-pat"></div>`;
    const q = (s) => el.querySelector(s);
    const set = (node, v) => { if (node.textContent !== v) node.textContent = v; };
    const bpm = q(".bpm"), meter = q(".meter"), key = q(".key"), pos = q(".pos"), clock = q(".clock"), line = q(".bankline"), status = q(".statusline"), pat = q(".lcd-pat");
    const title = q(".lcd-title"), bpmLabel = q(".lcd-big small"), megaLine = q(".mega-line");
    const update = () => {
      const { chops, bank, selected, project } = st();
      const T = session.tempoBpm, user = project.tempo?.bpm != null;
      set(title, session.source ? meta.title : "Pick a song to chop");   // meta is the open song's, replaced in place when the source changes
      title.classList.toggle("none", !session.source);
      // the three levels: song (above), mega bank and drum pad bank (here)
      const ml = `MEGA BANK ${env.nav.megaInfo(session.mega)?.name ?? "—"} ▸ BANK ${bankName(bank, bankCount(project, chops))}`;
      if (megaLine.textContent !== ml) megaLine.textContent = ml;
      set(bpmLabel, user ? "TEMPO · YOURS" : session.source ? "TEMPO · FOLLOWS SONG" : "TEMPO · DEFAULT");
      set(bpm, T.toFixed(1));
      set(meter, `${chops.beats_per_bar}/4`);
      const k = analysis.key;
      set(key, keyText(k));
      key.title = k ? `key ${k.tonic} ${k.mode} (confidence ${Math.round(k.confidence * 100)}%)` : "musical key";
      const t = session.position();
      set(clock, fmt(t));
      const c = chops.chops.find((x) => x.start <= t && t < x.end);
      set(pos, c ? session.chopLabel(c) : "—");
      const ids = chops.banks[bank] ?? [];
      const a = chops.chops[ids[0]], b = chops.chops[ids.at(-1)];
      const sec = st().sections.find((s) => s.start_s <= t && t < s.end_s);
      const sc = engine.getSlice(bank, selected);
      const text = `BANK ${bank + 1}/${bankCount(project, chops)} · BARS ${a ? a.bar + 1 : "—"}–${b ? b.bar + 1 : "—"}${sec ? " · " + sec.label.toUpperCase() : ""} · PAD ${selected + 1}${sc ? "=" + (sc.custom ? "★" : session.chopLabel(sc)) : ""}${seq.state.recording ? " · REC" : ""}`;
      if (line.textContent !== text) line.textContent = text;
      // what is different from the plain song: project tempo, 16 levels, a chain that is playing
      const bits = [];
      if (session.source && Math.abs(T - chops.bpm) > 0.005) bits.push(`${T.toFixed(1)} BPM (SONG ${chops.bpm.toFixed(1)})`);   // the user's tempo stays through a song change
      const sx = session.sixteen;
      if (sx) bits.push(`16 LEVELS: PAD ${sx.pad + 1} · ${sx.mode.toUpperCase()}`);
      const sg = project.song;
      if (sg?.mode === "song") {
        const cp = ctx.chainPos?.();
        bits.push(cp ? `SONG ▶ ${cp.entry + 1}/${sg.chain.length} ${(sg.patterns.find((x) => x.id === cp.id)?.name ?? "").toUpperCase()} ×${cp.rep + 1}/${sg.chain[cp.entry]?.repeats ?? 1}` : `SONG MODE · ${sg.chain.length} ENTRIES`);
      }
      const stText = bits.join(" · ");
      if (status.textContent !== stText) { status.textContent = stText; status.hidden = !stText; }
      // three busiest pad rows of the pattern
      const n = project.pattern.bars * session.patternBeatsPerBar * 4;
      const counts = new Map();
      for (const e of project.pattern.events) counts.set(e.pad, (counts.get(e.pad) ?? 0) + 1);
      const rows = [...counts.entries()].sort((x, y) => y[1] - x[1]).slice(0, 3).map(([p]) => p);
      for (const p of [0, 1, 2]) if (rows.length < 3 && !rows.includes(p)) rows.push(p);
      const cur = seq.state.playing ? Math.floor(seq.position() * 4) % n : -1;
      const html = rows.map((p) => {
        const on = new Set(project.pattern.events.filter((e) => e.pad === p).map((e) => Math.round(e.beat * 4)));
        let s = String(p + 1).padStart(2, " ") + " ";
        for (let i = 0; i < n; i++) s += i === cur ? "▌" : on.has(i) ? "■" : i % 4 === 0 ? "·" : " ";
        return s;
      }).join("\n");
      if (pat.textContent !== html) pat.textContent = html;
    };
    return { el, update };
  }
}
