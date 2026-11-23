// 4x4 pads (pad 1 bottom-left, MPC style), bank keys A-D with group arrows, the bank label and the note-variation slider.
// Also the pad / bank / panic events from the keyboard and MIDI input. createPadGrid() is shared with the phone pad mode.
import { cellToPad } from "/layout.js";
import { PADS, bankCount, sixteenLevel } from "/actions.js";
import { createDisposer, fmt, shortSong } from "./util.js";

/** Builds the 16 pad buttons inside `grid` and keeps them drawn from the store. velocity() gives the hit velocity.
 *  songTitle(id) names the song a kept pad came from (the library knows it; without it the id is shown). */
export function createPadGrid(grid, session, { velocity = () => 1, onDown, songTitle = () => null } = {}) {
  const titleOf = (id) => (id === session.source ? session.meta.title : songTitle(id)) ?? id;
  const d = createDisposer();
  const { store, engine } = session;
  const els = [];
  for (let row = 0; row < 4; row++) {
    for (let col = 0; col < 4; col++) {
      const p = cellToPad(row, col);
      const el = document.createElement("button");
      el.type = "button";
      el.className = "pad";
      el.dataset.pad = p;
      el.setAttribute("aria-label", `Pad ${p + 1}`);
      el.innerHTML = `<span class="pad-no">${p + 1}</span><span class="pad-label"></span><span class="pad-time"></span><span class="pad-mg" hidden></span><span class="pad-tag" hidden></span>`;
      const down = new Set();
      el.addEventListener("pointerdown", (e) => {
        e.preventDefault();
        down.add(e.pointerId);
        el.classList.add("held");
        try { el.setPointerCapture(e.pointerId); } catch { /* synthetic events */ }
        onDown?.(p);
        session.hit(p, velocity());
      });
      const up = (e) => { down.delete(e.pointerId); if (!down.size) { el.classList.remove("held"); session.release(p); } };
      el.addEventListener("pointerup", up);
      el.addEventListener("pointercancel", up);
      el.addEventListener("lostpointercapture", up);
      el.addEventListener("contextmenu", (e) => e.preventDefault());
      // keyboard users: Enter / Space on a focused pad plays it
      el.addEventListener("keydown", (e) => { if ((e.key === "Enter" || e.key === " ") && !e.repeat) { e.preventDefault(); e.stopPropagation(); session.hit(p, velocity()); } });
      els[p] = el;
      grid.append(el);
    }
  }
  const STEM_TAG = { drums: "DRM", bass: "BAS", vocals: "VOC", other: "OTH" };
  const fmtLevel = (mode, lv) => (mode === "velocity" ? [`${Math.round(lv.vel * 100)}`, "vel %"] : mode === "pitch" ? [`${lv.pitch > 0 ? "+" : ""}${lv.pitch}`, "semitones"]
    : [lv.cutoff >= 1000 ? `${(lv.cutoff / 1000).toFixed(1)}k` : String(lv.cutoff), "cutoff Hz"]);
  function render() {
    const { bank, selected, project, settings } = store.get();
    const sx = settings.sixteen;
    for (let p = 0; p < PADS; p++) {
      const c = engine.getSlice(bank, p), el = els[p];
      const o = project.overrides[`${bank}:${p}`], mg = project.pads[p].muteGroup ?? 0;
      el.classList.toggle("fitted", o?.stretch?.fit !== undefined);       // fitted to the project tempo (stretch.fit)
      el.classList.toggle("handstretched", !!o?.stretch && o.stretch.fit === undefined);
      el.classList.toggle("resampled", !!o?.src);
      el.classList.toggle("levels", !!sx);
      el.classList.toggle("kept", !!o?.kept);                                // CHOP-13: owns its audio and its sound
      el.classList.toggle("tagged", !!mg || !!o?.src || !!o?.stem || o?.stretch?.fit !== undefined);   // room under the label for the badges
      const mgEl = el.querySelector(".pad-mg"), tag = el.querySelector(".pad-tag");
      const mgText = mg ? `M${mg}` : "";
      if (mgEl.textContent !== mgText) { mgEl.textContent = mgText; mgEl.hidden = !mg; mgEl.title = mg ? `Mute group ${mg}` : ""; }
      const tagText = o?.kept ? "KEPT" : o?.src ? "RS" : o?.stem ? STEM_TAG[o.stem] ?? "STM" : o?.stretch?.fit !== undefined ? "FIT" : "";
      const from = o?.kept && o.from ? titleOf(o.from.song) : null;   // the song a kept pad came from
      const keptTitle = o?.kept ? `Kept pad: it owns its audio and its sound${from ? `, cut from ${from} at ${fmt(o.from.start)}` : ""}. It stays when the song changes.` : "";
      if (tag.textContent !== tagText || tag.title !== (keptTitle || tag.title)) { tag.textContent = tagText; tag.hidden = !tagText; tag.title = o?.kept ? keptTitle : o?.src ? "Resampled sound" : o?.stem ? `Plays the ${o.stem} stem` : tagText ? `Fitted to the project tempo (${o.stretch.fit} BPM)` : ""; }
      if (el.title !== keptTitle) el.title = keptTitle;
      el.setAttribute("aria-label", `Pad ${p + 1}${mg ? `, mute group ${mg}` : ""}${o?.kept ? `, kept${from ? ` from ${from}` : ""}` : o?.src ? ", resample" : ""}${o?.stem ? `, ${o.stem} stem` : ""}${o?.stretch?.fit !== undefined ? ", fitted to the project tempo" : o?.stretch ? ", stretched by hand" : ""}${sx ? `, level ${fmtLevel(sx.mode, sixteenLevel(sx.mode, p)).join(" ")}` : ""}`);
      el.classList.toggle("empty", !c);
      el.classList.toggle("custom", !!c?.custom);
      el.classList.toggle("stretched", !!c?.stretch);
      el.classList.toggle("foreign", !!c?.song);
      el.classList.toggle("selected", p === selected);
      let label = c ? (c.custom ? "★ " + fmt(c.start) : session.chopLabel(c)) : "";
      let time = c ? (c.custom ? `${(c.end - c.start).toFixed(2)}s` : fmt(c.start)) + (c.stretch ? ` ×${c.stretch.ratio.toFixed(2)}` : "") + (c.song ? " ♪" : "") : "";
      if (o?.kept) { label = from ? (shortSong(from).length > 8 ? shortSong(from).slice(0, 7) + "…" : shortSong(from)) : "kept"; time = from ? fmt(o.from.start) : `${(o.end - o.start).toFixed(2)}s`; }   // where it came from, not where it is now
      if (sx) [label, time] = fmtLevel(sx.mode, sixteenLevel(sx.mode, p));   // 16 levels: the pad shows its level, not the bar.beat
      const l = els[p].querySelector(".pad-label"), t = els[p].querySelector(".pad-time");
      if (l.textContent !== label) l.textContent = label;
      if (t.textContent !== time) t.textContent = time;
    }
  }
  const flash = (p) => {
    const el = els[p]; if (!el) return;
    el.classList.remove("hit"); void el.offsetWidth; el.classList.add("hit");
    clearTimeout(el._t); el._t = setTimeout(() => el.classList.remove("hit"), 180);
  };
  const onHit = (e) => flash(e.detail.pad), onFlash = (e) => flash(e.detail.pad);
  session.on("hit", onHit); session.on("flash", onFlash);
  d.add(() => { session.off("hit", onHit); session.off("flash", onFlash); });
  d.add(store.subscribe((s, prev) => { if (s.chops !== prev.chops || s.bank !== prev.bank || s.selected !== prev.selected || s.project.overrides !== prev.project.overrides || s.project.pads !== prev.project.pads || s.settings.sixteen !== prev.settings.sixteen) render(); }));
  render();
  return { els, render, flash, destroy() { d.run(); for (const e of els) clearTimeout(e._t); grid.innerHTML = ""; } };
}

export function mount(root, session, env) {
  const d = createDisposer();
  const { store } = session;
  const st = () => store.get();
  root.innerHTML = `<div class="pad-col">
      <div class="pads" data-c="5" role="group" aria-label="Pads"></div>
      <div class="banks" data-c="6" role="group" aria-label="Banks"></div>
      <div class="padkeys"><div class="bank-label" role="status"></div><button type="button" class="lvl-key" aria-pressed="false" title="16 levels: the 16 pads play the selected pad at 16 velocities, pitches or filter cutoffs. The mode is on the PAD page of the LCD."><i>▤</i><span>16 Levels</span></button><button type="button" class="keep-key" title="Keep the selected pad: its sound is saved into the mega bank and stays when the song changes."><i>◆</i><span>Keep</span></button></div>
    </div>
    <div class="nvwrap" data-c="7"><span class="cap vert">Note var.</span>
      <input type="range" min="1" max="127" step="1" class="nv" aria-label="Note variation: velocity of mouse and keyboard hits"><output class="nv-out"></output></div>`;
  const $ = (s) => root.querySelector(s);
  const nv = $(".nv"), nvOut = $(".nv-out");
  nv.value = env.noteVar.get();
  nv.addEventListener("input", () => env.noteVar.set(Number(nv.value)));
  d.add(env.noteVar.on((v) => { nv.value = v; nvOut.textContent = String(v); }));
  nvOut.textContent = String(env.noteVar.get());
  const velocity = () => env.noteVar.get() / 127;

  const grid = createPadGrid($(".pads"), session, { velocity, songTitle: (id) => env.nav.songInfo(id)?.title });
  d.add(() => grid.destroy());

  const banksEl = $(".banks");
  banksEl.innerHTML = `<button type="button" class="bkey nav bank-prev" aria-label="Previous group of four banks" title="Previous four banks">◂</button>` +
    ["A", "B", "C", "D"].map((l, i) => `<button type="button" class="bkey" data-bank="${i}" aria-label="Bank ${l} of this group">${l}</button>`).join("") +
    `<button type="button" class="bkey nav bank-next" aria-label="Next group of four banks" title="Next four banks">▸</button>`;
  banksEl.querySelector(".bank-prev").addEventListener("click", () => session.setBank(st().bank - 4, true));
  banksEl.querySelector(".bank-next").addEventListener("click", () => session.setBank(st().bank + 4, true));
  banksEl.addEventListener("click", (e) => {
    const b = e.target.closest("[data-bank]");
    if (b) session.setBank(Math.floor(st().bank / 4) * 4 + Number(b.dataset.bank), true);
  });
  function renderBanks() {
    const { bank, chops, project } = st();
    const n = bankCount(project, chops), g0 = Math.floor(bank / 4) * 4;
    banksEl.querySelectorAll("[data-bank]").forEach((b) => {
      const i = Number(b.dataset.bank), real = g0 + i;
      b.classList.toggle("on", real === bank);
      b.disabled = real >= n;
      b.setAttribute("aria-pressed", String(real === bank));
      b.title = real < n ? `Bank ${real + 1}` : "no such bank";
    });
    banksEl.querySelector(".bank-prev").disabled = bank < 4;
    banksEl.querySelector(".bank-next").disabled = g0 + 4 >= n;
    const ids = chops.banks[bank] ?? [];
    const a = chops.chops[ids[0]], z = chops.chops[ids.at(-1)];
    $(".bank-label").textContent = `Bank ${bank + 1}/${n} · bars ${a ? a.bar + 1 : "—"}–${z ? z.bar + 1 : "—"}`;
  }
  d.add(store.subscribe((s, prev) => { if (s.chops !== prev.chops || s.bank !== prev.bank || s.project.overrides !== prev.project.overrides) renderBanks(); }));
  renderBanks();

  // 16 LEVELS key: on = the selected pad is the source, the 16 pads become its levels (mode: env.levelMode, set on the LCD)
  const lvKey = $(".lvl-key"), lvText = lvKey.querySelector("span");
  lvKey.addEventListener("click", () => {
    if (session.sixteen) session.setSixteenLevels({ mode: "off" });
    else session.setSixteenLevels({ pad: st().selected, mode: env.levelMode.get() });
  });
  function renderLevels() {
    const sx = session.sixteen;
    lvKey.classList.toggle("on", !!sx);
    lvKey.setAttribute("aria-pressed", String(!!sx));
    lvText.textContent = sx ? `16 Lvl · pad ${sx.pad + 1}` : "16 Levels";
    lvKey.setAttribute("aria-label", sx ? `16 levels on: source pad ${sx.pad + 1}, ${sx.mode}. Press to turn off` : "16 levels: turn on with the selected pad as the source");
  }
  d.add(store.subscribe((s, prev) => { if (s.settings.sixteen !== prev.settings.sixteen) renderLevels(); }));
  renderLevels();

  // KEEP key: the selected pad becomes a kept pad now (the server renders its audio into the mega bank); the same as what a song change does to used pads
  const keepKey = $(".keep-key");
  const keepState = () => { const { bank, selected, project } = st(); return { bank, selected, empty: !session.engine.getSlice(bank, selected), kept: !!project.overrides[`${bank}:${selected}`]?.kept }; };
  function renderKeep() {
    const { selected, empty, kept } = keepState();
    keepKey.disabled = empty || kept;
    keepKey.classList.toggle("on", kept);
    keepKey.setAttribute("aria-label", empty ? `Keep: pad ${selected + 1} is empty` : kept ? `Keep: pad ${selected + 1} is already kept` : `Keep pad ${selected + 1}: save its sound into the mega bank so it stays when the song changes`);
  }
  keepKey.addEventListener("click", async () => {
    const { bank, selected } = keepState();
    keepKey.disabled = true;
    const r = await session.keepPad(bank, selected);
    renderKeep();
    const nm = `Pad ${selected + 1} (bank ${String.fromCharCode(65 + (bank % 4))})`;
    env.toast(r.ok ? (r.kept.length ? `${nm} kept: it stays when the song changes` : `${nm} was already kept`) : `${nm} not kept: ${r.error ?? "unknown error"}`, r.ok ? 2200 : 5000);
  });
  d.add(store.subscribe((s, prev) => { if (s.selected !== prev.selected || s.bank !== prev.bank || s.project.overrides !== prev.project.overrides || s.chops !== prev.chops) renderKeep(); }));
  renderKeep();

  // keyboard / MIDI input -> the session (same handlers as the classic screen)
  const handlers = {
    "pad-on": (e) => { const v = e.detail.source === "keyboard" ? velocity() : e.detail.velocity; session.hit(e.detail.pad, v); session.startRepeat(e.detail.pad, v); },
    "pad-off": (e) => { session.release(e.detail.pad); session.stopRepeat(e.detail.pad); },
    bank: (e) => session.setBank(st().bank + e.detail.delta, true),
    panic: () => session.panic(),
  };
  for (const [t, h] of Object.entries(handlers)) { env.input.addEventListener(t, h); d.add(() => env.input.removeEventListener(t, h)); }
  return { destroy() { d.run(); root.innerHTML = ""; }, grid };
}
