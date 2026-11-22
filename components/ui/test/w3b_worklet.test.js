// The master-bus capture worklet (components/integrate/capture_worklet.js), run against a fake AudioWorklet scope.
import { test } from "node:test";
import assert from "node:assert/strict";

let Processor;
globalThis.AudioWorkletProcessor = class { constructor() { this.port = { onmessage: null, sent: [], postMessage(m) { this.sent.push(m); } }; } };
globalThis.registerProcessor = (name, cls) => { assert.equal(name, "choppad-capture"); Processor = cls; };
globalThis.currentFrame = 0;
await import("../../integrate/capture_worklet.js");

const block = (value, n = 128) => [[new Float32Array(n).fill(value), new Float32Array(n).fill(-value)]];
const run = (p, from, blocks, valueAt) => {
  for (let b = 0; b < blocks; b++) {
    currentFrame = from + b * 128;
    const out = [[new Float32Array(128), new Float32Array(128)]];
    const inp = [[Float32Array.from({ length: 128 }, (_, i) => valueAt(currentFrame + i)), Float32Array.from({ length: 128 }, (_, i) => -valueAt(currentFrame + i))]];
    assert.equal(p.process(inp, out), true);
    assert.deepEqual(Array.from(out[0][0].slice(0, 3)), Array.from(inp[0][0].slice(0, 3)), "passes the audio through");
  }
};

test("capture starts at the requested frame, even inside a block, and takes exactly the frames asked for", () => {
  const p = new Processor();
  p.port.onmessage({ data: { cmd: "start", startFrame: 300, frames: 500 } });
  run(p, 0, 12, (f) => f);   // the signal is its own frame number
  const done = p.port.sent.find((m) => m.type === "done");
  assert.ok(done, "finished");
  assert.equal(done.channels[0].length, 500);
  assert.equal(done.channels[0][0], 300);
  assert.equal(done.channels[0][499], 799);
  assert.equal(done.channels[1][10], -310);
  assert.equal(done.startFrame, 300);
  assert.equal(p.port.sent.filter((m) => m.type === "done").length, 1);
});

test("nothing is kept before it starts and after it is done, and cancel stops it", () => {
  const p = new Processor();
  run(p, 0, 3, () => 1);
  assert.equal(p.port.sent.length, 0);
  p.port.onmessage({ data: { cmd: "start", startFrame: 0, frames: 100000 } });
  run(p, 0, 2, () => 1);
  p.port.onmessage({ data: { cmd: "cancel" } });
  assert.deepEqual(p.port.sent.map((m) => m.type), ["cancelled"]);
  run(p, 256, 2, () => 1);
  assert.equal(p.port.sent.length, 1);
});
