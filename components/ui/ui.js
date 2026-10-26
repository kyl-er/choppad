// Song screen: LCD + master volume/meter, video, 4x4 pads, bank strip (navigate the song), waveform,
// song transport (play the original, pads follow), regrid, modes, MIDI.
import { createEngine, resolveChop } from "/engine.js";
import { cellToPad } from "/layout.js";
import { createWaveform } from "/waveform.js";

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
    <div class="banks" title="Click a bank to jump there"><div class="banks-track"></div><i class="banks-head"></i></div>
    <div class="wave"></div>
    <div class="transport">
      <button class="play">▶ Play song</button><button class="stop">■ Stop</button>
      <label><input type="checkbox" class="follow" checked> pads follow song</label>
      <label class="zoom">zoom <input class="zoom-in" type="range" min="0" max="300" step="10" value="0"></label>
      <span class="hint muted">Pads Z X C V / A S D F / Q W E R / 1 2 3 4 · banks [ ] · Enter play/pause · Space stop</span>
    </div>
    <div class="panels">
      <div class="panel"><h3>Grid</h3><div class="row grid-btns"></div><div class="row meter-btns"></div>
        <div class="row tempo-btns"></div></div>
      <div class="panel"><h3>Pads</h3>
        <label>mode <select class="mode"><option value="oneshot">one-shot</option><option value="gate">gate (hold)</option><option value="playthrough">play-through</option></select></label>
        <label><input type="checkbox" class="choke" checked> choke</label>
        <label><input type="checkbox" class="full" checked> full level</label></div>
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
    onChopClick: (idx) => {
      const b = chopToBank[idx];
      if (b !== bank) setBank(b, false);
      hit(chops.banks[b].indexOf(idx), 1);
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
  function renderBank() {
    const ids = chops.banks[bank] ?? [];
    for (let p = 0; p < 16; p++) {
      const c = resolveChop(chops, bank, p);
      const el = padEls[p];
      el.classList.toggle("empty", !c);
      el.querySelector(".pad-label").textContent = c ? padLabel(c) : "";
      el.querySelector(".pad-time").textContent = c ? fmt(c.start) : "";
      el.style.setProperty("--hue", c ? BAR_HUES[c.bar % 4] : 0);
    }
    const first = chops.chops[ids[0]], last = chops.chops[ids.at(-1)];
    $(".bank-label").textContent = `Bank ${bank + 1}/${chops.banks.length} · bars ${first.bar + 1}–${last.bar + 1}`;
    root.querySelectorAll(".bank-seg").forEach((s, n) => s.classList.toggle("active", n === bank));
    wave.showChops(ids.map((i) => chops.chops[i]), (i) => ids.indexOf(i));
    wave.scrollTo(first.start);
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
    const c = resolveChop(chops, bank, p);
    if (!c) return;
    stopSong();
    engine.trigger(p, velocity);
    lastChop = c;
    const el = padEls[p];
    el.classList.remove("hit");
    void el.offsetWidth;
    el.classList.add("hit");
    wave.flash(c.index);
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
  $(".zoom-in").addEventListener("input", (e) => wave.zoom(Number(e.target.value)));
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
    raf = requestAnimationFrame(tick);
  }

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
    "pad-on": (e) => hit(e.detail.pad, e.detail.velocity),
    "pad-off": (e) => engine.release(e.detail.pad),
    bank: (e) => setBank(bank + e.detail.delta, true),
    panic: () => { stopSong(); engine.stopAll(); },
    devices: (e) => renderDevices(e.detail.devices),
    "learn-state": (e) => {
      $(".learn-state").textContent = e.detail.active ? `Learning: tap pad ${e.detail.nextPad + 1} on your controller…` : "Pad map saved.";
    },
  };
  for (const [t, h] of Object.entries(handlers)) input.addEventListener(t, h);
  $(".learn").addEventListener("click", () => input.startLearn());
  $(".reset").addEventListener("click", () => { input.resetMap(); $(".learn-state").textContent = "Default map (notes 36–51)."; });
  const onKey = (e) => {
    if (e.code === "Enter" && !/INPUT|TEXTAREA|SELECT/.test(e.target?.tagName ?? "")) { e.preventDefault(); song ? stopSong() : playSong(); }
  };
  document.addEventListener("keydown", onKey);

  indexBanks();
  setBank(0, true);
  renderLcd();
  tick();

  return {
    destroy() {
      cancelAnimationFrame(raf);
      for (const [t, h] of Object.entries(handlers)) input.removeEventListener(t, h);
      document.removeEventListener("keydown", onKey);
      stopSong();
      engine.stopAll();
      wave.destroy();
      video.removeAttribute("src");
      video.load();
      root.innerHTML = "";
    },
    get state() { return { bank, banks: chops.banks.length, grid: chops.grid, bpm: chops.bpm, playing: !!song, lastChop }; },
  };
}
