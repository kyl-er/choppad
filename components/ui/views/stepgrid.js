// Step grid: 16 pad rows x 1/16 steps for the current pattern. Click toggles a hit (shift = soft hit), a row name plays the pad.
// Keyboard: focus the grid, arrows move the cursor cell, Enter / Space toggles it.
import { stepOf } from "/actions.js";
import { createDisposer } from "./util.js";

export function mount(root, session, env) {
  const d = createDisposer();
  const { store, engine, seq } = session;
  const st = () => store.get();
  root.innerHTML = `<span class="fignum fig-steps">FIG. 6 — STEP GRID · 16 × <b class="sg-n-steps"></b> · <b class="sg-bars"></b> OF 1/16</span>
    <div class="stepgrid" role="grid" tabindex="0" aria-label="Step grid. Arrow keys move, Enter toggles a hit."></div>
    <span class="seq-info"></span>`;
  const grid = root.querySelector(".stepgrid"), info = root.querySelector(".seq-info");
  let nowCell = -1, cursor = { pad: 0, step: 0 }, built = 0;
  const stepCount = () => st().project.pattern.bars * st().chops.beats_per_bar * 4;

  function build() {
    const { chops } = st(), n = stepCount();
    grid.style.setProperty("--steps", n);
    let html = "";
    for (let p = 15; p >= 0; p--) {
      html += `<button type="button" class="row-name" data-pad="${p}" tabindex="-1" aria-label="Play pad ${p + 1}">${p + 1}</button>`;
      for (let i = 0; i < n; i++) html += `<i role="gridcell" data-pad="${p}" data-step="${i}" class="${i % 4 === 0 ? "beat" : ""}${i % (4 * chops.beats_per_bar) === 0 ? " bar" : ""}"></i>`;
    }
    grid.innerHTML = html;
    built = n; nowCell = -1;
    root.querySelector(".sg-n-steps").textContent = n;
    root.querySelector(".sg-bars").textContent = `${st().project.pattern.bars} BAR${st().project.pattern.bars === 1 ? "" : "S"}`;
    cursor.step = Math.min(cursor.step, n - 1);
  }
  const cell = (pad, step) => grid.querySelector(`i[data-pad="${pad}"][data-step="${step}"]`);
  function render() {
    const { chops, bank, selected, project } = st();
    if (built !== stepCount()) build();
    const at = new Map();
    for (const e of project.pattern.events) at.set(`${e.pad}:${stepOf(e)}`, e);
    for (const c of grid.querySelectorAll("i")) {
      const e = at.get(`${c.dataset.pad}:${c.dataset.step}`);
      c.classList.toggle("on", !!e);
      c.classList.toggle("soft", !!e && e.vel < 0.8);
      c.classList.toggle("other", !!e && e.bank !== bank);
      const t = e ? `pad ${Number(c.dataset.pad) + 1} · bank ${e.bank + 1}${e.vel < 0.8 ? " · soft" : ""}` : "";
      if (c.title !== t) c.title = t;
    }
    for (const r of grid.querySelectorAll(".row-name")) {
      const p = Number(r.dataset.pad);
      r.classList.toggle("empty", !engine.getSlice(bank, p));
      r.classList.toggle("selected", p === selected);
    }
    info.textContent = `${project.pattern.events.length} hits · ${chops.bpm.toFixed(1)} BPM`;
    markCursor();
  }
  function markCursor() {
    grid.querySelectorAll(".kc").forEach((c) => c.classList.remove("kc"));
    if (document.activeElement === grid) cell(cursor.pad, cursor.step)?.classList.add("kc");
  }
  function toggle(pad, step, soft = false) {
    const existed = st().project.pattern.events.some((x) => x.pad === pad && stepOf(x) === step);
    store.dispatch({ type: "pattern.toggleStep", pad, step, bank: st().bank, vel: soft ? 0.55 : 1 });
    if (!existed && !seq.state.playing) engine.trigger(pad, 1);   // audition the new hit
  }
  grid.addEventListener("click", (e) => {
    const t = e.target;
    if (t.classList.contains("row-name")) { session.hit(Number(t.dataset.pad), 1); return; }
    if (t.tagName !== "I") return;
    cursor = { pad: Number(t.dataset.pad), step: Number(t.dataset.step) };
    toggle(cursor.pad, cursor.step, e.shiftKey);
  });
  grid.addEventListener("keydown", (e) => {
    const n = stepCount();
    const move = { ArrowLeft: [0, -1], ArrowRight: [0, 1], ArrowUp: [1, 0], ArrowDown: [-1, 0] }[e.key];
    if (move) { cursor = { pad: Math.max(0, Math.min(15, cursor.pad + move[0])), step: (cursor.step + move[1] + n) % n }; markCursor(); e.preventDefault(); }
    else if (e.key === "Enter") { e.preventDefault(); e.stopPropagation(); toggle(cursor.pad, cursor.step, e.shiftKey); }
  });
  grid.addEventListener("focus", markCursor);
  grid.addEventListener("blur", markCursor);

  d.add(env.ticker.add(() => {
    if (!seq.state.playing) { if (nowCell >= 0) { grid.querySelectorAll(".now").forEach((c) => c.classList.remove("now")); nowCell = -1; } return; }
    const c = Math.floor(seq.position() * 4) % built;
    if (c !== nowCell) {
      grid.querySelectorAll(".now").forEach((x) => x.classList.remove("now"));
      grid.querySelectorAll(`[data-step="${c}"]`).forEach((x) => x.classList.add("now"));
      nowCell = c;
    }
  }));
  d.add(store.subscribe((s, prev) => {
    if (s.project.pattern !== prev.project.pattern || s.bank !== prev.bank || s.selected !== prev.selected || s.chops !== prev.chops || s.project.overrides !== prev.project.overrides) render();
  }));
  build(); render();
  return { destroy() { d.run(); root.innerHTML = ""; } };
}
