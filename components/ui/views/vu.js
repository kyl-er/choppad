// Master meters driven by the real analyser: a VU needle (RMS, slow ballistics), a PK needle (peak, fast attack,
// slow fall) and a 14-step LED ladder with peak hold. The analyser sees the mono sum of the master bus, so the two
// needles show two different readings of the same signal, not left and right.
import { clamp, createDisposer, svg } from "./util.js";

const dbOf = (x) => 20 * Math.log10(Math.max(x, 1e-5));
const scale = (db) => clamp((db + 36) / 36, 0, 1.15);        // -36 dB .. 0 dB across the dial

export function mount(root, session, env) {
  const d = createDisposer();
  const analyser = env.audio.analyser;
  root.innerHTML = `<div class="vus" data-c="10"><div class="vu" data-vu="VU" role="img" aria-label="VU meter, average level"></div><div class="vu" data-vu="PK" role="img" aria-label="Peak meter"></div></div>
    <div data-leds class="leds" role="img" aria-label="Level ladder"></div>`;
  const needles = {};
  for (const el of root.querySelectorAll("[data-vu]")) {
    const s = svg("svg", { viewBox: "0 0 120 72", class: "vu-svg", "aria-hidden": "true" }, el);
    svg("rect", { x: 1, y: 1, width: 118, height: 70, rx: 4, class: "vu-face" }, s);
    const cx = 60, cy = 66, R = 50;
    for (let i = 0; i <= 20; i++) {
      const a = ((-50 + i * 5) * Math.PI) / 180, big = i % 4 === 0, r2 = R - (big ? 8 : 4);
      svg("line", { x1: cx + Math.sin(a) * R, y1: cy - Math.cos(a) * R, x2: cx + Math.sin(a) * r2, y2: cy - Math.cos(a) * r2, class: `vu-tick${i >= 15 ? " hot" : ""}${big ? " big" : ""}` }, s);
    }
    svg("path", { d: `M ${cx + Math.sin(0.2) * 44} ${cy - Math.cos(0.2) * 44} A 44 44 0 0 1 ${cx + Math.sin(0.873) * 44} ${cy - Math.cos(0.873) * 44}`, class: "vu-arc" }, s);
    svg("text", { x: cx, y: 36, class: "vu-txt", "text-anchor": "middle" }, s).textContent = el.dataset.vu;
    needles[el.dataset.vu] = svg("line", { x1: cx, y1: cy, x2: cx, y2: cy - 52, class: "vu-needle" }, s);
    svg("circle", { cx, cy, r: 4, class: "vu-hub" }, s);
  }
  const leds = root.querySelector("[data-leds]");
  for (let i = 0; i < 14; i++) leds.append(document.createElement("i"));
  const buf = new Float32Array(analyser.fftSize);
  let vu = 0, pk = 0, hold = 0, holdT = 0, shown = "";
  d.add(env.ticker.add((now, dt) => {
    analyser.getFloatTimeDomainData(buf);
    let peak = 0, sum = 0;
    for (let i = 0; i < buf.length; i++) { const a = Math.abs(buf[i]); if (a > peak) peak = a; sum += buf[i] * buf[i]; }
    const rms = Math.sqrt(sum / buf.length);
    const tv = scale(dbOf(rms) + 3), tp = scale(dbOf(peak));
    vu += (tv - vu) * Math.min(1, dt * (tv > vu ? 7 : 3));            // VU ballistics: ~300 ms
    pk += (tp - pk) * Math.min(1, dt * (tp > pk ? 40 : 5));
    if (tp >= hold || now - holdT > 900) { hold = tp; holdT = now; }
    needles.VU.setAttribute("transform", `rotate(${(-48 + clamp(vu, 0, 1.15) * 90).toFixed(1)} 60 66)`);
    needles.PK.setAttribute("transform", `rotate(${(-48 + clamp(pk, 0, 1.15) * 90).toFixed(1)} 60 66)`);
    const k = Math.round(clamp(pk, 0, 1) * 14), h = Math.min(13, Math.round(clamp(hold, 0, 1) * 14) - 1);
    const key = `${k}:${h}`;
    if (key !== shown) { shown = key; [...leds.children].forEach((c, i) => { c.className = i < k || i === h ? (i > 11 ? "r" : i > 9 ? "y" : "g") : ""; }); }
  }));
  return { destroy() { d.run(); root.innerHTML = ""; } };
}
