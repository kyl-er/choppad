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
});
export const PADS = 16;
export const DEFAULT_TEMPO = Object.freeze({ bpm: null, follow: true });
export const TEMPO_MIN = 20, TEMPO_MAX = 400;

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

/** Build the starting state from what the server returns for a song. */
export function initialState({ project = {}, chops, sections = [] } = {}) {
  const pattern = project?.pattern;
  return {
    project: {
      pads: Array.from({ length: PADS }, (_, p) => ({ ...DEFAULT_PAD, ...cleanPad(project?.pads?.[p]) })),
      overrides: { ...(project?.overrides ?? {}) },
      pattern: { bars: pattern?.bars ?? 2, events: [...(pattern?.events ?? [])] },
      fx: { ...DEFAULT_FX, ...pick(project?.fx, FX_KEYS) },
      // optional keys: absent in old projects (and until first used), which then mean the defaults
      ...(cleanTempo(project?.tempo) ? { tempo: cleanTempo(project.tempo) } : {}),
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
const clampBank = (state, n) => Math.max(0, Math.min((state.chops?.banks?.length ?? 1) - 1, n));

/** Pure reducer. Unknown or invalid actions return the same state object (so nothing re-renders or saves). */
export function reduce(state, action) {
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
      let base = cur;
      if (!base) {
        const ch = state.chops?.chops?.[state.chops?.banks?.[a.bank]?.[a.pad]];
        if (!ch) return state;
        base = { start: ch.start, end: ch.end };
      }
      return withProject(state, { overrides: { ...state.project.overrides, [key]: { ...base, stretch: cleanStretch(a.stretch) } } });
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
        : [...pattern.events, { beat: a.step / 4, pad: a.pad, bank: a.bank ?? state.bank, vel: a.vel ?? 1, pass: -1 }];
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
      if (same(state.settings, params)) return state;
      return { ...state, settings: { ...state.settings, ...params } };
    }
    default:
      return state;
  }
}
