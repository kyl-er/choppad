// FIG. 1: the vox view, a particle spectrogram driven by the REAL master analyser (audio.analyser).
// Frequency runs across (log scale, 40 Hz - 16 kHz), time recedes in depth (about 4.5 s), energy is height and brightness.
// Pad hits (session "hit" / "flash") throw a ripple coloured by the pad's section (chorus-like: orange, otherwise blue).
//
// Off the audio path: the analyser is only READ, in requestAnimationFrame (one frequency snapshot per 1/24 s), nothing
// is connected or changed in the audio graph, and nothing here runs on the pad trigger path (hits are only listened to).
// Quality off / lite / full (remembered in localStorage), automatic drop to lite under 50 fps for 3 s, paused when the
// tab is hidden or under prefers-reduced-motion, 2D-canvas fallback when WebGL or three.js is unavailable.
import { clamp, createDisposer, cssVar, reducedMotion, remember, sectionAt, sectionIsLive, store as stored } from "./util.js";

const QUALITY = { off: { nx: 0, nz: 0 }, lite: { nx: 80, nz: 48, pr: 1, size: 1.7 }, full: { nx: 192, nz: 110, pr: Math.min(devicePixelRatio || 1, 1.5), size: 1 } };
const ROW_RATE = 24, F_LO = 40, F_HI = 16000, RIPPLES = 8;
const QS = new URLSearchParams(location.search);

export function mount(root, session, env) {
  const d = createDisposer();
  const { store, engine } = session;
  const analyser = env.audio.analyser;
  root.innerHTML = `<span class="fignum" style="z-index:4">FIG. 1 — VOX SPECTROGRAM · LIVE</span>
    <div class="vox-host" data-vox><div class="vox-holder"></div></div>
    <div class="qbar" role="group" aria-label="Vox view quality">
      <span data-quality></span><output class="fps" data-fps role="status"></output></div>
    <span class="ax y">ENERGY ↑</span><span class="ax z">TIME −4 s → NOW</span><i class="ax tick"></i>
    <span class="ax x"><span>40 Hz</span><span>FREQUENCY</span><span>16 kHz</span></span>`;
  const host = root.querySelector(".vox-host"), holder = root.querySelector(".vox-holder");
  const qEl = root.querySelector("[data-quality]"), fpsEl = root.querySelector("[data-fps]");
  qEl.innerHTML = `<button type="button" data-q="off">off</button><button type="button" data-q="lite">lite</button><button type="button" data-q="full">full</button><button type="button" data-act="pause" aria-label="Pause the vox view" aria-pressed="false">❚❚</button>`;

  let quality = QS.get("vox") && QUALITY[QS.get("vox")] ? QS.get("vox") : (stored("choppad.vox") in QUALITY ? stored("choppad.vox") : "full");
  const noAuto = QS.has("noauto");
  const force2d = QS.get("vox") === "2d" || QS.get("fallback") === "1";
  let paused = reducedMotion();
  let THREE = null, renderer = null, canvas2d = null, loadingThree = null;
  let scene, cam, points, mat, line, gridLines, tex;
  const hist = { nx: 0, nz: 0, data: null, head: 0, acc: 0, frac: 0 };
  let lostT = 0;
  let W = 10, H = 10, dirty = true, frames = 0, kick = 0, pulse = 0, lowPrev = 0;
  const freq = new Uint8Array(analyser.frequencyBinCount);
  let cols = [];     // per column: [lo bin, hi bin, centre bin (float)]
  const ripples = Array.from({ length: RIPPLES }, () => ({ x: 0.5, z: 0.1, age: 9, amp: 0, c: [1, 1, 1] }));
  let ripI = 0;
  const theme = {};
  const readTheme = () => {
    Object.assign(theme, {
      ink: cssVar("--vox-ink"), hot: cssVar("--acc-o"), blue: cssVar("--acc-b"), line: cssVar("--vox-line"), grid: cssVar("--vox-grid"),
      alpha: Number(cssVar("--vox-alpha")) || 0.4, add: cssVar("--vox-add") === "1", gridAlpha: Number(cssVar("--vox-grid-alpha")) || 0.2,
    });
  };

  // ------------------------------------------------------------ analyser -> one row
  function planColumns(nx) {
    const hz = analyser.context.sampleRate / analyser.fftSize, nb = analyser.frequencyBinCount;
    cols = Array.from({ length: nx }, (_, i) => {
      const lo = F_LO * Math.pow(F_HI / F_LO, i / nx), hi = F_LO * Math.pow(F_HI / F_LO, (i + 1) / nx), c = Math.sqrt(lo * hi) / hz;
      return [clamp(Math.floor(lo / hz), 0, nb - 1), clamp(Math.ceil(hi / hz), 0, nb - 1), clamp(c, 0, nb - 1)];
    });
  }
  function pushRow() {
    analyser.getByteFrequencyData(freq);
    hist.head = (hist.head + 1) % hist.nz;
    const base = hist.head * hist.nx, nx = hist.nx;
    let low = 0;
    for (let i = 0; i < nx; i++) {
      const [lo, hi, c] = cols[i];
      let b;
      if (hi - lo <= 1) { const a = Math.floor(c), f = c - a; b = freq[a] * (1 - f) + freq[Math.min(a + 1, freq.length - 1)] * f; }
      else { b = 0; for (let k = lo; k <= hi; k++) if (freq[k] > b) b = freq[k]; }
      const x = i / nx;
      // dB-ish floor, then a gentle treble lift (music spectra fall ~4 dB/oct) so the right side is not flat
      const v = Math.pow(clamp((b - 55) / 150, 0, 1.15), 1.1) * (1 + 0.9 * x);
      hist.data[base + i] = v;
      if (i < nx * 0.18) low += v;
    }
    low /= nx * 0.18;
    kick = Math.max(kick * 0.8, clamp((low - lowPrev) * 3.2, 0, 1));
    lowPrev = low;
    if (tex) tex.needsUpdate = true;
  }

  // ------------------------------------------------------------ build
  async function ensureThree() {
    if (THREE || force2d) return;
    loadingThree ??= import("/vendor/three.module.min.js").then((m) => { THREE = m; }, () => { THREE = null; });
    await loadingThree;
  }
  function makeRenderer() {
    if (renderer || canvas2d || !THREE) return;
    try {
      renderer = new THREE.WebGLRenderer({ antialias: false, alpha: true, powerPreference: "default" });
      renderer.setClearColor(0x000000, 0);
      renderer.domElement.setAttribute("aria-hidden", "true");
      holder.append(renderer.domElement);
      // a lost context (GPU reset, driver hiccup) usually comes back: wait for it, fall back to 2D only if it does not
      renderer.domElement.addEventListener("webglcontextlost", (e) => { e.preventDefault(); clearTimeout(lostT); lostT = setTimeout(useFallback, 2500); });
      renderer.domElement.addEventListener("webglcontextrestored", () => { clearTimeout(lostT); dirty = true; });
    } catch { renderer = null; }
  }
  function useFallback() {
    try { renderer?.dispose(); } catch { /* lost */ }
    renderer?.domElement?.remove();
    renderer = null; scene = null; mat = null; points = null;
    if (!canvas2d) { canvas2d = document.createElement("canvas"); canvas2d.setAttribute("aria-hidden", "true"); holder.append(canvas2d); }
    root.dataset.engine = "2d";
    resize(); build(quality);
  }
  async function build(q) {
    quality = q;
    qEl.querySelectorAll("[data-q]").forEach((b) => { b.classList.toggle("on", b.dataset.q === q); b.setAttribute("aria-pressed", String(b.dataset.q === q)); });
    root.dataset.quality = q;
    if (!QUALITY[q].nx) { holder.style.visibility = "hidden"; hist.nx = 0; fpsEl.textContent = "off"; return; }
    holder.style.visibility = "";
    if (!renderer && !canvas2d) {
      await ensureThree();
      if (quality !== q) return;                                       // switched again while loading
      makeRenderer();
      if (!renderer) { canvas2d = document.createElement("canvas"); canvas2d.setAttribute("aria-hidden", "true"); holder.append(canvas2d); }
      root.dataset.engine = renderer ? "three" : "2d";
      resize();
    }
    const cfg = !renderer && q === "full" ? { nx: 96, nz: 64, pr: 1, size: 1 } : QUALITY[q];
    readTheme();
    hist.nx = cfg.nx; hist.nz = cfg.nz; hist.data = new Float32Array(cfg.nx * cfg.nz); hist.head = 0; hist.acc = 0;
    planColumns(cfg.nx);
    for (let i = 0; i < cfg.nz; i++) pushRow();                         // fill with what the analyser shows now
    if (renderer) buildScene(cfg, q);
    resize(); dirty = true;
  }
  const col3 = (c) => new THREE.Color(c);
  function buildScene(cfg) {
    scene = new THREE.Scene();
    cam = new THREE.PerspectiveCamera(40, 1, 0.1, 50);
    cam.position.set(0, 1.5, 3.5); cam.lookAt(0, 0.55, -1.8);
    renderer.setPixelRatio(cfg.pr);
    const n = cfg.nx * cfg.nz, idx = new Float32Array(n * 2);
    for (let z = 0; z < cfg.nz; z++) for (let x = 0; x < cfg.nx; x++) { const i = z * cfg.nx + x; idx[i * 2] = x / (cfg.nx - 1); idx[i * 2 + 1] = z / (cfg.nz - 1); }
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(new Float32Array(n * 3), 3));
    g.setAttribute("aIdx", new THREE.BufferAttribute(idx, 2));
    g.boundingSphere = new THREE.Sphere(new THREE.Vector3(), 100);
    tex?.dispose();
    tex = new THREE.DataTexture(hist.data, cfg.nx, cfg.nz, THREE.RedFormat, THREE.FloatType);
    tex.minFilter = tex.magFilter = THREE.NearestFilter; tex.needsUpdate = true;
    mat?.dispose();
    mat = new THREE.ShaderMaterial({
      transparent: true, depthTest: false, depthWrite: false, blending: theme.add ? THREE.AdditiveBlending : THREE.NormalBlending,
      uniforms: {
        uTex: { value: tex }, uHead: { value: 0 }, uNX: { value: cfg.nx }, uNZ: { value: cfg.nz }, uFrac: { value: 0 }, uSize: { value: 3.0 * cfg.size }, uPR: { value: cfg.pr },
        uKick: { value: 0 }, uPulse: { value: 0 }, uSpanX: { value: 4.4 }, uSpanZ: { value: 3.6 }, uHeight: { value: 1.75 },
        uRip: { value: ripples.map(() => new THREE.Vector4(0.5, 0.1, 9, 0)) }, uRipCol: { value: ripples.map(() => new THREE.Vector3(1, 1, 1)) },
        uInk: { value: col3(theme.ink) }, uHot: { value: col3(theme.hot) }, uSec: { value: col3(theme.blue) }, uAlpha: { value: theme.alpha },
      },
      vertexShader: `attribute vec2 aIdx; uniform sampler2D uTex; uniform float uHead,uNX,uNZ,uFrac,uSize,uPR,uKick,uPulse,uSpanX,uSpanZ,uHeight; uniform vec4 uRip[${RIPPLES}]; uniform vec3 uRipCol[${RIPPLES}];
        varying float vH, vF, vR; varying vec3 vRC;
        void main(){
          float iz = floor(aIdx.y*(uNZ-1.0)+0.5);
          float row = mod(uHead - iz + uNZ*2.0, uNZ);
          float h = pow(texture2D(uTex, vec2((aIdx.x*(uNX-1.0)+0.5)/uNX, (row+0.5)/uNZ)).r, 0.72);
          float zf = (iz + uFrac)/(uNZ-1.0);
          float rip = 0.0, rw = 0.0; vec3 rc = vec3(0.0);
          for(int i=0;i<${RIPPLES};i++){ vec4 r=uRip[i]; float dd = length(vec2((aIdx.x-r.x)*1.7, zf*1.6 - r.y)); float a=r.z;
            float w = r.w*exp(-a*2.4)*exp(-dd*2.6)*smoothstep(0.0,0.05,a); rip += w*sin(dd*15.0 - a*10.0); rw += w; rc += w*uRipCol[i]; }
          float y = (h + rip*0.30 + uKick*0.12*exp(-pow(aIdx.x/0.22,2.0))) * uHeight * (1.0 - 0.35*zf);
          vec3 p = vec3((aIdx.x-0.5)*uSpanX, y, -zf*uSpanZ);
          vec4 mv = modelViewMatrix*vec4(p,1.0);
          gl_Position = projectionMatrix*mv;
          gl_PointSize = uSize*uPR*(1.0 + h*1.2 + abs(rip)*1.2)*(4.2/(-mv.z));
          vH = clamp(h + rip*0.4 + uPulse*0.05, 0.0, 1.3); vR = clamp(rw*1.6, 0.0, 1.0); vRC = rw > 0.001 ? rc/rw : vec3(0.0);
          vF = (1.0 - smoothstep(0.55,1.0,zf)) * smoothstep(0.0,0.03,zf+0.02);
        }`,
      fragmentShader: `precision mediump float; varying float vH,vF,vR; varying vec3 vRC; uniform vec3 uInk,uHot,uSec; uniform float uAlpha;
        void main(){ vec2 c=gl_PointCoord-0.5; float dd=length(c); if(dd>0.5) discard; float a=smoothstep(0.5,0.12,dd);
          vec3 col = mix(uInk, uSec, smoothstep(0.30,0.46,vH));
          col = mix(col, uHot, clamp(smoothstep(0.72,0.88,vH), 0.0, 1.0));
          col = mix(col, vRC, clamp(vR*1.1, 0.0, 1.0));
          gl_FragColor = vec4(col, clamp(a*(uAlpha + vH*0.9 + vR*0.5)*vF, 0.0, 1.0)); }`,
    });
    points?.geometry.dispose();
    points = new THREE.Points(g, mat); points.frustumCulled = false; scene.add(points);
    const lg = new THREE.BufferGeometry(); lg.setAttribute("position", new THREE.BufferAttribute(new Float32Array(cfg.nx * 3), 3));
    line?.geometry.dispose(); line?.material.dispose();
    line = new THREE.Line(lg, new THREE.LineBasicMaterial({ color: theme.line, transparent: true, opacity: 0.9, depthTest: false }));
    line.frustumCulled = false; scene.add(line);
    const gp = [];
    for (let i = 0; i <= 12; i++) { const x = -2.2 + (i / 12) * 4.4; gp.push(x, 0, 0, x, 0, -3.6); }
    for (let j = 0; j <= 8; j++) { const z = -(j / 8) * 3.6; gp.push(-2.2, 0, z, 2.2, 0, z); }
    const gg = new THREE.BufferGeometry(); gg.setAttribute("position", new THREE.Float32BufferAttribute(gp, 3));
    gridLines?.geometry.dispose(); gridLines?.material.dispose();
    gridLines = new THREE.LineSegments(gg, new THREE.LineBasicMaterial({ color: theme.grid, transparent: true, opacity: theme.gridAlpha, depthTest: false }));
    gridLines.frustumCulled = false; scene.add(gridLines);
  }
  function applyTheme() {
    readTheme();
    if (mat) {
      mat.uniforms.uInk.value.set(theme.ink); mat.uniforms.uHot.value.set(theme.hot); mat.uniforms.uSec.value.set(theme.blue); mat.uniforms.uAlpha.value = theme.alpha;
      mat.blending = theme.add ? THREE.AdditiveBlending : THREE.NormalBlending; mat.needsUpdate = true;
      line.material.color.set(theme.line); gridLines.material.color.set(theme.grid); gridLines.material.opacity = theme.gridAlpha;
    }
    dirty = true;
  }
  d.add(env.theme.on(applyTheme));

  function resize() {
    const r = host.getBoundingClientRect();
    W = Math.max(10, Math.round(r.width)); H = Math.max(10, Math.round(r.height));
    if (renderer) { renderer.setSize(W, H, false); renderer.domElement.style.width = "100%"; renderer.domElement.style.height = "100%"; if (cam) { cam.aspect = W / H; cam.updateProjectionMatrix(); } }
    else if (canvas2d) { const dp = Math.min(devicePixelRatio || 1, 1.5); canvas2d.width = W * dp; canvas2d.height = H * dp; canvas2d.style.width = "100%"; canvas2d.style.height = "100%"; canvas2d._d = dp; }
    dirty = true;
  }
  const ro = new ResizeObserver(resize); ro.observe(host); d.add(() => ro.disconnect());

  // ------------------------------------------------------------ hits -> ripples
  function padColor(pad) {
    const { bank, sections } = store.get();
    const c = engine.getSlice(bank, pad);
    if (!c) return theme.blue;
    if (c.custom) return theme.blue;
    const sec = sectionAt(sections, c.start);
    return sec ? (sectionIsLive(sec.label) ? theme.hot : theme.blue) : ((c.bar % 4) >= 2 ? theme.hot : theme.blue);
  }
  function spawn(pad, vel = 1) {
    if (!hist.nx || paused) return;
    const r = ripples[ripI++ % RIPPLES];
    r.x = (pad % 4 + 0.5) / 4 * 0.8 + 0.1; r.z = 0.04 + 0.04 * Math.floor(pad / 4); r.age = 0; r.amp = 0.35 + clamp(vel, 0, 1) * 0.4;
    r.c = padColor(pad); pulse = 1;
  }
  const onHit = (e) => spawn(e.detail.pad, e.detail.velocity), onFlash = (e) => spawn(e.detail.pad, 0.9);
  session.on("hit", onHit); session.on("flash", onFlash);
  d.add(() => { session.off("hit", onHit); session.off("flash", onFlash); });

  // ------------------------------------------------------------ draw
  const tmpC = new Map();
  const rgb = (c) => { if (!tmpC.has(c)) { const t = THREE ? new THREE.Color(c) : null; tmpC.set(c, t ? [t.r, t.g, t.b] : [1, 1, 1]); } return tmpC.get(c); };
  function render() {
    if (!hist.nx) return;
    if (renderer && mat) {
      const u = mat.uniforms;
      u.uHead.value = hist.head; u.uFrac.value = hist.frac; u.uKick.value = kick; u.uPulse.value = pulse;
      ripples.forEach((r, i) => { u.uRip.value[i].set(r.x, r.z, r.age, r.age < 3 ? r.amp : 0); u.uRipCol.value[i].set(...rgb(r.c)); });
      const pa = line.geometry.attributes.position, base = hist.head * hist.nx;
      for (let i = 0; i < hist.nx; i++) pa.setXYZ(i, (i / (hist.nx - 1) - 0.5) * 4.4, Math.pow(hist.data[base + i], 0.72) * 1.75, 0);
      pa.needsUpdate = true;
      cam.position.set(0, 1.5 + kick * 0.025, 3.5); cam.lookAt(0, 0.55, -1.8);
      renderer.render(scene, cam);
    } else if (canvas2d) draw2d();
    frames++;
    root.dataset.frames = String(frames);
  }
  function draw2d() {
    const g = canvas2d.getContext("2d"), dp = canvas2d._d;
    g.setTransform(dp, 0, 0, dp, 0, 0); g.clearRect(0, 0, W, H);
    const f = W * 0.62;
    for (let iz = hist.nz - 1; iz >= 0; iz--) {
      const row = (hist.head - iz + hist.nz * 2) % hist.nz, zf = (iz + hist.frac) / (hist.nz - 1), Z = -zf * 3.6, k = f / (3.5 - Z);
      const fade = 1 - clamp((zf - 0.55) / 0.45, 0, 1);
      for (let ix = 0; ix < hist.nx; ix++) {
        const h = Math.pow(hist.data[row * hist.nx + ix], 0.72), X = (ix / (hist.nx - 1) - 0.5) * 4.4, Y = h * 1.75 * (1 - 0.35 * zf);
        const px = W / 2 + X * k, py = H * 0.86 - Y * k * 0.42 - zf * H * 0.26;
        g.fillStyle = h > 0.72 ? theme.hot : h > 0.3 ? theme.blue : theme.ink;
        g.globalAlpha = clamp((theme.alpha + h * 0.9) * fade, 0, 1);
        const s = Math.max(1.2, 3 * (1 + h) * (4.2 / (3.5 - Z)) * 0.55);
        g.fillRect(px - s / 2, py - s / 2, s, s);
      }
    }
    g.globalAlpha = 1;
  }

  // ------------------------------------------------------------ loop, quality, fps
  const fps = { n: 0, t: performance.now(), v: 60, low: 0 };
  function setQuality(q) { remember("choppad.vox", q); fps.low = 0; build(q); }
  qEl.addEventListener("click", (e) => {
    const b = e.target.closest("button"); if (!b) return;
    if (b.dataset.q) setQuality(b.dataset.q);
    if (b.dataset.act === "pause") { paused = !paused; b.setAttribute("aria-pressed", String(paused)); dirty = true; }
  });
  qEl.querySelector('[data-act="pause"]').setAttribute("aria-pressed", String(paused));
  d.add(env.ticker.add((now, dt) => {
    if (!hist.nx || document.hidden) return;
    if (paused) { if (dirty) { render(); dirty = false; } return; }
    hist.acc += dt;
    while (hist.acc >= 1 / ROW_RATE) { hist.acc -= 1 / ROW_RATE; pushRow(); }
    hist.frac = hist.acc * ROW_RATE;
    for (const r of ripples) r.age += dt;
    pulse *= Math.exp(-dt * 6); kick *= Math.exp(-dt * 3);
    render(); dirty = false;
    fps.n++;
    if (now - fps.t >= 1000) {
      fps.v = (fps.n * 1000) / (now - fps.t); fps.n = 0; fps.t = now;
      root.dataset.fps = String(Math.round(fps.v));
      fpsEl.textContent = `${Math.round(fps.v)} fps · ${renderer ? "three.js" : "2D"}`;
      if (quality === "full" && !noAuto) {
        fps.low = fps.v < 50 ? fps.low + 1 : 0;
        if (fps.low >= 3) { fps.low = 0; remember("choppad.vox", "lite"); build("lite"); env.toast("vox: dropped to lite (under 50 fps for 3 s)"); }
      }
    }
  }));
  const onVis = () => { if (!document.hidden) { fps.t = performance.now(); fps.n = 0; fps.low = 0; } };
  d.on(document, "visibilitychange", onVis);

  // lets e2e and debugging read what the view is doing
  env.voxState = { get quality() { return quality; }, get frames() { return frames; }, get paused() { return paused; }, get engine() { return renderer ? "three" : canvas2d ? "2d" : "none"; }, get fps() { return fps.v; } };
  build(quality);
  return {
    destroy() {
      d.run();
      clearTimeout(lostT);
      try { tex?.dispose(); mat?.dispose(); points?.geometry.dispose(); line?.geometry.dispose(); gridLines?.geometry.dispose(); renderer?.dispose(); renderer?.forceContextLoss?.(); } catch { /* already gone */ }
      root.innerHTML = "";
    },
  };
}
