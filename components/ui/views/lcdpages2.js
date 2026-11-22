// LCD sub-pages for the second sampler-core slice (CHOP-12), appended to the pages of lcdpages.js by moreSubpages():
//   PAD    5 env · 6 filter · 7 mute · 8 levels      GRID 4 project tempo (+ fit to tempo)     SEQ 3 song · 4 chain
//   EXPORT 3 sample (resample) · 4 DAW bridge        AI   2 stems
// They follow the rules of lcdpages.js: declarative fields, at most six per sub-page. Where a sub-page needs more than a row
// (the ADSR curve, the chain editor, results lists) it gives an `extra` element: { el, update(), destroy?() }.
// Nothing here holds song state: values come from the store / session and changes go out as actions or session calls.
import { SIXTEEN_MODES, STEMS } from "/actions.js";
import { act, bool, blank, download, en, inp, num, txt } from "./lcdpages.js";
import { esc, svg } from "./util.js";

const letter = (b) => String.fromCharCode(65 + (Number(b) % 26));
const logScale = (min, max, round) => ({ fwd: (v) => Math.log(v / min) / Math.log(max / min), inv: (x) => min * Math.pow(max / min, x), round });
export const fmtHz = (v) => (v >= 1000 ? `${(v / 1000).toFixed(v >= 10000 ? 1 : 2)} kHz` : `${Math.round(v)} Hz`);
const roundHz = (v) => (v >= 1000 ? Math.round(v / 10) * 10 : Math.round(v));
const roundQ = (v) => (v < 1 ? Math.round(v * 100) / 100 : Math.round(v * 10) / 10);
const fmtMs = (s) => (s === 0 ? "0 ms" : s < 1 ? `${Math.round(s * 1000)} ms` : `${s.toFixed(2)} s`);
const h = (cls, html = "") => { const el = document.createElement("div"); el.className = cls; el.innerHTML = html; return el; };
const setHtml = (el, html) => { if (el._h !== html) { el._h = html; el.innerHTML = html; } };

export function moreSubpages(ctx) {
  const { session, st, dispatch, out, env } = ctx;
  const { meta, engine, seq } = session;
  const P = () => st().project.pads[st().selected];
  const padSet = (params) => dispatch({ type: "pad.set", pad: st().selected, params });
  const base = () => meta.title.replace(/[^\w-]+/g, "_").slice(0, 40);
  const ov = (bank = st().bank, pad = st().selected) => st().project.overrides[`${bank}:${pad}`];

  // ------------------------------------------------------------------ PAD 5: envelope
  const adsr = () => {
    const el = document.createElement("div");
    el.className = "adsr";
    el.setAttribute("role", "img");
    const s = svg("svg", { viewBox: "0 0 260 44", preserveAspectRatio: "none", class: "adsr-svg" }, el);
    svg("line", { x1: 0, y1: 40, x2: 260, y2: 40, class: "adsr-base" }, s);
    const fill = svg("polygon", { class: "adsr-fill" }, s), line = svg("polyline", { class: "adsr-line" }, s);
    const marks = svg("g", { class: "adsr-marks" }, s);
    let last = "";
    return { el, destroy() {}, update() {
      const { attack: a, decay: d, sustain: u, release: r } = P();
      const key = `${a}|${d}|${u}|${r}`;
      if (key === last) return;
      last = key;
      const wA = 6 + 50 * Math.min(a / 0.2, 1), wD = d > 0 ? 6 + 56 * Math.min(Math.cbrt(d / 10), 1) : 0, wS = 36, wR = 8 + 52 * Math.min(r / 0.8, 1);
      const top = 3, sy = 40 - u * 37;
      let x = 0;
      const pts = [[0, 40], [(x += wA), top], [(x += wD), sy], [(x += wS), sy], [(x += wR), 40]];
      line.setAttribute("points", pts.map((p) => p.join(",")).join(" "));
      fill.setAttribute("points", pts.map((p) => p.join(",")).join(" "));
      marks.innerHTML = "";
      let acc = 0;
      [["A", wA], ["D", wD], ["S", wS], ["R", wR]].forEach(([t, w]) => { if (w > 0) { const tx = svg("text", { x: acc + w / 2, y: 10, class: "adsr-t" }, marks); tx.textContent = t; } acc += w; });
      el.setAttribute("aria-label", `Envelope: attack ${fmtMs(a)}, decay ${d ? fmtMs(d) : "none"}, sustain ${Math.round(u * 100)} percent, release ${fmtMs(r)}`);
    } };
  };
  const env_ = { name: "env", fields: () => [
    num("Attack", { name: "attack", min: 0, max: 200, step: 1, get: () => Math.round(P().attack * 1000), set: (v) => padSet({ attack: v / 1000 }), fmt: (v) => `${v} ms` }),
    num("Decay", { name: "decay", min: 0, max: 10, step: 0.01, scale: { fwd: (v) => Math.cbrt(v / 10), inv: (x) => x ** 3 * 10, round: (v) => Math.round(v * 100) / 100 },
      get: () => P().decay ?? 0, set: (v) => padSet({ decay: v }), fmt: (v) => (v === 0 ? "off" : fmtMs(v)) }),
    num("Sustain", { name: "sustain", min: 0, max: 1, step: 0.01, get: () => P().sustain ?? 1, set: (v) => padSet({ sustain: v }), fmt: (v) => `${Math.round(v * 100)}%` }),
    num("Release", { name: "release", min: 3, max: 800, step: 1, get: () => Math.round(P().release * 1000), set: (v) => padSet({ release: v / 1000 }), fmt: (v) => `${v} ms` }),
    blank(), blank(),
  ], cls: "cols2 env", extra: adsr, info: { html: () => `PAD ${st().selected + 1} · DECAY 0 = NO DECAY STAGE · SUSTAIN 100% = FULL LEVEL UNTIL RELEASE` } };

  // ------------------------------------------------------------------ PAD 6: filter
  const filt = () => P().filter ?? { type: "off", cutoff: 8000, q: 0.7 };
  const filtSet = (patch) => padSet({ filter: { ...filt(), ...patch } });
  const filter = { name: "filter", fields: () => [
    en("Type", { name: "ftype", cls: "f-type", optAttr: "data-ftype", opts: [{ v: "off", l: "off" }, { v: "lp", l: "LP" }, { v: "hp", l: "HP" }, { v: "bp", l: "BP" }], get: () => filt().type, set: (v) => filtSet({ type: v }) }),
    num("Cutoff", { name: "cutoff", cls: "f-cutoff", min: 20, max: 20000, step: 1, scale: logScale(20, 20000, roundHz), wheel: 14, get: () => filt().cutoff, set: (v) => filtSet({ cutoff: v }), fmt: fmtHz }),
    num("Q", { name: "q", cls: "f-q", min: 0.1, max: 24, step: 0.1, scale: logScale(0.1, 24, roundQ), wheel: 14, get: () => filt().q, set: (v) => filtSet({ q: v }), fmt: (v) => v.toFixed(v < 1 ? 2 : 1) }),
    blank(), blank(), blank(),
  ], info: { html: () => `PAD ${st().selected + 1} FILTER · ${{ off: "OFF (BYPASS)", lp: "LOW-PASS: KEEPS WHAT IS BELOW THE CUTOFF", hp: "HIGH-PASS: KEEPS WHAT IS ABOVE IT", bp: "BAND-PASS: KEEPS A BAND AROUND IT" }[filt().type]} · Q = PEAK WIDTH` } };

  // ------------------------------------------------------------------ PAD 7: mute group
  const mute = { name: "mute", fields: () => [
    num("Mute group", { name: "muteGroup", cls: "mg", min: 0, max: 8, step: 1, get: () => P().muteGroup ?? 0, set: (v) => padSet({ muteGroup: Math.round(v) }), fmt: (v) => (v ? `group ${v}` : "none") }),
    bool("Choke", { name: "choke", cls: "mg-choke", get: () => st().settings.choke, set: (v) => ctx.setting({ choke: v }) }),
    blank(), blank(), blank(), blank(),
  ], info: { cls: "mg-info", html: () => (st().settings.choke
    ? "GLOBAL CHOKE IS ON: EVERY PAD CUTS EVERY PAD, SO MUTE GROUPS DO NOTHING. SWITCH CHOKE OFF TO USE THEM."
    : "GLOBAL CHOKE IS OFF: PADS IN THE SAME GROUP CUT EACH OTHER (E.G. OPEN AND CLOSED HI-HAT). 0 = NO GROUP.") } };

  // ------------------------------------------------------------------ PAD 8: 16 levels
  const lvl = env.levelMode;
  const sixteen = () => session.sixteen;
  const lvSource = () => sixteen()?.pad ?? st().selected;
  const levels = { name: "levels", fields: () => [
    en("Mode", { name: "lvmode", cls: "lv-mode", optAttr: "data-lv", opts: [...SIXTEEN_MODES, "off"], get: () => sixteen()?.mode ?? "off",
      set: (v) => { if (v === "off") session.setSixteenLevels({ mode: "off" }); else { lvl.set(v); session.setSixteenLevels({ pad: lvSource(), mode: v }); } } }),
    num("Source pad", { name: "lvsrc", cls: "lv-src", min: 1, max: 16, step: 1, get: () => lvSource() + 1,
      set: (v) => { if (sixteen()) session.setSixteenLevels({ pad: v - 1, mode: sixteen().mode }); else dispatch({ type: "pad.select", pad: v - 1 }); } }),
    blank(), blank(), blank(), blank(),
  ], info: { cls: "lv-info", html: () => {
    const x = sixteen();
    return x ? `16 LEVELS ON · SOURCE PAD ${x.pad + 1} · ${x.mode.toUpperCase()} · THE 16 PADS PLAY THAT ONE PAD AT 16 ${{ velocity: "LOUDNESSES", pitch: "PITCHES", filter: "FILTER CUTOFFS" }[x.mode]}`
      : "16 LEVELS IS OFF. PRESS THE 16 LEVELS KEY BESIDE THE PADS (SOURCE = THE SELECTED PAD) OR PICK A MODE HERE.";
  } } };

  // ------------------------------------------------------------------ GRID 4: project tempo, fit to tempo
  const tempoDoc = () => st().project.tempo ?? { bpm: null, follow: true };
  const fittedKeys = () => Object.entries(st().project.overrides).filter(([, o]) => o.stretch && o.stretch.fit !== undefined).map(([k]) => k);
  const runFit = (fn) => async () => { out.fit = { busy: true }; ctx.refresh(); let r = null; try { r = await fn(); } catch (e) { r = { ok: false, error: String(e?.message ?? e) }; } out.fit = { r }; ctx.refresh(); };
  const fitList = () => {
    const el = h("fit-out"); el.tabIndex = 0; el.setAttribute("role", "region"); el.setAttribute("aria-label", "Fit result");
    return { el, update() {
      const f = out.fit;
      let html;
      if (!f) html = `<span class="dim">RESULT OF FIT TO TEMPO SHOWS HERE</span>`;
      else if (f.busy) html = "FITTING PADS TO THE TEMPO (RUBBERBAND)…";
      else {
        const r = f.r ?? {}, bad = (r.fitted ?? []).filter((x) => !x.ok), good = (r.fitted ?? []).length - bad.length;
        html = r.error ? `<span class="error">${esc(r.error)}</span>`
          : `<b class="fit-n">✓ ${good} PAD${good === 1 ? "" : "S"} FITTED</b>${r.tempo ? ` TO ${r.tempo.toFixed(1)} BPM` : r.cleared ? ` · ${r.cleared} BACK TO PLAIN SLICES` : ""} · <b class="skip-n">${(r.skipped ?? []).length} SKIPPED</b>` +
            bad.map((x) => `<div class="fit-bad">BANK ${x.bank + 1} PAD ${x.pad + 1} — ${esc(x.error)}</div>`).join("") +
            (r.skipped ?? []).map((x) => `<div class="fit-skip">BANK ${x.bank + 1} PAD ${x.pad + 1} — ${esc(x.reason)}</div>`).join("");
      }
      setHtml(el, html);
    } };
  };
  const ptempo = { name: "project tempo", cls: "cols2 ptempo", fields: () => [
    inp("Tempo", { name: "ptempo", cls: "pt-bpm", min: 20, max: 400, step: 0.1, deferred: true, get: () => session.tempoBpm, fmt: (v) => v.toFixed(1),
      set: (v) => runFit(() => session.setTempo(v))() }),
    act("Song tempo", { cls: "pt-song", disabled: () => tempoDoc().bpm === null, fn: runFit(() => session.setTempo(null)) }),
    bool("Follow", { name: "ptfollow", cls: "pt-follow", get: () => tempoDoc().follow !== false, set: (v) => dispatch({ type: "tempo.set", follow: v }) }),
    act("Refit all", { cls: "pt-refit", val: () => `${fittedKeys().length} fitted`, fn: runFit(() => session.refitAll()) }),
    act("Fit pattern", { cls: "pt-fit-pattern", disabled: () => !tempoDoc().bpm, fn: runFit(() => session.fitToTempo({ scope: "pattern" })) }),
    act("Fit bank", { cls: "pt-fit-bank", disabled: () => !tempoDoc().bpm, fn: runFit(() => session.fitToTempo({ scope: "bank" })) }),
  ], extra: fitList, info: { cls: "pt-info", html: () => {
    const T = tempoDoc().bpm, bpm = st().chops.bpm;
    return T === null ? `PROJECT TEMPO = THE SONG'S OWN (${bpm.toFixed(1)} BPM). TYPE A TEMPO TO PLAY, RECORD AND EXPORT AT IT, THEN FIT PADS TO IT.`
      : `PROJECT ${T.toFixed(1)} BPM (SONG ${bpm.toFixed(1)}) · FITTED PADS ARE MARKED “FIT” · HAND-STRETCHED PADS ARE NEVER TOUCHED`;
  } } };

  // ------------------------------------------------------------------ SEQ 3 / 4: song mode
  const song = () => st().project.song;
  const pats = () => song()?.patterns ?? [];
  const curId = () => song()?.current ?? null;
  const chain = () => song()?.chain ?? [];
  const ensureSong = () => {
    if (song()?.patterns.length) return;
    const pt = st().project.pattern;
    dispatch({ type: "song.load", song: { patterns: [{ id: "p1", name: "Pattern 1", bars: pt.bars, events: pt.events }], chain: [], mode: "pattern", current: "p1" } });
  };
  /** Where a playing chain is: { entry (index in the chain), rep (0-based repeat), id, bar }, or null. */
  const chainPos = () => {
    if (!seq.state.song || !seq.state.playing) return null;
    const w = session.songPosition(), segs = seq.state.song.segments, c = chain();
    let k = 0, entry = 0, rep = 0;
    for (let i = 0; i < c.length; i++) { const n = c[i].repeats; if (w.index < k + n) { entry = i; rep = w.index - k; break; } k += n; entry = i; }
    return { entry, rep, id: w.id, bar: 1 + Math.floor(w.inPattern / (st().chops.beats_per_bar)), segs: segs.length, index: w.index };
  };
  ctx.chainPos = chainPos;
  const nameOf = (id) => pats().find((p) => p.id === id)?.name ?? id;
  const patternList = () => {
    const el = h("pat-list"); el.setAttribute("role", "group"); el.setAttribute("aria-label", "Patterns: choose the one edited in the step grid");
    let sig = "";
    return { el, update() {
      const list = pats(), cur = curId(), pos = chainPos();
      const s = JSON.stringify([list.map((p) => [p.id, p.name, p.bars, p.events.length]), cur, st().project.pattern.events.length, st().project.pattern.bars]);
      if (s !== sig) {
        sig = s;
        el.innerHTML = "";
        const shown = list.length ? list : [{ id: null, name: "Pattern 1", bars: st().project.pattern.bars, events: st().project.pattern.events }];
        shown.forEach((p, i) => {
          const b = document.createElement("button");
          b.type = "button"; b.className = "pat"; b.dataset.id = p.id ?? ""; b.dataset.n = i + 1;
          const live = p.id === cur || p.id === null;
          const events = live ? st().project.pattern.events.length : p.events.length;
          b.innerHTML = `<b>${i + 1}</b> ${esc(p.name)} <small>${p.bars}b·${events}</small>`;
          b.setAttribute("aria-label", `Pattern ${i + 1}, ${p.name}, ${p.bars} bars, ${events} hits${live ? ", being edited" : ""}`);
          b.setAttribute("aria-pressed", String(live));
          b.classList.toggle("cur", live);
          if (p.id !== null) b.addEventListener("click", () => dispatch({ type: "song.selectPattern", id: p.id }));
          el.append(b);
        });
      }
      for (const b of el.children) b.classList.toggle("playing", !!pos && b.dataset.id === pos.id);
    } };
  };
  const curPattern = () => pats().find((p) => p.id === curId());
  const songPage = { name: "song", cls: "cols2 song", fields: () => [
    en("Mode", { name: "songmode", cls: "song-mode", optAttr: "data-sm", opts: [{ v: "pattern", l: "pattern" }, { v: "song", l: "song" }], get: () => song()?.mode ?? "pattern",
      set: (v) => { if (v === "song") ensureSong(); dispatch({ type: "song.setMode", mode: v }); } }),
    act("New", { cls: "song-new", val: "+", fn: () => dispatch({ type: "song.addPattern" }) }),
    txt("Name", { name: "patname", cls: "pat-name", placeholder: "pattern name", get: () => curPattern()?.name ?? (song() ? "" : "Pattern 1"),
      set: (v) => { if (curId() && v.trim()) dispatch({ type: "song.renamePattern", id: curId(), name: v }); } }),
    act("Copy", { cls: "song-copy", val: "⧉", fn: () => dispatch({ type: "song.addPattern", copy: true, name: `${curPattern()?.name ?? "Pattern 1"} copy`.slice(0, 40) }) }),
    num("Edit", { name: "songedit", cls: "song-edit", min: 1, max: Math.max(1, pats().length), step: 1, get: () => Math.max(1, pats().findIndex((p) => p.id === curId()) + 1),
      set: (v) => { const p = pats()[Math.round(v) - 1]; if (p) dispatch({ type: "song.selectPattern", id: p.id }); }, fmt: (v) => `${v}/${Math.max(1, pats().length)}` }),
    act("Remove", { cls: "song-remove", val: "×", disabled: () => pats().length < 2 || !curId(), fn: () => dispatch({ type: "song.removePattern", id: curId() }) }),
  ], extra: patternList, info: { cls: "song-info", html: () => {
    const pos = chainPos();
    return pos ? `▶ PLAYING CHAIN ENTRY ${pos.entry + 1}/${chain().length} · ${esc(nameOf(pos.id)).toUpperCase()} · REPEAT ${pos.rep + 1}/${chain()[pos.entry]?.repeats ?? 1}`
      : "BLUE = THE PATTERN IN THE STEP GRID · SONG MODE PLAYS THE CHAIN";
  } } };

  const chainEditor = () => {
    const el = h("chain"); el.setAttribute("role", "group"); el.setAttribute("aria-label", "Chain editor");
    let sig = "";
    const set = (c) => dispatch({ type: "song.setChain", chain: c });
    const mk = (cls, text, label, fn, dis = false) => { const b = document.createElement("button"); b.type = "button"; b.className = cls; b.textContent = text; b.setAttribute("aria-label", label); b.disabled = dis; b.addEventListener("click", fn); return b; };
    return { el, update() {
      const c = chain(), list = pats(), pos = chainPos();
      const s = JSON.stringify([c, list.map((p) => [p.id, p.name])]);
      if (s !== sig) {
        sig = s;
        const keep = el.contains(document.activeElement) ? document.activeElement.dataset.k : null;
        el.innerHTML = "";
        if (!c.length) el.append(h("dim chain-empty", list.length ? "CHAIN IS EMPTY — ADD AN ENTRY (F1)" : "NO PATTERNS YET — MAKE ONE ON THE SONG PAGE, THEN CHAIN IT HERE"));
        c.forEach((e, i) => {
          const row = h("chain-row"); row.dataset.i = i;
          const idx = list.findIndex((p) => p.id === e.pattern);
          row.append(h("mk", "▶"), h("no", String(i + 1)));
          const prev = mk("cr-prev", "◂", `Entry ${i + 1}: previous pattern`, () => set(c.map((x, k) => (k === i ? { ...x, pattern: list[(idx + list.length - 1) % list.length].id } : x))), list.length < 2);
          const next = mk("cr-next", "▸", `Entry ${i + 1}: next pattern`, () => set(c.map((x, k) => (k === i ? { ...x, pattern: list[(idx + 1) % list.length].id } : x))), list.length < 2);
          const nm = h("nm", esc(nameOf(e.pattern)));
          const rep = document.createElement("input");
          rep.type = "number"; rep.min = 1; rep.max = 64; rep.step = 1; rep.value = e.repeats; rep.className = "cr-rep"; rep.dataset.k = `rep${i}`;
          rep.setAttribute("aria-label", `Entry ${i + 1}: repeats, 1 to 64`);
          rep.addEventListener("change", () => { const v = Math.max(1, Math.min(64, Math.round(Number(rep.value)) || 1)); set(c.map((x, k) => (k === i ? { ...x, repeats: v } : x))); });
          const up = mk("cr-up", "↑", `Entry ${i + 1}: move up`, () => { const n = [...c]; [n[i - 1], n[i]] = [n[i], n[i - 1]]; set(n); }, i === 0);
          const dn = mk("cr-down", "↓", `Entry ${i + 1}: move down`, () => { const n = [...c]; [n[i + 1], n[i]] = [n[i], n[i + 1]]; set(n); }, i === c.length - 1);
          const rm = mk("cr-del", "×", `Entry ${i + 1}: remove`, () => set(c.filter((_, k) => k !== i)));
          for (const b of [prev, next, up, dn, rm]) b.dataset.k = `${b.className}${i}`;
          row.append(prev, nm, next, h("x", "×"), rep, up, dn, rm);
          el.append(row);
        });
        if (keep) el.querySelector(`[data-k="${keep}"]`)?.focus();
      }
      el.querySelectorAll(".chain-row").forEach((r) => { const on = !!pos && Number(r.dataset.i) === pos.entry; r.classList.toggle("playing", on); r.dataset.rep = on ? pos.rep + 1 : ""; });
    } };
  };
  const wavSong = async () => {
    out.song = "RENDERING THE WHOLE CHAIN…"; ctx.refresh();
    const w = await session.renderSongWav();
    if (!w) { out.song = "NOTHING TO RENDER: THE PATTERNS HAVE NO HITS"; ctx.refresh(); return; }
    download(w, `${base()}-song.wav`, "audio/wav");
    out.song = "✓ SONG .WAV RENDERED"; ctx.refresh();
  };
  const midSong = () => { download(session.songMidi(), `${base()}-song.mid`, "audio/midi"); out.song = "✓ SONG .MID SAVED"; ctx.refresh(); };
  const chainPage = { name: "chain", cls: "cols2 chainpage", fields: () => [
    act("Add entry", { cls: "chain-add", val: "+", fn: () => {
      ensureSong();
      const c = chain(), last = c.at(-1)?.pattern, cur = curId() ?? pats()[0]?.id;
      if (cur) dispatch({ type: "song.setChain", chain: [...c, { pattern: last ?? cur, repeats: 1 }] });
    } }),
    en("Mode", { name: "songmode2", cls: "song-mode", optAttr: "data-sm", opts: [{ v: "pattern", l: "pattern" }, { v: "song", l: "song" }], get: () => song()?.mode ?? "pattern",
      set: (v) => { if (v === "song") ensureSong(); dispatch({ type: "song.setMode", mode: v }); } }),
    act("Song .WAV", { cls: "song-wav", fn: wavSong }),
    act("Song .MID", { cls: "song-mid", fn: midSong }),
    blank(), blank(),
  ], extra: chainEditor, info: { cls: "chain-info", html: () => {
    const pos = chainPos();
    return pos ? `▶ ENTRY ${pos.entry + 1}/${chain().length} · ${esc(nameOf(pos.id)).toUpperCase()} · REPEAT ${pos.rep + 1}/${chain()[pos.entry]?.repeats ?? 1} · BAR ${pos.bar}` : out.song ?? "THE CHAIN IS THE PLAYING ORDER · RECORDING IS OFF WHILE IT PLAYS";
  } } };

  // ------------------------------------------------------------------ EXPORT 3: resample
  const RS = (ctx.rs ??= { source: "pattern", unit: "bars", len: 2, pad: null, job: null, res: null });
  const rsPad = () => RS.pad ?? st().selected;
  const rsExtra = () => {
    const el = h("rs-out"); el.innerHTML = `<div class="bar"><i></i></div><div class="rs-text"></div>`;
    const bar = el.querySelector(".bar i"), text = el.querySelector(".rs-text");
    return { el, update() {
      const j = RS.job;
      let pct = 0, html;
      if (j) {
        if (j.source === "master") { const t = (performance.now() - j.t0) / 1000; pct = Math.min(1, t / j.secs); html = `<b class="rs-rec">● RECORDING…</b> ${Math.min(t, j.secs).toFixed(1)} / ${j.secs.toFixed(1)} S, REAL TIME: THE MASTER BUS (PATTERN PLAYS ALONG)`; }
        else { pct = 0.5; html = "RENDERING THE PATTERN OFFLINE…"; }
      } else if (RS.res) {
        const r = RS.res;
        pct = r.ok ? 1 : 0;
        html = r.ok ? `<b class="rs-done">✓ PAD ${r.pad + 1} (BANK ${r.bank + 1}) NOW PLAYS A ${r.duration.toFixed(2)} S RESAMPLE</b> <a class="rs-link" href="${esc(r.url)}" download>.wav</a>` : `<span class="error">${esc(r.error)}</span>`;
      } else html = `<span class="dim">PRESS GO · THE RESULT REPLACES THE TARGET PAD (MARKED “RS”)</span>`;
      bar.style.width = `${Math.round(pct * 100)}%`;
      setHtml(text, html);
    } };
  };
  const sample = { name: "sample", cls: "cols2 sample", fields: () => [
    en("Source", { name: "rssrc", cls: "rs-source", optAttr: "data-rs", opts: [{ v: "pattern", l: "pattern" }, { v: "master", l: "master" }], get: () => RS.source, set: (v) => { RS.source = v; } }),
    en("Length in", { name: "rsunit", cls: "rs-unit", optAttr: "data-ru", opts: [{ v: "bars", l: "bars" }, { v: "seconds", l: "sec" }], get: () => RS.unit, set: (v) => { RS.unit = v; RS.len = v === "bars" ? 2 : 2; } }),
    inp("Length", { name: "rslen", cls: "rs-len", min: 0.1, max: 300, step: 0.25, get: () => RS.len, set: (v) => { RS.len = v; }, fmt: (v) => v.toFixed(2) }),
    num("Target pad", { name: "rspad", cls: "rs-pad", min: 1, max: 16, step: 1, get: () => rsPad() + 1, set: (v) => { RS.pad = Math.round(v) - 1; } }),
    act("GO", { cls: "rs-go", val: () => (RS.job ? "…" : "▸"), disabled: () => !!RS.job, fn: async () => {
      const bpm = session.tempoBpm, bpb = st().chops.beats_per_bar;
      const secs = RS.unit === "bars" ? (RS.len * bpb * 60) / bpm : RS.len;
      RS.res = null; RS.job = { source: RS.source, t0: performance.now(), secs }; ctx.refresh();
      const pad = rsPad();
      const r = await session.resample({ source: RS.source, [RS.unit]: RS.len, pad });
      RS.job = null; RS.res = r.ok ? r : { ok: false, error: r.error }; ctx.refresh();
    } }),
    blank(),
  ], extra: rsExtra, info: { html: () => "PATTERN = INSTANT OFFLINE RENDER · MASTER = LIVE RECORDING, REAL TIME" } };

  // ------------------------------------------------------------------ EXPORT 4: DAW bridge
  const bridgeText = () => { const d = session.remote.daw; return d.open ? `→ ${(d.daw ?? "DAW").toUpperCase()} (BANK ${letter(d.bank ?? 0)})` : "NO BRIDGE OPEN"; };
  const dawRun = (command, label) => async () => {
    out.daw = `${label}…`; ctx.refresh();
    const r = await session.dawCommand("hydrogen", command);
    out.daw = r.ok ? `✓ ${command.toUpperCase()}${r.bpm ? ` · ${Number(r.bpm).toFixed(1)} BPM` : ""}${r.detail ? "" : ""}` : `<span class="error">${esc(r.error || r.not_implemented || "failed")}</span>`;
    ctx.refresh();
  };
  const dawPage = { name: "DAW", cls: "daw", fields: () => [
    act("> Hydrogen", { cls: "daw-open", val: "▸", fn: dawRun("open", "opening Hydrogen") }),
    act("Sync", { cls: "daw-sync", disabled: () => !session.remote.daw.open, fn: dawRun("sync", "syncing") }),
    act("Stop", { cls: "daw-stop", val: "■", disabled: () => !session.remote.daw.open, fn: dawRun("stop", "stopping") }),
    blank(), blank(), blank(),
  ], info: { cls: "daw-out", html: () => `BRIDGE: ${bridgeText()}${out.daw ? `<br>${out.daw}` : "<br>OPENS THE CURRENT BANK AS A KIT AND THE PATTERN IN HYDROGEN; SYNC PUSHES YOUR EDITS. THERE IS NO CLOSE: QUIT HYDROGEN ITSELF."}` } };

  // ------------------------------------------------------------------ AI 2: stems
  const stemsList = () => (meta.stems?.length ? meta.stems : null);
  const stemsPage = { name: "stems", cls: "stems", fields: () => [
    act(() => (stemsList() ? "Redo" : "Separate"), { cls: "stems-run", disabled: () => !!out.stemBusy, fn: async () => {
      out.stemBusy = true; out.stem = null; ctx.refresh();
      const r = await session.separateStems({ force: !!stemsList() });
      out.stemBusy = false; out.stem = r; ctx.refresh();
    } }),
    en("Pad stem", { name: "padstem", cls: "pad-stem", optAttr: "data-stem", opts: [{ v: "", l: "mix" }, ...STEMS.map((s) => ({ v: s, l: s }))], get: () => ov()?.stem ?? "",
      set: async (v) => {
        if (v && !stemsList()) { out.stem = { ok: false, soft: true, error: "Separate the song first, then pick a stem for the pad." }; ctx.refresh(); return; }
        if (!engine.getSlice(st().bank, st().selected)) { out.stem = { ok: false, soft: true, error: `Pad ${st().selected + 1} is empty.` }; ctx.refresh(); return; }
        out.stem = null; await session.setStem(st().bank, st().selected, v || null); ctx.refresh();
      } }),
    blank(), blank(), blank(), blank(),
  ], extra: () => {
    const el = h("stem-out");
    return { el, update() {
      const r = out.stem;
      let html;
      if (out.stemBusy) html = "SEPARATING STEMS… THIS CAN TAKE MINUTES; IT IS CACHED AFTER THE FIRST TIME.";
      else if (r && !r.ok && r.status === 501) html = `<b class="stem-none">NO STEMS BACKEND IS SET UP.</b> THE SERVER NEEDS <code>CHOPPAD_STEMS_BACKEND=remote</code> AND <code>CHOPPAD_STEMS_URL=…</code> (OPTIONAL <code>CHOPPAD_STEMS_KEY</code>) IN ITS ENVIRONMENT; RESTART CHOPPAD, THEN TRY AGAIN.`;
      else if (r && !r.ok) html = `<span class="error">${esc(r.error)}</span>`;
      else if (stemsList()) html = `<b>STEMS READY:</b> ${stemsList().join(" · ").toUpperCase()}. PAD ${st().selected + 1} IS SELECTED: PICK A STEM FOR IT (MARKED ON THE PAD); “MIX” PUTS IT BACK.`;
      else html = "THE SONG IS NOT SEPARATED YET. “SEPARATE STEMS” SPLITS IT INTO DRUMS, BASS, VOCALS AND OTHER (NEEDS A STEMS BACKEND).";
      setHtml(el, html);
    } };
  }, info: { html: () => `STEMS: ${stemsList() ? stemsList().join(" · ").toUpperCase() : "NOT SEPARATED"}` } };

  return { PAD: [env_, filter, mute, levels], GRID: [ptempo], SEQ: [songPage, chainPage], EXPORT: [sample, dawPage], AI: [stemsPage] };
}
