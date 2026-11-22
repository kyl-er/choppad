// The sheet: header, theme, the grid that holds every view, and the small shared environment (env) the views use for
// things that are not song state: the shared ticker, theme, wave zoom, master volume, note variation, toasts.
// It composes the views; it never touches sound, the store or the network itself.
import { createCell, createDisposer, createTicker, remember, store as stored } from "./util.js";
import * as lcd from "./lcd.js";
import * as pads from "./pads.js";
import * as transport from "./transport.js";
import * as stepgrid from "./stepgrid.js";
import * as wavestrip from "./wavestrip.js";
import * as platter from "./platter.js";
import * as monitor from "./monitor.js";
import * as vu from "./vu.js";
import * as fxdeck from "./fxdeck.js";
import * as vox from "./vox.js";
import * as librarypane from "./librarypane.js";

const SITE = "CP-16 · SONG STATION · MODEL DRAWING";

/** Shared, non-song view state. `audio` and `input` come from main.js; `library` is { host, park() }. */
export function createEnv({ audio, input, library = null }) {
  const ticker = createTicker();
  const html = document.documentElement;
  const theme = createCell(html.dataset.theme === "dark" ? "dark" : "light");
  const setTheme = (t, persist = true) => { html.dataset.theme = t; if (persist) remember("choppad.theme", t); theme.set(t); };
  const volumeCell = createCell(audio.master.gain.value);
  const volume = {
    get: () => volumeCell.get(),
    set(v) { audio.master.gain.cancelScheduledValues(audio.ctx.currentTime); audio.master.gain.value = v; remember("choppad.volume", v); volumeCell.set(v); },
    on: (fn) => volumeCell.on(fn),
  };
  const message = createCell(null);
  let toastEl = null, toastT = 0;
  const toast = (text) => {
    message.set({ text, t: performance.now() });
    if (!toastEl) { toastEl = document.createElement("div"); toastEl.className = "toast"; toastEl.setAttribute("role", "status"); document.body.append(toastEl); }
    toastEl.textContent = text; toastEl.classList.add("show");
    clearTimeout(toastT); toastT = setTimeout(() => toastEl.classList.remove("show"), 2200);
  };
  // follow the OS theme until the person picks one
  const mq = globalThis.matchMedia?.("(prefers-color-scheme: dark)");
  const onScheme = (e) => { if (!stored("choppad.theme")) setTheme(e.matches ? "dark" : "light", false); };
  mq?.addEventListener?.("change", onScheme);
  return {
    audio, input, ticker, theme: { get: theme.get, on: theme.on, set: setTheme }, volume, message, toast,
    zoom: createCell(0), noteVar: createCell(127), levelMode: createCell("velocity"), actions: {},
    libraryHost: library?.host ?? null, parkLibrary: library?.park ?? null,
    dispose() { ticker.stop(); mq?.removeEventListener?.("change", onScheme); toastEl?.remove(); },
  };
}

const zones = `<div class="zt" aria-hidden="true">${[..."ABCDEFGH"].map((c) => `<span>${c}</span>`).join("")}</div><div class="zl" aria-hidden="true">${[1, 2, 3, 4, 5].map((c) => `<span>${c}</span>`).join("")}</div>`;
const headerHtml = (extra = "") => `<header class="hd">
    <div class="logo">Chop<i>Pad</i></div><div class="sub">${SITE}</div>
    <div class="song">${extra}</div>
    <div class="hd-st" aria-live="polite"><span class="daw-st" hidden></span><span class="role-st" hidden></span></div>
    <button type="button" class="hbtn" data-act="callouts" aria-pressed="true" title="Show or hide the numbered callouts and their key">Key</button>
    <button type="button" class="hbtn" data-act="theme" aria-pressed="false" title="Light (blueprint) or dark (night shift)">Dark</button>
  </header>`;

function wireHeader(root, env, d) {
  const html = document.documentElement;
  const cb = root.querySelector('[data-act="callouts"]'), tb = root.querySelector('[data-act="theme"]');
  const applyCallouts = (on) => { html.dataset.callouts = on ? "on" : "off"; cb.setAttribute("aria-pressed", String(on)); };
  applyCallouts(stored("choppad.callouts", "on") !== "off");
  cb.addEventListener("click", () => { const on = html.dataset.callouts === "off"; remember("choppad.callouts", on ? "on" : "off"); applyCallouts(on); });
  const paintTheme = () => { const dark = env.theme.get() === "dark"; tb.textContent = dark ? "Light" : "Dark"; tb.setAttribute("aria-pressed", String(dark)); tb.setAttribute("aria-label", dark ? "Switch to the light theme" : "Switch to the dark theme"); };
  tb.addEventListener("click", () => env.theme.set(env.theme.get() === "dark" ? "light" : "dark"));
  d.add(env.theme.on(paintTheme));
  paintTheme();
}

/** The full station for one open song. Returns { destroy }. */
export function mountShell(root, session, env) {
  const d = createDisposer();
  const { store, meta, analysis } = session;
  const st = () => store.get();
  root.innerHTML = `<div class="sheet" data-view="station">${zones}
    ${headerHtml(`<span class="s-title"></span><span class="s-meta"></span>`)}
    <aside class="left">
      <section class="panel lib" data-c="11" data-slot="library"></section>
      <section class="panel plat" data-c="12" data-slot="platter"></section>
      <section class="panel vid" data-c="13" data-slot="monitor"></section>
    </aside>
    <main class="mid">
      <section class="hw" data-slot="surface"></section>
      <section class="row2"><span class="fignum">FIG. 5 — PADS · TRANSPORT · MASTER</span>
        <div class="padzone" data-slot="pads"></div>
        <div class="right2"><div class="vuzone" data-slot="vu"></div><div class="transport" data-c="8" data-slot="transport"></div><div class="knobs" data-c="9" data-slot="knobs"></div></div>
      </section>
      <section class="panel steps" data-c="16" data-slot="stepgrid"></section>
    </main>
    <aside class="rt">
      <section class="voxfig" data-c="14" data-slot="vox"></section>
      <section class="legend"><span class="fignum">KEY TO CALLOUTS</span>
        <ol><li><b>1</b>LCD · 6 soft keys</li><li><b>2</b>page keys</li><li><b>3</b>data wheel</li><li><b>4</b>keypad</li><li><b>5</b>pads 4×4</li><li><b>6</b>banks A–D · groups</li><li><b>7</b>note variation</li><li><b>8</b>transport</li><li><b>9</b>master knobs</li><li><b>10</b>VU · PK</li><li><b>11</b>library · kits</li><li><b>12</b>platter</li><li><b>13</b>video</li><li><b>14</b>vox view</li><li><b>15</b>waveform</li><li><b>16</b>steps</li></ol>
        <div class="state"><span><i class="sw-o"></i>playing · armed</span><span><i class="sw-b"></i>selected · custom slice</span></div></section>
      <div class="tblock"><div><small>TITLE</small><b>CHOPPAD CP-16</b></div><div><small>SHEET</small>1 / 1</div><div><small>SCALE</small>1 : 1</div><div><small>SONG</small><span class="tb-song"></span></div><div><small>BANKS</small><span class="tb-banks"></span></div><div><small>REV</small>D</div></div>
    </aside>
    <section class="wv" data-c="15" data-slot="wavestrip"></section>
  </div>`;
  const slot = (n) => root.querySelector(`[data-slot="${n}"]`);

  const fillHeader = () => {
    const { chops, bank } = st();
    root.querySelector(".s-title").textContent = meta.title;
    const T = session.tempoBpm;   // the project tempo when it differs from the song's own
    const tempo = Math.abs(T - chops.bpm) > 0.005 ? `${T.toFixed(1)} BPM (song ${chops.bpm.toFixed(1)})` : `${chops.bpm.toFixed(1)} BPM`;
    root.querySelector(".s-meta").textContent = ` · ${tempo} · ${chops.beats_per_bar}/4 · BANK ${bank + 1}/${chops.banks.length}`;
    root.querySelector(".tb-song").textContent = String(meta.id ?? session.id).slice(0, 14);
    root.querySelector(".tb-banks").textContent = String(chops.banks.length);
  };
  d.add(store.subscribe((s, prev) => { if (s.chops !== prev.chops || s.bank !== prev.bank || s.project.tempo !== prev.project.tempo) fillHeader(); }));
  fillHeader();
  // DAW bridge and tab role: the session keeps them from the song stream; there is no event for them, so look 4 times a second
  const dawEl = root.querySelector(".daw-st"), roleEl = root.querySelector(".role-st");
  let lastLook = 0;
  d.add(env.ticker.add((now) => {
    if (now - lastLook < 250) return;
    lastLook = now;
    const dw = session.remote.daw, primary = session.remote.primary;
    const a = dw.open ? `→ ${dw.daw ? dw.daw[0].toUpperCase() + dw.daw.slice(1) : "DAW"} (bank ${String.fromCharCode(65 + ((dw.bank ?? 0) % 26))})` : "";
    if (dawEl.textContent !== a) { dawEl.textContent = a; dawEl.hidden = !a; }
    const r = primary ? "" : "display only";
    if (roleEl.textContent !== r) { roleEl.textContent = r; roleEl.hidden = !r; roleEl.title = r ? "Another tab on this song is the primary one: it plays agent and remote commands. This tab only shows." : ""; }
  }));
  wireHeader(root, env, d);

  session.setBank(0, true);
  session.loadSections();
  if (!analysis.key) session.detectKey().then((k) => { if (k) window.choppad?.library?.refresh?.(); });

  // global keys that belong to no single view: Space must not also press a focused button
  d.on(document, "keyup", (e) => { if (e.code === "Space" && e.target?.tagName === "BUTTON" && !e.target.closest(".lcd, .pads")) e.preventDefault(); });

  const parts = [
    ["library", librarypane], ["platter", platter], ["monitor", monitor], ["surface", lcd], ["pads", pads], ["vu", vu],
    ["transport", transport], ["knobs", fxdeck], ["stepgrid", stepgrid], ["vox", vox], ["wavestrip", wavestrip],
  ];
  const mounted = parts.map(([name, mod]) => mod.mount(slot(name), session, env));
  return {
    destroy() {
      for (const v of mounted.reverse()) { try { v.destroy(); } catch (err) { console.error(err); } }
      d.run();
      env.dispose();
      root.innerHTML = "";
    },
  };
}

/** The sheet with no song open: header and library, and a note in the middle. */
export function mountWelcome(root, env) {
  const d = createDisposer();
  root.innerHTML = `<div class="sheet welcome-sheet" data-view="welcome">${zones}${headerHtml()}
    <aside class="left"><section class="panel lib" data-c="11" data-slot="library"></section></aside>
    <main class="welcome"><h1>ChopPad</h1><p>Paste a YouTube link in the parts list, or pick a song from the library.</p>
      <p class="muted">Pads: Z X C V · A S D F · Q W E R · 1 2 3 4 — banks: [ ] — song: Enter — pattern: P — rec: B — stop all: Space</p></main></div>`;
  wireHeader(root, env, d);
  const lib = root.querySelector('[data-slot="library"]');
  lib.innerHTML = `<span class="fignum">PARTS LIST — LIBRARY</span><div class="lib-slot"></div>`;
  if (env.libraryHost) lib.querySelector(".lib-slot").append(env.libraryHost);
  return { destroy() { d.run(); env.libraryHost?.remove(); env.parkLibrary?.(); env.dispose(); root.innerHTML = ""; } };
}
