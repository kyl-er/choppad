// FIG. 2: the vinyl platter. It turns while the song, the pattern or a pad plays; the orange ring is the song position
// (drag the disc or the ring to scrub, arrow keys step a bar). Turning stops under prefers-reduced-motion.
import { clamp, createDisposer, fmt, reducedMotion, svg } from "./util.js";

export function mount(root, session, env) {
  const d = createDisposer();
  const { store, seq, meta, video } = session;
  const st = () => store.get();
  const dur = meta.duration_s;
  root.innerHTML = `<div class="vin" tabindex="0" role="slider" aria-label="Song position: drag the record or use the arrow keys" aria-valuemin="0" aria-valuemax="${Math.round(dur)}" aria-valuenow="0"></div>
    <div class="rd"><span>33⅓ RPM</span><span class="rd-clock">0:00.0</span><span class="rd-sect"></span></div>`;
  const host = root.querySelector(".vin");
  const s = svg("svg", { viewBox: "0 0 200 200", class: "plat-svg", "aria-hidden": "true" }, host);
  const ring = svg("g", { class: "p-ring" }, s);
  svg("circle", { cx: 100, cy: 100, r: 97, class: "p-rim" }, ring);
  const bars = Math.max(1, (st().chops.chops.at(-1)?.bar ?? 0) + 1);
  const every = Math.max(1, Math.ceil(bars / 96)), nt = Math.ceil(bars / every);
  for (let i = 0; i < nt; i++) {
    const a = (i / nt) * Math.PI * 2, big = i % 4 === 0, r2 = big ? 89 : 91.5;
    svg("line", { x1: 100 + Math.sin(a) * 94, y1: 100 - Math.cos(a) * 94, x2: 100 + Math.sin(a) * r2, y2: 100 - Math.cos(a) * r2, class: `p-tick${big ? " big" : ""}` }, ring);
  }
  const arc = svg("circle", { cx: 100, cy: 100, r: 85, class: "p-arc", transform: "rotate(-90 100 100)" }, ring);
  const dot = svg("circle", { r: 3.2, class: "p-dot" }, ring);
  const spin = svg("g", { class: "p-spin" }, s);
  svg("circle", { cx: 100, cy: 100, r: 80, class: "p-disc" }, spin);
  for (let r = 34; r < 79; r += 2.1) if (!(r > 55 && r < 57.5) && !(r > 66 && r < 68.5)) svg("circle", { cx: 100, cy: 100, r, class: "p-groove" }, spin);
  svg("circle", { cx: 100, cy: 100, r: 30, class: "p-label" }, spin);
  svg("path", { d: "M100 70 A30 30 0 0 1 130 100 L100 100Z", class: "p-wedge" }, spin);
  const short = meta.title.replace(/^.*?[—:-]\s*/, "").slice(0, 14).toUpperCase();
  svg("text", { x: 100, y: 91, class: "p-lt", "text-anchor": "middle" }, spin).textContent = "CHOPPAD";
  svg("text", { x: 100, y: 112, class: "p-lt s", "text-anchor": "middle" }, spin).textContent = short;
  svg("text", { x: 100, y: 120, class: "p-lt s", "text-anchor": "middle" }, spin).textContent = `${Math.round(st().chops.bpm)} BPM · 33⅓`;
  svg("line", { x1: 100, y1: 74, x2: 100, y2: 82, class: "p-notch" }, spin);
  svg("circle", { cx: 100, cy: 100, r: 2.2, class: "p-hole" }, s);
  svg("path", { d: "M100 100 L100 20 A80 80 0 0 1 160 31Z", class: "p-sheen" }, s);
  const arm = svg("g", { class: "p-arm" }, s);
  const armLine = svg("line", { class: "p-armline", x1: 186, y1: 18 }, arm);
  const head = svg("rect", { width: 9, height: 5, rx: 1, class: "p-head" }, arm);
  svg("circle", { cx: 186, cy: 18, r: 7, class: "p-pivot" }, s);

  let rot = 0, speed = 0, playingUntil = 0, dragging = false, shown = -1;
  const bump = (pad) => {
    const c = session.engine.getSlice(st().bank, pad);
    playingUntil = performance.now() + clamp(((c ? c.end - c.start : 0.5) * 1000) + 150, 400, 4000);
  };
  const onHit = (e) => bump(e.detail.pad), onFlash = (e) => bump(e.detail.pad);
  session.on("hit", onHit); session.on("flash", onFlash);
  d.add(() => { session.off("hit", onHit); session.off("flash", onFlash); });

  const progOf = (t) => clamp(t / dur, 0, 1);
  function draw(prog) {
    const C = 2 * Math.PI * 85;
    arc.setAttribute("stroke-dasharray", `${(C * prog).toFixed(1)} ${C}`);
    const a = prog * Math.PI * 2;
    dot.setAttribute("cx", (100 + Math.sin(a) * 85).toFixed(1));
    dot.setAttribute("cy", (100 - Math.cos(a) * 85).toFixed(1));
    const rr = 76 - clamp(prog * 1.6, 0, 1) * 38, ang = (-50 * Math.PI) / 180;
    const sx = 100 + Math.cos(ang) * rr * 0.62, sy = 100 + Math.sin(ang) * rr * 0.62;
    armLine.setAttribute("x2", sx.toFixed(1)); armLine.setAttribute("y2", sy.toFixed(1));
    head.setAttribute("transform", `translate(${(sx - 4.5).toFixed(1)} ${(sy - 2.5).toFixed(1)}) rotate(${((Math.atan2(sy - 18, sx - 186) * 180) / Math.PI).toFixed(1)} 4.5 2.5)`);
  }
  const clock = root.querySelector(".rd-clock"), sect = root.querySelector(".rd-sect");
  d.add(env.ticker.add((now, dt) => {
    const t = session.position();
    const going = !reducedMotion() && (session.songPlaying || seq.state.playing || now < playingUntil);
    speed += ((going ? 200 : 0) - speed) * Math.min(1, dt * (going ? 2.5 : 1.2));
    if (speed > 0.05) { rot = (rot + speed * dt) % 360; spin.setAttribute("transform", `rotate(${rot.toFixed(2)} 100 100)`); }
    const sec = Math.floor(t * 4);
    if (sec !== shown || dragging) {
      shown = sec; draw(progOf(t));
      clock.textContent = fmt(t);
      const cur = st().sections.find((x) => x.start_s <= t && t < x.end_s);
      sect.textContent = cur ? cur.label : "";
      host.setAttribute("aria-valuenow", String(Math.round(t)));
    }
  }));

  // ---- scrub
  const timeAt = (e) => {
    const r = host.getBoundingClientRect();
    const a = Math.atan2(e.clientX - (r.left + r.width / 2), -(e.clientY - (r.top + r.height / 2)));   // 0 at 12 o'clock, clockwise
    return ((a < 0 ? a + Math.PI * 2 : a) / (Math.PI * 2)) * dur;
  };
  let wasPlaying = false, lastSeek = 0;
  const seekTo = (t, final = false) => {
    if (session.songPlaying) { if (final || performance.now() - lastSeek > 120) { lastSeek = performance.now(); session.playSong(t); } }
    else { video.currentTime = t; session.followTo(t + 1e-3); }
  };
  host.addEventListener("pointerdown", (e) => { dragging = true; wasPlaying = session.songPlaying; host.setPointerCapture(e.pointerId); host.classList.add("drag"); seekTo(timeAt(e), true); draw(progOf(timeAt(e))); });
  host.addEventListener("pointermove", (e) => { if (!dragging) return; const t = timeAt(e); draw(progOf(t)); clock.textContent = fmt(t); seekTo(t); });
  const end = (e) => { if (!dragging) return; dragging = false; host.classList.remove("drag"); if (wasPlaying) seekTo(timeAt(e), true); };
  host.addEventListener("pointerup", end); host.addEventListener("pointercancel", end);
  host.addEventListener("keydown", (e) => {
    const bar = (60 / st().chops.bpm) * st().chops.beats_per_bar;
    const dt = { ArrowRight: bar, ArrowUp: bar, ArrowLeft: -bar, ArrowDown: -bar, PageUp: bar * 4, PageDown: -bar * 4 }[e.key];
    if (dt === undefined) return;
    e.preventDefault(); e.stopPropagation();
    seekTo(clamp(session.position() + dt, 0, dur - 0.05), true);
  });
  draw(progOf(session.position()));
  return { destroy() { d.run(); root.innerHTML = ""; } };
}
