// The sheet: header, theme, the grid that holds every view, and the small shared environment (env) the views use for
// things that are not song state: the shared ticker, theme, wave zoom, master volume, note variation, toasts.
// It composes the views; it never touches sound, the store or the network itself.
import { bankName, createCell, createDisposer, createTicker, remember, store as stored, tempoLabel } from "./util.js";
import { bankCount } from "/actions.js";
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
import * as songchoice from "./songchoice.js";

const SITE = "CP-16 · SONG STATION · MODEL DRAWING";

/** Shared, non-song view state. `audio` and `input` come from main.js; `library` is { host, park() }. */
export function createEnv({ audio, input, library = null, nav = null }) {
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
  const toast = (text, ms = 2200) => {
    message.set({ text, t: performance.now() });
    if (!toastEl) { toastEl = document.createElement("div"); toastEl.className = "toast"; toastEl.setAttribute("role", "status"); document.body.append(toastEl); }
    toastEl.textContent = text; toastEl.classList.add("show");
    clearTimeout(toastT); toastT = setTimeout(() => toastEl.classList.remove("show"), ms);
  };
  // follow the OS theme until the person picks one
  const mq = globalThis.matchMedia?.("(prefers-color-scheme: dark)");
  const onScheme = (e) => { if (!stored("choppad.theme")) setTheme(e.matches ? "dark" : "light", false); };
  mq?.addEventListener?.("change", onScheme);
  return {
    audio, input, ticker, theme: { get: theme.get, on: theme.on, set: setTheme }, volume, message, toast,
    zoom: createCell(0), noteVar: createCell(127), levelMode: createCell("velocity"), actions: {},
    libraryHost: library?.host ?? null, parkLibrary: library?.park ?? null,
    // what the page around the screen offers (main.js): the library's lists, a way to open or make a mega bank, a note from the last screen
    nav: nav ?? { songInfo: () => null, megaInfo: () => null, focusSongs() {}, focusMegas() {}, openMega() {}, switchSource: async () => null, newMega: async () => null, takeNote: () => null },
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

// SONG ▸ DRUM PAD MEGA BANK ▸ DRUM PAD BANK: where you are (the owner's three levels), each segment opens its picker
const seg = (k, label, title) => `<button type="button" class="seg seg-${k}" data-seg="${k}" title="${title}"><small>${label}</small><b class="s-${k === "song" ? "title" : k}"></b></button>`;
const pathHtml = `<nav class="path" aria-label="Where you are: song, drum pad mega bank, drum pad bank">
    ${seg("song", "SONG", "Pick another song: the SONGS list")}<i class="sep" aria-hidden="true">▸</i>
    ${seg("mega", "DRUM PAD MEGA BANK", "Pick a drum pad mega bank: the list")}<i class="sep" aria-hidden="true">▸</i>
    ${seg("bank", "DRUM PAD BANK", "Pick a drum pad bank: the bank keys")}</nav><span class="s-meta"><span class="s-tempo"></span><span class="sr-only s-tempo-long"></span></span>`;

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
    ${headerHtml(pathHtml)}
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
    <div class="mc-slot" data-slot="choice"></div>
  </div>`;
  const slot = (n) => root.querySelector(`[data-slot="${n}"]`);

  const fillHeader = () => {
    const { chops, bank, project } = st();
    const nb = bankCount(project, chops);
    const set = (el, v) => { if (el.textContent !== v) el.textContent = v; };
    const titleEl = root.querySelector(".s-title");
    set(titleEl, session.source ? meta.title : "no song");
    titleEl.classList.toggle("none", !session.source);
    set(root.querySelector(".s-mega"), env.nav.megaInfo(session.mega)?.name ?? "—");
    set(root.querySelector(".s-bank"), bankName(bank, nb));
    // tempo (contracts §15): the user's own tempo, once set, stays through every song change; until then it follows the open song
    const T = session.tempoBpm, user = project.tempo?.bpm != null;
    const tempo = tempoLabel({ T, user, source: session.source, title: meta.title });
    const sm = root.querySelector(".s-meta");
    set(sm.querySelector(".s-tempo"), tempo);
    // the long form for assistive technology (and the older wording): the user tempo with the song's beside it
    set(sm.querySelector(".s-tempo-long"), !session.source ? " no song" : user ? ` USER ${T.toFixed(1)} BPM${Math.abs(T - chops.bpm) > 0.005 ? ` (song ${chops.bpm.toFixed(1)})` : ""}` : ` SONG ${chops.bpm.toFixed(1)} BPM`);
    sm.dataset.tempo = user ? "user" : "song";
    sm.title = session.source ? `Chop tempo of ${meta.title}: ${chops.bpm.toFixed(1)} BPM (it only cuts this song's chops on its own grid)` : "";
    root.querySelector(".tb-song").textContent = String(meta.id ?? session.id ?? "—").slice(0, 14);
    root.querySelector(".tb-banks").textContent = String(nb);
    root.querySelector(".sheet").dataset.source = session.source ?? "";
  };
  d.add(store.subscribe((s, prev) => { if (s.chops !== prev.chops || s.bank !== prev.bank || s.project.tempo !== prev.project.tempo || s.project.overrides !== prev.project.overrides) fillHeader(); }));
  d.on(window, "choppad:megas", fillHeader);   // the mega bank list changed (a rename): the header names it
  // each segment of the path opens its picker: song -> the SONGS list, mega bank -> the list, bank -> the bank keys
  const picker = { song: () => env.nav.focusSongs(), mega: () => env.nav.focusMegas(), bank: () => (root.querySelector(".bkey.on") ?? root.querySelector(".bkey:not(:disabled)"))?.focus() };
  root.querySelector(".path").addEventListener("click", (e) => { const b = e.target.closest("[data-seg]"); if (b) picker[b.dataset.seg]?.(); });
  fillHeader();
  // a source change keeps this whole sheet; only what shows the song is updated (each source-dependent view does the same)
  const onSource = () => { fillHeader(); if (!analysis.key) session.detectKey().then((k) => { if (k) window.choppad?.library?.refresh?.(); }); };
  const sheet = root.querySelector(".sheet");
  const onSourceError = (e) => { sheet.dataset.sourceError = e.detail.error; env.toast(`Couldn't load that song: ${e.detail.error}`, 6000); };
  const onSourceLoading = () => { sheet.dataset.loading = "1"; };
  const onSourceDone = () => { delete sheet.dataset.loading; };
  session.on("source", onSource); session.on("source", onSourceDone); session.on("source-error", onSourceDone);
  session.on("source-error", onSourceError); session.on("source-loading", onSourceLoading);
  d.add(() => { session.off("source", onSource); session.off("source", onSourceDone); session.off("source-error", onSourceDone); session.off("source-error", onSourceError); session.off("source-loading", onSourceLoading); });
  // DAW bridge and tab role: the session keeps them from the song stream; there is no event for them, so look 4 times a second
  const dawEl = root.querySelector(".daw-st"), roleEl = root.querySelector(".role-st");
  let lastLook = 0;
  d.add(env.ticker.add((now) => {
    if (now - lastLook < 250) return;
    lastLook = now;
    const dw = session.remote.daw, primary = session.remote.primary;
    const a = dw.open ? `→ ${session.remote.dawName ? session.remote.dawName[0].toUpperCase() + session.remote.dawName.slice(1) : "DAW"} (bank ${String.fromCharCode(65 + ((dw.bank ?? 0) % 26))})` : "";
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
    ["transport", transport], ["knobs", fxdeck], ["stepgrid", stepgrid], ["vox", vox], ["wavestrip", wavestrip], ["choice", songchoice],
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
