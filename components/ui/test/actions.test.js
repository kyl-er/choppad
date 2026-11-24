import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { DEFAULT_PAD, PADS, initialState, projectDoc, reduce, stepOf } from "../actions.js";
import { createStore } from "../store.js";

const fixture = JSON.parse(readFileSync(new URL("../../../contracts/fixtures/actions.json", import.meta.url), "utf8"));

// objects match on the listed keys only; arrays and scalars must be equal
function assertMatches(actual, expected, path = "state") {
  if (Array.isArray(expected)) {
    assert.ok(Array.isArray(actual), `${path} should be an array`);
    assert.equal(actual.length, expected.length, `${path}.length`);
    expected.forEach((e, i) => assertMatches(actual[i], e, `${path}[${i}]`));
  } else if (expected && typeof expected === "object") {
    assert.ok(actual && typeof actual === "object", `${path} should be an object`);
    if (Object.keys(expected).length === 0) assert.deepEqual(actual, {}, path);
    for (const [k, v] of Object.entries(expected)) assertMatches(actual[k], v, `${path}.${k}`);
  } else {
    assert.equal(actual, expected, path);
  }
}

for (const c of fixture.cases) {
  test(`contract: ${c.name}`, () => {
    const start = initialState({ project: c.project, chops: fixture.chops });
    const end = c.actions.reduce(reduce, start);
    if (c.unchanged) assert.equal(end, start, "state object must be returned untouched");
    if (c.expect) assertMatches(end, c.expect);
  });
}

test("reduce never mutates the previous state", () => {
  const start = initialState({ chops: fixture.chops });
  const snapshot = JSON.stringify(start);
  const actions = fixture.cases.flatMap((c) => c.actions);
  let s = start;
  for (const a of actions) {
    const before = JSON.stringify(s);
    const next = reduce(s, a);
    assert.equal(JSON.stringify(s), before, `mutated by ${a.type}`);
    s = next;
  }
  assert.equal(JSON.stringify(start), snapshot);
});

test("initial state has 16 complete pads and the saved doc is the project", () => {
  const s = initialState({ project: { pads: [{ pitch: 7 }] }, chops: fixture.chops });
  assert.equal(s.project.pads.length, PADS);
  assert.deepEqual(s.project.pads[0], { ...DEFAULT_PAD, pitch: 7 });
  assert.deepEqual(s.project.pads[15], { ...DEFAULT_PAD });
  assert.deepEqual(Object.keys(projectDoc(s)).sort(), ["fx", "overrides", "pads", "pattern"]);
});

test("pad.copyToAll and pad.reset", () => {
  let s = initialState({ chops: fixture.chops });
  s = reduce(s, { type: "pad.set", pad: 2, params: { gainDb: -3, reverse: true } });
  s = reduce(s, { type: "pad.copyToAll", pad: 2 });
  assert.ok(s.project.pads.every((P) => P.gainDb === -3 && P.reverse === true));
  s = reduce(s, { type: "pad.reset", pad: 5 });
  assert.deepEqual(s.project.pads[5], { ...DEFAULT_PAD });
  assert.equal(s.project.pads[4].gainDb, -3);
});

test("stepOf is the 1/16 index of an event", () => {
  assert.equal(stepOf({ beat: 0 }), 0);
  assert.equal(stepOf({ beat: 1.25 }), 5);
});

test("store notifies only on change and passes meta through", () => {
  const store = createStore(reduce, initialState({ chops: fixture.chops }));
  const seen = [];
  const off = store.subscribe((s, prev, action, meta) => seen.push([action.type, s !== prev, meta.from ?? null]));
  store.dispatch({ type: "bank.set", bank: 1 }, { from: "test" });
  store.dispatch({ type: "bank.set", bank: 1 }); // no change
  store.dispatch({ type: "nope" });
  off();
  store.dispatch({ type: "bank.set", bank: 2 });
  assert.deepEqual(seen, [["bank.set", true, "test"]]);
  assert.equal(store.get().bank, 2);
});
