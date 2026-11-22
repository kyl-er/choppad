// CLASSIC screen (kept as the ?ui=classic fallback; the default screen is ui.js + views/). Song screen (view only): LCD + master volume/meter, video, 4x4 pads, bank strip, waveform, transport,
// step grid, panels. All state lives in the session's store (actions.js); this file draws it and turns
// clicks and keys into `session` calls or dispatched actions. It can be replaced without touching the sound.
import { cellToPad } from "/layout.js";
import { createWaveform } from "/waveform.js";
import { DEFAULT_FX, PRESETS } from "/fx.js";
import { PADS, stepOf } from "/actions.js";
import { openSong } from "/session.js";

const BAR_HUES = [140, 210, 35, 300];
const fmt = (s) => `${Math.floor(s / 60)}:${(s % 60).toFixed(1).padStart(4, "0")}`;
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

/** Returns { destroy(), state, session }. Throws if the song can't be loaded. */
export async function mountSong(root, { id, audio, input }) {
  const { ctx, master, analyser } = audio;
  root.innerHTML = `<p class="loading">Loading song…</p>`;
  const session = await openSong({ id, audio, onProgress: (t) => { const el = root.querySelector(".loading"); if (el) el.textContent = t; } });
  const { meta, analysis, peaks, store, engine, seq, video } = session;
  const dispatch = (action, m) => store.dispatch(action, m);
  const st = () => store.get();

  root.innerHTML = `
  <section class="song-view">
    <header class="lcd">
      <div class="lcd-main"><div class="lcd-title">${esc(meta.title)}</div>
        <div class="lcd-row"><b class="bpm"></b> BPM · <b class="meter"></b> · <b class="key" title="musical key"></b> · <span class="pos">bar 1.1</span> · <span class="clock">0:00.0</span> / ${fmt(meta.duration_s)}
        · confidence <span class="conf">${Math.round((analysis.confidence ?? 0) * 100)}%</span></div></div>
      <div class="master"><label>Volume <input class="vol" type="range" min="0" max="1.5" step="0.01"></label>
        <div class="vu"><i></i></div></div>
    </header>
    <div class="stage">
      <div class="video-wrap"></div>
      <div class="pads-wrap">
        <div class="pads"></div>
        <div class="bank-nav"><button class="bank-prev" title="[">◂</button><span class="bank-label"></span><button class="bank-next" title="]">▸</button></div>
      </div>
    </div>
    <div class="fx">
      <div class="fx-main">
        <label class="fx-knob"><span>Tone</span><input name="tone" type="range" min="-1" max="1" step="0.01"><output data-fx="tone"></output></label>
        <label class="fx-knob"><span>Reverb</span><input name="reverb" type="range" min="0" max="1" step="0.01"><output data-fx="reverb"></output></label>
        <div class="fx-presets"></div>
      </div>
      <details class="fx-more"><summary>more sound: room size · filter · EQ</summary>
        <label class="slider">room size <input name="size" type="range" min="0" max="1" step="0.01"><output data-fx="size"></output></label>
        <label class="slider">filter <input name="filter" type="range" min="-1" max="1" step="0.01"><output data-fx="filter"></output></label>
        <label class="slider">low <input name="low" type="range" min="-12" max="12" step="0.5"><output data-fx="low"></output></label>
        <label class="slider">mid <input name="mid" type="range" min="-12" max="12" step="0.5"><output data-fx="mid"></output></label>
        <label class="slider">high <input name="high" type="range" min="-12" max="12" step="0.5"><output data-fx="high"></output></label>
        <p class="muted small">Effects shape everything you hear: pads, song, pattern, and the .wav export. Double-click a slider to reset it.</p>
      </details>
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
      <span class="seq-info muted"></span>
      <div class="stepgrid" title="Click a cell to add/remove a hit on the current bank · Shift-click = soft hit · click a row name to hear the pad"></div>
    </div>
    <div class="panels">
      <div class="panel"><h3>Grid</h3>
        <div class="row chop-mode"><span class="muted small">chop by:</span>
          <button class="mode-grid" type="button">grid</button><button class="mode-transient" type="button">transients</button>
          <input class="sens" type="range" min="0" max="1" step="0.05" value="0.5" title="transient sensitivity"><span class="sens-out muted small"></span></div>
        <div class="row grid-btns"></div><div class="row meter-btns"></div>
        <div class="row tempo-btns"></div>
        <div class="row"><button class="tap" title="T">tap tempo (T)</button><span class="tap-bpm muted">tap 4×…</span><button class="tap-apply" disabled>apply</button></div></div>
      <div class="panel stretch"><h3>Time-stretch</h3>
        <div class="row"><label>fit to <input class="st-bpm" type="number" min="30" max="300" step="0.1" style="width:5em"> BPM</label></div>
        <div class="row"><button class="st-pad">pad</button><button class="st-bank">bank</button><button class="st-pattern">pattern pads</button><button class="st-clear">reset pad</button></div>
        <div class="st-out muted small">Same pitch, new length (rubberband). A stretched pad plays its rendering instead of the song region; pitch still works on top.</div></div>
      <div class="panel kit"><h3>Kit</h3>
        <div class="row"><input class="kit-name" placeholder="kit name" style="width:9em"><button class="kit-save">save bank as kit</button></div>
        <div class="kit-out muted small">Kits (16 pads, any songs) are listed in the library on the left; click one to put it on this bank.</div></div>
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
        <div class="row"><button class="exp-h2">install bank → Hydrogen kit</button><button class="exp-sfz">write bank → SFZ</button><button class="exp-zip">bank → zip of WAVs</button></div>
        <div class="exp-out muted small">Exports the current bank (16 pads). The Hydrogen kit gets your tone/reverb baked in; SFZ stays dry.</div></div>
      <div class="panel"><h3>MIDI</h3><ul class="devices"></ul>
        <div class="row"><button class="learn">Learn pads</button><button class="reset">Reset map</button></div>
        <div class="learn-state muted"></div></div>
    </div>
  </section>`;
  const $ = (s) => root.querySelector(s);

  $(".video-wrap").append(video);

  // ---- song transport ----
  $(".play").addEventListener("click", () => (session.songPlaying ? session.stopSong() : session.playSong()));
  $(".stop").addEventListener("click", () => { session.stopSong(); engine.stopAll(); });
  const onSong = (e) => { $(".play").textContent = e.detail.playing ? "❚❚ Pause" : "▶ Play song"; };
  session.on("song", onSong);

  // ---- banks / pads ----
  const padLabel = session.chopLabel;
  const padEls = [];
  for (let row = 0; row < 4; row++) {
    for (let col = 0; col < 4; col++) {
      const p = cellToPad(row, col);
      const el = document.createElement("button");
      el.className = "pad";
      el.dataset.pad = p;
      el.innerHTML = `<span class="pad-no">${p + 1}</span><span class="pad-label"></span><span class="pad-time"></span>`;
      el.addEventListener("pointerdown", (e) => { e.preventDefault(); session.hit(p, 1); });
      el.addEventListener("pointerup", () => engine.release(p));
      el.addEventListener("pointerleave", () => engine.release(p));
      padEls[p] = el;
      $(".pads").append(el);
    }
  }
  const wave = createWaveform($(".wave"), {
    peaks: peaks.peaks, duration: peaks.duration_s, media: video,
    onPadClick: (p) => session.hit(p, 1),
    // the region is already where the user dragged it, so the view must not redraw the regions for this change
    onSliceEdit: (p, start, end) => {
      dispatch({ type: "slice.set", bank: st().bank, pad: p, start, end }, { from: "wave" });
      dispatch({ type: "pad.select", pad: p });
    },
    onUserSeek: (t) => { if (session.songPlaying) session.playSong(t); else if (st().settings.follow) session.followTo(t); },
  });

  function renderBankStrip() {
    const { chops } = st();
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
      seg.addEventListener("click", () => session.setBank(n, true));
      track.append(seg);
    });
  }
  function renderPads() {
    const { bank, selected } = st();
    for (let p = 0; p < PADS; p++) {
      const c = engine.getSlice(bank, p);
      const el = padEls[p];
      el.classList.toggle("empty", !c);
      el.classList.toggle("custom", !!c?.custom);
      el.classList.toggle("stretched", !!c?.stretch);   // W3: plays a time-stretched rendering
      el.classList.toggle("foreign", !!c?.song);         // W3: slice of another song (kit)
      el.classList.toggle("selected", p === selected);
      el.querySelector(".pad-label").textContent = c ? (c.custom ? "★ " + fmt(c.start) : padLabel(c)) : "";
      el.querySelector(".pad-time").textContent = c ? (c.custom ? `${(c.end - c.start).toFixed(2)}s` : fmt(c.start)) + (c.stretch ? ` ×${c.stretch.ratio.toFixed(2)}` : "") + (c.song ? " ♪" : "") : "";
      el.style.setProperty("--hue", c ? (c.custom ? 48 : BAR_HUES[c.bar % 4]) : 0);
    }
  }
  // redrawRegions=false when the change came from dragging a region on the waveform
  function renderBank(redrawRegions = true) {
    const { chops, bank } = st();
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
    if (redrawRegions) {
      wave.showSlices(slices);
      fitZoom(first.start, last.end);
    }
    renderInspector();
    renderSteps();
  }
  function flashPad(p) {
    const el = padEls[p];
    el.classList.remove("hit");
    void el.offsetWidth;
    el.classList.add("hit");
  }
  const onHit = (e) => { flashPad(e.detail.pad); wave.flash(e.detail.pad); };
  const onFlash = (e) => flashPad(e.detail.pad);
  session.on("hit", onHit);
  session.on("flash", onFlash);
  $(".bank-prev").addEventListener("click", () => session.setBank(st().bank - 1, true));
  $(".bank-next").addEventListener("click", () => session.setBank(st().bank + 1, true));

  // ---- regrid ----
  const regrid = (change) => session.regrid(change);
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
  $(".mode-grid").addEventListener("click", () => regrid({ mode: "grid" }));
  $(".mode-transient").addEventListener("click", () => regrid({ mode: "transient" }));
  $(".sens").addEventListener("change", (e) => regrid({ mode: "transient", sensitivity: Number(e.target.value) }));
  btn(".tempo-btns", "÷2", () => regrid({ mult: 0.5 }));
  btn(".tempo-btns", "×2", () => regrid({ mult: 2 }));
  btn(".tempo-btns", "beat −1", () => regrid({ shift: -1 }));
  btn(".tempo-btns", "beat +1", () => regrid({ shift: 1 }));
  btn(".tempo-btns", "set bar 1 = last pad", () => regrid({ bar1: true }));

  // ---- kits ----
  $(".kit-save").addEventListener("click", async () => {
    const r = await session.saveBankAsKit($(".kit-name").value.trim());
    $(".kit-out").innerHTML = r.ok ? `✓ saved kit “${esc(r.kit.name)}” (${r.kit.pads.filter(Boolean).length} pads)` : `<span class="error">${esc(r.error)}</span>`;
  });

  // ---- time-stretch ----
  const stBpm = $(".st-bpm");
  stBpm.value = st().chops.bpm.toFixed(1);
  const stretchTo = () => ({ target_bpm: Number(stBpm.value) });
  const stretchDone = (r) => {
    const list = [].concat(r), bad = list.filter((x) => !x.ok);
    $(".st-out").innerHTML = bad.length ? `<span class="error">${esc(bad[0].error)}</span>` : `✓ ${list.length} pad${list.length === 1 ? "" : "s"} fitted to ${stBpm.value} BPM`;
  };
  $(".st-pad").addEventListener("click", async () => { $(".st-out").textContent = "stretching…"; stretchDone(await session.stretchPad(st().bank, st().selected, stretchTo())); });
  $(".st-bank").addEventListener("click", async () => { $(".st-out").textContent = "stretching…"; stretchDone(await session.stretchBank(stretchTo())); });
  $(".st-pattern").addEventListener("click", async () => { $(".st-out").textContent = "stretching…"; stretchDone(await session.stretchPattern(stretchTo())); });
  $(".st-clear").addEventListener("click", () => session.clearStretch(st().bank, st().selected));

  // ---- LCD, volume, meter ----
  function renderLcd() {
    const { chops } = st();
    $(".bpm").textContent = chops.bpm.toFixed(1);
    $(".meter").textContent = `${chops.beats_per_bar}/4`;
    const transient = chops.mode === "transient";
    $(".mode-grid").classList.toggle("on", !transient);
    $(".mode-transient").classList.toggle("on", transient);
    $(".sens-out").textContent = transient ? `${chops.chops.length} slices` : "";
    if (transient && document.activeElement !== $(".sens")) $(".sens").value = chops.sensitivity ?? 0.5;
    root.querySelectorAll(".grid-btns button").forEach((b) => b.classList.toggle("on", !transient && b.textContent === chops.grid));
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
    const { chops } = st();
    const t = session.position();
    $(".clock").textContent = fmt(t);
    const c = chops.chops.find((x) => x.start <= t && t < x.end);
    $(".pos").textContent = c ? `bar ${padLabel(c)}` : "—";
    $(".banks-head").style.left = `${(t / meta.duration_s) * 100}%`;
    if (session.songPlaying && st().settings.follow) session.followTo(t);
    markSection(t);
    if (seq.state.playing) {
      const cell = Math.floor(seq.position() * 4);
      if (cell !== nowCell) {
        stepsEl.querySelectorAll(".now").forEach((c) => c.classList.remove("now"));
        stepsEl.querySelectorAll(`[data-step="${cell}"]`).forEach((c) => c.classList.add("now"));
        nowCell = cell;
      }
    }
    raf = requestAnimationFrame(tick);
  }

  // ---- pad inspector (MPC program edit) ----
  const insp = $(".inspector");
  function renderInspector() {
    const { bank, selected, project } = st();
    const P = project.pads[selected];
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
      dispatch({ type: "pad.set", pad: st().selected, params: { [k]: v } });
    });
  }
  insp.querySelector(".grab").addEventListener("click", () => {
    const { bank, selected, chops } = st();
    const t = session.position();
    const c = engine.getSlice(bank, selected);
    const len = c ? c.end - c.start : 60 / chops.bpm;
    dispatch({ type: "slice.set", bank, pad: selected, start: t, end: Math.min(meta.duration_s, t + len) });
  });
  insp.querySelector(".unslice").addEventListener("click", () => dispatch({ type: "slice.clear", bank: st().bank, pad: st().selected }));
  insp.querySelector(".all").addEventListener("click", () => dispatch({ type: "pad.copyToAll", pad: st().selected }));
  insp.querySelector(".resetpad").addEventListener("click", () => dispatch({ type: "pad.reset", pad: st().selected }));

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
    if (!(await session.reanalyze(bpm))) { $(".tap-bpm").textContent = "failed"; return; }
    $(".tap-bpm").textContent = `now ${st().chops.bpm.toFixed(1)} BPM`;
  });

  // ---- sound: tone / reverb up front, size / filter / EQ under "more" ----
  const fxEl = $(".fx");
  const fxInputs = Object.fromEntries([...fxEl.querySelectorAll("input[name]")].map((el) => [el.name, el]));
  const fxText = {
    tone: (v) => (Math.abs(v) < 0.02 ? "neutral" : v < 0 ? `darker ${Math.round(-v * 100)}%` : `brighter ${Math.round(v * 100)}%`),
    reverb: (v) => (v < 0.01 ? "off" : `${Math.round(v * 100)}%`),
    size: (v) => (v < 0.33 ? "room" : v < 0.66 ? "studio" : "hall") + ` · ${(0.4 + v * 5.6).toFixed(1)}s`,
    filter: (v) => (Math.abs(v) < 0.02 ? "off" : v < 0 ? `low-pass ${Math.round(-v * 100)}%` : `high-pass ${Math.round(v * 100)}%`),
    low: (v) => `${v > 0 ? "+" : ""}${v} dB`, mid: (v) => `${v > 0 ? "+" : ""}${v} dB`, high: (v) => `${v > 0 ? "+" : ""}${v} dB`,
  };
  function renderFx() {
    const P = st().project.fx;
    for (const [k, el] of Object.entries(fxInputs)) {
      el.value = P[k];
      fxEl.querySelector(`[data-fx="${k}"]`).textContent = fxText[k](P[k]);
    }
    const match = Object.entries(PRESETS).find(([, pr]) => Object.keys(DEFAULT_FX).every((k) => Math.abs(pr[k] - P[k]) < 1e-6));
    fxEl.querySelectorAll(".fx-presets button").forEach((b) => b.classList.toggle("on", b.dataset.preset === match?.[0]));
  }
  for (const [k, el] of Object.entries(fxInputs)) {
    el.addEventListener("input", () => dispatch({ type: "fx.set", params: { [k]: Number(el.value) } }));
    el.addEventListener("dblclick", () => dispatch({ type: "fx.set", params: { [k]: DEFAULT_FX[k] } }));
  }
  for (const name of Object.keys(PRESETS)) {
    const b = document.createElement("button");
    b.textContent = name;
    b.dataset.preset = name;
    b.addEventListener("click", () => dispatch({ type: "fx.set", params: PRESETS[name] }));
    fxEl.querySelector(".fx-presets").append(b);
  }
  renderFx();

  // ---- sequencer (record / loop / quantize / swing / note repeat / export) ----
  $(".seq-bars").value = String(st().project.pattern.bars);
  // Step grid: 16 pad rows x 1/16 steps. Click toggles a hit for that pad on the current bank.
  const stepsEl = $(".stepgrid");
  let nowCell = -1;
  const stepCount = () => st().project.pattern.bars * st().chops.beats_per_bar * 4;
  function buildStepGrid() {
    const { chops } = st();
    const n = stepCount();
    stepsEl.style.setProperty("--steps", n);
    let html = "";
    for (let p = 15; p >= 0; p--) { // pad 16 on top, like the pad grid
      html += `<button class="row-name" data-pad="${p}">${p + 1}</button>`;
      for (let i = 0; i < n; i++) html += `<i data-pad="${p}" data-step="${i}" class="${i % 4 === 0 ? "beat" : ""}${i % (4 * chops.beats_per_bar) === 0 ? " bar" : ""}"></i>`;
    }
    stepsEl.innerHTML = html;
    stepsEl.dataset.n = n;
    nowCell = -1;
  }
  function renderSteps() {
    const { chops, bank, selected, project } = st();
    const n = stepCount();
    if (Number(stepsEl.dataset.n) !== n) buildStepGrid();
    const at = new Map(); // "pad:step" -> event
    for (const e of project.pattern.events) at.set(`${e.pad}:${stepOf(e)}`, e);
    for (const c of stepsEl.querySelectorAll("i")) {
      const e = at.get(`${c.dataset.pad}:${c.dataset.step}`);
      c.classList.toggle("on", !!e);
      c.classList.toggle("soft", !!e && e.vel < 0.8);
      c.classList.toggle("other", !!e && e.bank !== bank);
      c.title = e ? `pad ${Number(c.dataset.pad) + 1} · bank ${e.bank + 1}${e.vel < 0.8 ? " · soft" : ""}` : "";
    }
    for (const r of stepsEl.querySelectorAll(".row-name")) {
      const p = Number(r.dataset.pad);
      r.classList.toggle("empty", !engine.getSlice(bank, p));
      r.classList.toggle("selected", p === selected);
    }
    $(".seq-info").textContent = `${project.pattern.events.length} hits · ${chops.bpm.toFixed(1)} BPM`;
  }
  stepsEl.addEventListener("click", (e) => {
    const t = e.target;
    if (t.classList.contains("row-name")) { session.hit(Number(t.dataset.pad), 1); return; }
    if (t.tagName !== "I") return;
    const pad = Number(t.dataset.pad), step = Number(t.dataset.step);
    const existed = st().project.pattern.events.some((x) => x.pad === pad && stepOf(x) === step);
    dispatch({ type: "pattern.toggleStep", pad, step, bank: st().bank, vel: e.shiftKey ? 0.55 : 1 });
    if (!existed && !seq.state.playing) engine.trigger(pad, 1); // audition the new hit
  });
  function seqButtons() {
    $(".rec").classList.toggle("on", seq.state.recording);
    $(".seq-play").textContent = seq.state.playing ? "■ pattern" : "▶ pattern";
  }
  $(".rec").addEventListener("click", () => session.toggleRecord());
  $(".seq-play").addEventListener("click", () => session.togglePattern());
  session.on("seq", seqButtons);
  const setting = (params) => dispatch({ type: "settings.set", params });
  $(".metro").addEventListener("change", (e) => setting({ metronome: e.target.checked }));
  $(".seq-bars").addEventListener("change", (e) => dispatch({ type: "pattern.setBars", bars: Number(e.target.value), beatsPerBar: st().chops.beats_per_bar }));
  $(".seq-q").addEventListener("change", (e) => setting({ quant: e.target.value }));
  $(".seq-swing").addEventListener("input", (e) => setting({ swing: Number(e.target.value) }));
  $(".seq-undo").addEventListener("click", () => dispatch({ type: "pattern.undo" }));
  $(".seq-clear").addEventListener("click", () => dispatch({ type: "pattern.clear" }));
  $(".repeat").addEventListener("change", (e) => setting({ repeat: e.target.checked }));
  $(".repeat-rate").addEventListener("change", (e) => setting({ repeatRate: Number(e.target.value) }));
  $(".follow").addEventListener("change", (e) => setting({ follow: e.target.checked }));
  const download = (bytes, name, type) => {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([bytes], { type }));
    a.download = name;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 5000);
  };
  const baseName = () => meta.title.replace(/[^\w-]+/g, "_").slice(0, 40);
  $(".seq-midi").addEventListener("click", () => download(session.patternMidi(), `${baseName()}-pattern.mid`, "audio/midi"));
  $(".seq-wav").addEventListener("click", async () => {
    if (!st().project.pattern.events.length) return;
    $(".seq-info").textContent = "rendering…";
    download(await session.renderPatternWav(), `${baseName()}-pattern.wav`, "audio/wav");
    renderSteps();
  });

  // ---- sections (offline structure; AI can relabel) ----
  const SECTION_HUES = { intro: 200, verse: 140, chorus: 35, bridge: 280, outro: 200 };
  function renderSections() {
    const el = $(".sections");
    el.innerHTML = "";
    for (const sc of st().sections) {
      const b = document.createElement("button");
      b.className = "section-seg";
      b.style.left = `${(sc.start_s / meta.duration_s) * 100}%`;
      b.style.width = `${((sc.end_s - sc.start_s) / meta.duration_s) * 100}%`;
      b.style.setProperty("--hue", SECTION_HUES[sc.label] ?? (sc.letter.charCodeAt(0) * 47) % 360);
      b.textContent = sc.label;
      b.title = `${sc.label} (${sc.letter}) · bars ${sc.start_bar + 1}–${sc.end_bar} · ${fmt(sc.start_s)}`;
      b.addEventListener("click", () => {
        if (session.songPlaying) session.playSong(sc.start_s); else { video.currentTime = sc.start_s; session.followTo(sc.start_s + 1e-3); }
      });
      el.append(b);
    }
  }
  function markSection(t) {
    const list = st().sections;
    root.querySelectorAll(".section-seg").forEach((b, i) => b.classList.toggle("active", list[i] && list[i].start_s <= t && t < list[i].end_s));
  }
  session.loadSections();

  // ---- AI assist ----
  async function runAI() {
    const out = $(".ai-out");
    out.textContent = "Listening to the song with an audio model…";
    $(".ai-run").disabled = true;
    try {
      const ai = await session.advise();
      const chk = ai.bpm_meter_check || {};
      out.innerHTML = `<b>${esc(ai.model)}</b>: ${ai.sections.length} sections · felt ${esc(chk.felt_bpm)} BPM (${esc(chk.meter_hint)})` +
        (chk.bpm_mult_hint && chk.bpm_mult_hint !== 1 ? ` — suggests ${chk.bpm_mult_hint === 2 ? "×2" : "÷2"}` : "") + `<br>${esc(chk.notes || "")}`;
      $(".ai-bank").disabled = false;
    } catch (err) {
      const msg = String(err.message || err);
      out.innerHTML = `<span class="error">${esc(/401|User not found/.test(msg) ? "The audio model endpoint rejected the API key (401). Set CHOPPAD_LLM_KEY (or OPENROUTER_API_KEY) and restart choppad; CHOPPAD_LLM_URL / CHOPPAD_LLM_MODEL pick another endpoint, such as the local router." : msg)}</span>`;
    } finally {
      $(".ai-run").disabled = false;
    }
  }
  $(".ai-run").addEventListener("click", runAI);
  $(".ai-bank").addEventListener("click", () => {
    const labels = session.loadAiBank();
    if (labels) $(".ai-out").insertAdjacentHTML("beforeend", `<br>Loaded on bank ${st().bank + 1}: ${labels.map(esc).join(", ")}`);
  });

  // ---- export ----
  async function doExport(kind) {
    const out = $(".exp-out");
    out.textContent = "exporting…";
    const r = await session.exportBank(kind);
    out.innerHTML = r.ok ? `✓ ${esc(r.path)}${r.url ? ` <a class="exp-download" href="${esc(r.url)}" download>download</a>` : ""}${r.note ? "<br>" + esc(r.note) : ""}` : `<span class="error">${esc(r.error)}</span>`;
  }
  $(".exp-h2").addEventListener("click", () => doExport("hydrogen"));
  $(".exp-sfz").addEventListener("click", () => doExport("sfz"));
  $(".exp-zip").addEventListener("click", () => doExport("zip"));

  // ---- key (shown in the LCD; detected once on the server for songs that have none) ----
  const keyText = (k) => (k ? `${k.tonic} ${k.mode === "minor" ? "min" : "maj"}` : "");
  const renderKey = () => {
    const k = analysis.key;
    $(".key").textContent = keyText(k);
    $(".key").title = k ? `key ${k.tonic} ${k.mode} (confidence ${Math.round(k.confidence * 100)}%)` : "musical key";
  };
  renderKey();
  if (!analysis.key) session.detectKey().then((k) => { if (k) { renderKey(); window.choppad?.library?.refresh?.(); } });

  // ---- modes ----
  $(".mode").addEventListener("change", (e) => setting({ mode: e.target.value }));
  $(".choke").addEventListener("change", (e) => setting({ choke: e.target.checked }));
  $(".full").addEventListener("change", (e) => setting({ fullLevel: e.target.checked }));

  // ---- MIDI / keyboard ----
  const renderDevices = (devs) => {
    $(".devices").innerHTML = devs.length ? devs.map((d) => `<li>🎹 ${esc(d.name)} <span class="muted">${esc(d.state)}</span></li>`).join("")
      : `<li class="muted">No MIDI devices — plug in your phone (USB → MIDI mode)</li>`;
  };
  renderDevices(input.getDevices());
  const handlers = {
    "pad-on": (e) => { session.hit(e.detail.pad, e.detail.velocity); session.startRepeat(e.detail.pad, e.detail.velocity); },
    "pad-off": (e) => { session.release(e.detail.pad); session.stopRepeat(e.detail.pad); },
    bank: (e) => session.setBank(st().bank + e.detail.delta, true),
    panic: () => session.panic(),
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
    if (e.code === "Enter") { e.preventDefault(); session.songPlaying ? session.stopSong() : session.playSong(); }
    if (e.code === "KeyT" && !e.repeat) tap();
    if (e.code === "KeyP" && !e.repeat) $(".seq-play").click();
    if (e.code === "KeyB" && !e.repeat) $(".rec").click();
  };
  document.addEventListener("keydown", onKey);

  // ---- redraw on state changes ----
  function syncControls() {
    const S = st().settings;
    $(".mode").value = S.mode;
    $(".choke").checked = S.choke;
    $(".full").checked = S.fullLevel;
    $(".metro").checked = S.metronome;
    $(".seq-q").value = S.quant;
    $(".seq-swing").value = S.swing;
    $(".repeat").checked = S.repeat;
    $(".repeat-rate").value = String(S.repeatRate);
    $(".follow").checked = S.follow;
    $(".seq-bars").value = String(st().project.pattern.bars);
  }
  const unsubscribe = store.subscribe((s, prev, _action, m) => {
    const P = s.project, Q = prev.project;
    if (s.chops !== prev.chops) { renderBankStrip(); renderLcd(); }
    if (s.chops !== prev.chops || s.bank !== prev.bank || P.overrides !== Q.overrides) renderBank(m.from !== "wave");
    else {
      if (s.selected !== prev.selected) { renderPads(); renderInspector(); renderSteps(); }
      else {
        if (P.pads !== Q.pads) renderInspector();
        if (P.pattern !== Q.pattern) renderSteps();
      }
    }
    if (P.fx !== Q.fx) renderFx();
    if (s.sections !== prev.sections) renderSections();
    if (s.settings !== prev.settings || P.pattern.bars !== Q.pattern.bars) syncControls();
  });

  renderBankStrip();
  session.setBank(0, true);
  renderBank();
  renderLcd();
  renderFx();
  syncControls();
  seqButtons();
  tick();

  return {
    session,
    destroy() {
      cancelAnimationFrame(raf);
      unsubscribe();
      for (const [t, h] of Object.entries(handlers)) input.removeEventListener(t, h);
      document.removeEventListener("keydown", onKey);
      wave.destroy();
      session.destroy();
      root.innerHTML = "";
    },
    get state() {
      const { bank, chops } = st();
      return { bank, banks: chops.banks.length, grid: chops.grid, bpm: chops.bpm, playing: session.songPlaying, lastChop: session.lastChop };
    },
  };
}
