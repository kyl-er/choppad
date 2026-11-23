// session: one open DRUM PAD MEGA BANK (contracts §15) and, inside it, the source song being chopped. It owns the store, the
// audio engine, the sequencer, the song transport and everything that talks to the server. The engine, sequencer, master-bus
// wiring, store and event stream are made ONCE per mega bank; `setSource(song)` swaps the song underneath them (audio buffer,
// video, peaks, analysis, chop table) while the pattern keeps playing and kept pads play from their own sample files.
// It has no layout and no styling: a view (ui.js today) reads `session.store`, calls the methods below and listens for "hit",
// "flash", "song" and "source" events. Anything that changes saved state goes through `session.store.dispatch(action)`.
import { createEngine, normalizationGain } from "./engine.js";
import { createSequencer, encodeWav, patternToMidi } from "./sequencer.js";
import { createFx } from "./fx.js";
import { createStore } from "./store.js";
import { PADS, SIXTEEN_MODES, bankCount, initialState, projectBpm, projectDoc, reduce, sixteenLevel, songTimeline, stemUrl, timelineEvents } from "./actions.js";

const STEPS = { "1/4": 1, "1/8": 2, "1/16": 4 };
const MULTS = [0.5, 1, 2];
const SAVE_DELAY_MS = 80; // local edits are sent to the server in batches this often
const PEAK_POINTS = 6000; // as server.py: the waveform's peak count
// One random id per page load; sent with every write so the server and the other tabs know who wrote it (contracts §10).
const ORIGIN = globalThis.crypto?.randomUUID?.() ?? `tab-${Math.random().toString(36).slice(2)}${Date.now().toString(36)}`;
const ORIGIN_HEADER = "X-ChopPad-Origin";
// Local actions that are saved on the server and shown by other tabs. Selection, settings and sections stay per tab.
// `source.set` is never forwarded: it only comes from the server (POST /api/mega/<mid>/source).
const FORWARDED = new Set(["pad.set", "pad.reset", "pad.copyToAll", "slice.set", "slice.clear", "slice.setMany", "slice.stretch", "slice.src", "slice.stem", "slice.keep", "kit.load", "fx.set", "tempo.set",
  "song.addPattern", "song.selectPattern", "song.renamePattern", "song.removePattern", "song.setChain", "song.setMode", "song.load",
  "pattern.toggleStep", "pattern.addEvent", "pattern.undo", "pattern.clear", "pattern.setBars", "pattern.load", "bank.set"]);
const NO_BANK = 1 << 20;   // a bank no pad is kept on: `pad.set` there reaches pads[pad], not a kept pad's own params

const postJson = (url, body = {}) => fetch(url, { method: "POST", headers: { "Content-Type": "application/json", [ORIGIN_HEADER]: ORIGIN }, body: JSON.stringify(body) });
const replaceKeys = (obj, next) => { for (const k of Object.keys(obj)) delete obj[k]; return Object.assign(obj, next); };

/** What the browser needs of a song besides what the server's mega endpoints give, read from the files of its media folder (so
 *  asking creates nothing on the server): meta, analysis, chops, peaks (computed here when the server has not cached them yet) and
 *  the decoded audio. Throws when the song or a file is missing. */
async function fetchSong(id, ctx, onProgress = () => {}) {
  const get = async (name, optional = false) => {
    const r = await fetch(`/media/${id}/${name}`);
    if (!r.ok) { if (optional) return null; throw new Error(`song ${id}: ${name} HTTP ${r.status}`); }
    return r.json();
  };
  const [meta, analysis, chops, cachedPeaks] = await Promise.all([get("meta.json"), get("analysis.json"), get("chops.json"), get("peaks.json", true)]);
  onProgress("Decoding audio…");
  const r = await fetch(`/media/${id}/audio.wav`);
  if (!r.ok) throw new Error(`song ${id}: audio.wav HTTP ${r.status}`);
  const buffer = await ctx.decodeAudioData(await r.arrayBuffer());
  return { meta, analysis, chops, peaks: cachedPeaks ?? peaksOf(buffer), buffer };
}
/** Max-abs peaks of a decoded buffer, normalised, as the server's compute_peaks. */
function peaksOf(buffer, points = PEAK_POINTS) {
  const n = buffer.length, chans = Array.from({ length: buffer.numberOfChannels }, (_, c) => buffer.getChannelData(c));
  const out = new Array(points).fill(0);
  for (let i = 0; i < points; i++) {
    const a = Math.floor((i * n) / points), b = Math.floor(((i + 1) * n) / points);
    let m = 0;
    for (const d of chans) for (let k = a; k < b; k++) { const v = Math.abs(d[k]); if (v > m) m = v; }
    out[i] = m;
  }
  const top = Math.max(...out) || 1;
  return { duration_s: n / buffer.sampleRate, peaks: out.map((p) => Math.round((p / top) * 1e4) / 1e4) };
}

/** Open the mega bank of song `id` (the song's most recently updated mega bank, made on first use). Thin wrapper of openMega. */
export async function openSong({ id, audio, onProgress = () => {} }) {
  onProgress("Loading song…");
  const res = await fetch(`/api/songs/${id}`);
  if (!res.ok) throw new Error(`song ${id}: HTTP ${res.status}`);
  const data = await res.json();
  return openMega({ mid: data.mega, audio, onProgress });
}

/** What a mega bank with no source song shows and plays under: no title, no length, one silent frame of audio. Kept pads and the
 *  pattern work as ever; `setSource` brings a song in later. The chop table has no tempo of its own, so 120 stands in until a
 *  song (or the user) gives one. */
function noSong(ctx, chops) {
  return { meta: { id: null, title: "", duration_s: 0 }, analysis: {}, peaks: { duration_s: 0, peaks: [] }, buffer: ctx.createBuffer(1, 1, ctx.sampleRate),
    chops: { ...chops, bpm: chops?.bpm ?? 120, beats_per_bar: chops?.beats_per_bar ?? 4, chops: chops?.chops ?? [], banks: chops?.banks ?? [] } };
}

/** Open mega bank `mid` and its source song. The returned session lives as long as the mega bank is open: `setSource` changes
 *  the song without making anything new. Throws if the mega bank or its song can't be loaded. A mega bank with no source song
 *  (`source: null`) opens too: `session.source` is null, kept pads and the sequence play, the song parts are empty.
 *  `onProgress(text)` is optional. */
export async function openMega({ mid, audio, onProgress = () => {} }) {
  const { ctx, master, fx } = audio;
  onProgress("Loading mega bank…");
  const res = await fetch(`/api/mega/${mid}`);
  if (!res.ok) throw new Error(`mega bank ${mid}: HTTP ${res.status}`);
  const data = await res.json();
  const first = data.mega.source ?? null;
  const loaded = first ? await fetchSong(first, ctx, onProgress) : noSong(ctx, data.chops);
  // meta / analysis / peaks are the same objects for the whole life of the session: a source change replaces their content in
  // place, so a view that holds them (`const { meta } = session`) always reads the open song's.
  const meta = loaded.meta, analysis = loaded.analysis, peaks = loaded.peaks;
  let buffer = loaded.buffer;
  let sourceId = first;

  // One muted <video> for the life of the session; its src follows the source song.
  const video = document.createElement("video");
  video.className = "video";
  video.muted = true;
  video.playsInline = true;
  video.preload = "auto";
  if (sourceId) video.src = `/media/${sourceId}/video.mp4`;

  // W3: a pad can play a time-stretched rendering of its slice, a slice of another song (kits) or a file of the mega bank (kept
  // pads). Buffers are fetched and decoded once (cached by url) and handed to the engine; none of this happens on the trigger path.
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
  if (sourceId) decoded.set(`/media/${sourceId}/audio.wav`, buffer);
  const events = new EventTarget();
  const emit = (type, detail) => events.dispatchEvent(new CustomEvent(type, { detail }));
  const store = createStore(reduce, initialState({ project: data.project, chops: loaded.chops, source: sourceId, mega: mid }));
  const engine = createEngine({ ctx, buffer, chops: loaded.chops, video, output: master });
  engine.setBankCount(bankCount(store.get().project, store.get().chops));

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
  const seq = createSequencer({ ctx, click: clickAt, play: (p, vel, when, b, ev) => { engine.trigger(p, vel, when, b, ev); flashAt(when, p, b); } });
  // The pattern is stored in beats; its bar length is the one it was made with. It follows the chop table only when the table is
  // changed under the same song (regrid / reanalyze: `adoptChops`), never when the SOURCE song changes (a song in another meter
  // must not change the loop length). Not saved with the pattern: after a reload it is the open song's again (that needs a
  // reducer field, reported for step D).
  let patternBpb = loaded.chops.beats_per_bar ?? 4;
  const syncSeqTempo = () => {   // the project tempo when set, else the song's own
    const st = store.get();
    seq.setBpm(projectBpm(st));
    seq.state.beatsPerBar = patternBpb;
    giveSong(st);
  };
  /** Song mode: the sequencer plays the chain (bar-accurate, one beat clock); otherwise it loops the edited pattern. */
  function giveSong(st = store.get()) {
    seq.setSong(st.project.song?.mode === "song" ? songTimeline(st.project, patternBpb) : null);
  }

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
  // What a pad plays instead of the song's own audio, from its override: a stretched rendering (the buffer IS the slice), a
  // resample (`src`, played as it is), a stem of the song (`stem`, same time axis as the song: the video follows) or another
  // song (kits). Anything else: null = the song. Buffers are fetched and decoded once, never on the trigger path.
  let songNorm = normalizationGain(buffer);
  function sourceOf(o) {
    if (!o) return null;
    if (o.stretch) return { url: o.stretch.url, whole: false };
    if (o.src) return { url: o.src, whole: true, gain: o.kept ? (o.gain ?? 1) : 1, follow: false };   // a kept pad plays at the level it had before it was kept
    if (o.stem) { const own = !o.song || o.song === sourceId; return { url: stemUrl(o.song ?? sourceId, o.stem), whole: true, gain: own ? songNorm : undefined, follow: own }; }
    if (o.song && o.song !== sourceId) return { url: `/media/${o.song}/audio.wav`, whole: true };
    return null;
  }
  const padSourceOpts = (src) => ({ whole: src.whole, gain: src.gain, follow: src.follow });
  /** Point the engine at the right sound source for (bank, pad) given its override. */
  function bindSource(b, p, o) {
    const src = sourceOf(o);
    if (!src) { engine.setPadBuffer(b, p, null); return; }
    const have = decoded.get(src.url);
    if (have) { engine.setPadBuffer(b, p, have, padSourceOpts(src)); return; }   // already decoded: bound in the same step as the override
    const same = () => sourceOf(store.get().project.overrides[`${b}:${p}`])?.url === src.url;
    const job = loadBuffer(src.url).then((buf) => { if (same()) engine.setPadBuffer(b, p, buf, padSourceOpts(src)); }).catch(() => {});
    pending.add(job); job.finally(() => pending.delete(job));
  }
  let chopToBank = [];
  const indexBanks = (chops) => {
    chopToBank = [];
    chops.banks.forEach((b, n) => b.forEach((i) => { chopToBank[i] = n; }));
  };
  const unsubscribe = store.subscribe((s, prev, action, meta) => {
    if (s.chops !== prev.chops || s.project.overrides !== prev.project.overrides) engine.setBankCount(bankCount(s.project, s.chops));
    if (s.chops !== prev.chops) { engine.setChops(s.chops); indexBanks(s.chops); syncSeqTempo(); }
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
    if (P.tempo !== Q.tempo) {
      syncSeqTempo();
      if (action?.type === "tempo.set" && !meta.remote && P.tempo?.follow !== false) scheduleRefit();
    }
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
    if (P.song !== Q.song || P.pattern !== Q.pattern) { giveSong(s); if (P.song?.mode !== Q.song?.mode) emit("seq", {}); }
  });

  // ---- local changes -> server (saved there, shown by every other tab), server changes -> store ----
  // Local actions are queued and sent as one batch about every SAVE_DELAY_MS; the server applies them with its own copy
  // of the reducer, writes project.json and tells the other tabs. If that fails the whole project is PUT instead.
  const outbox = [];
  let flushTimer = 0;
  let inflight = Promise.resolve();
  const send = (url, init) => fetch(url, { ...init, keepalive: true, headers: { "Content-Type": "application/json", [ORIGIN_HEADER]: ORIGIN } });
  function queue(action, prev) {
    const last = outbox[outbox.length - 1];
    // runs of the same edit (a slider drag, a waveform drag) collapse into one
    if (last && last.type === action.type) {
      if ((action.type === "pad.set" && last.pad === action.pad && last.bank === action.bank) || action.type === "fx.set") {
        last.params = { ...last.params, ...action.params };
        return;
      }
      if (action.type === "bank.set" || (action.type === "slice.set" && last.bank === action.bank && last.pad === action.pad)) {
        outbox[outbox.length - 1] = { ...action };
        return;
      }
    }
    // a toggle without a bank means "the bank on screen"; the server does not know which one that was for this tab
    outbox.push(action.type === "pattern.toggleStep" && action.bank == null ? { ...action, bank: prev.bank } : { ...action });
  }
  function flush() {
    clearTimeout(flushTimer);
    flushTimer = 0;
    const batch = outbox.splice(0);
    if (!batch.length) return inflight;
    inflight = inflight.then(async () => {
      try {
        const r = await send(`/api/mega/${mid}/actions`, { method: "POST", body: JSON.stringify({ actions: batch }) });
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
      } catch {
        await send(`/api/mega/${mid}/project`, { method: "PUT", body: JSON.stringify(projectDoc(store.get())) }).catch(() => {});
      }
    });
    return inflight;
  }
  const unsubscribeOutbox = store.subscribe((s, prev, action, meta) => {
    if (meta.remote || meta.save === false || !FORWARDED.has(action.type)) return;
    queue(action, prev);
    if (!flushTimer) flushTimer = setTimeout(flush, SAVE_DELAY_MS);
  });
  const onPageHide = () => { flush(); };
  window.addEventListener("pagehide", onPageHide);
  applyAll(store.get());
  giveSong();
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
    const first = chops.chops[chops.banks[bank]?.[0]];   // a bank past the song's own (kept pads only) has no chop to seek to
    if (seekVideo && !song && first) video.currentTime = first.start;
  }
  /** Move to the bank that contains song time `t` (used while the song plays). */
  function followTo(t) {
    const { chops, bank } = store.get();
    const i = chops.chops.findIndex((c) => c.start <= t && t < c.end);
    if (i >= 0 && chopToBank[i] !== bank) setBank(chopToBank[i], false);
  }
  /** Play a pad on the current bank. Records it when the sequencer is recording.
   *  With 16 levels on (setSixteenLevels) the pressed pad is a level of the source pad: the source pad sounds, and a
   *  recorded hit carries the source pad with its own vel / pitch / cutoff. */
  function hit(pad, velocity = 1, { raw = false } = {}) {   // raw: a remote hit names the pad itself, 16 levels do not remap it
    const { bank, settings } = store.get();
    const lv = settings.sixteen && !raw ? sixteenLevel(settings.sixteen.mode, pad) : null;
    const src = lv ? settings.sixteen.pad : pad;
    const vel = lv?.vel ?? velocity;
    const extra = lv && (lv.pitch !== undefined || lv.cutoff !== undefined) ? lv : null;
    const c = engine.getSlice(bank, src);
    if (!c) return;
    stopSong();
    engine.trigger(src, vel, null, bank, extra);
    const ev = seq.record(src, bank, vel, extra);
    if (ev) store.dispatch({ type: "pattern.addEvent", event: ev });
    lastChop = c;
    store.dispatch({ type: "pad.select", pad: src });
    emit("hit", { pad, velocity: vel, ...(lv ? { source: src, level: lv } : {}) });
    dawHit(bank, src, vel); // CHOP-11: tell an open DAW bridge (fire and forget, after the sound)
  }
  // ---- 16 levels: one pad's chop on all 16 pads, by velocity, pitch or filter cutoff ----
  let fullLevelBefore = null;   // velocity mode needs Full level off; remembered so "off" can put it back
  const restoreFullLevel = () => {
    if (fullLevelBefore === null) return;
    store.dispatch({ type: "settings.set", params: { fullLevel: fullLevelBefore } });
    fullLevelBefore = null;
  };
  /** {pad, mode: "velocity" | "pitch" | "filter" | "off"} (a view setting, not saved). In velocity mode Full level is switched
   *  off until the mode is left, so the 16 velocities are audible. Returns { ok, mode, pad, levels: [{pad, vel|pitch|cutoff}] }. */
  function setSixteenLevels({ pad, mode } = {}) {
    if (mode === "off" || mode == null) {
      store.dispatch({ type: "settings.set", params: { sixteen: null } });
      restoreFullLevel();
      return { ok: true, mode: "off", pad: null, levels: [] };
    }
    if (!Number.isInteger(pad) || pad < 0 || pad >= PADS || !SIXTEEN_MODES.includes(mode)) {
      return { ok: false, error: `give a source pad 0..15 and a mode of ${SIXTEEN_MODES.join(", ")} or off` };
    }
    if (mode === "velocity") {
      if (fullLevelBefore === null) fullLevelBefore = store.get().settings.fullLevel;
      store.dispatch({ type: "settings.set", params: { fullLevel: false } });
    } else restoreFullLevel();
    store.dispatch({ type: "settings.set", params: { sixteen: { pad, mode } } });
    return { ok: true, mode, pad, levels: Array.from({ length: PADS }, (_, i) => ({ pad: i, ...sixteenLevel(mode, i) })) };
  }
  // note repeat: a held pad retriggers at the chosen rate, in time with the tempo
  const held = new Map(); // pad -> interval handle
  function startRepeat(pad, velocity) {
    const { settings } = store.get();
    if (!settings.repeat || held.has(pad)) return;
    held.set(pad, setInterval(() => hit(pad, velocity), (settings.repeatRate * 60000) / projectBpm(store.get())));
  }
  function stopRepeat(pad) { clearInterval(held.get(pad)); held.delete(pad); }

  // ---- regrid / tempo ----
  function adoptChops(chops) {
    patternBpb = chops.beats_per_bar ?? patternBpb;   // the same song's table changed (regrid, meter): the pattern follows, as before
    const t = position();
    store.dispatch({ type: "chops.replace", chops });
    followTo(t);
  }
  let lastGrid = null; // the grid to return to when switching back from transient mode
  /** change: { grid } | { meter } | { mult } | { shift } | { bar1: true } | { mode: "grid"|"transient", sensitivity? } */
  async function regrid(change) {
    if (!sourceId) return false;
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
    const r = await postJson(`/api/songs/${sourceId}/regrid`, { grid, meter: meterOverride, beat_offset, bpm_mult, mode, sensitivity });
    if (!r.ok) return false;
    adoptChops(await r.json());
    return true;
  }
  /** Re-run the analysis with a forced tempo (tap tempo). */
  async function reanalyze(bpm) {
    if (!sourceId) return false;
    const r = await postJson(`/api/songs/${sourceId}/reanalyze`, { bpm, meter: meterOverride });
    if (!r.ok) return false;
    const out = await r.json();
    Object.assign(analysis, out.analysis);
    adoptChops(out.chops);
    return true;
  }

  // ---- pattern transport and renders ----
  function toggleRecord() {
    if (seq.state.song) { emit("seq", {}); return; }   // a chain is playing, not one pattern: there is nothing to record into
    stopSong(); syncSeqTempo(); seq.toggleRecord(); emit("seq", {});
  }
  function togglePattern() {
    if (seq.state.playing) seq.stop(); else { stopSong(); syncSeqTempo(); seq.start(); }
    emit("seq", {});
    dawTransport(seq.state.playing ? "play-pattern" : "stop"); // CHOP-11
  }
  /** Standard MIDI File of the whole chain (song mode's playing order; just the edited pattern when there is no chain). */
  const songMidi = () => {
    const st = store.get();
    const bpb = patternBpb;
    const tl = songTimeline(st.project, bpb, { force: true });
    return patternToMidi({ bars: tl.total / bpb, events: timelineEvents(tl) }, { bpm: projectBpm(st), beatsPerBar: bpb });
  };
  const patternMidi = () => {
    const st = store.get();
    return patternToMidi(st.project.pattern, { bpm: projectBpm(st), beatsPerBar: patternBpb });
  };
  /** Offline render of `events` (absolute beats from 0, with their pad, bank, vel, pitch, cutoff) through the pad sounds and the
   *  master fx, at the project tempo. `beats` = length of the take; `tail` adds room for the last hit and the reverb to ring out
   *  (without it the render is exactly `beats` long: a loop). Resolves an AudioBuffer. */
  async function renderEvents(events, { beats, tail = true }) {
    const { chops, project, settings } = store.get();
    const bpm = projectBpm(store.get());
    const seconds = (beats * 60) / bpm + (tail ? 2 + (project.fx.reverb > 0 ? 0.4 + project.fx.size * 5.6 : 0) : 0);
    const off = new OfflineAudioContext(2, Math.max(1, Math.round(seconds * buffer.sampleRate)), buffer.sampleRate);
    const ofx = createFx(off, project.fx);
    ofx.output.connect(off.destination);
    const oe = createEngine({ ctx: off, buffer, chops, output: ofx.input, choke: settings.choke, fullLevel: settings.fullLevel });
    for (let p = 0; p < PADS; p++) oe.setPadParams(p, project.pads[p]);
    await Promise.all([...pending]);
    for (const [k, sl] of Object.entries(project.overrides)) {
      const [b, p] = k.split(":").map(Number);
      oe.setOverride(b, p, sl);
      const src = sourceOf(sl);
      const alt = src ? decoded.get(src.url) : null;
      if (alt) oe.setPadBuffer(b, p, alt, padSourceOpts(src));
    }
    const q = { off: 0, "1/8": 0.5, "1/16": 0.25 }[settings.quant];
    for (const e of events) {
      const step = q ? Math.round(e.beat / q) : 0;
      const beat = q && step % 2 === 1 ? e.beat + settings.swing * q : e.beat;
      if (beat >= beats) continue;
      oe.trigger(e.pad, e.vel, (beat * 60) / bpm, e.bank, e);
    }
    return off.startRendering();
  }
  /** Offline render of the whole chain (every pattern as often as the chain says), WAV bytes, or null when there is nothing to play. */
  async function renderSongWav() {
    const st = store.get();
    const tl = songTimeline(st.project, patternBpb, { force: true });
    const events = timelineEvents(tl);
    if (!events.length) return null;
    return encodeWav(await renderEvents(events, { beats: tl.total }));
  }
  /** The pattern repeated to fill `beats` beats (a pattern shorter than that loops). */
  function loopedEvents(pat, beats, bpb) {
    const len = pat.bars * bpb, out = [];
    for (let k = 0; k * len < beats; k++) for (const e of pat.events) out.push({ ...e, beat: e.beat + k * len });
    return out;
  }
  /** Offline render of the pattern through the pad sounds and master fx. Returns WAV bytes, or null when empty. */
  async function renderPatternWav() {
    const { project } = store.get();
    const pat = project.pattern;
    if (!pat.events.length) return null;
    return encodeWav(await renderEvents(pat.events, { beats: pat.bars * patternBpb }));
  }

  // ---- stems (components/stems): drums, bass, vocals and other of the song, from a backend the server is set up with ----
  /** Split the song into stems (the server asks its backend, which can take minutes; cached after the first time). Resolves
   *  { ok, stems: ["drums", ...] } and puts the list in session.meta.stems, or { ok: false, status, error } (501 = no backend set up). */
  async function separateStems({ force = false } = {}) {
    if (!sourceId) return { ok: false, error: "pick a song first" };
    try {
      const r = await postJson(`/api/songs/${sourceId}/stems`, force ? { force: true } : {});
      const body = await r.json().catch(() => ({}));
      if (!r.ok) return { ok: false, status: r.status, error: body.error || `HTTP ${r.status}` };
      meta.stems = body.stems;
      emit("stems", { stems: body.stems });
      return { ok: true, stems: body.stems };
    } catch (e) {
      return { ok: false, error: String(e?.message ?? e) };
    }
  }
  /** Make (bank, pad) play its slice from a stem of the song ("drums" | "bass" | "vocals" | "other"), or from the full mix again
   *  with null. Resolves when the stem is loaded. */
  async function setStem(bank, pad, stem) {
    store.dispatch({ type: "slice.stem", bank, pad, stem });
    await Promise.all([...pending]);
    return { ok: stem === null ? !store.get().project.overrides[`${bank}:${pad}`]?.stem : store.get().project.overrides[`${bank}:${pad}`]?.stem === stem };
  }

  // ---- resample: a new sound from what the pad machine plays ----
  let workletReady = null;
  /** Record `secs` seconds of the master bus (after the fx) with an AudioWorklet; sample-accurate, no ScriptProcessor.
   *  With `play` the pattern is started in step with it when it is not already running (without the metronome click). */
  async function captureMaster(secs, { play }) {
    const { ctx: c } = audio;
    if (!c.audioWorklet) throw new Error("this browser has no AudioWorklet");
    workletReady ??= c.audioWorklet.addModule("/capture-worklet.js");
    await workletReady;
    if (c.state !== "running") await c.resume();
    const tap = audio.fx?.output ?? master;
    const node = new AudioWorkletNode(c, "choppad-capture", { numberOfInputs: 1, numberOfOutputs: 1, outputChannelCount: [2] });
    const sink = c.createGain();
    sink.gain.value = 0;
    tap.connect(node); node.connect(sink); sink.connect(c.destination);
    const sr = c.sampleRate, frames = Math.round(secs * sr);
    const at = c.currentTime + 0.12;
    const done = new Promise((res, rej) => { node.port.onmessage = (e) => (e.data.type === "done" ? res(e.data) : rej(new Error("capture cancelled"))); });
    node.port.postMessage({ cmd: "start", startFrame: Math.round(at * sr), frames });
    let started = false;
    const click = seq.state.metronome;
    if (play && !seq.state.playing) { stopSong(); syncSeqTempo(); seq.state.metronome = false; seq.start(at); started = true; emit("seq", {}); }
    let timer;
    try {
      const got = await Promise.race([done, new Promise((_, rej) => { timer = setTimeout(() => rej(new Error("capture timed out")), secs * 1000 + 8000); })]);
      const out = c.createBuffer(2, frames, sr);
      out.copyToChannel(got.channels[0], 0);
      out.copyToChannel(got.channels[1], 1);
      return out;
    } finally {
      clearTimeout(timer);
      try { node.port.postMessage({ cmd: "cancel" }); } catch { /* gone */ }
      tap.disconnect(node); node.disconnect(); sink.disconnect();
      if (started) { seq.stop(); seq.state.metronome = click; emit("seq", {}); }
    }
  }
  /** Render or record `bars` bars (or `seconds`) at the project tempo into a new sound, upload it and put it on `pad` of `bank`
   *  (default: the bank on screen) as a resample override (`src`). With no `pad` it is only uploaded.
   *  source "pattern": an offline render of the pattern through the pad sounds and master fx (deterministic, no waiting; a
   *  short pattern loops). source "master": a live recording of the master bus after the fx (what is playing, in real time).
   *  Resolves { ok, url, duration, bank, pad, source } or { ok: false, error }. */
  async function resample({ bars, seconds, source = "pattern", pad, bank, play = true } = {}) {
    try {
      const st = store.get();
      const bpm = projectBpm(st), bpb = patternBpb;
      const secs = Number.isFinite(seconds) && seconds > 0 ? seconds : Number.isFinite(bars) && bars > 0 ? (bars * bpb * 60) / bpm : 0;
      if (!(secs >= 0.05 && secs <= 300)) return { ok: false, error: "give bars or seconds (at most 300 s)" };
      if (pad !== undefined && !(Number.isInteger(pad) && pad >= 0 && pad < PADS)) return { ok: false, error: "pad must be 0..15" };
      let out;
      if (source === "pattern") {
        const pat = st.project.pattern;
        if (!pat.events.length) return { ok: false, error: "the pattern is empty" };
        const beats = (secs * bpm) / 60;
        out = await renderEvents(loopedEvents(pat, beats, bpb), { beats, tail: false });
      } else if (source === "master") out = await captureMaster(secs, { play });
      else return { ok: false, error: 'source must be "pattern" or "master"' };
      const r = await fetch(`/api/songs/${sourceId}/resample`, { method: "POST", headers: { "Content-Type": "audio/wav", [ORIGIN_HEADER]: ORIGIN }, body: encodeWav(out) });
      const body = await r.json().catch(() => ({}));
      if (!r.ok) return { ok: false, error: body.error || `HTTP ${r.status}` };
      const at = bank ?? store.get().bank;
      if (pad !== undefined) {
        store.dispatch({ type: "slice.src", bank: at, pad, src: body.url, start: 0, end: body.duration });
        await Promise.all([...pending]);
      }
      return { ok: true, url: body.url, duration: body.duration, bank: at, pad: pad ?? null, source };
    } catch (e) {
      return { ok: false, error: String(e?.message ?? e) };
    }
  }

  // ---- server features ----
  async function loadSections() {
    if (!sourceId) return;
    try {
      const r = await postJson(`/api/songs/${sourceId}/sections`);
      if (r.ok) store.dispatch({ type: "sections.set", sections: (await r.json()).sections });
    } catch { /* sections are optional */ }
  }
  let ai = null;
  /** Ask the audio model about the song. Returns its answer; throws with the server's error text. */
  async function advise() {
    if (!sourceId) throw new Error("pick a song first");
    const r = await postJson(`/api/songs/${sourceId}/advise`);
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
  /** kind: "hydrogen" | "sfz" | "zip" (16 WAVs; also returns `url` to download). Returns { ok, path, url?, note, error }. */
  async function exportBank(kind) {
    if (!sourceId) return { ok: false, error: "pick a song first" };
    const { bank, project } = store.get();
    const r = await postJson(`/api/songs/${sourceId}/export`, { kind, bank, fx: project.fx, overrides: project.overrides, pads: project.pads });
    const body = await r.json().catch(() => ({}));
    return { ok: r.ok, ...body, error: r.ok ? undefined : (body.error || r.status) };
  }

  // ---- key (analysis.key, detected once on the server) ----
  async function detectKey() {
    if (analysis.key || !sourceId) return analysis.key ?? null;
    const r = await postJson(`/api/songs/${sourceId}/key`);
    if (!r.ok) return null;
    analysis.key = await r.json();
    emit("key", analysis.key);
    return analysis.key;
  }

  // ---- time-stretch (rubberband on the server; the result is a cached WAV the pad plays instead of the song region) ----
  /** Fit the slice on (bank, pad) to `{ target_bpm }` or `{ ratio }` (new length / old). Resolves { ok, ratio, url, duration, error }. */
  async function stretchPad(bank, pad, how, { fit } = {}) {
    const sl = engine.getSlice(bank, pad);
    if (!sl) return { ok: false, error: `pad ${pad + 1} is empty` };
    if (sl.kept) return { ok: false, error: `pad ${pad + 1} is kept: it owns its audio and is not stretched` };
    if (sl.src) return { ok: false, error: `pad ${pad + 1} plays a resample, which is already at the project tempo` };
    const src = sl.song ?? sourceId;   // a kit pad from another song is rendered from that song's audio
    const r = await postJson(`/api/songs/${src}/stretch`, { bank, pad, start: sl.start, end: sl.end, ...(sl.stem ? { stem: sl.stem } : {}), ...how });
    const body = await r.json().catch(() => ({}));
    if (!r.ok) return { ok: false, error: body.error || `HTTP ${r.status}` };
    if (fit && Math.abs(body.ratio - 1) < 0.002) {   // already at the project tempo: play the plain slice
      store.dispatch({ type: "slice.stretch", bank, pad, stretch: null });
      return { ok: true, ...body, cleared: true };
    }
    store.dispatch({ type: "slice.stretch", bank, pad, stretch: { ratio: body.ratio, url: body.url, ...(fit ? { fit } : {}) } });
    await Promise.all([...pending]);
    return { ok: true, ...body };
  }

  // ---- project tempo: fit pads to it, and keep fitted pads fitted when it changes ----
  // A pad is "fitted" when its stretch carries `fit` (the project tempo it was made for); a stretch without it was made by
  // hand (stretchPad) and is never touched here. Pads that play a resample (`src`) are already at the project tempo.
  const fitState = () => {
    const { project, bank } = store.get();
    return { T: project.tempo?.bpm ?? null, overrides: project.overrides, bank, pattern: project.pattern };
  };
  const isFitted = (o) => !!o?.stretch && o.stretch.fit !== undefined;
  const isHand = (o) => !!o?.stretch && o.stretch.fit === undefined;
  async function fitKeys(keys, T) {
    const results = [], skipped = [];
    await Promise.all(keys.map(async (k) => {
      const [bank, pad] = k.split(":").map(Number);
      const o = store.get().project.overrides[k];
      if (isHand(o)) { skipped.push({ bank, pad, reason: "stretched by hand" }); return; }
      if (o?.kept) { skipped.push({ bank, pad, reason: "kept pad (owns its audio)" }); return; }
      if (o?.src) { skipped.push({ bank, pad, reason: "resample (already at the project tempo)" }); return; }
      if (!engine.getSlice(bank, pad)) return;
      if (isFitted(o) && o.stretch.fit === T) { skipped.push({ bank, pad, reason: "already fitted" }); return; }
      const r = await stretchPad(bank, pad, { target_bpm: T }, { fit: T });
      results.push({ bank, pad, ...r });
    }));
    return { ok: results.every((r) => r.ok), tempo: T, fitted: results, skipped };
  }
  let chain = Promise.resolve(null);
  /** Stretch the pads of `scope` ("pattern": every pad the pattern plays; "bank": the bank on screen) to the project tempo
   *  (ratio = the slice's song tempo / project tempo). Needs project.tempo.bpm. Never touches pads stretched by hand. */
  async function fitToTempo({ scope = "pattern" } = {}) {
    const { T, bank, pattern } = fitState();
    if (!T) return { ok: false, error: "set a project tempo first (tempo.set)", fitted: [], skipped: [] };
    let keys;
    if (scope === "bank") keys = Array.from({ length: PADS }, (_, p) => `${bank}:${p}`);
    else if (scope === "pattern") keys = [...new Set(pattern.events.map((e) => `${e.bank}:${e.pad}`))];   // kept pads are skipped in fitKeys
    else return { ok: false, error: 'scope must be "pattern" or "bank"', fitted: [], skipped: [] };
    chain = chain.then(() => fitKeys(keys, T));
    return chain;
  }
  let refitTimer = 0;
  let refitWaiters = [];
  /** Re-fit every pad that was fitted (any bank) to the project tempo; with no project tempo they go back to plain slices. */
  function refitAll() {
    const { T, overrides } = fitState();
    const keys = Object.keys(overrides).filter((k) => isFitted(overrides[k]) && !overrides[k].kept);
    if (!keys.length) return Promise.resolve({ ok: true, tempo: T, fitted: [], skipped: [] });
    if (T === null) {
      for (const k of keys) { const [bank, pad] = k.split(":").map(Number); store.dispatch({ type: "slice.stretch", bank, pad, stretch: null }); }
      return Promise.resolve({ ok: true, tempo: null, fitted: [], skipped: [], cleared: keys.length });
    }
    chain = chain.then(() => fitKeys(keys, T));
    return chain;
  }
  function scheduleRefit() {
    clearTimeout(refitTimer);
    refitTimer = setTimeout(() => {
      refitTimer = 0;
      const waiters = refitWaiters; refitWaiters = [];
      refitAll().then((r) => waiters.forEach((w) => w(r)), () => waiters.forEach((w) => w(null)));
    }, 150);
  }
  /** Resolves when a pending or running re-fit has finished (immediately when there is none). */
  const whenFitted = () => (refitTimer ? new Promise((res) => refitWaiters.push(res)) : chain);
  /** Set (or, with null, clear) the project tempo and wait for fitted pads to follow it. */
  async function setTempo(bpm, { follow } = {}) {
    store.dispatch({ type: "tempo.set", bpm, ...(follow === undefined ? {} : { follow }) });
    return whenFitted();
  }
  /** Stretch every pad on the current bank. Resolves the list of per-pad results (empty pads skipped). */
  async function stretchBank(how) {
    const { bank } = store.get();
    const jobs = [];
    for (let p = 0; p < PADS; p++) if (engine.getSlice(bank, p) && !engine.getSlice(bank, p).kept) jobs.push(stretchPad(bank, p, how).then((r) => ({ pad: p, ...r })));
    return Promise.all(jobs);
  }
  /** Stretch every pad the pattern uses (all banks it touches). */
  async function stretchPattern(how) {
    const seen = new Set(store.get().project.pattern.events.map((e) => `${e.bank}:${e.pad}`).filter((k) => !store.get().project.overrides[k]?.kept));
    return Promise.all([...seen].map((k) => { const [b, p] = k.split(":").map(Number); return stretchPad(b, p, how).then((r) => ({ bank: b, pad: p, ...r })); }));
  }
  // ---- kits: 16 pads from any songs (saved on the server in media/_kits/<id>/kit.json) ----
  /** Save what the current bank holds (slices, pad sounds, master fx) as a kit. Resolves { ok, kit, error }. */
  async function saveBankAsKit(name) {
    const { bank, project } = store.get();
    if (Array.from({ length: PADS }, (_, p) => engine.getSlice(bank, p)).some((c) => c?.kept)) {
      // a kit pad names a song slice, a kept pad owns its audio: the server says so (400) until step D; show its words
      const r = await postJson(`/api/songs/${sourceId}/savekit`, { bank, name: name || undefined, overrides: project.overrides, pads: project.pads, fx: project.fx });
      const body = await r.json().catch(() => ({}));
      if (!r.ok) return { ok: false, error: body.error || `HTTP ${r.status}` };
      window.dispatchEvent(new CustomEvent("choppad:kits"));
      return { ok: true, kit: body };
    }
    const pads = Array.from({ length: PADS }, (_, p) => {
      const sl = engine.getSlice(bank, p);
      if (!sl) return null;
      const k = { song_id: sl.song ?? sourceId, start: sl.start, end: sl.end, params: { ...project.pads[p] } };
      if (sl.stretch) k.stretch = { ...sl.stretch };
      if (sl.src) k.src = sl.src;       // W3b: a resample or a stem travels with the kit
      if (sl.stem) k.stem = sl.stem;
      return k;
    });
    const r = await postJson("/api/kits", { name: name || `${meta.title ?? sourceId} bank ${bank + 1}`, pads, fx: project.fx });
    const body = await r.json().catch(() => ({}));
    if (!r.ok) return { ok: false, error: body.error || `HTTP ${r.status}` };
    window.dispatchEvent(new CustomEvent("choppad:kits"));
    return { ok: true, kit: body };
  }
  /** Put a kit (object, or id to fetch) on the current bank. Pads from other songs load their audio once (cached).
   *  Resolves { ok, loaded, name, error }. */
  async function loadKit(kitOrId) {
    let kit = kitOrId;
    if (typeof kitOrId === "string") {
      const r = await fetch(`/api/kits/${kitOrId}`);
      if (!r.ok) return { ok: false, error: `kit ${kitOrId}: HTTP ${r.status}` };
      kit = await r.json();
    }
    store.dispatch({ type: "kit.load", bank: store.get().bank, song: sourceId, pads: kit.pads });
    if (kit.fx && Object.keys(kit.fx).length) store.dispatch({ type: "fx.set", params: kit.fx });
    await Promise.all([...pending]);
    return { ok: true, loaded: kit.pads.filter(Boolean).length, name: kit.name };
  }
  function clearStretch(bank, pad) { store.dispatch({ type: "slice.stretch", bank, pad, stretch: null }); }

  function panic() {
    stopSong();
    engine.stopAll();
    seq.stop();
    for (const p of [...held.keys()]) stopRepeat(p);
    emit("seq", {});
  }

  // ---- remote control: what an agent (or another tab) does arrives on /events and is played here ----
  const remoteMeta = { remote: true, save: false }; // already on disk, and not ours to send back
  // ---- CHOP-11: while a DAW bridge is open on the server, our own pad hits and pattern play/stop are reported to it ----
  // The server tells us on the stream (`daw` event, first message of every stream) whether one is open and which bank its
  // kit holds. Reports are `local: true` transport posts: the server hands them to the bridge and does NOT rebroadcast
  // them, so other tabs do not play them too. Nothing here waits for the network or can fail the audio trigger.
  const daw = { open: false, name: null, bank: null, tokens: 40, t: 0 };
  // Roles: with several tabs on one song only the primary one (the first to connect; the server says so with a `role` event)
  // executes transport commands, so an agent's `hit` or `play` sounds once, not once per tab. Others only display.
  let primary = true;
  let fromRemote = false; // true while executing a command that came from the stream: never reported back
  function dawSend(body) {
    send(`/api/mega/${mid}/transport`, { method: "POST", body: JSON.stringify({ ...body, local: true }) }).catch(() => {});
  }
  function dawHit(bank, pad, velocity) {
    if (fromRemote || !daw.open || (daw.bank != null && daw.bank !== bank)) return;
    const now = performance.now(); // 80 hits a second, bursts of 40: well above what hands can play
    daw.tokens = Math.min(40, daw.tokens + (now - daw.t) * 0.08);
    daw.t = now;
    if (daw.tokens < 1) return;
    daw.tokens -= 1;
    dawSend({ command: "hit", pad, velocity, bank });
  }
  function dawTransport(command) {
    if (!fromRemote && daw.open) dawSend({ command });
  }
  function runTransport(msg) {
    if (!primary) return;
    if (ctx.state !== "running") ctx.resume().catch(() => {});
    switch (msg.command) {
      case "play-pattern": if (!seq.state.playing) { fromRemote = true; try { togglePattern(); } finally { fromRemote = false; } } break;
      case "stop": panic(); break;
      case "play-song": playSong(Number.isFinite(msg.from) ? msg.from : undefined); break;
      case "resample":   // an agent asked this tab to render or record and put the result on a pad
        resample({ source: msg.source, bars: msg.bars, seconds: msg.seconds, pad: msg.pad, bank: msg.bank }).catch(() => {});
        break;
      case "hit":
        if (Number.isInteger(msg.bank)) setBank(msg.bank);
        fromRemote = true;
        try { if (Number.isInteger(msg.pad)) hit(msg.pad, Number.isFinite(msg.velocity) ? msg.velocity : 1, { raw: true }); } finally { fromRemote = false; }
        break;
    }
  }

  // ---- the source song: changed under a running session (contracts §15; neo: "i dont want it to completely refresh") ----
  // setSource(song) = option (b): the used pads are kept (the server renders them into the mega bank's samples/), the others
  // are released, and the new song comes in. Nothing is made again: same AudioContext, engine, sequencer, store, stream, video.
  // Order (see docs/mega-banks-step-b.md): 1 load the new song (audio decoded, files read) while the old one keeps working;
  // 2 flush our edits, POST /source; 3 decode the kept pads' sample files; 4 ONE synchronous step that swaps the engine's main
  // buffer, the video source, meta / analysis / peaks and applies the server's actions (slice.keep ... source.set). No audio
  // trigger can run inside step 4, and every kept pad is bound to its decoded sample in that same step, so there is no moment
  // where a kept pad plays the wrong audio. Any failure before step 4 leaves the old source exactly as it was.
  let sourceChain = Promise.resolve();   // source changes (and the remote batches that must wait for them), in order
  let switches = 0;                      // source changes queued or running
  let lastSourceError = null;   // { song, error, retry: { song, actions } }
  const keptOf = (actions) => actions.filter((a) => a.type === "slice.keep");
  /** Drop every decoded buffer nothing refers to any more (the old song's audio once no un-kept override uses it, ...). */
  function collectBuffers() {
    const keep = new Set(sourceId ? [`/media/${sourceId}/audio.wav`] : []);
    for (const o of Object.values(store.get().project.overrides)) { const u = sourceOf(o)?.url; if (u) keep.add(u); }
    for (const u of [...decoded.keys()]) if (!keep.has(u)) decoded.delete(u);
  }
  /** Step 4: synchronous. `next` = fetchSong result, `actions` = what the server did (keeps, tempo, source.set). */
  function commitSwap(songId, next, actions) {
    stopSong();   // the original audio of the old song does not play on under a new one
    buffer = next.buffer;
    songNorm = normalizationGain(buffer);
    decoded.set(`/media/${songId}/audio.wav`, buffer);
    engine.setBuffer(buffer);
    sourceId = songId;
    replaceKeys(meta, next.meta);
    replaceKeys(analysis, next.analysis);
    replaceKeys(peaks, next.peaks);
    video.src = `/media/${songId}/video.mp4`;
    lastChop = null;
    for (const a of actions) store.dispatch(a, remoteMeta);   // slice.keep ..., source.set: engine, chops and sequencer follow
    store.dispatch({ type: "sections.set", sections: [] }, remoteMeta);
    collectBuffers();
    lastSourceError = null;
    emit("source", { song: songId, meta });
    loadSections();
  }
  async function changeSource(songId, given) {
    if (songId === sourceId) {   // already the source: only what came with it (kept pads of another tab's change) is applied
      if (given) { await Promise.all(keptOf(given).map((a) => loadBuffer(a.src))); for (const a of given) store.dispatch(a, remoteMeta); }
      return { ok: true, song: songId, same: true };
    }
    const from = sourceId;
    let posted = false, reverted = false, acts = given, out = {};
    emit("source-loading", { song: songId });
    try {
      const next = await fetchSong(songId, ctx);
      if (!acts) {
        await flush();   // the server must know every hit recorded so far: those pads are "used"
        const r = await postJson(`/api/mega/${mid}/source`, { song: songId });
        const body = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(body.error || `HTTP ${r.status}`);
        posted = true;
        acts = body.actions ?? [];
        out = { kept: body.kept ?? [], released: body.released ?? 0, failed: body.failed ?? [] };
      }
      if (!acts.some((a) => a.type === "source.set")) acts = [...acts, { type: "source.set", song: songId, chops: next.chops }];
      await Promise.all(keptOf(acts).map((a) => loadBuffer(a.src)));
      commitSwap(songId, next, acts);
      return { ok: true, song: songId, ...out };
    } catch (e) {
      // the tab goes back to the old song, so the server must too (a mega bank that had no song cannot go back: no endpoint clears
      // the source, so a retry must take the server's batch as it is)
      if (posted && from) { try { await postJson(`/api/mega/${mid}/source`, { song: from }); reverted = true; } catch { /* the next resync sets it right */ } }
      const error = String(e?.message ?? e);
      const retry = { song: songId, actions: given ?? (posted && !reverted ? acts : null) };
      lastSourceError = { song: songId, error, retry };
      emit("source-error", { song: songId, error });
      return { ok: false, song: songId, error };
    }
  }
  /** Make `songId` the source song of this mega bank (kept pads stay, the pattern keeps playing). Resolves
   *  { ok, song, kept, released, failed } or { ok: false, error } (the old source then works as before). `actions` is for the
   *  stream: the batch another tab's change already made on the server. */
  function setSource(songId, { actions = null } = {}) {
    switches++;
    const run = sourceChain.then(() => changeSource(songId, actions));
    sourceChain = run.then(() => {}, () => {});
    return run.finally(() => { switches--; });
  }

  /** Keep one pad now (the KEEP key): the server renders its audio as played into the mega bank and the pad becomes a kept override.
   *  The calling tab does not get the batch on its stream, so it is applied here (buffer decoded first, then one dispatch).
   *  Waits behind a source change that is loading. Resolves { ok, kept, already, failed, error }. */
  function keepPad(bank, pad) {
    const run = sourceChain.then(async () => {
      try {
        await flush();
        const r = await postJson(`/api/mega/${mid}/keep`, { bank, pad });
        const body = await r.json().catch(() => ({}));
        if (!r.ok) return { ok: false, error: body.error || `HTTP ${r.status}`, kept: [], already: [], failed: [] };
        const acts = body.actions ?? [];
        await Promise.all(keptOf(acts).map((a) => loadBuffer(a.src)));
        for (const a of acts) store.dispatch(a, remoteMeta);
        const kept = body.kept ?? [], already = body.already ?? [], failed = body.failed ?? [];
        return { ok: kept.length + already.length > 0, kept, already, failed, error: failed[0]?.reason };
      } catch (e) {
        return { ok: false, error: String(e?.message ?? e), kept: [], already: [], failed: [] };
      }
    });
    sourceChain = run.then(() => {}, () => {});
    return run;
  }

  /** After a dropped connection: events were missed, so compare with what the server has and apply the difference. */
  async function resync() {
    const r = await fetch(`/api/mega/${mid}`);
    if (!r.ok) return;
    const d = await r.json();
    if (d.daw) { daw.open = !!d.daw.open; daw.name = d.daw.daw ?? null; daw.bank = Number.isInteger(d.daw.bank) ? d.daw.bank : null; } // CHOP-11
    const want = initialState({ project: d.project, chops: d.chops }).project;
    if (d.mega.source && d.mega.source !== sourceId) {   // the source changed while we were away: follow it, with its kept pads
      const have0 = store.get().project.overrides;
      const keeps = Object.entries(want.overrides).filter(([k, o]) => o?.kept && JSON.stringify(o) !== JSON.stringify(have0[k]))
        .map(([k, o]) => { const [bank, pad] = k.split(":").map(Number); return { type: "slice.keep", bank, pad, src: o.src, start: o.start, end: o.end, params: o.params, from: o.from, gain: o.gain }; });
      await setSource(d.mega.source, { actions: [...keeps, { type: "source.set", song: d.mega.source, chops: d.chops }] });
    }
    const have = store.get().project;
    const apply = (a) => store.dispatch(a, remoteMeta);
    // `bank: NO_BANK`: a pad's own sound, not that of a kept pad on the bank on screen (kept pads get theirs from slice.keep below)
    want.pads.forEach((P, pad) => { if (JSON.stringify(P) !== JSON.stringify(have.pads[pad])) apply({ type: "pad.set", pad, bank: NO_BANK, params: P }); });
    for (const k of Object.keys(have.overrides)) {
      if (!(k in want.overrides)) { const [bank, pad] = k.split(":").map(Number); apply({ type: "slice.clear", bank, pad }); }
    }
    for (const [k, sl] of Object.entries(want.overrides)) {
      if (JSON.stringify(sl) === JSON.stringify(have.overrides[k])) continue;
      const [bank, pad] = k.split(":").map(Number);
      if (sl.kept) {   // a kept pad: its own sample, sound and origin (a one-pad kit.load would drop kept, params and from)
        apply({ type: "slice.keep", bank, pad, src: sl.src, start: sl.start, end: sl.end, params: sl.params, from: sl.from, gain: sl.gain });
      } else if (sl.song || sl.stretch || sl.src || sl.stem) { // a slice with a source or a stretch travels as a one-pad kit.load
        const pads = Array(PADS).fill(null);
        pads[pad] = { song_id: sl.song ?? "", start: sl.start, end: sl.end, stretch: sl.stretch, src: sl.src, stem: sl.stem };
        apply({ type: "kit.load", bank, song: "", pads });
      } else apply({ type: "slice.set", bank, pad, start: sl.start, end: sl.end });
    }
    apply({ type: "fx.set", params: want.fx });
    apply({ type: "song.load", song: want.song ?? null });
    apply({ type: "tempo.set", bpm: want.tempo?.bpm ?? null, follow: want.tempo?.follow ?? true });
    apply({ type: "pattern.load", pattern: want.pattern });
    if (d.mega.source && JSON.stringify(d.chops) !== JSON.stringify(store.get().chops)) adoptChops(d.chops);
  }
  /** The DAW bridge endpoint (POST /api/songs/<id>/daw): command "open" | "sync" | "play" | "stop" for "hydrogen" | "ardour".
   *  open and sync take the bank on screen and this tab's swing / quant / choke. Resolves { ok, ...server answer } or { ok: false, error }. */
  async function dawCommand(name, command, { bank } = {}) {
    try {
      const { settings, bank: shown } = store.get();
      const body = { daw: name, command };
      if (command === "open" || command === "sync") Object.assign(body, { bank: bank ?? shown, swing: settings.swing, quant: settings.quant, choke: settings.choke });
      const r = await postJson(`/api/songs/${sourceId}/daw`, body);
      const out = await r.json().catch(() => ({}));
      if (!r.ok) return { ok: false, status: r.status, error: out.error || `HTTP ${r.status}` };
      return { ok: out.ok !== false, ...out };
    } catch (e) {
      return { ok: false, error: String(e?.message ?? e) };
    }
  }
  let stream = null;
  let streamDropped = false;
  if (typeof EventSource !== "undefined") {
    stream = new EventSource(`/api/mega/${mid}/events?origin=${ORIGIN}&role=1`);
    stream.addEventListener("open", () => {
      if (streamDropped) { streamDropped = false; resync().catch(() => {}); }
    });
    stream.addEventListener("error", () => { streamDropped = true; primary = false; }); // reconnects by itself and is told its role again
    const onMessage = (type, handle) => stream.addEventListener(type, (e) => {
      let data;
      try { data = JSON.parse(e.data); } catch { return; }
      if (data.origin === ORIGIN) return; // our own write coming back
      handle(data);
    });
    onMessage("actions", (d) => {
      const acts = d.actions ?? [];
      const sw = acts.find((a) => a.type === "source.set");
      if (sw) { setSource(sw.song, { actions: acts }); return; }   // another tab (or an agent) changed the source song
      const apply = () => { for (const a of acts) store.dispatch(a, remoteMeta); };
      if (switches > 0) sourceChain = sourceChain.then(apply); else apply();   // stay behind a change that is loading
    });
    onMessage("chops", (d) => adoptChops(d.chops));
    onMessage("transport", runTransport);
    onMessage("role", (d) => { primary = !!d.primary; });
    onMessage("daw", (d) => { daw.open = !!d.open; daw.name = d.daw ?? null; daw.bank = Number.isInteger(d.bank) ? d.bank : null; });
  }

  return {
    get id() { return sourceId; },   // the open source song
    get source() { return sourceId; },
    mega: mid,
    get buffer() { return buffer; },
    meta, analysis, peaks, video, store, engine, seq,
    setSource,
    get lastSourceError() { return lastSourceError; },
    /** Try the source change that failed again (a tab that followed another tab's switch takes the same batch again). */
    retrySource() { const r = lastSourceError?.retry; return r ? setSource(r.song, r.actions ? { actions: r.actions } : {}) : Promise.resolve({ ok: false, error: "nothing to retry" }); },
    keepPad,
    /** Bar length (beats) of the pattern: the one it was made with; not the open song's when a song of another meter came in. */
    get patternBeatsPerBar() { return patternBpb; },
    get switching() { return switches > 0; },
    /** urls of the decoded buffers held right now (tests: what stays in memory) */
    heldBuffers: () => [...decoded.keys()],
    remote: { origin: ORIGIN, get connected() { return stream?.readyState === 1; }, get daw() { return { open: daw.open, bank: daw.bank }; }, get dawName() { return daw.name; }, get primary() { return primary; }, flush },
    on: (type, fn) => events.addEventListener(type, fn),
    off: (type, fn) => events.removeEventListener(type, fn),
    position, playSong, stopSong,
    get songPlaying() { return !!song; },
    get lastChop() { return lastChop; },
    chopLabel, setBank, followTo, hit, setSixteenLevels,
    get sixteen() { return store.get().settings.sixteen; },
    release: (pad) => engine.release(store.get().settings.sixteen?.pad ?? pad),
    startRepeat, stopRepeat,
    regrid, reanalyze,
    toggleRecord, togglePattern, patternMidi, renderPatternWav, resample, songMidi, renderSongWav, separateStems, setStem,
    /** Where the play head is: { beat, index, id, inPattern } (beats in the whole loop / the chain entry / its pattern). */
    songPosition: () => seq.locate(),
    loadSections, advise, loadAiBank, exportBank,
    stretchPad, stretchBank, stretchPattern, clearStretch, saveBankAsKit, loadKit, detectKey,
    dawCommand, setTempo, fitToTempo, refitAll, whenFitted, get tempoBpm() { return projectBpm(store.get()); },
    panic,
    destroy() {
      unsubscribeOutbox();
      window.removeEventListener("pagehide", onPageHide);
      flush(); // edits made in the last moments still reach the server
      stream?.close();
      unsubscribe();
      for (const t of timers) clearTimeout(t);
      clearTimeout(refitTimer);
      panic();
      video.removeAttribute("src");
      video.load();
    },
  };
}
