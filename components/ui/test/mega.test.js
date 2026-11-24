// CHOP-13: usedPads, bankCount, state.source / state.mega and the kept-pad actions in the browser reducer (the shared
// fixture cases are in contracts/fixtures/actions.json, run by actions.test.js).
import { test } from "node:test";
import assert from "node:assert/strict";
import { DEFAULT_PAD, MEGA_SAMPLE_URL, bankCount, initialState, reduce, usedPads } from "../actions.js";

const SRC = "/media/_mega/m1/samples/0123456789abcdef.wav";
const CHOPS = { grid: "1/4", bpm: 120, beats_per_bar: 4, banks: [[0], [1], [2]] };
const keep = (bank, pad, extra = {}) => ({ type: "slice.keep", bank, pad, src: SRC, start: 0, end: 1.5, ...extra });

test("usedPads: overrides and the events of the pattern and of every song pattern, sorted", () => {
  const project = {
    overrides: { "1:3": { start: 0, end: 1 }, x: {}, "0:16": {} },
    pattern: { bars: 2, events: [{ beat: 0, pad: 2, bank: 0 }, { beat: 1, pad: 3 }, { beat: 1, pad: 99 }] },
    song: { patterns: [{ id: "p1", events: [{ beat: 0, pad: 5, bank: 2 }] }, { id: "p2", events: [] }] },
  };
  assert.deepEqual(usedPads(project), [[0, 2], [0, 3], [1, 3], [2, 5]]);
  assert.deepEqual(usedPads({}), []);
  assert.deepEqual(usedPads(initialState({ project }).project), [[0, 2], [0, 3], [1, 3], [2, 5]]);
});

test("bankCount: the source's banks or the banks kept pads need, at least one", () => {
  assert.equal(bankCount({ overrides: {} }, CHOPS), 3);
  assert.equal(bankCount({ overrides: { "6:0": {} } }, CHOPS), 7);
  assert.equal(bankCount({ overrides: {} }, undefined), 1);
  assert.equal(bankCount({ overrides: {} }, { banks: [] }), 1);
});

test("initialState carries source and mega", () => {
  const s = initialState({ chops: CHOPS, source: "a", mega: "m1" });
  assert.deepEqual([s.source, s.mega], ["a", "m1"]);
  const t = initialState({ chops: CHOPS });
  assert.deepEqual([t.source, t.mega], [null, null]);
});

test("slice.keep builds the kept override and is idempotent", () => {
  let s = initialState({ chops: CHOPS });
  s = reduce(s, keep(1, 2, { params: { gainDb: -4, filter: { type: "lp", cutoff: 900 } }, from: { song: "a", start: 2, end: 3.5 } }));
  const o = s.project.overrides["1:2"];
  assert.deepEqual(Object.keys(o).sort(), ["end", "from", "kept", "params", "src", "start"]);
  assert.equal(o.kept, true);
  assert.deepEqual(o.params, { ...DEFAULT_PAD, gainDb: -4, filter: { type: "lp", cutoff: 900, q: 0.7 } });
  assert.equal(reduce(s, keep(1, 2, { params: { gainDb: -4, filter: { type: "lp", cutoff: 900 } }, from: { song: "a", start: 2, end: 3.5 } })), s);
});

test("slice.keep refuses urls that are not a mega sample", () => {
  const s = initialState({ chops: CHOPS });
  for (const src of ["/media/a/resample/0123456789abcdef.wav", "/media/_mega/../x/samples/0123456789abcdef.wav",
    "/media/_mega/m1/samples/0123456789abcdef.wav/../../x.wav", "/media/_mega/m1/samples/ABCDEF0123456789.wav", "", null, 4]) {
    assert.equal(reduce(s, keep(0, 0, { src })), s, String(src));
    assert.equal(MEGA_SAMPLE_URL.test(String(src)), false);
  }
  assert.equal(MEGA_SAMPLE_URL.test(SRC), true);
});

test("pad.set and pad.reset follow a kept pad to its own params", () => {
  let s = reduce(initialState({ chops: CHOPS }), keep(2, 5, { params: { gainDb: -3 } }));
  s = reduce(s, { type: "pad.set", pad: 5, bank: 2, params: { pitch: 7, gainDb: -9 } });
  assert.equal(s.project.overrides["2:5"].params.pitch, 7);
  assert.equal(s.project.pads[5].pitch, 0);
  s = reduce(s, { type: "pad.set", pad: 5, bank: 0, params: { pitch: 1 } });
  assert.equal(s.project.pads[5].pitch, 1);
  assert.equal(s.project.overrides["2:5"].params.pitch, 7);
  s = reduce(s, { type: "pad.reset", pad: 5, bank: 2 });
  assert.deepEqual(s.project.overrides["2:5"].params, { ...DEFAULT_PAD });
  assert.equal(s.project.pads[5].pitch, 1);
});

test("source.set clamps the bank and leaves the project alone; bank.set follows kept pads", () => {
  let s = reduce(initialState({ chops: CHOPS }), { type: "bank.set", bank: 2 });
  const project = s.project;
  s = reduce(s, { type: "source.set", song: "b", chops: { ...CHOPS, banks: [[0]] } });
  assert.deepEqual([s.source, s.bank, s.project === project], ["b", 0, true]);
  s = reduce(s, keep(4, 0));
  s = reduce(s, { type: "bank.set", bank: 99 });
  assert.equal(s.bank, 4);
  const t = reduce(s, { type: "source.set", song: "b", chops: { ...CHOPS, banks: [[0]] } });
  assert.equal(t, s);
});
