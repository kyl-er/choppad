// FIG. 7: section strip, bank strip and the waveform (wavesurfer, reused through waveform.js) of the current bank.
// Clicking a section or bank jumps there; dragging or resizing a pad's region re-slices it.
import { createWaveform } from "/waveform.js";
import { PADS, bankCount } from "/actions.js";
import { createDisposer, cssVar, fmt, noSong, sectionIsLive } from "./util.js";

export function mount(root, session, env) {
  const d = createDisposer();
  const { store, engine, video, meta, peaks } = session;
  const st = () => store.get();
  root.innerHTML = `<span class="fignum">FIG. 7 — SOURCE WAVEFORM · CURRENT BANK <b class="bank-n"></b> · BARS <span class="bank-bars"></span></span>
    <div class="sections" title="Song sections (click to jump)"></div>
    <div class="bankstrip" title="Click a bank to jump there"><div class="banks-track"></div><i class="banks-head"></i></div>
    <div class="wave"></div>`;
  const $ = (s) => root.querySelector(s);
  const waveEl = $(".wave");
  const readStyle = () => ({
    height: Math.max(40, waveEl.clientHeight - 4),
    wave: cssVar("--wf-ink"), progress: cssVar("--wf-progress"), cursor: cssVar("--acc-o"), region: cssVar("--wf-region"),
    custom: cssVar("--wf-custom"), label: cssVar("--wf-label"), sel: cssVar("--acc-b"), selFill: cssVar("--wf-sel-fill"), play: cssVar("--acc-o"),
  });
  // The container (`.wave`) lives as long as the view; the wavesurfer inside it is made again when the source song changes.
  // a mega bank with no song has no waveform: the view shows "Pick a song to chop" and this stand-in takes the calls
  const noWave = { destroy() {}, setHeight() {}, setStyle() {}, showSlices() {}, markSelected() {}, zoom() {}, flash() {}, scrollTo() {} };
  waveEl.append(noSong(session, env, d));
  const makeWave = () => !session.source ? noWave : createWaveform(waveEl, {
    peaks: peaks.peaks, duration: peaks.duration_s, media: video, style: readStyle(),
    onPadClick: (p) => session.hit(p, env.noteVar.get() / 127),
    onSliceEdit: (p, start, end) => {
      if (engine.getSlice(st().bank, p)?.kept) return;   // a kept pad owns its audio: dragging a region must not re-slice it
      store.dispatch({ type: "slice.set", bank: st().bank, pad: p, start, end }, { from: "wave" });
      store.dispatch({ type: "pad.select", pad: p });
    },
    onUserSeek: (t) => { if (session.songPlaying) session.playSong(t); else if (st().settings.follow) session.followTo(t); },
  });
  let wave = makeWave();
  d.add(() => wave.destroy());
  const ro = new ResizeObserver(() => { wave.setHeight(Math.max(40, waveEl.clientHeight - 4)); fitZoom(); });
  ro.observe(waveEl);
  d.add(() => ro.disconnect());
  d.add(env.theme.on(() => wave.setStyle(readStyle())));

  // ---- bank strip + sections
  let dur = meta.duration_s || 1;
  function renderBankStrip() {
    const { chops } = st(), track = $(".banks-track");
    track.innerHTML = "";
    chops.banks.forEach((b, n) => {
      const s = chops.chops[b[0]].start, e = chops.chops[b.at(-1)].end;
      const seg = document.createElement("button");
      seg.type = "button";
      seg.className = "bank-seg";
      seg.style.left = `${(s / dur) * 100}%`;
      seg.style.width = `${((e - s) / dur) * 100}%`;
      seg.title = `Bank ${n + 1}: bars ${chops.chops[b[0]].bar + 1}–${chops.chops[b.at(-1)].bar + 1} (${fmt(s)})`;
      seg.setAttribute("aria-label", `Bank ${n + 1}, bars ${chops.chops[b[0]].bar + 1} to ${chops.chops[b.at(-1)].bar + 1}`);
      seg.textContent = n + 1;
      seg.addEventListener("click", () => session.setBank(n, true));
      track.append(seg);
    });
  }
  function renderSections() {
    const el = $(".sections");
    el.innerHTML = "";
    for (const sc of st().sections) {
      const b = document.createElement("button");
      b.type = "button";
      b.className = `section-seg${sectionIsLive(sc.label) ? " live" : ""}`;
      b.style.left = `${(sc.start_s / dur) * 100}%`;
      b.style.width = `${((sc.end_s - sc.start_s) / dur) * 100}%`;
      b.textContent = sc.label;
      b.title = `${sc.label} (${sc.letter}) · bars ${sc.start_bar + 1}–${sc.end_bar} · ${fmt(sc.start_s)}`;
      b.addEventListener("click", () => {
        if (session.songPlaying) session.playSong(sc.start_s); else { video.currentTime = sc.start_s; session.followTo(sc.start_s + 1e-3); }
      });
      el.append(b);
    }
    el.insertAdjacentHTML("beforeend", `<i class="sec-head"></i>`);
  }

  // ---- bank change: pads' regions + zoom
  const slicesOf = (bank) => {
    const out = [];
    for (let p = 0; p < PADS; p++) {
      const c = engine.getSlice(bank, p);
      // a kept pad owns its audio and is not a slice of the open song: no region, so nothing offers to re-slice it
      if (c && !c.kept) out.push({ pad: p, start: c.start, end: c.end, bar: c.bar, custom: !!c.custom, label: `${p + 1}  ${c.custom ? "★" : session.chopLabel(c)}` });
    }
    return out;
  };
  let range = [0, 1];
  function fitZoom() {
    const z = env.zoom.get(), width = waveEl.clientWidth || 900;
    wave.zoom(z > 0 ? z : width / ((range[1] - range[0]) * 1.02), range[0]);
  }
  function renderBank(redrawRegions = true) {
    const { chops, bank, selected, project } = st();
    const ids = chops.banks[bank] ?? [];
    const first = chops.chops[ids[0]], last = chops.chops[ids.at(-1)];   // undefined on a bank past the song's own (kept pads only)
    $(".bank-n").textContent = `${bank + 1}/${bankCount(project, chops)}`;
    $(".bank-bars").textContent = first ? `${first.bar + 1}–${last.bar + 1}` : "—";
    root.querySelectorAll(".bank-seg").forEach((s, n) => s.classList.toggle("active", n === bank));
    if (redrawRegions) { wave.showSlices(slicesOf(bank)); if (first) range = [first.start, last.end]; fitZoom(); }
    wave.markSelected(selected);
  }
  d.add(env.zoom.on(() => fitZoom()));
  d.add(store.subscribe((s, prev, _a, m) => {
    if (s.chops !== prev.chops) renderBankStrip();
    if (s.chops !== prev.chops || s.bank !== prev.bank || s.project.overrides !== prev.project.overrides) renderBank(m.from !== "wave");
    else if (s.selected !== prev.selected) wave.markSelected(s.selected);
    if (s.sections !== prev.sections) renderSections();
  }));
  const onHit = (e) => wave.flash(e.detail.pad);
  session.on("hit", onHit);
  d.add(() => session.off("hit", onHit));
  // the source song changed (the session emits it after swapping buffer, peaks, chops): same view, same container, new wavesurfer
  const onSource = () => {
    dur = meta.duration_s || 1;
    wave.destroy();
    wave = makeWave();
    renderBankStrip(); renderSections(); renderBank();
    $(".banks-head").style.left = "0%"; $(".sec-head").style.left = "0%";
  };
  session.on("source", onSource);
  d.add(() => session.off("source", onSource));

  // ---- playhead: strips, section highlight, follow
  let lastSec = -2;
  d.add(env.ticker.add(() => {
    const t = session.position(), pct = `${Math.min(100, (t / dur) * 100)}%`;
    const bh = $(".banks-head"); if (bh.style.left !== pct) bh.style.left = pct;
    const sh = $(".sec-head"); if (sh && sh.style.left !== pct) sh.style.left = pct;
    if (session.songPlaying && st().settings.follow) session.followTo(t);
    const list = st().sections, idx = list.findIndex((s) => s.start_s <= t && t < s.end_s);
    if (idx !== lastSec) { lastSec = idx; root.querySelectorAll(".section-seg").forEach((b, i) => b.classList.toggle("active", i === idx)); }
  }));

  renderBankStrip(); renderSections(); renderBank();
  return { destroy() { d.run(); root.innerHTML = ""; } };
}
