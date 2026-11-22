// 4x4 pads (pad 1 bottom-left, MPC style), bank keys A-D with group arrows, the bank label and the note-variation slider.
// Also the pad / bank / panic events from the keyboard and MIDI input. createPadGrid() is shared with the phone pad mode.
import { cellToPad } from "/layout.js";
import { PADS } from "/actions.js";
import { createDisposer, fmt } from "./util.js";

/** Builds the 16 pad buttons inside `grid` and keeps them drawn from the store. velocity() gives the hit velocity. */
export function createPadGrid(grid, session, { velocity = () => 1, onDown } = {}) {
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
      el.innerHTML = `<span class="pad-no">${p + 1}</span><span class="pad-label"></span><span class="pad-time"></span>`;
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
  function render() {
    const { bank, selected } = store.get();
    for (let p = 0; p < PADS; p++) {
      const c = engine.getSlice(bank, p), el = els[p];
      el.classList.toggle("empty", !c);
      el.classList.toggle("custom", !!c?.custom);
      el.classList.toggle("stretched", !!c?.stretch);
      el.classList.toggle("foreign", !!c?.song);
      el.classList.toggle("selected", p === selected);
      const label = c ? (c.custom ? "★ " + fmt(c.start) : session.chopLabel(c)) : "";
      const time = c ? (c.custom ? `${(c.end - c.start).toFixed(2)}s` : fmt(c.start)) + (c.stretch ? ` ×${c.stretch.ratio.toFixed(2)}` : "") + (c.song ? " ♪" : "") : "";
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
  d.add(store.subscribe((s, prev) => { if (s.chops !== prev.chops || s.bank !== prev.bank || s.selected !== prev.selected || s.project.overrides !== prev.project.overrides) render(); }));
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
      <div class="bank-label" role="status"></div>
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

  const grid = createPadGrid($(".pads"), session, { velocity });
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
    const { bank, chops } = st();
    const n = chops.banks.length, g0 = Math.floor(bank / 4) * 4;
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
  d.add(store.subscribe((s, prev) => { if (s.chops !== prev.chops || s.bank !== prev.bank) renderBanks(); }));
  renderBanks();

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
