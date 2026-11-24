import { test } from "node:test";
import assert from "node:assert/strict";
import { DEFAULT_FX, PRESETS, filterSetting, reverbSeconds, tiltGains, wetDry } from "../fx.js";

test("tone tilts: dark boosts lows and cuts highs, bright the opposite, 0 is flat", () => {
  assert.deepEqual(tiltGains(0), { lowDb: -0, highDb: 0 });
  assert.deepEqual(tiltGains(-1), { lowDb: 9, highDb: -9 });
  assert.deepEqual(tiltGains(1), { lowDb: -9, highDb: 9 });
  assert.deepEqual(tiltGains(5), tiltGains(1)); // clamped
});

test("one-knob filter: centre is off, left is lowpass sweeping down, right is highpass sweeping up", () => {
  assert.equal(filterSetting(0), null);
  assert.equal(filterSetting(0.01), null);
  const lp = filterSetting(-1), hp = filterSetting(1);
  assert.equal(lp.type, "lowpass"); assert.ok(Math.abs(lp.freq - 150) < 1e-6);
  assert.equal(hp.type, "highpass"); assert.ok(Math.abs(hp.freq - 4000) < 1e-6);
  assert.ok(filterSetting(-0.5).freq > filterSetting(-0.8).freq);
});

test("reverb mix is equal-power and size maps to 0.4..6 s", () => {
  const off = wetDry(0), full = wetDry(1), half = wetDry(0.5);
  assert.equal(off.dry, 1); assert.equal(off.wet, 0);
  assert.ok(full.dry < 1e-9 && Math.abs(full.wet - 1) < 1e-9);
  assert.ok(Math.abs(half.dry ** 2 + half.wet ** 2 - 1) < 1e-9);
  assert.equal(reverbSeconds(0), 0.4); assert.equal(reverbSeconds(1), 6);
});

test("presets only use known params", () => {
  for (const p of Object.values(PRESETS)) assert.deepEqual(Object.keys(p).sort(), Object.keys(DEFAULT_FX).sort());
  assert.deepEqual(PRESETS.dry, { ...DEFAULT_FX });
});
