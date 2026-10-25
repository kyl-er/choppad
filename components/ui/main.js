import { createInput } from "/midi.js";
import { createEngine } from "/engine.js";
import { mountUI } from "/ui.js";

(async () => {
  const params = new URLSearchParams(location.search);
  const id = params.get("id");
  if (!id) {
    document.getElementById("app").innerHTML = "<p>No ?id= in URL</p>";
    return;
  }

  const base = `/media/${id}`;

  const fetchJSON = async (name) => {
    const res = await fetch(`${base}/${name}`);
    if (!res.ok) throw new Error(`${name}: ${res.status}`);
    return res.json();
  };

  let meta, analysis, chops, audioBuffer;
  try {
    [meta, analysis, chops] = await Promise.all([
      fetchJSON("meta.json"),
      fetchJSON("analysis.json"),
      fetchJSON("chops.json"),
    ]);
  } catch (err) {
    document.getElementById("app").innerHTML = `<p>Failed to load: ${err.message}</p>`;
    return;
  }

  const ctx = new AudioContext({ latencyHint: "interactive" });

  const audioRes = await fetch(`${base}/audio.wav`);
  if (!audioRes.ok) {
    document.getElementById("app").innerHTML = `<p>audio.wav: ${audioRes.status}</p>`;
    return;
  }
  const audioData = await audioRes.arrayBuffer();
  audioBuffer = await ctx.decodeAudioData(audioData);

  let videoEl = null;
  try {
    const check = await fetch(`${base}/video.mp4`, { method: "HEAD" });
    if (check.ok) {
      const blobRes = await fetch(`${base}/video.mp4`);
      const blob = await blobRes.blob();
      const url = URL.createObjectURL(blob);
      videoEl = document.createElement("video");
      videoEl.src = url;
      videoEl.setAttribute("playsinline", "");
      videoEl.preload = "auto";
    }
  } catch {
    videoEl = null;
  }

  let midiAccess = null;
  try {
    const ma = await navigator.requestMIDIAccess?.();
    midiAccess = ma || null;
  } catch {
    midiAccess = null;
  }

  const input = createInput({ midiAccess, keyboardTarget: document, storage: window.localStorage });
  const engine = createEngine({ ctx, buffer: audioBuffer, chops, video: videoEl });

  const onRegrid = async (key, value) => {
    let grid = chops.grid;
    let meter = null;
    let beat_offset = chops.beat_offset ?? 0;
    let bpm_mult = chops.bpm_mult ?? 1;

    if (key === "grid") grid = value;
    else if (key === "meter") meter = value;
    else if (key === "beat_offset") beat_offset = value;
    else if (key === "bpm_mult") bpm_mult = value;
    else if (key === "set_bar1") {
      const currentBar1 = chops.beat_offset ?? 0;
      beat_offset = currentBar1 === 0 ? -1 : 0;
    }

    const res = await fetch("/api/regrid", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id, grid, meter, beat_offset, bpm_mult }),
    });
    if (!res.ok) return;
    const newChops = await res.json();
    chops = newChops;
    instance.updateChops(newChops);
  };

  const instance = mountUI(document.getElementById("app"), { meta, analysis, chops, engine, input, onRegrid });
})();
