// The five knobs of the deck: TONE, REVERB, FILTER (master fx), VOLUME (master gain), SWING (sequencer).
// Drag up/down, mouse wheel, arrow keys (Shift = coarse), Home/End, double-click resets. Each is a slider for assistive tech.
import { DEFAULT_FX } from "/fx.js";
import { clamp, createDisposer } from "./util.js";

export function mount(root, session, env) {
  const d = createDisposer();
  const { store } = session;
  const st = () => store.get();
  const KNOBS = {
    tone: { label: "TONE", min: -1, max: 1, def: DEFAULT_FX.tone, get: () => st().project.fx.tone, set: (v) => store.dispatch({ type: "fx.set", params: { tone: v } }), out: (v) => (v > 0 ? "+" : "") + v.toFixed(2) },
    reverb: { label: "REVERB", min: 0, max: 1, def: DEFAULT_FX.reverb, get: () => st().project.fx.reverb, set: (v) => store.dispatch({ type: "fx.set", params: { reverb: v } }), out: (v) => v.toFixed(2) },
    filter: { label: "FILTER", min: -1, max: 1, def: DEFAULT_FX.filter, get: () => st().project.fx.filter, set: (v) => store.dispatch({ type: "fx.set", params: { filter: v } }), out: (v) => (Math.abs(v) < 0.02 ? "off" : v < 0 ? `LP ${Math.round(-v * 100)}` : `HP ${Math.round(v * 100)}`) },
    vol: { label: "VOLUME", min: 0, max: 1.5, def: 0.9, get: () => env.volume.get(), set: (v) => env.volume.set(v), out: (v) => String(Math.round((v / 1.5) * 100)) },
    swing: { label: "SWING", min: 0, max: 0.5, def: 0, get: () => st().settings.swing, set: (v) => store.dispatch({ type: "settings.set", params: { swing: v } }), out: (v) => `${Math.round(v * 100)}%` },
  };
  root.setAttribute("role", "group");
  root.setAttribute("aria-label", "Master knobs");
  const els = {};
  for (const [k, K] of Object.entries(KNOBS)) {
    const el = document.createElement("div");
    el.dataset.knob = k; el.tabIndex = 0;
    el.setAttribute("role", "slider"); el.setAttribute("aria-label", K.label.charAt(0) + K.label.slice(1).toLowerCase());
    el.setAttribute("aria-valuemin", K.min); el.setAttribute("aria-valuemax", K.max);
    el.title = `${K.label.charAt(0) + K.label.slice(1).toLowerCase()}: drag, scroll or use arrow keys; double-click resets`;
    el.innerHTML = `<i class="k-body"><i class="k-ind"></i></i><b class="k-lab">${K.label}</b><output class="k-out"></output>`;
    const range = K.max - K.min;
    let y0 = 0, v0 = 0;
    const setv = (v) => K.set(clamp(+v.toFixed(4), K.min, K.max));
    el.addEventListener("pointerdown", (e) => { el.setPointerCapture(e.pointerId); y0 = e.clientY; v0 = K.get(); el.classList.add("drag"); });
    el.addEventListener("pointermove", (e) => { if (el.hasPointerCapture(e.pointerId)) setv(v0 + ((y0 - e.clientY) / 160) * range); });
    el.addEventListener("pointerup", (e) => { try { el.releasePointerCapture(e.pointerId); } catch { /* gone */ } el.classList.remove("drag"); });
    el.addEventListener("dblclick", () => setv(K.def));
    el.addEventListener("wheel", (e) => { e.preventDefault(); setv(K.get() - Math.sign(e.deltaY) * (range / 50)); }, { passive: false });
    el.addEventListener("keydown", (e) => {
      const step = (range / 50) * (e.shiftKey ? 5 : 1);
      const v = { ArrowUp: K.get() + step, ArrowRight: K.get() + step, ArrowDown: K.get() - step, ArrowLeft: K.get() - step, Home: K.min, End: K.max }[e.key];
      if (v !== undefined) { setv(v); e.preventDefault(); e.stopPropagation(); }
    });
    els[k] = el; root.append(el);
  }
  function render() {
    for (const [k, K] of Object.entries(KNOBS)) {
      const v = K.get(), t = (v - K.min) / (K.max - K.min), el = els[k];
      el.style.setProperty("--ang", `${(-135 + t * 270).toFixed(1)}deg`);
      el.setAttribute("aria-valuenow", v.toFixed(2));
      el.setAttribute("aria-valuetext", K.out(v));
      const o = el.querySelector(".k-out"), txt = K.out(v);
      if (o.textContent !== txt) o.textContent = txt;
    }
  }
  d.add(store.subscribe((s, prev) => { if (s.project.fx !== prev.project.fx || s.settings !== prev.settings) render(); }));
  d.add(env.volume.on(render));
  render();
  return { destroy() { d.run(); root.innerHTML = ""; } };
}
