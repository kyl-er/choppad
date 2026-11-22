// session: one open song. Owns the store, the audio engine, the sequencer, the song transport and
// everything that talks to the server. It has no layout and no styling: a view (ui.js today) reads
// `session.store`, calls the methods below and listens for "hit", "flash" and "song" events.
// Anything that changes saved state goes through `session.store.dispatch(action)` (see actions.js).
import { createEngine } from "./engine.js";
import { createSequencer, encodeWav, patternToMidi } from "./sequencer.js";
import { createFx } from "./fx.js";
import { createStore } from "./store.js";
import { PADS, initialState, projectDoc, reduce } from "./actions.js";

const STEPS = { "1/4": 1, "1/8": 2, "1/16": 4 };
const MULTS = [0.5, 1, 2];
const SAVE_DELAY_MS = 300;

const postJson = (url, body = {}) => fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

/** Load a song and return its session. Throws if the song can't be loaded. `onProgress(text)` is optional. */
export async function openSong({ id, audio, onProgress = () => {} }) {
  const { ctx, master, fx } = audio;
  onProgress("Loading song…");
  const res = await fetch(`/api/songs/${id}`);
  if (!res.ok) throw new Error(`song ${id}: HTTP ${res.status}`);
  const data = await res.json();
  const { meta, analysis, peaks } = data;
  onProgress("Decoding audio…");
  const buffer = await fetch(`/media/${id}/audio.wav`).then((r) => r.arrayBuffer()).then((b) => ctx.decodeAudioData(b));

  const video = document.createElement("video");
  video.className = "video";
  video.muted = true;
  video.playsInline = true;
  video.preload = "auto";
  video.src = `/media/${id}/video.mp4`;

  // W3: a pad can play a time-stretched rendering of its slice, or a slice of another song (kits). Buffers are fetched and
  // decoded once (cached by url) and handed to the engine; none of this happens on the trigger path.
  const decoded = new Map();   // url -> AudioBuffer
  const loading = new Map();   // url -> Promise<AudioBuffer>
  const pending = new Set();   // promises of in-flight pad-source loads
  const loadBuffer = (url) => {
    if (decoded.has(url)) return Promise.resolve(decoded.get(url));
    if (!loading.has(url)) {
      loading.set(url, fetch(url).then((r) => { if (!r.ok) throw new Error(`${url}: HTTP ${r.status}`); return r.arrayBuffer(); })
        .then((b) => ctx.decodeAudioData(b)).then((buf) => { decoded.set(url, buf); return buf; })
        .finally(() => loading.delete(url)));
    }
    return loading.get(url);
  };
  decoded.set(`/media/${id}/audio.wav`, buffer);
  const events = new EventTarget();
  const emit = (type, detail) => events.dispatchEvent(new CustomEvent(type, { detail }));
  const store = createStore(reduce, initialState({ project: data.project, chops: data.chops }));
  const engine = createEngine({ ctx, buffer, chops: data.chops, video, output: master });

  // ---- sequencer ----
  function clickAt(when, accent) {
    const o = ctx.createOscillator(), g = ctx.createGain();
    o.frequency.value = accent ? 1600 : 1000;
    g.gain.setValueAtTime(0, when);
    g.gain.linearRampToValueAtTime(accent ? 0.35 : 0.2, when + 0.002);
    g.gain.exponentialRampToValueAtTime(1e-4, when + 0.05);
    o.connect(g).connect(master);
    o.start(when);
    o.stop(when + 0.06);
  }
  const timers = new Set();
  function flashAt(when, pad, bank) {
    const t = setTimeout(() => {
      timers.delete(t);
      if (bank === store.get().bank) emit("flash", { pad });
    }, Math.max(0, (when - ctx.currentTime) * 1000));
    timers.add(t);
  }
  const seq = createSequencer({ ctx, click: clickAt, play: (p, vel, when, b) => { engine.trigger(p, vel, when, b); flashAt(when, p, b); } });
  const syncSeqTempo = () => {
    const { chops } = store.get();
    seq.state.bpm = chops.bpm;
    seq.state.beatsPerBar = chops.beats_per_bar;
  };

  // ---- state -> engine, sequencer, fx, disk ----
  // The sequencer gets its own copy of the pattern because it appends to it while recording.
  const givePattern = (pattern) => {
    seq.state.pattern = { bars: pattern.bars, events: [...pattern.events] };
    seq.state.bars = pattern.bars;
  };
  function applyAll(s) {
    engine.setChops(s.chops);
    engine.setBank(s.bank);
    engine.setMode(s.settings.mode);
    engine.setChoke(s.settings.choke);
    engine.setFullLevel(s.settings.fullLevel);
    for (let p = 0; p < PADS; p++) engine.setPadParams(p, s.project.pads[p]);
    for (const [k, sl] of Object.entries(s.project.overrides)) {
      const [b, p] = k.split(":").map(Number);
      engine.setOverride(b, p, sl);
      bindSource(b, p, sl);
    }
    fx.set(s.project.fx);
    seq.state.quant = s.settings.quant;
    seq.state.swing = s.settings.swing;
    seq.state.metronome = s.settings.metronome;
  }
  /** Point the engine at the right sound source for (bank, pad) given its override (stretch url, other song, or the song). */
  function bindSource(b, p, o) {
    const same = () => { const cur = store.get().project.overrides[`${b}:${p}`]; return cur && cur.song === o.song && cur.stretch?.url === o.stretch?.url; };
    if (o?.stretch) {
      const job = loadBuffer(o.stretch.url).then((buf) => { if (same()) engine.setPadBuffer(b, p, buf, { whole: false }); }).catch(() => {});
      pending.add(job); job.finally(() => pending.delete(job));
    } else if (o?.song && o.song !== id) {
      const job = loadBuffer(`/media/${o.song}/audio.wav`).then((buf) => { if (same()) engine.setPadBuffer(b, p, buf, { whole: true }); }).catch(() => {});
      pending.add(job); job.finally(() => pending.delete(job));
    } else engine.setPadBuffer(b, p, null);
  }
  let saveTimer = 0;
  let chopToBank = [];
  const indexBanks = (chops) => {
    chopToBank = [];
    chops.banks.forEach((b, n) => b.forEach((i) => { chopToBank[i] = n; }));
  };
  const unsubscribe = store.subscribe((s, prev, _action, meta) => {
    if (s.chops !== prev.chops) { engine.setChops(s.chops); indexBanks(s.chops); }
    if (s.bank !== prev.bank || s.chops !== prev.chops) engine.setBank(s.bank);
    if (s.settings !== prev.settings) {
      engine.setMode(s.settings.mode);
      engine.setChoke(s.settings.choke);
      engine.setFullLevel(s.settings.fullLevel);
      seq.state.quant = s.settings.quant;
      seq.state.swing = s.settings.swing;
      seq.state.metronome = s.settings.metronome;
    }
    const P = s.project, Q = prev.project;
    if (P === Q) return;
    if (P.pads !== Q.pads) for (let p = 0; p < PADS; p++) if (P.pads[p] !== Q.pads[p]) engine.setPadParams(p, P.pads[p]);
    if (P.overrides !== Q.overrides) {
      for (const k of new Set([...Object.keys(P.overrides), ...Object.keys(Q.overrides)])) {
        if (P.overrides[k] === Q.overrides[k]) continue;
        const [b, p] = k.split(":").map(Number);
        engine.setOverride(b, p, P.overrides[k] ?? null);
        bindSource(b, p, P.overrides[k]);
      }
    }
    if (P.fx !== Q.fx) fx.set(P.fx);
    if (P.pattern !== Q.pattern) givePattern(P.pattern);
    if (meta.save === false) return; // for example a change that came from the server and is already on disk
    clearTimeout(saveTimer);
    saveTimer = setTimeout(() => {
      fetch(`/api/songs/${id}/project`, { method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(projectDoc(store.get())) }).catch(() => {});
    }, SAVE_DELAY_MS);
  });
  applyAll(store.get());
  if (pending.size) { onProgress("Loading stretched and kit sounds…"); await Promise.all([...pending]); }
  indexBanks(store.get().chops);
  seq.load(store.get().project.pattern);
  syncSeqTempo();

  // ---- song transport (the original audio; pads choke it) ----
  let song = null; // { src, gain, startCtx, startPos }
  const position = () => (song ? Math.min(meta.duration_s, song.startPos + (ctx.currentTime - song.startCtx)) : video.currentTime);
  function stopSong() {
    if (!song) return;
    const { src, gain } = song;
    gain.gain.setTargetAtTime(0, ctx.currentTime, 0.005);
    src.stop(ctx.currentTime + 0.03);
    song = null;
    video.pause();
    emit("song", { playing: false });
  }
  function playSong(from = video.currentTime) {
    stopSong();
    engine.stopAll();
    const src = ctx.createBufferSource();
    src.buffer = buffer;
    const gain = ctx.createGain();
    src.connect(gain).connect(master);
    src.start(ctx.currentTime, from);
    src.onended = () => { if (song?.src === src) stopSong(); };
    song = { src, gain, startCtx: ctx.currentTime, startPos: from };
    video.currentTime = from;
    video.play().catch(() => {});
    emit("song", { playing: true });
  }

  // ---- banks and pads ----
  let lastChop = null;
  let meterOverride = null;
  const steps = () => STEPS[store.get().chops.grid];
  /** "bar.beat" (plus the subdivision on finer grids) for a chop. */
  const chopLabel = (c) => {
    const n = steps();
    return `${c.bar + 1}.${Math.floor(c.beat / n) + 1}${n > 1 ? "." + ((c.beat % n) + 1) : ""}`;
  };
  function setBank(n, seekVideo = false) {
    store.dispatch({ type: "bank.set", bank: n });
    const { chops, bank } = store.get();
    if (seekVideo && !song) video.currentTime = chops.chops[chops.banks[bank][0]].start;
  }
  /** Move to the bank that contains song time `t` (used while the song plays). */
  function followTo(t) {
    const { chops, bank } = store.get();
    const i = chops.chops.findIndex((c) => c.start <= t && t < c.end);
    if (i >= 0 && chopToBank[i] !== bank) setBank(chopToBank[i], false);
  }
  /** Play a pad on the current bank. Records it when the sequencer is recording. */
  function hit(pad, velocity = 1) {
    const { bank } = store.get();
    const c = engine.getSlice(bank, pad);
    if (!c) return;
    stopSong();
    engine.trigger(pad, velocity);
    const ev = seq.record(pad, bank, velocity);
    if (ev) store.dispatch({ type: "pattern.addEvent", event: ev });
    lastChop = c;
    store.dispatch({ type: "pad.select", pad });
    emit("hit", { pad, velocity });
  }
  // note repeat: a held pad retriggers at the chosen rate, in time with the tempo
  const held = new Map(); // pad -> interval handle
  function startRepeat(pad, velocity) {
    const { settings, chops } = store.get();
    if (!settings.repeat || held.has(pad)) return;
    held.set(pad, setInterval(() => hit(pad, velocity), (settings.repeatRate * 60000) / chops.bpm));
  }
  function stopRepeat(pad) { clearInterval(held.get(pad)); held.delete(pad); }

  // ---- regrid / tempo ----
  function adoptChops(chops) {
    const t = position();
    store.dispatch({ type: "chops.replace", chops });
    followTo(t);
  }
  let lastGrid = null; // the grid to return to when switching back from transient mode
  /** change: { grid } | { meter } | { mult } | { shift } | { bar1: true } | { mode: "grid"|"transient", sensitivity? } */
  async function regrid(change) {
    const { chops } = store.get();
    let mode = chops.mode ?? "grid";
    let sensitivity = chops.sensitivity ?? 0.5;
    if (mode === "grid") lastGrid = chops.grid;
    if (change.mode) mode = change.mode;
    if (change.sensitivity !== undefined) { sensitivity = change.sensitivity; mode = "transient"; }
    if (change.grid) mode = "grid";
    let beat_offset = chops.beat_offset ?? 0;
    let bpm_mult = chops.bpm_mult ?? 1;
    let grid = chops.grid;
    if (mode === "grid" && chops.mode === "transient") grid = lastGrid ?? "1/4";
    if (change.grid) grid = change.grid;
    if (change.meter !== undefined) meterOverride = change.meter;
    if (change.mult) bpm_mult = MULTS.reduce((a, b) => (Math.abs(b - bpm_mult * change.mult) < Math.abs(a - bpm_mult * change.mult) ? b : a));
    if (change.shift) beat_offset += change.shift;
    if (change.bar1) {
      if (!lastChop) return false;
      beat_offset += Math.floor(lastChop.beat / steps());
    }
    const bpb = meterOverride ? Number(meterOverride.split("/")[0]) : chops.beats_per_bar;
    beat_offset = ((beat_offset % bpb) + bpb) % bpb;
    if (beat_offset > 3) beat_offset -= bpb;
    const r = await postJson(`/api/songs/${id}/regrid`, { grid, meter: meterOverride, beat_offset, bpm_mult, mode, sensitivity });
    if (!r.ok) return false;
    adoptChops(await r.json());
    return true;
  }
  /** Re-run the analysis with a forced tempo (tap tempo). */
  async function reanalyze(bpm) {
    const r = await postJson(`/api/songs/${id}/reanalyze`, { bpm, meter: meterOverride });
    if (!r.ok) return false;
    const out = await r.json();
    Object.assign(analysis, out.analysis);
    adoptChops(out.chops);
    return true;
  }

  // ---- pattern transport and renders ----
  function toggleRecord() { stopSong(); syncSeqTempo(); seq.toggleRecord(); emit("seq", {}); }
  function togglePattern() {
    if (seq.state.playing) seq.stop(); else { stopSong(); syncSeqTempo(); seq.start(); }
    emit("seq", {});
  }
  const patternMidi = () => {
    const { chops, project } = store.get();
    return patternToMidi(project.pattern, { bpm: chops.bpm, beatsPerBar: chops.beats_per_bar });
  };
  /** Offline render of the pattern through the pad sounds and master fx. Returns WAV bytes, or null when empty. */
  async function renderPatternWav() {
    const { chops, project, settings } = store.get();
    const pat = project.pattern;
    if (!pat.events.length) return null;
    const seconds = (pat.bars * chops.beats_per_bar * 60) / chops.bpm + 2 + (project.fx.reverb > 0 ? 0.4 + project.fx.size * 5.6 : 0);
    const off = new OfflineAudioContext(2, Math.ceil(seconds * buffer.sampleRate), buffer.sampleRate);
    const ofx = createFx(off, project.fx);
    ofx.output.connect(off.destination);
    const oe = createEngine({ ctx: off, buffer, chops, output: ofx.input });
    for (let p = 0; p < PADS; p++) oe.setPadParams(p, project.pads[p]);
    await Promise.all([...pending]);
    for (const [k, sl] of Object.entries(project.overrides)) {
      const [b, p] = k.split(":").map(Number);
      oe.setOverride(b, p, sl);
      const alt = sl.stretch ? decoded.get(sl.stretch.url) : sl.song && sl.song !== id ? decoded.get(`/media/${sl.song}/audio.wav`) : null;
      if (alt) oe.setPadBuffer(b, p, alt, { whole: !sl.stretch });
    }
    const q = { off: 0, "1/8": 0.5, "1/16": 0.25 }[settings.quant];
    for (const e of pat.events) {
      const step = q ? Math.round(e.beat / q) : 0;
      const beat = q && step % 2 === 1 ? e.beat + settings.swing * q : e.beat;
      oe.trigger(e.pad, e.vel, (beat * 60) / chops.bpm, e.bank);
    }
    return encodeWav(await off.startRendering());
  }

  // ---- server features ----
  async function loadSections() {
    try {
      const r = await postJson(`/api/songs/${id}/sections`);
      if (r.ok) store.dispatch({ type: "sections.set", sections: (await r.json()).sections });
    } catch { /* sections are optional */ }
  }
  let ai = null;
  /** Ask the audio model about the song. Returns its answer; throws with the server's error text. */
  async function advise() {
    const r = await postJson(`/api/songs/${id}/advise`);
    const body = await r.json();
    if (!r.ok) throw new Error(body.error || r.status);
    ai = body;
    store.dispatch({ type: "sections.set", sections: ai.sections.map((sc, i) => ({ ...sc, letter: sc.letter ?? String.fromCharCode(65 + i) })) });
    return ai;
  }
  /** Put the model's suggested 16 chops on the current bank. Returns the pad labels, or null without an answer. */
  function loadAiBank() {
    if (!ai) return null;
    const { chops, bank } = store.get();
    const slices = ai.suggested_bank.slice(0, PADS).map((ci, pad) => ({ pad, ...(chops.chops[ci] ?? {}) })).filter((s) => s.end > s.start);
    store.dispatch({ type: "slice.setMany", bank, slices: slices.map(({ pad, start, end }) => ({ pad, start, end })) });
    return ai.pad_labels.slice(0, PADS);
  }
  /** kind: "hydrogen" | "sfz". Returns { ok, path, note, error }. */
  async function exportBank(kind) {
    const { bank, project } = store.get();
    const r = await postJson(`/api/songs/${id}/export`, { kind, bank, fx: project.fx, overrides: project.overrides });
    const body = await r.json().catch(() => ({}));
    return { ok: r.ok, ...body, error: r.ok ? undefined : (body.error || r.status) };
  }

  // ---- time-stretch (rubberband on the server; the result is a cached WAV the pad plays instead of the song region) ----
  /** Fit the slice on (bank, pad) to `{ target_bpm }` or `{ ratio }` (new length / old). Resolves { ok, ratio, url, duration, error }. */
  async function stretchPad(bank, pad, how) {
    const sl = engine.getSlice(bank, pad);
    if (!sl) return { ok: false, error: `pad ${pad + 1} is empty` };
    const src = sl.song ?? id;   // a kit pad from another song is rendered from that song's audio
    const r = await postJson(`/api/songs/${src}/stretch`, { bank, pad, start: sl.start, end: sl.end, ...how });
    const body = await r.json().catch(() => ({}));
    if (!r.ok) return { ok: false, error: body.error || `HTTP ${r.status}` };
    store.dispatch({ type: "slice.stretch", bank, pad, stretch: { ratio: body.ratio, url: body.url } });
    await Promise.all([...pending]);
    return { ok: true, ...body };
  }
  /** Stretch every pad on the current bank. Resolves the list of per-pad results (empty pads skipped). */
  async function stretchBank(how) {
    const { bank } = store.get();
    const jobs = [];
    for (let p = 0; p < PADS; p++) if (engine.getSlice(bank, p)) jobs.push(stretchPad(bank, p, how).then((r) => ({ pad: p, ...r })));
    return Promise.all(jobs);
  }
  /** Stretch every pad the pattern uses (all banks it touches). */
  async function stretchPattern(how) {
    const seen = new Set(store.get().project.pattern.events.map((e) => `${e.bank}:${e.pad}`));
    return Promise.all([...seen].map((k) => { const [b, p] = k.split(":").map(Number); return stretchPad(b, p, how).then((r) => ({ bank: b, pad: p, ...r })); }));
  }
  function clearStretch(bank, pad) { store.dispatch({ type: "slice.stretch", bank, pad, stretch: null }); }

  function panic() {
    stopSong();
    engine.stopAll();
    seq.stop();
    for (const p of [...held.keys()]) stopRepeat(p);
    emit("seq", {});
  }

  return {
    id, meta, analysis, peaks, buffer, video, store, engine, seq,
    on: (type, fn) => events.addEventListener(type, fn),
    off: (type, fn) => events.removeEventListener(type, fn),
    position, playSong, stopSong,
    get songPlaying() { return !!song; },
    get lastChop() { return lastChop; },
    chopLabel, setBank, followTo, hit, release: (pad) => engine.release(pad),
    startRepeat, stopRepeat,
    regrid, reanalyze,
    toggleRecord, togglePattern, patternMidi, renderPatternWav,
    loadSections, advise, loadAiBank, exportBank,
    stretchPad, stretchBank, stretchPattern, clearStretch,
    panic,
    destroy() {
      unsubscribe();
      for (const t of timers) clearTimeout(t);
      panic();
      video.removeAttribute("src");
      video.load();
    },
  };
}
