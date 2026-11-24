// capture_worklet: records a stretch of what flows into it, sample-accurate, for resampling the master bus (W3b).
// Served at /capture-worklet.js and loaded with audioContext.audioWorklet.addModule(). No ScriptProcessor.
//
//   node.port.postMessage({ cmd: "start", startFrame, frames })  start at the context frame `startFrame`, take `frames` frames
//   node.port.postMessage({ cmd: "cancel" })
//   node.port.onmessage -> { type: "done", channels: [Float32Array, Float32Array], startFrame } | { type: "cancelled" }
// It passes its input through to its output unchanged.
class Capture extends AudioWorkletProcessor {
  constructor() {
    super();
    this.active = false;
    this.start = 0;
    this.want = 0;
    this.chunks = [[], []];
    this.got = 0;
    this.port.onmessage = (e) => {
      const m = e.data || {};
      if (m.cmd === "start") {
        this.active = true;
        this.start = Math.max(0, Math.round(m.startFrame));
        this.want = Math.max(1, Math.round(m.frames));
        this.chunks = [[], []];
        this.got = 0;
      } else if (m.cmd === "cancel" && this.active) {
        this.active = false;
        this.port.postMessage({ type: "cancelled" });
      }
    };
  }

  process(inputs, outputs) {
    const input = inputs[0] || [];
    const output = outputs[0] || [];
    for (let c = 0; c < output.length; c++) if (input[c]) output[c].set(input[c]);
    if (!this.active) return true;
    const n = (input[0] || output[0] || []).length || 128;
    const blockStart = currentFrame;
    if (blockStart + n <= this.start) return true;          // not there yet
    const skip = Math.max(0, this.start - blockStart);      // samples of this block before the start frame
    const take = Math.min(n - skip, this.want - this.got);
    for (let c = 0; c < 2; c++) {
      const src = input[c] || input[0];
      const chunk = new Float32Array(take);
      if (src) chunk.set(src.subarray(skip, skip + take));
      this.chunks[c].push(chunk);
    }
    this.got += take;
    if (this.got >= this.want) {
      const channels = this.chunks.map((list) => {
        const out = new Float32Array(this.want);
        let o = 0;
        for (const ch of list) { out.set(ch, o); o += ch.length; }
        return out;
      });
      this.active = false;
      this.chunks = [[], []];
      this.port.postMessage({ type: "done", channels, startFrame: this.start }, channels.map((c) => c.buffer));
    }
    return true;
  }
}
registerProcessor("choppad-capture", Capture);
