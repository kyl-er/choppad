// Phone pad mode (?mode=pad): a full-screen 4x4 pad grid with bank keys, for a phone browser on the LAN or USB.
// Same session as the station: touch plays the pad, multi-touch plays several. Nothing depends on hover.
import { createPadGrid } from "./pads.js";
import { bankCount } from "/actions.js";
import { createDisposer } from "./util.js";

export function mount(root, session, env) {
  const d = createDisposer();
  const { store, meta } = session;
  const st = () => store.get();
  root.innerHTML = `<div class="phone" data-view="phone">
    <header class="ph-hd"><b class="ph-title"></b><span class="ph-bank" role="status"></span>
      <button type="button" class="hbtn" data-act="theme" aria-pressed="false">Dark</button></header>
    <div class="pads ph-pads" role="group" aria-label="Pads"></div>
    <div class="ph-keys">
      <button type="button" class="bkey nav ph-prev" aria-label="Previous bank">◂</button>
      <span class="ph-banks"></span>
      <button type="button" class="bkey nav ph-next" aria-label="Next bank">▸</button>
      <button type="button" class="tkey ph-stop" aria-label="Stop everything"><i>■</i><span>Stop</span></button>
    </div></div>`;
  const $ = (s) => root.querySelector(s);
  $(".ph-title").textContent = meta.title;
  const grid = createPadGrid($(".ph-pads"), session, { velocity: () => 1 });
  d.add(() => grid.destroy());
  const tb = $('[data-act="theme"]');
  const paint = () => { const dark = env.theme.get() === "dark"; tb.textContent = dark ? "Light" : "Dark"; tb.setAttribute("aria-pressed", String(dark)); };
  tb.addEventListener("click", () => env.theme.set(env.theme.get() === "dark" ? "light" : "dark"));
  d.add(env.theme.on(paint)); paint();
  $(".ph-prev").addEventListener("click", () => session.setBank(st().bank - 1, true));
  $(".ph-next").addEventListener("click", () => session.setBank(st().bank + 1, true));
  $(".ph-stop").addEventListener("click", () => session.panic());
  const banks = $(".ph-banks");
  banks.innerHTML = [0, 1, 2, 3].map((i) => `<button type="button" class="bkey" data-bank="${i}">${"ABCD"[i]}</button>`).join("");
  banks.addEventListener("click", (e) => { const b = e.target.closest("[data-bank]"); if (b) session.setBank(Math.floor(st().bank / 4) * 4 + Number(b.dataset.bank), true); });
  function render() {
    const { bank, chops, project } = st(), g0 = Math.floor(bank / 4) * 4, n = bankCount(project, chops);
    $(".ph-title").textContent = meta.title;
    $(".ph-bank").textContent = `Bank ${bank + 1}/${n}`;
    banks.querySelectorAll("[data-bank]").forEach((b) => { const real = g0 + Number(b.dataset.bank); b.classList.toggle("on", real === bank); b.disabled = real >= n; });
    $(".ph-prev").disabled = bank <= 0; $(".ph-next").disabled = bank >= n - 1;
  }
  d.add(store.subscribe((s, prev) => { if (s.bank !== prev.bank || s.chops !== prev.chops || s.project.overrides !== prev.project.overrides) render(); }));
  render();
  session.setBank(0, true);
  return { destroy() { d.run(); root.innerHTML = ""; } };
}
