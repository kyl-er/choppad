// Song screen: LCD + master volume/meter, video, 4x4 pads, bank strip (navigate the song), waveform,
// song transport (play the original, pads follow), regrid, modes, MIDI.
import { DEFAULT_PAD, createEngine } from "/engine.js";
import { cellToPad } from "/layout.js";
import { createWaveform } from "/waveform.js";
import { createSequencer, encodeWav, patternToMidi } from "/sequencer.js";

const STEPS = { "1/4": 1, "1/8": 2, "1/16": 4 };
const MULTS = [0.5, 1, 2];
const BAR_HUES = [140, 210, 35, 300];
const fmt = (s) => `${Math.floor(s / 60)}:${(s % 60).toFixed(1).padStart(4, "0")}`;
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

/** Returns { destroy() }. Throws if the song can't be loaded. */
export async function mountSong(root, { id, audio, input }) {
  const { ctx, master, analyser } = audio;
  root.innerHTML = `<p class="loading">Loading song…</p>`;
  const res = await fetch(`/api/songs/${id}`);
  if (!res.ok) throw new Error(`song ${id}: HTTP ${res.status}`);
  const data = await res.json();
  const { meta, analysis, peaks } = data;
  let chops = data.chops;
  root.querySelector(".loading").textContent = "Decoding audio…";
  const buffer = await fetch(`/media/${id}/audio.wav`).then((r) => r.arrayBuffer()).then((b) => ctx.decodeAudioData(b));

  root.innerHTML = `
  <section class="song-view">
    <header class="lcd">
      <div class="lcd-main"><div class="lcd-title">${esc(meta.title)}</div>
        <div class="lcd-row"><b class="bpm"></b> BPM · <b class="meter"></b> · <span class="pos">bar 1.1</span> · <span class="clock">0:00.0</span> / ${fmt(meta.duration_s)}
        · confidence <span class="conf">${Math.round((analysis.confidence ?? 0) * 100)}%</span></div></div>
      <div class="master"><label>Volume <input class="vol" type="range" min="0" max="1.5" step="0.01"></label>
        <div class="vu"><i></i></div></div>
    </header>
    <div class="stage">
      <div class="video-wrap"><video class="video" muted playsinline preload="auto"></video></div>
      <div class="pads-wrap">
        <div class="pads"></div>
        <div class="bank-nav"><button class="bank-prev" title="[">◂</button><span class="bank-label"></span><button class="bank-next" title="]">▸</button></div>
      </div>
    </div>
    <div class="sections" title="Song sections (click to jump)"></div>
    <div class="banks" title="Click a bank to jump there"><div class="banks-track"></div><i class="banks-head"></i></div>
    <div class="wave"></div>
    <div class="transport">
      <button class="play">▶ Play song</button><button class="stop">■ Stop</button>
      <label><input type="checkbox" class="follow" checked> pads follow song</label>
      <label class="zoom" title="0 = fit the current bank">zoom <input class="zoom-in" type="range" min="0" max="400" step="10" value="0"></label>
      <span class="hint muted">Pads Z X C V / A S D F / Q W E R / 1 2 3 4 · banks [ ] · Enter song · P pattern · B rec · T tap · Space stop</span>
    </div>
    <div class="seq">
      <button class="rec" title="R">● rec</button><button class="seq-play" title="P">▶ pattern</button>
      <label><input type="checkbox" class="metro" checked> click</label>
      <label>bars <select class="seq-bars"><option>1</option><option selected>2</option><option>4</option></select></label>
      <label>quantize <select class="seq-q"><option value="off">off</option><option value="1/8">1/8</option><option value="1/16" selected>1/16</option></select></label>
      <label>swing <input class="seq-swing" type="range" min="0" max="0.5" step="0.05" value="0"></label>
      <label><input type="checkbox" class="repeat"> note repeat <select class="repeat-rate"><option value="0.5">1/8</option><option value="0.25" selected>1/16</option></select></label>
      <button class="seq-undo">undo</button><button class="seq-clear">clear</button>
      <button class="seq-midi">⇩ .mid</button><button class="seq-wav">⇩ .wav</button>
      <div class="steps"></div><span class="seq-info muted"></span>
    </div>
    <div class="panels">
      <div class="panel"><h3>Grid</h3><div class="row grid-btns"></div><div class="row meter-btns"></div>
        <div class="row tempo-btns"></div>
        <div class="row"><button class="tap" title="T">tap tempo (T)</button><span class="tap-bpm muted">tap 4×…</span><button class="tap-apply" disabled>apply</button></div></div>
      <div class="panel"><h3>Pads</h3>
        <label>mode <select class="mode"><option value="oneshot">one-shot</option><option value="gate">gate (hold)</option><option value="playthrough">play-through</option></select></label>
        <label><input type="checkbox" class="choke" checked> choke</label>
        <label><input type="checkbox" class="full" checked> full level</label></div>
      <div class="panel inspector"><h3 class="insp-title">Pad 1</h3>
        <label class="slider">gain <input name="gainDb" type="range" min="-24" max="12" step="0.5"><output data-out="gainDb"></output></label>
        <label class="slider">pitch <input name="pitch" type="range" min="-12" max="12" step="1"><output data-out="pitch"></output></label>
        <label class="slider">attack <input name="attack" type="range" min="0" max="200" step="1"><output data-out="attack"></output></label>
        <label class="slider">release <input name="release" type="range" min="3" max="800" step="1"><output data-out="release"></output></label>
        <div class="row"><label><input name="reverse" type="checkbox"> reverse</label>
          <label>mode <select name="padmode"><option value="">(global)</option><option value="oneshot">one-shot</option><option value="gate">gate</option><option value="playthrough">play-through</option></select></label></div>
        <div class="row"><button class="grab" title="Put the current playhead on this pad">grab from playhead</button><button class="unslice">reset slice</button>
          <button class="all">copy sound to all pads</button><button class="resetpad">reset sound</button></div>
        <p class="muted small">Hit a pad to select it. Drag or resize its region on the waveform to re-slice.</p></div>
      <div class="panel ai"><h3>AI assist</h3>
        <div class="row"><button class="ai-run">Analyze with AI</button><button class="ai-bank" disabled>load AI bank on pads</button></div>
        <div class="ai-out muted small">Sends a 32 kbps mono copy to an OpenRouter audio model (≤ $0.05/song, cached). Sections above work offline.</div></div>
      <div class="panel export"><h3>Export</h3>
        <div class="row"><button class="exp-h2">install bank → Hydrogen kit</button><button class="exp-sfz">write bank → SFZ</button></div>
        <div class="exp-out muted small">Exports the current bank (16 pads).</div></div>
      <div class="panel"><h3>MIDI</h3><ul class="devices"></ul>
        <div class="row"><button class="learn">Learn pads</button><button class="reset">Reset map</button></div>
        <div class="learn-state muted"></div></div>
    </div>
  </section>`;
  const $ = (s) => root.querySelector(s);

  // ---- media + engine ----
  const video = $(".video");
  video.src = `/media/${id}/video.mp4`;
  const engine = createEngine({ ctx, buffer, chops, video, output: master });
  let bank = 0;
  let chopToBank = [];
  let lastChop = null;
  let selected = 0;
  let meterOverride = null;

  // ---- song transport (the original audio, pads choke it) ----
  let song = null; // { src, gain, startCtx, startPos }
  const position = () => (song ? Math.min(meta.duration_s, song.startPos + (ctx.currentTime - song.startCtx)) : video.currentTime);
  function stopSong() {
    if (!song) return;
    const { src, gain } = song;
    gain.gain.setTargetAtTime(0, ctx.currentTime, 0.005);
    src.stop(ctx.currentTime + 0.03);
    song = null;
    video.pause();
    $(".play").textContent = "▶ Play song";
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
    $(".play").textContent = "❚❚ Pause";
  }
  $(".play").addEventListener("click", () => (song ? stopSong() : playSong()));
  $(".stop").addEventListener("click", () => { stopSong(); engine.stopAll(); });

  // ---- banks / pads ----
  const steps = () => STEPS[chops.grid];
  const padLabel = (c) => {
    const beat = Math.floor(c.beat / steps()) + 1;
    return `${c.bar + 1}.${beat}${steps() > 1 ? "." + ((c.beat % steps()) + 1) : ""}`;
  };
  const padEls = [];
  for (let row = 0; row < 4; row++) {
    for (let col = 0; col < 4; col++) {
      const p = cellToPad(row, col);
      const el = document.createElement("button");
      el.className = "pad";
      el.dataset.pad = p;
      el.innerHTML = `<span class="pad-no">${p + 1}</span><span class="pad-label"></span><span class="pad-time"></span>`;
      el.addEventListener("pointerdown", (e) => { e.preventDefault(); hit(p, 1); });
      el.addEventListener("pointerup", () => engine.release(p));
      el.addEventListener("pointerleave", () => engine.release(p));
      padEls[p] = el;
      $(".pads").append(el);
    }
  }
  const wave = createWaveform($(".wave"), {
    peaks: peaks.peaks, duration: peaks.duration_s, media: video,
    onPadClick: (p) => hit(p, 1),
    onSliceEdit: (p, start, end) => {
      engine.setOverride(bank, p, { start, end });
      select(p);
      renderPads();
      saveProject();
    },
    onUserSeek: (t) => { if (song) playSong(t); else if ($(".follow").checked) followTo(t); },
  });

  function indexBanks() {
    chopToBank = [];
    chops.banks.forEach((b, n) => b.forEach((i) => { chopToBank[i] = n; }));
    const track = $(".banks-track");
    track.innerHTML = "";
    chops.banks.forEach((b, n) => {
      const s = chops.chops[b[0]].start, e = chops.chops[b.at(-1)].end;
      const seg = document.createElement("button");
      seg.className = "bank-seg";
      seg.style.left = `${(s / meta.duration_s) * 100}%`;
      seg.style.width = `${((e - s) / meta.duration_s) * 100}%`;
      seg.title = `Bank ${n + 1}: bars ${chops.chops[b[0]].bar + 1}–${chops.chops[b.at(-1)].bar + 1} (${fmt(s)})`;
      seg.textContent = n + 1;
      seg.addEventListener("click", () => setBank(n, true));
      track.append(seg);
    });
  }
  function renderPads() {
    for (let p = 0; p < 16; p++) {
      const c = engine.getSlice(bank, p);
      const el = padEls[p];
      el.classList.toggle("empty", !c);
      el.classList.toggle("custom", !!c?.custom);
      el.classList.toggle("selected", p === selected);
      el.querySelector(".pad-label").textContent = c ? (c.custom ? "★ " + fmt(c.start) : padLabel(c)) : "";
      el.querySelector(".pad-time").textContent = c ? (c.custom ? `${(c.end - c.start).toFixed(2)}s` : fmt(c.start)) : "";
      el.style.setProperty("--hue", c ? (c.custom ? 48 : BAR_HUES[c.bar % 4]) : 0);
    }
  }
  function renderBank() {
    const ids = chops.banks[bank] ?? [];
    renderPads();
    const first = chops.chops[ids[0]], last = chops.chops[ids.at(-1)];
    $(".bank-label").textContent = `Bank ${bank + 1}/${chops.banks.length} · bars ${first.bar + 1}–${last.bar + 1}`;
    root.querySelectorAll(".bank-seg").forEach((s, n) => s.classList.toggle("active", n === bank));
    const slices = [];
    for (let p = 0; p < 16; p++) {
      const c = engine.getSlice(bank, p);
      if (c) slices.push({ pad: p, start: c.start, end: c.end, bar: c.bar, custom: !!c.custom });
    }
    wave.showSlices(slices);
    fitZoom(first.start, last.end);
    renderInspector();
  }
  function setBank(n, seekVideo) {
    bank = Math.max(0, Math.min(chops.banks.length - 1, n));
    engine.setBank(bank);
    if (seekVideo && !song) video.currentTime = chops.chops[chops.banks[bank][0]].start;
    renderBank();
  }
  function followTo(t) {
    const i = chops.chops.findIndex((c) => c.start <= t && t < c.end);
    if (i >= 0 && chopToBank[i] !== bank) setBank(chopToBank[i], false);
  }
  function hit(p, velocity) {
    const c = engine.getSlice(bank, p);
    if (!c) return;
    stopSong();
    engine.trigger(p, velocity);
    if (seq.record(p, bank, velocity)) { renderSteps(); saveProject(); }
    lastChop = c;
    select(p);
    const el = padEls[p];
    el.classList.remove("hit");
    void el.offsetWidth;
    el.classList.add("hit");
    wave.flash(p);
  }
  $(".bank-prev").addEventListener("click", () => setBank(bank - 1, true));
  $(".bank-next").addEventListener("click", () => setBank(bank + 1, true));

  // ---- regrid ----
  async function regrid(change) {
    let beat_offset = chops.beat_offset ?? 0;
    let bpm_mult = chops.bpm_mult ?? 1;
    let grid = chops.grid;
    if (change.grid) grid = change.grid;
    if (change.meter !== undefined) meterOverride = change.meter;
    if (change.mult) bpm_mult = MULTS.reduce((a, b) => (Math.abs(b - bpm_mult * change.mult) < Math.abs(a - bpm_mult * change.mult) ? b : a));
    if (change.shift) beat_offset += change.shift;
    if (change.bar1) {
      if (!lastChop) return;
      beat_offset += Math.floor(lastChop.beat / steps());
    }
    const bpb = meterOverride ? Number(meterOverride.split("/")[0]) : chops.beats_per_bar;
    beat_offset = ((beat_offset % bpb) + bpb) % bpb;
    if (beat_offset > 3) beat_offset -= bpb;
    const r = await fetch(`/api/songs/${id}/regrid`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ grid, meter: meterOverride, beat_offset, bpm_mult }),
    });
    if (!r.ok) return;
    const t = position();
    chops = await r.json();
    engine.setChops(chops);
    indexBanks();
    bank = 0;
    followTo(t);
    setBank(bank, false);
    renderLcd();
  }
  const btn = (parent, label, fn, cls = "") => {
    const b = document.createElement("button");
    b.textContent = label;
    b.className = cls;
    b.addEventListener("click", fn);
    $(parent).append(b);
    return b;
  };
  for (const g of ["1/4", "1/8", "1/16"]) btn(".grid-btns", g, () => regrid({ grid: g }), "grid-" + g.replace("/", "-"));
  for (const m of ["2/4", "3/4", "4/4"]) btn(".meter-btns", m, () => regrid({ meter: m }), "meter-" + m.replace("/", "-"));
  btn(".tempo-btns", "÷2", () => regrid({ mult: 0.5 }));
  btn(".tempo-btns", "×2", () => regrid({ mult: 2 }));
  btn(".tempo-btns", "beat −1", () => regrid({ shift: -1 }));
  btn(".tempo-btns", "beat +1", () => regrid({ shift: 1 }));
  btn(".tempo-btns", "set bar 1 = last pad", () => regrid({ bar1: true }));

  // ---- LCD, volume, meter ----
  function renderLcd() {
    $(".bpm").textContent = chops.bpm.toFixed(1);
    $(".meter").textContent = `${chops.beats_per_bar}/4`;
    root.querySelectorAll(".grid-btns button").forEach((b) => b.classList.toggle("on", b.textContent === chops.grid));
    root.querySelectorAll(".meter-btns button").forEach((b) => b.classList.toggle("on", b.textContent === `${chops.beats_per_bar}/4`));
  }
  const vol = $(".vol");
  vol.value = master.gain.value;
  vol.addEventListener("input", () => {
    master.gain.cancelScheduledValues(ctx.currentTime);
    master.gain.value = Number(vol.value);
    try { localStorage.setItem("choppad.volume", vol.value); } catch { /* private mode */ }
  });
  // zoom slider: 0 = auto (fit the current bank), otherwise px per second
  function fitZoom(start, end) {
    const z = Number($(".zoom-in").value);
    const width = $(".wave").clientWidth || 900;
    wave.zoom(z > 0 ? z : width / ((end - start) * 1.25), start);
  }
  $(".zoom-in").addEventListener("input", () => renderBank());
  const tbuf = new Float32Array(analyser.fftSize);
  let raf = 0;
  function tick() {
    analyser.getFloatTimeDomainData(tbuf);
    let peak = 0;
    for (const v of tbuf) peak = Math.max(peak, Math.abs(v));
    $(".vu i").style.width = `${Math.min(100, peak * 100)}%`;
    $(".vu").classList.toggle("clip", peak >= 0.99);
    const t = position();
    $(".clock").textContent = fmt(t);
    let k = 0;
    while (k + 1 < analysis.beats.length && analysis.beats[k + 1] <= t) k++;
    const c = chops.chops.find((x) => x.start <= t && t < x.end);
    $(".pos").textContent = c ? `bar ${padLabel(c)}` : "—";
    $(".banks-head").style.left = `${(t / meta.duration_s) * 100}%`;
    if (song && $(".follow").checked) followTo(t);
    markSection(t);
    if (seq.state.playing) {
      const cell = Math.floor(seq.position() * 4);
      [...stepsEl.children].forEach((c, i) => c.classList.toggle("now", i === cell));
    }
    raf = requestAnimationFrame(tick);
  }

  // ---- pad inspector (MPC program edit) ----
  function select(p) {
    if (p === selected) return;
    selected = p;
    renderPads();
    renderInspector();
  }
  const insp = $(".inspector");
  function renderInspector() {
    const P = engine.getPadParams(selected);
    const c = engine.getSlice(bank, selected);
    insp.querySelector(".insp-title").textContent = `Pad ${selected + 1}` + (c ? ` · ${c.custom ? "custom slice" : "bar " + padLabel(c)} · ${fmt(c.start)}–${fmt(c.end)}` : " · empty");
    for (const [k, el] of Object.entries(inspInputs)) {
      if (k === "reverse") el.checked = P.reverse;
      else if (k === "mode") el.value = P.mode ?? "";
      else el.value = k === "attack" || k === "release" ? Math.round(P[k] * 1000) : P[k];
      const out = insp.querySelector(`[data-out="${k}"]`);
      if (out) out.textContent = k === "gainDb" ? `${P.gainDb > 0 ? "+" : ""}${P.gainDb} dB` : k === "pitch" ? `${P.pitch > 0 ? "+" : ""}${P.pitch} st` : `${Math.round(P[k] * 1000)} ms`;
    }
  }
  const inspInputs = {
    gainDb: insp.querySelector('[name="gainDb"]'), pitch: insp.querySelector('[name="pitch"]'),
    attack: insp.querySelector('[name="attack"]'), release: insp.querySelector('[name="release"]'),
    reverse: insp.querySelector('[name="reverse"]'), mode: insp.querySelector('[name="padmode"]'),
  };
  for (const [k, el] of Object.entries(inspInputs)) {
    el.addEventListener(k === "reverse" || k === "mode" ? "change" : "input", () => {
      const v = k === "reverse" ? el.checked : k === "mode" ? (el.value || null)
        : k === "attack" || k === "release" ? Number(el.value) / 1000 : Number(el.value);
      engine.setPadParams(selected, { [k]: v });
      renderInspector();
      saveProject();
    });
  }
  insp.querySelector(".grab").addEventListener("click", () => {
    const t = position();
    const c = engine.getSlice(bank, selected);
    const len = c ? c.end - c.start : 60 / chops.bpm;
    engine.setOverride(bank, selected, { start: t, end: Math.min(meta.duration_s, t + len) });
    renderBank();
    saveProject();
  });
  insp.querySelector(".unslice").addEventListener("click", () => { engine.setOverride(bank, selected, null); renderBank(); saveProject(); });
  insp.querySelector(".all").addEventListener("click", () => {
    const P = engine.getPadParams(selected);
    for (let p = 0; p < 16; p++) engine.setPadParams(p, { ...P });
    saveProject();
  });
  insp.querySelector(".resetpad").addEventListener("click", () => { engine.setPadParams(selected, { ...DEFAULT_PAD }); renderInspector(); saveProject(); });

  // ---- project persistence (pad params + custom slices) ----
  for (let p = 0; p < 16; p++) engine.setPadParams(p, { ...DEFAULT_PAD, ...(data.project?.pads?.[p] ?? {}) });
  for (const [k, sl] of Object.entries(data.project?.overrides ?? {})) {
    const [b, p] = k.split(":").map(Number);
    engine.setOverride(b, p, sl);
  }
  let saveTimer = 0;
  function saveProject() {
    clearTimeout(saveTimer);
    saveTimer = setTimeout(() => {
      const pads = Array.from({ length: 16 }, (_, p) => engine.getPadParams(p));
      fetch(`/api/songs/${id}/project`, { method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pads, overrides: engine.getOverrides(), pattern: seq.state.pattern }) }).catch(() => {});
    }, 300);
  }

  // ---- tap tempo ----
  let taps = [];
  function tap() {
    const now = performance.now();
    if (taps.length && now - taps.at(-1) > 2000) taps = [];
    taps.push(now);
    if (taps.length > 8) taps.shift();
    const out = $(".tap-bpm");
    if (taps.length < 3) { out.textContent = `tap ${4 - taps.length} more…`; return; }
    const iv = taps.slice(1).map((t, i) => t - taps[i]).sort((a, b) => a - b);
    out.dataset.bpm = (60000 / iv[Math.floor(iv.length / 2)]).toFixed(1);
    out.textContent = `tapped ${out.dataset.bpm} BPM`;
    $(".tap-apply").disabled = false;
  }
  $(".tap").addEventListener("click", tap);
  $(".tap-apply").addEventListener("click", async () => {
    const bpm = Number($(".tap-bpm").dataset.bpm);
    if (!bpm) return;
    $(".tap-bpm").textContent = "re-analyzing…";
    const r = await fetch(`/api/songs/${id}/reanalyze`, { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ bpm, meter: meterOverride }) });
    if (!r.ok) { $(".tap-bpm").textContent = "failed"; return; }
    const out = await r.json();
    Object.assign(analysis, out.analysis);
    const t = position();
    chops = out.chops;
    engine.setChops(chops);
    indexBanks();
    bank = 0;
    followTo(t);
    setBank(bank, false);
    renderLcd();
    $(".tap-bpm").textContent = `now ${chops.bpm.toFixed(1)} BPM`;
  });

  // ---- sequencer (record / loop / quantize / swing / note repeat / export) ----
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
  function flashPadAt(when, p, b) {
    setTimeout(() => {
      if (b !== bank) return;
      const el = padEls[p];
      el.classList.remove("hit");
      void el.offsetWidth;
      el.classList.add("hit");
    }, Math.max(0, (when - ctx.currentTime) * 1000));
  }
  const seq = createSequencer({ ctx, click: clickAt, play: (p, vel, when, b) => { engine.trigger(p, vel, when, b); flashPadAt(when, p, b); } });
  const syncSeqTempo = () => { seq.state.bpm = chops.bpm; seq.state.beatsPerBar = chops.beats_per_bar; };
  syncSeqTempo();
  if (data.project?.pattern) seq.load(data.project.pattern);
  $(".seq-bars").value = String(seq.state.bars);
  const stepsEl = $(".steps");
  function renderSteps() {
    const L = seq.loopBeats(), n = L * 4; // 1/16 cells
    if (stepsEl.childElementCount !== n) stepsEl.innerHTML = Array.from({ length: n }, (_, i) => `<i class="${i % 4 === 0 ? "beat" : ""}"></i>`).join("");
    const filled = new Set(seq.state.pattern.events.map((e) => Math.floor(e.beat * 4 + 1e-6)));
    [...stepsEl.children].forEach((c, i) => c.classList.toggle("on", filled.has(i)));
    $(".seq-info").textContent = `${seq.state.pattern.events.length} hits · ${chops.bpm.toFixed(1)} BPM`;
  }
  function seqButtons() {
    $(".rec").classList.toggle("on", seq.state.recording);
    $(".seq-play").textContent = seq.state.playing ? "■ pattern" : "▶ pattern";
  }
  $(".rec").addEventListener("click", () => { stopSong(); syncSeqTempo(); seq.toggleRecord(); seqButtons(); });
  $(".seq-play").addEventListener("click", () => {
    if (seq.state.playing) seq.stop(); else { stopSong(); syncSeqTempo(); seq.start(); }
    seqButtons();
    saveProject();
  });
  $(".metro").addEventListener("change", (e) => { seq.state.metronome = e.target.checked; });
  $(".seq-bars").addEventListener("change", (e) => { seq.state.bars = Number(e.target.value); seq.state.pattern.bars = seq.state.bars; renderSteps(); saveProject(); });
  $(".seq-q").addEventListener("change", (e) => { seq.state.quant = e.target.value; });
  $(".seq-swing").addEventListener("input", (e) => { seq.state.swing = Number(e.target.value); });
  $(".seq-undo").addEventListener("click", () => { seq.undo(); renderSteps(); saveProject(); });
  $(".seq-clear").addEventListener("click", () => { seq.clear(); renderSteps(); saveProject(); });
  const download = (bytes, name, type) => {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([bytes], { type }));
    a.download = name;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 5000);
  };
  const baseName = () => meta.title.replace(/[^\w-]+/g, "_").slice(0, 40);
  $(".seq-midi").addEventListener("click", () => download(patternToMidi(seq.state.pattern, { bpm: chops.bpm, beatsPerBar: chops.beats_per_bar }), `${baseName()}-pattern.mid`, "audio/midi"));
  $(".seq-wav").addEventListener("click", async () => {
    const pat = seq.state.pattern;
    if (!pat.events.length) return;
    const seconds = (pat.bars * chops.beats_per_bar * 60) / chops.bpm + 2;
    const off = new OfflineAudioContext(2, Math.ceil(seconds * buffer.sampleRate), buffer.sampleRate);
    const oe = createEngine({ ctx: off, buffer, chops, output: off.destination });
    for (let p = 0; p < 16; p++) oe.setPadParams(p, engine.getPadParams(p));
    for (const [k, sl] of Object.entries(engine.getOverrides())) { const [b, p] = k.split(":").map(Number); oe.setOverride(b, p, sl); }
    const q = { off: 0, "1/8": 0.5, "1/16": 0.25 }[seq.state.quant];
    for (const e of pat.events) {
      const step = q ? Math.round(e.beat / q) : 0;
      const beat = q && step % 2 === 1 ? e.beat + seq.state.swing * q : e.beat;
      oe.trigger(e.pad, e.vel, (beat * 60) / chops.bpm, e.bank);
    }
    $(".seq-info").textContent = "rendering…";
    download(encodeWav(await off.startRendering()), `${baseName()}-pattern.wav`, "audio/wav");
    renderSteps();
  });
  // note repeat: held pads retrigger at the chosen rate (synced to tempo)
  const held = new Map(); // pad -> interval handle
  function startRepeat(p, vel) {
    if (!$(".repeat").checked || held.has(p)) return;
    const ms = (Number($(".repeat-rate").value) * 60000) / chops.bpm;
    held.set(p, setInterval(() => hit(p, vel), ms));
  }
  function stopRepeat(p) { clearInterval(held.get(p)); held.delete(p); }

  // ---- sections (offline structure; AI can relabel) ----
  const SECTION_HUES = { intro: 200, verse: 140, chorus: 35, bridge: 280, outro: 200 };
  let sectionList = [];
  function renderSections() {
    const el = $(".sections");
    el.innerHTML = "";
    for (const sc of sectionList) {
      const b = document.createElement("button");
      b.className = "section-seg";
      b.style.left = `${(sc.start_s / meta.duration_s) * 100}%`;
      b.style.width = `${((sc.end_s - sc.start_s) / meta.duration_s) * 100}%`;
      b.style.setProperty("--hue", SECTION_HUES[sc.label] ?? (sc.letter.charCodeAt(0) * 47) % 360);
      b.textContent = sc.label;
      b.title = `${sc.label} (${sc.letter}) · bars ${sc.start_bar + 1}–${sc.end_bar} · ${fmt(sc.start_s)}`;
      b.addEventListener("click", () => {
        if (song) playSong(sc.start_s); else { video.currentTime = sc.start_s; followTo(sc.start_s + 1e-3); }
      });
      el.append(b);
    }
  }
  function markSection(t) {
    root.querySelectorAll(".section-seg").forEach((b, i) => b.classList.toggle("active", sectionList[i] && sectionList[i].start_s <= t && t < sectionList[i].end_s));
  }
  fetch(`/api/songs/${id}/sections`, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" })
    .then((r) => (r.ok ? r.json() : null)).then((d) => { if (d) { sectionList = d.sections; renderSections(); } }).catch(() => {});

  // ---- AI assist ----
  let ai = null;
  async function runAI() {
    const out = $(".ai-out");
    out.textContent = "Listening to the song with an audio model…";
    $(".ai-run").disabled = true;
    try {
      const r = await fetch(`/api/songs/${id}/advise`, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
      const body = await r.json();
      if (!r.ok) throw new Error(body.error || r.status);
      ai = body;
      const chk = ai.bpm_meter_check || {};
      out.innerHTML = `<b>${esc(ai.model)}</b>: ${ai.sections.length} sections · felt ${esc(chk.felt_bpm)} BPM (${esc(chk.meter_hint)})` +
        (chk.bpm_mult_hint && chk.bpm_mult_hint !== 1 ? ` — suggests ${chk.bpm_mult_hint === 2 ? "×2" : "÷2"}` : "") + `<br>${esc(chk.notes || "")}`;
      sectionList = ai.sections.map((sc, i) => ({ ...sc, letter: sc.letter ?? String.fromCharCode(65 + i) }));
      renderSections();
      $(".ai-bank").disabled = false;
    } catch (err) {
      const msg = String(err.message || err);
      out.innerHTML = `<span class="error">${esc(/401|User not found/.test(msg) ? "OpenRouter rejected the API key (401). Put a valid key in OPENROUTER_API_KEY and restart choppad." : msg)}</span>`;
    } finally {
      $(".ai-run").disabled = false;
    }
  }
  $(".ai-run").addEventListener("click", runAI);
  $(".ai-bank").addEventListener("click", () => {
    if (!ai) return;
    ai.suggested_bank.slice(0, 16).forEach((ci, p) => {
      const c = chops.chops[ci];
      if (c) engine.setOverride(bank, p, { start: c.start, end: c.end });
    });
    renderBank();
    saveProject();
    $(".ai-out").insertAdjacentHTML("beforeend", `<br>Loaded on bank ${bank + 1}: ${ai.pad_labels.slice(0, 16).map(esc).join(", ")}`);
  });

  // ---- export ----
  async function doExport(kind) {
    const out = $(".exp-out");
    out.textContent = "exporting…";
    const r = await fetch(`/api/songs/${id}/export`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ kind, bank }) });
    const body = await r.json().catch(() => ({}));
    out.innerHTML = r.ok ? `✓ ${esc(body.path)}${body.note ? "<br>" + esc(body.note) : ""}` : `<span class="error">${esc(body.error || r.status)}</span>`;
  }
  $(".exp-h2").addEventListener("click", () => doExport("hydrogen"));
  $(".exp-sfz").addEventListener("click", () => doExport("sfz"));

  // ---- modes ----
  $(".mode").addEventListener("change", (e) => engine.setMode(e.target.value));
  $(".choke").addEventListener("change", (e) => engine.setChoke(e.target.checked));
  $(".full").addEventListener("change", (e) => engine.setFullLevel(e.target.checked));

  // ---- MIDI / keyboard ----
  const renderDevices = (devs) => {
    $(".devices").innerHTML = devs.length ? devs.map((d) => `<li>🎹 ${esc(d.name)} <span class="muted">${esc(d.state)}</span></li>`).join("")
      : `<li class="muted">No MIDI devices — plug in your phone (USB → MIDI mode)</li>`;
  };
  renderDevices(input.getDevices());
  const handlers = {
    "pad-on": (e) => { hit(e.detail.pad, e.detail.velocity); startRepeat(e.detail.pad, e.detail.velocity); },
    "pad-off": (e) => { engine.release(e.detail.pad); stopRepeat(e.detail.pad); },
    bank: (e) => setBank(bank + e.detail.delta, true),
    panic: () => { stopSong(); engine.stopAll(); seq.stop(); seqButtons(); for (const p of [...held.keys()]) stopRepeat(p); },
    devices: (e) => renderDevices(e.detail.devices),
    "learn-state": (e) => {
      $(".learn-state").textContent = e.detail.active ? `Learning: tap pad ${e.detail.nextPad + 1} on your controller…` : "Pad map saved.";
    },
  };
  for (const [t, h] of Object.entries(handlers)) input.addEventListener(t, h);
  $(".learn").addEventListener("click", () => input.startLearn());
  $(".reset").addEventListener("click", () => { input.resetMap(); $(".learn-state").textContent = "Default map (notes 36–51)."; });
  const onKey = (e) => {
    if (/INPUT|TEXTAREA|SELECT/.test(e.target?.tagName ?? "")) return;
    if (e.code === "Enter") { e.preventDefault(); song ? stopSong() : playSong(); }
    if (e.code === "KeyT" && !e.repeat) tap();
    if (e.code === "KeyP" && !e.repeat) $(".seq-play").click();
    if (e.code === "KeyB" && !e.repeat) $(".rec").click();
  };
  document.addEventListener("keydown", onKey);

  indexBanks();
  setBank(0, true);
  renderLcd();
  renderSteps();
  tick();

  return {
    destroy() {
      cancelAnimationFrame(raf);
      for (const [t, h] of Object.entries(handlers)) input.removeEventListener(t, h);
      document.removeEventListener("keydown", onKey);
      stopSong();
      seq.stop();
      for (const p of [...held.keys()]) stopRepeat(p);
      engine.stopAll();
      wave.destroy();
      video.removeAttribute("src");
      video.load();
      root.innerHTML = "";
    },
    get state() { return { bank, banks: chops.banks.length, grid: chops.grid, bpm: chops.bpm, playing: !!song, lastChop }; },
  };
}
