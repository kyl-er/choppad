import { createInput } from "/midi.js";
import { createEngine, resolveChop } from "/engine.js";
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

  // Stream the muted video straight from the server (HTTP Range); it is visual follow only.
  const videoEl = document.createElement("video");
  videoEl.src = `${base}/video.mp4`;
  videoEl.muted = true;
  videoEl.playsInline = true;
  videoEl.preload = "auto";

  let midiAccess = null;
  try {
    const ma = await navigator.requestMIDIAccess?.();
    midiAccess = ma || null;
  } catch {
    midiAccess = null;
  }

  const input = createInput({ midiAccess, keyboardTarget: document, storage: window.localStorage });
  const engine = createEngine({ ctx, buffer: audioBuffer, chops, video: videoEl });

  // Remember the last chop played so "Set Bar 1" can move bar 1 onto it.
  let lastChop = null;
  input.addEventListener("pad-on", (e) => { lastChop = resolveChop(chops, engine.bank, e.detail.pad) ?? lastChop; });

  const STEPS = { "1/4": 1, "1/8": 2, "1/16": 4 };
  const MULTS = [0.5, 1, 2];
  let meter = null; // sticky user meter override ("3/4"), null = analysis meter
  const onRegrid = async (key, value) => {
    let grid = chops.grid;
    let beat_offset = chops.beat_offset ?? 0;
    let bpm_mult = chops.bpm_mult ?? 1;
    const bpb = chops.beats_per_bar;

    if (key === "grid") grid = value;
    else if (key === "meter") meter = value;
    else if (key === "beat_offset") beat_offset += value;
    else if (key === "bpm_mult") bpm_mult = MULTS.reduce((a, b) => (Math.abs(b - bpm_mult * value) < Math.abs(a - bpm_mult * value) ? b : a));
    else if (key === "set_bar1") {
      if (!lastChop) return;
      beat_offset += Math.floor(lastChop.beat / STEPS[chops.grid]); // that chop's beat becomes beat 1
    }
    // Offsets are equivalent modulo one bar; keep them inside the contract's [-3, 3].
    beat_offset = ((beat_offset % bpb) + bpb) % bpb;
    if (beat_offset > 3) beat_offset -= bpb;

    const res = await fetch("/api/regrid", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id, grid, meter, beat_offset, bpm_mult }),
    });
    if (!res.ok) return;
    chops = await res.json();
    engine.setChops(chops);
    instance.updateChops(chops);
  };

  const appEl = document.getElementById("app");
  const instance = mountUI(appEl, { meta, analysis, chops, engine, input, onRegrid });
  {
    videoEl.style.width = "100%";
    videoEl.style.maxHeight = "220px";
    videoEl.style.borderRadius = "6px";
    videoEl.style.background = "#000";
    const lcdEl = appEl.querySelector(".lcd");
    if (lcdEl && lcdEl.nextSibling) {
      appEl.insertBefore(videoEl, lcdEl.nextSibling);
    } else {
      appEl.prepend(videoEl);
    }
  }
})();
