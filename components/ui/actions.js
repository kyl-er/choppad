// actions: every change to a song's state, as a pure reducer. No DOM, no WebAudio, no network.
// The same action objects are what an agent or the server sends (contracts/README.md §8), and
// contracts/fixtures/actions.json holds the cases any other implementation must reproduce.
//
// State:
//   project  { pads[16], overrides{"bank:pad": {start,end}}, pattern{bars, events[]}, fx{} }   saved per song
//   chops    the chop table from the server (replaced on regrid)
//   sections [{label, letter, start_s, end_s, start_bar, end_bar}]
//   bank, selected                                                                            what the player is looking at
//   settings { mode, choke, fullLevel, quant, swing, metronome, repeat, repeatRate, follow }   not saved

import { DEFAULT_FILTER, DEFAULT_PAD, FILTER_TYPES } from "./engine.js";
import { DEFAULT_FX } from "./fx.js";

export { DEFAULT_PAD, DEFAULT_FX };
export const DEFAULT_SETTINGS = Object.freeze({
  mode: "oneshot", choke: true, fullLevel: true,
  quant: "1/16", swing: 0, metronome: true,
  repeat: false, repeatRate: 0.25, follow: true,
  sixteen: null,   // W3b: 16 levels, {pad, mode: "velocity" | "pitch" | "filter"} or null. A view setting, never saved.
});
/** W3b 16 levels: what pressed pad `i` (0..15) plays of the source pad, by mode. velocity = (i + 1) / 16, pitch = -7 .. +8
 *  semitones around the pad's own, cutoff = 150 Hz .. 16 kHz in 16 equal ratios. Pure. */
export const SIXTEEN_MODES = Object.freeze(["velocity", "pitch", "filter"]);
export function sixteenLevel(mode, i) {
  if (!Number.isInteger(i) || i < 0 || i > 15) return null;
  if (mode === "velocity") return { vel: (i + 1) / 16 };
  if (mode === "pitch") return { pitch: i - 7 };
  if (mode === "filter") return { cutoff: Math.round(150 * Math.pow(16000 / 150, i / 15)) };
  return null;
}
export const PADS = 16;
export const DEFAULT_TEMPO = Object.freeze({ bpm: null, follow: true });
export const TEMPO_MIN = 20, TEMPO_MAX = 400;
/** Files of a song a pad can play instead of its audio (contracts §13): a resample url, or a stem by name. */
export const STEMS = Object.freeze(["drums", "bass", "vocals", "other"]);
export const RESAMPLE_URL = /^\/media\/[A-Za-z0-9_-]{1,64}\/resample\/[0-9a-f]{16}\.wav$/;
export const stemUrl = (song, stem) => `/media/${song}/stems/${stem}.wav`;

const PAD_KEYS = Object.keys(DEFAULT_PAD);
const FX_KEYS = Object.keys(DEFAULT_FX);
const SETTING_KEYS = Object.keys(DEFAULT_SETTINGS);
const isPad = (p) => Number.isInteger(p) && p >= 0 && p < PADS;
const pick = (obj, keys) => Object.fromEntries(keys.filter((k) => obj && k in obj).map((k) => [k, obj[k]]));
const isNum = (x) => typeof x === "number" && Number.isFinite(x);
const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
/** Deep equality for JSON values (pad filters are objects). */
const eq = (a, b) => a === b || (!!a && !!b && typeof a === "object" && typeof b === "object" && Array.isArray(a) === Array.isArray(b)
  && Object.keys(a).length === Object.keys(b).length && Object.keys(a).every((k) => eq(a[k], b[k])));
const same = (a, b) => Object.keys(b).every((k) => eq(a[k], b[k]));

/** Pure: a filter {type, cutoff, q} made from `f` on top of `base`; fields that are not valid are ignored. null when `f` is no object. */
function cleanFilter(f, base) {
  if (!f || typeof f !== "object" || Array.isArray(f)) return null;
  const o = { ...base };
  if (FILTER_TYPES.includes(f.type)) o.type = f.type;
  if (isNum(f.cutoff)) o.cutoff = clamp(f.cutoff, 20, 20000);
  if (isNum(f.q)) o.q = clamp(f.q, 0.1, 24);
  return o;
}
/** Pure: the valid part of pad params. gainDb/pitch/attack/release/reverse/mode pass through as before; the W3b keys are
 *  validated (decay 0..10 s, sustain 0..1, filter merged onto the pad's current filter). */
export function cleanPad(raw, cur = DEFAULT_PAD) {
  const out = pick(raw, PAD_KEYS);
  if ("decay" in out) { if (isNum(out.decay)) out.decay = clamp(out.decay, 0, 10); else delete out.decay; }
  if ("muteGroup" in out && !(Number.isInteger(out.muteGroup) && out.muteGroup >= 0 && out.muteGroup <= 8)) delete out.muteGroup;
  if ("sustain" in out) { if (isNum(out.sustain)) out.sustain = clamp(out.sustain, 0, 1); else delete out.sustain; }
  if ("filter" in out) { const f = cleanFilter(out.filter, cur.filter ?? DEFAULT_FILTER); if (f) out.filter = f; else delete out.filter; }
  return out;
}

/** Tempo the sequencer, metronome, note repeat and every render use: the project tempo when set, else the song's own. */
export const projectBpm = (state) => state.project.tempo?.bpm ?? state.chops?.bpm;

/** Pure: a clean {bpm, follow} from a saved or sent tempo, or null when it is not one. */
function cleanTempo(t) {
  if (!t || typeof t !== "object") return null;
  const bpm = t.bpm === null || t.bpm === undefined ? null : isNum(t.bpm) && t.bpm >= TEMPO_MIN && t.bpm <= TEMPO_MAX ? t.bpm : undefined;
  if (bpm === undefined) return null;
  return { bpm, follow: typeof t.follow === "boolean" ? t.follow : true };
}

// ---- W3b song mode: project.song = { patterns: [{id, name, bars, events}], chain: [{pattern, repeats}], mode, current } ----
// `project.pattern` stays the pattern being edited; `current` names the entry of `patterns` it belongs to (null = none yet, an old
// project). Every pattern.* action is mirrored into that entry, so `patterns` is always up to date. The key is absent until used.
export const DEFAULT_SONG = Object.freeze({ patterns: [], chain: [], mode: "pattern", current: null });
export const MAX_PATTERNS = 64, MAX_CHAIN = 256, MAX_REPEATS = 64;
const nextPatternId = (patterns) => `p${1 + Math.max(0, ...patterns.map((p) => /^p(\d+)$/.exec(p.id)?.[1] | 0))}`;
const cleanName = (n, fallback) => (typeof n === "string" && n.trim() && n.trim().length <= 40 ? n.trim() : fallback);
const cleanEvents = (events) => (Array.isArray(events) ? events : []).filter((e) => e && typeof e === "object" && isPad(e.pad) && isNum(e.beat)).map((e) => ({ ...e }));
function cleanChain(chain, patterns) {
  if (!Array.isArray(chain)) return [];
  const out = [];
  for (const c of chain.slice(0, MAX_CHAIN)) {
    if (!c || typeof c.pattern !== "string" || !patterns.some((p) => p.id === c.pattern)) continue;
    if (c.repeats !== undefined && !isNum(c.repeats)) continue;
    out.push({ pattern: c.pattern, repeats: clamp(Math.round(c.repeats ?? 1), 1, MAX_REPEATS) });
  }
  return out;
}
/** Pure: a clean song from a saved or sent one, or null when it is just the default (nothing to save).
 *  `pattern` = the edited pattern, which wins over the copy in `patterns[current]`. */
export function cleanSong(raw, pattern) {
  if (!raw || typeof raw !== "object") return null;
  const seen = new Set();
  let patterns = [];
  for (const p of (Array.isArray(raw.patterns) ? raw.patterns : []).slice(0, MAX_PATTERNS)) {
    if (!p || typeof p.id !== "string" || !/^[A-Za-z0-9_-]{1,32}$/.test(p.id) || seen.has(p.id)) continue;
    seen.add(p.id);
    patterns.push({ id: p.id, name: cleanName(p.name, `Pattern ${patterns.length + 1}`), bars: isNum(p.bars) && p.bars >= 1 ? p.bars : 2, events: cleanEvents(p.events) });
  }
  const chain = cleanChain(raw.chain, patterns);
  const mode = raw.mode === "song" ? "song" : "pattern";
  const current = patterns.some((p) => p.id === raw.current) ? raw.current : null;
  if (current !== null && pattern) patterns = patterns.map((p) => (p.id === current ? { ...p, bars: pattern.bars, events: pattern.events } : p));
  if (!patterns.length && !chain.length && mode === "pattern") return null;
  return { patterns, chain, mode, current };
}
/** Pure: the playing order as a timeline { total, segments: [{start, len, id, events}], chain }, all in beats from 0. In song
 *  mode (or with `force`) with a chain, every chain entry repeats its pattern; otherwise the edited pattern alone. */
export function songTimeline(project, beatsPerBar = 4, { force = false } = {}) {
  const s = project.song;
  const single = () => {
    const len = project.pattern.bars * beatsPerBar;
    return { total: len, chain: false, segments: [{ start: 0, len, id: s?.current ?? null, events: project.pattern.events }] };
  };
  if (!s || !s.chain.length || !(s.mode === "song" || force)) return single();
  const segments = [];
  let cursor = 0;
  for (const c of s.chain) {
    const p = s.patterns.find((x) => x.id === c.pattern);
    if (!p) continue;
    const len = p.bars * beatsPerBar;
    const events = p.events.filter((e) => e.beat < len);
    for (let r = 0; r < c.repeats; r++) { segments.push({ start: cursor, len, id: p.id, events }); cursor += len; }
  }
  return segments.length ? { total: cursor, chain: true, segments } : single();
}
/** Pure: the timeline's events with their beats made absolute. */
export const timelineEvents = (tl) => tl.segments.flatMap((seg) => seg.events.map((e) => ({ ...e, beat: e.beat + seg.start })));

/** Build the starting state from what the server returns for a song. */
export function initialState({ project = {}, chops, sections = [] } = {}) {
  const pattern = project?.pattern;
  const pat = { bars: pattern?.bars ?? 2, events: [...(pattern?.events ?? [])] };
  const song = cleanSong(project?.song, pat);
  return {
    project: {
      pads: Array.from({ length: PADS }, (_, p) => ({ ...DEFAULT_PAD, ...cleanPad(project?.pads?.[p]) })),
      overrides: { ...(project?.overrides ?? {}) },
      pattern: pat,
      fx: { ...DEFAULT_FX, ...pick(project?.fx, FX_KEYS) },
      // optional keys: absent in old projects (and until first used), which then mean the defaults
      ...(cleanTempo(project?.tempo) ? { tempo: cleanTempo(project.tempo) } : {}),
      ...(song ? { song } : {}),
    },
    chops,
    sections,
    bank: 0,
    selected: 0,
    settings: { ...DEFAULT_SETTINGS },
  };
}

/** The part of the state that is written to project.json. */
export const projectDoc = (state) => state.project;

/** Step index (1/16 notes from the loop start) of a pattern event. */
export const stepOf = (event) => Math.round(event.beat * 4);

/** {ratio, url[, fit]}: `fit` = the project tempo this rendering was made for (absent = stretched by hand). */
const cleanStretch = (st) => ({ ratio: Number(st.ratio), url: st.url, ...(isNum(st.fit) && st.fit > 0 ? { fit: st.fit } : {}) });

const withProject = (state, patch) => ({ ...state, project: { ...state.project, ...patch } });
/** A project with `song` set; a song that is just the default is not kept (the key is absent in old projects). */
const withSong = (state, song) => {
  if (!song.patterns.length && !song.chain.length && song.mode === "pattern" && song.current === null) {
    const { song: _s, ...project } = state.project;
    return { ...state, project };
  }
  return withProject(state, { song });
};
/** Copy the edited pattern into its entry of project.song.patterns. */
function mirrorCurrent(state) {
  const song = state.project.song;
  if (!song || song.current === null) return state;
  const i = song.patterns.findIndex((p) => p.id === song.current);
  const pat = state.project.pattern;
  if (i < 0 || (song.patterns[i].bars === pat.bars && song.patterns[i].events === pat.events)) return state;
  return withProject(state, { song: { ...song, patterns: song.patterns.map((p, k) => (k === i ? { ...p, bars: pat.bars, events: pat.events } : p)) } });
}
const clampBank = (state, n) => Math.max(0, Math.min((state.chops?.banks?.length ?? 1) - 1, n));

/** Pure reducer. Unknown or invalid actions return the same state object (so nothing re-renders or saves). */
export function reduce(state, action) {
  const next = reduceCore(state, action);
  return next !== state && typeof action?.type === "string" && action.type.startsWith("pattern.") ? mirrorCurrent(next) : next;
}

function reduceCore(state, action) {
  const a = action ?? {};
  switch (a.type) {
    case "pad.set": {
      if (!isPad(a.pad)) return state;
      const params = cleanPad(a.params, state.project.pads[a.pad]);
      if (same(state.project.pads[a.pad], params)) return state;
      const pads = state.project.pads.map((P, p) => (p === a.pad ? { ...P, ...params } : P));
      return withProject(state, { pads });
    }
    case "pad.reset": {
      if (!isPad(a.pad)) return state;
      return withProject(state, { pads: state.project.pads.map((P, p) => (p === a.pad ? { ...DEFAULT_PAD } : P)) });
    }
    case "pad.copyToAll": {
      if (!isPad(a.pad)) return state;
      const src = state.project.pads[a.pad];
      return withProject(state, { pads: state.project.pads.map(() => ({ ...src })) });
    }
    case "pad.select":
      return isPad(a.pad) && a.pad !== state.selected ? { ...state, selected: a.pad } : state;

    case "slice.set": {
      if (!isPad(a.pad) || !(a.end > a.start)) return state;
      return withProject(state, { overrides: { ...state.project.overrides, [`${a.bank}:${a.pad}`]: { start: a.start, end: a.end } } });
    }
    case "slice.clear": {
      const key = `${a.bank}:${a.pad}`;
      if (!(key in state.project.overrides)) return state;
      const { [key]: _gone, ...overrides } = state.project.overrides;
      return withProject(state, { overrides });
    }
    case "slice.setMany": {
      const overrides = { ...state.project.overrides };
      for (const s of a.slices ?? []) {
        if (isPad(s.pad) && s.end > s.start) overrides[`${a.bank}:${s.pad}`] = { start: s.start, end: s.end };
      }
      return withProject(state, { overrides });
    }

    case "slice.stretch": {   // W3: attach (or, with stretch null, remove) a time-stretched rendering of the pad's slice
      if (!isPad(a.pad)) return state;
      const key = `${a.bank}:${a.pad}`;
      const cur = state.project.overrides[key];
      if (!a.stretch) {
        if (!cur?.stretch) return state;
        const { stretch: _s, ...rest } = cur;
        return withProject(state, { overrides: { ...state.project.overrides, [key]: rest } });
      }
      const ratio = Number(a.stretch.ratio);
      if (!(ratio > 0) || typeof a.stretch.url !== "string" || !a.stretch.url) return state;
      if (cur?.src) return state;   // a resample is already at the project tempo and has no slice of the song to stretch
      let base = cur;
      if (!base) {
        const ch = state.chops?.chops?.[state.chops?.banks?.[a.bank]?.[a.pad]];
        if (!ch) return state;
        base = { start: ch.start, end: ch.end };
      }
      return withProject(state, { overrides: { ...state.project.overrides, [key]: { ...base, stretch: cleanStretch(a.stretch) } } });
    }

    case "slice.src": {   // W3b: put a rendered file of the song (a resample) on a pad; src null removes it again
      const bank = a.bank;
      if (!isPad(a.pad) || !Number.isInteger(bank) || bank < 0) return state;
      const key = `${bank}:${a.pad}`;
      const cur = state.project.overrides[key];
      if (a.src === null) {
        if (!cur?.src) return state;
        const { [key]: _gone, ...overrides } = state.project.overrides;
        return withProject(state, { overrides });
      }
      if (typeof a.src !== "string" || !RESAMPLE_URL.test(a.src) || !isNum(a.start) || !isNum(a.end) || !(a.start >= 0) || !(a.end > a.start)) return state;
      return withProject(state, { overrides: { ...state.project.overrides, [key]: { start: a.start, end: a.end, src: a.src } } });
    }
    case "slice.stem": {   // W3b: the pad plays its slice from a stem of the song (drums, bass, vocals, other); null = the full mix again
      const bank = a.bank;
      if (!isPad(a.pad) || !Number.isInteger(bank) || bank < 0) return state;
      const key = `${bank}:${a.pad}`;
      const cur = state.project.overrides[key];
      if (a.stem === null) {
        if (!cur?.stem) return state;
        const { stem: _s, stretch: _t, ...rest } = cur;   // a stretch was rendered from the stem: it goes with it
        return withProject(state, { overrides: { ...state.project.overrides, [key]: rest } });
      }
      if (!STEMS.includes(a.stem) || cur?.src) return state;
      if (cur?.stem === a.stem) return state;
      let base = cur;
      if (!base) {
        const ch = state.chops?.chops?.[state.chops?.banks?.[bank]?.[a.pad]];
        if (!ch) return state;
        base = { start: ch.start, end: ch.end };
      }
      const { stretch: _x, ...rest } = base;
      return withProject(state, { overrides: { ...state.project.overrides, [key]: { ...rest, stem: a.stem } } });
    }

    case "kit.load": {   // W3: put a kit's pads (kit.json `pads`, 16 entries or null) on a bank; null = leave the pad alone
      const bank = Number(a.bank);
      if (!Number.isInteger(bank) || bank < 0 || !Array.isArray(a.pads)) return state;
      const overrides = { ...state.project.overrides };
      let pads = state.project.pads;
      a.pads.slice(0, PADS).forEach((k, pad) => {
        if (!k || typeof k.song_id !== "string" || !(k.end > k.start) || !(k.start >= 0)) return;
        const o = { start: k.start, end: k.end };
        if (k.song_id !== a.song) o.song = k.song_id;   // `song` = the open song's id: its own slices need no source
        if (k.stretch && k.stretch.url && Number(k.stretch.ratio) > 0) o.stretch = cleanStretch(k.stretch);
        if (typeof k.src === "string" && RESAMPLE_URL.test(k.src)) o.src = k.src;
        else if (STEMS.includes(k.stem)) o.stem = k.stem;
        overrides[`${bank}:${pad}`] = o;
        const params = cleanPad(k.params, pads[pad]);
        if (Object.keys(params).length) pads = pads.map((P, i) => (i === pad ? { ...P, ...params } : P));
      });
      return withProject(state, { overrides, pads });
    }

    case "tempo.set": {   // W3b: project tempo; bpm null = the song's own tempo
      const cur = state.project.tempo ?? DEFAULT_TEMPO;
      const bpm = a.bpm === undefined ? cur.bpm : a.bpm;   // bpm omitted = keep it (set only `follow`)
      if (bpm !== null && !(isNum(bpm) && bpm >= TEMPO_MIN && bpm <= TEMPO_MAX)) return state;
      const follow = typeof a.follow === "boolean" ? a.follow : cur.follow;
      if (bpm === cur.bpm && follow === cur.follow) return state;
      return withProject(state, { tempo: { bpm, follow } });
    }

    case "fx.set": {
      const params = pick(a.params, FX_KEYS);
      if (same(state.project.fx, params)) return state;
      return withProject(state, { fx: { ...state.project.fx, ...params } });
    }

    case "pattern.toggleStep": {
      if (!isPad(a.pad) || !(a.step >= 0)) return state;
      const { pattern } = state.project;
      const i = pattern.events.findIndex((e) => e.pad === a.pad && stepOf(e) === a.step);
      const events = i >= 0
        ? pattern.events.filter((_, k) => k !== i)
        // pass -1: drawn by hand, not recorded, so "undo last take" leaves it alone
        : [...pattern.events, { beat: a.step / 4, pad: a.pad, bank: a.bank ?? state.bank, vel: a.vel ?? 1, pass: -1,
          // W3b: a hit can carry its own pitch offset (semitones) and cutoff (Hz), as recorded in 16 levels
          ...(isNum(a.pitch) ? { pitch: a.pitch } : {}), ...(isNum(a.cutoff) ? { cutoff: a.cutoff } : {}) }];
      return withProject(state, { pattern: { ...pattern, events } });
    }
    case "pattern.addEvent": {
      const e = a.event;
      if (!e || !isPad(e.pad)) return state;
      const { pattern } = state.project;
      return withProject(state, { pattern: { ...pattern, events: [...pattern.events, { ...e }] } });
    }
    case "pattern.undo": {
      const { pattern } = state.project;
      const last = Math.max(0, ...pattern.events.map((e) => e.pass));
      const events = pattern.events.filter((e) => e.pass !== last);
      return events.length === pattern.events.length ? state : withProject(state, { pattern: { ...pattern, events } });
    }
    case "pattern.clear": {
      const { pattern } = state.project;
      return pattern.events.length ? withProject(state, { pattern: { ...pattern, events: [] } }) : state;
    }
    case "pattern.setBars": {
      const bars = Number(a.bars);
      if (!(bars >= 1)) return state;
      const loopBeats = bars * (a.beatsPerBar ?? state.chops?.beats_per_bar ?? 4);
      const events = state.project.pattern.events.filter((e) => e.beat < loopBeats);
      return withProject(state, { pattern: { bars, events } });
    }
    case "pattern.load": {
      const p = a.pattern ?? {};
      return withProject(state, { pattern: { bars: p.bars ?? state.project.pattern.bars, events: [...(p.events ?? [])] } });
    }

    case "song.addPattern": {   // W3b: a new pattern, which becomes the one being edited ({name?, bars?, copy?: bool})
      const song = state.project.song ?? DEFAULT_SONG;
      const pat = state.project.pattern;
      let patterns = song.patterns;
      let current = song.current;
      if (patterns.length >= MAX_PATTERNS - 1) return state;
      if (current === null) {   // the pattern being edited so far becomes "Pattern 1"
        current = nextPatternId(patterns);
        patterns = [...patterns, { id: current, name: `Pattern ${patterns.length + 1}`, bars: pat.bars, events: pat.events }];
      }
      const id = nextPatternId(patterns);
      const bars = isNum(a.bars) && a.bars >= 1 ? a.bars : pat.bars;
      const bpb = a.beatsPerBar ?? state.chops?.beats_per_bar ?? 4;
      const events = a.copy === true ? pat.events.filter((e) => e.beat < bars * bpb).map((e) => ({ ...e })) : [];
      patterns = [...patterns, { id, name: cleanName(a.name, `Pattern ${patterns.length + 1}`), bars, events }];
      return withSong({ ...state, project: { ...state.project, pattern: { bars, events } } }, { ...song, patterns, current: id });
    }
    case "song.selectPattern": {   // the pattern to edit
      const song = state.project.song;
      const p = song?.patterns.find((x) => x.id === a.id);
      if (!p || song.current === a.id) return state;
      return withSong({ ...state, project: { ...state.project, pattern: { bars: p.bars, events: p.events.map((e) => ({ ...e })) } } }, { ...song, current: p.id });
    }
    case "song.renamePattern": {
      const song = state.project.song;
      const i = song ? song.patterns.findIndex((x) => x.id === a.id) : -1;
      const name = cleanName(a.name, null);
      if (i < 0 || name === null || song.patterns[i].name === name) return state;
      return withSong(state, { ...song, patterns: song.patterns.map((p, k) => (k === i ? { ...p, name } : p)) });
    }
    case "song.removePattern": {   // also leaves the chain; removing the edited pattern selects the first one left
      const song = state.project.song;
      if (!song || !song.patterns.some((x) => x.id === a.id)) return state;
      const patterns = song.patterns.filter((p) => p.id !== a.id);
      const chain = song.chain.filter((c) => c.pattern !== a.id);
      let next = { ...song, patterns, chain };
      let st = state;
      if (song.current === a.id) {
        const first = patterns[0];
        next = { ...next, current: first ? first.id : null };
        if (first) st = { ...state, project: { ...state.project, pattern: { bars: first.bars, events: first.events.map((e) => ({ ...e })) } } };
      }
      return withSong(st, next);
    }
    case "song.setChain": {   // [{pattern: id, repeats: 1..64}]; entries naming a pattern that does not exist are dropped
      const song = state.project.song ?? DEFAULT_SONG;
      const chain = cleanChain(a.chain, song.patterns);
      if (eq(chain, song.chain)) return state;
      return withSong(state, { ...song, chain });
    }
    case "song.setMode": {   // "pattern" loops the edited pattern, "song" plays the chain
      if (a.mode !== "pattern" && a.mode !== "song") return state;
      const song = state.project.song ?? DEFAULT_SONG;
      if (song.mode === a.mode) return state;
      return withSong(state, { ...song, mode: a.mode });
    }
    case "song.load": {   // replace the whole song document (used to sync tabs and by agents); null clears it
      const song = a.song === null ? null : cleanSong(a.song, state.project.pattern);
      if (a.song !== null && !a.song) return state;
      if (eq(song, state.project.song ?? null)) return state;
      return song ? withProject(state, { song }) : withSong(state, { ...DEFAULT_SONG });
    }

    case "bank.set": {
      const bank = clampBank(state, Number(a.bank));
      return bank === state.bank ? state : { ...state, bank };
    }
    case "chops.replace":
      return a.chops ? { ...state, chops: a.chops, bank: 0 } : state;
    case "sections.set":
      return { ...state, sections: [...(a.sections ?? [])] };

    case "settings.set": {
      const params = pick(a.params, SETTING_KEYS);
      if ("sixteen" in params) {   // null, or a source pad and a mode; anything else is ignored
        const v = params.sixteen;
        if (v === null) { /* off */ } else if (v && typeof v === "object" && isPad(v.pad) && SIXTEEN_MODES.includes(v.mode)) params.sixteen = { pad: v.pad, mode: v.mode };
        else delete params.sixteen;
      }
      if (same(state.settings, params)) return state;
      return { ...state, settings: { ...state.settings, ...params } };
    }
    default:
      return state;
  }
}
