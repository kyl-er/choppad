// ChopPad app bootstrap: one AudioContext + master bus, one MIDI/keyboard input, hash router.
import { createInput } from "/midi.js";
import { mountLibrary } from "/library.js";
import { createFx } from "/fx.js";

const ctx = new AudioContext({ latencyHint: "interactive" });
const master = ctx.createGain();
const analyser = ctx.createAnalyser();
analyser.fftSize = 2048;
const fx = createFx(ctx); // master sound shaping: tone, EQ, filter, reverb (per-song settings, set by ui.js)
master.connect(fx.input);
fx.output.connect(analyser);
analyser.connect(ctx.destination);
try { master.gain.value = Number(localStorage.getItem("choppad.volume") ?? 0.9); } catch { master.gain.value = 0.9; }
const audio = { ctx, master, analyser, fx };

// Browsers keep audio suspended until a real gesture (Web MIDI does not count).
const unlock = document.getElementById("unlock");
const refreshUnlock = () => { unlock.hidden = ctx.state === "running"; };
ctx.addEventListener("statechange", refreshUnlock);
const resume = () => { if (ctx.state !== "running") ctx.resume().then(refreshUnlock, () => {}); };
document.addEventListener("pointerdown", resume);
document.addEventListener("keydown", resume);
refreshUnlock();

let midiAccess = null;
try { midiAccess = (await navigator.requestMIDIAccess?.()) ?? null; } catch { midiAccess = null; }
let storage = null;
try { storage = window.localStorage; } catch { storage = null; }
const input = createInput({ midiAccess, keyboardTarget: document, storage });

// ?ui=classic keeps the previous screen; the default is the new station (ui.js + views/). ?mode=pad = phone pad screen.
const classic = new URLSearchParams(location.search).get("ui") === "classic";
const ui = await import(classic ? "/ui-classic.js" : "/ui.js");
const sidebar = document.getElementById("sidebar");

const mainEl = document.getElementById("main");
const stats = { mounts: 0, destroys: 0, switches: 0 };   // for e2e: how often the page was (re)built
let current = null;
let routeSeq = 0;
let syncedHash = null;   // the hash this script wrote itself with replaceState (that never fires hashchange; guarded anyway)
let note = null;         // a line the next screen shows when it opens (what the switch to a new mega bank did)

// Choosing a song while a mega bank is open changes that bank's SOURCE (session.setSource): nothing is torn down or rebuilt. When
// the bank has used pads the screen first asks: new mega bank, or keep the drum pads (views/songchoice.js, env.actions.chooseSong).
// With nothing open it opens the song's mega bank through the route. The classic screen has no question: a song change is always
// "keep the used pads" (the server switches the source), then the screen is made again on the same mega bank.
const library = mountLibrary(sidebar, {
  onOpen: (id) => { if (classic) classicSwitch(id); else if (current?.session) chooseSong(id); else location.hash = `#/song/${id}`; },
  mega: classic ? null : {
    open: (mid) => openMega(mid),
    currentSource: () => current?.session?.source ?? null,
    // the open bank is about to be moved to trash: close it first so nothing is saved to it afterwards
    beforeRemove: async (mid) => {
      if (current?.session?.mega !== mid) return;
      await current.session.remote.flush();
      closeOpen();
    },
    removed: async (mid, wasOpen) => {
      if (!wasOpen) return;
      const next = library.megas()[0];
      location.hash = next ? `#/mega/${next.id}` : "#/";
    },
  },
});
// the new screen borrows the library element while it is open; between screens it is parked back in the page
const libraryRef = { host: sidebar, park: () => document.body.append(sidebar) };

// What the screen's views need from the page around them (not song state): lists, a way to change mega bank, a note for the next screen.
const nav = {
  songInfo: (id) => library.songInfo(id),
  megaInfo: (mid) => library.megaInfo(mid),
  focusSongs: () => library.focusSongs(),
  focusMegas: () => library.focusMegas(),
  openMega: (mid) => openMega(mid),
  switchSource: (id) => switchSource(id),
  /** Option (a): a new mega bank with `id` as its source, opened. The bank that was open is left as it is. */
  async newMega(id, text) {
    const m = await library.createMega({ source: id });
    note = text ? { ...text, mega: m.name } : null;
    openMega(m.id);
    return m;
  },
  takeNote() { const n = note; note = null; return n; },
};

// Old links used ?id=<id>
const legacy = new URLSearchParams(location.search).get("id");
if (legacy && !location.hash) history.replaceState(null, "", `/#/song/${legacy}`);

// #/mega/<mid>  ·  #/song/<id>[/mega/<mid>]  (the second form is what the address shows: it names the song and the mega bank)
function parseRoute(hash) {
  let m = hash.match(/^#\/mega\/([\w-]+)(?:\/song\/([\w-]+))?/);
  if (m) return { mid: m[1], song: m[2] ?? null };
  m = hash.match(/^#\/song\/([\w-]+)(?:\/mega\/([\w-]+))?/);
  if (m) return { mid: m[2] ?? null, song: m[1] };
  return null;
}
function openMega(mid) { if (location.hash === `#/mega/${mid}`) route(); else location.hash = `#/mega/${mid}`; }
/** The address names the open source song and mega bank, without a navigation (replaceState): no hashchange, no remount. */
function syncUrl() {
  const s = current?.session;
  if (!s) return;
  const h = s.source ? `#/song/${s.source}/mega/${s.mega}` : `#/mega/${s.mega}`;
  if (location.hash !== h) { syncedHash = h; history.replaceState(null, "", h); }
  library.setActive(s.source);
  library.setActiveMega(s.mega);
}
/** A source change on the live session. A failure leaves everything as it was (the session reports it) and the address too. */
async function switchSource(id) {
  const live = current?.session;
  if (!live) return null;
  stats.switches++;
  library.setActive(id);
  const out = await live.setSource(id);
  syncUrl();
  return out;
}
/** The user picked a song while a mega bank is open: the screen asks when there are used pads, else the source just changes. */
async function chooseSong(id) {
  const live = current?.session;
  if (!live) return null;
  if (id === live.source) { syncUrl(); return null; }
  const ask = current.env?.actions?.chooseSong;
  const out = ask ? await ask(id) : await switchSource(id);
  if (!out?.opened) syncUrl();   // a cancelled or failed choice leaves the address and the library marks on the song that is open
  return out;                    // (a new mega bank sets its own address and the route makes the screen again)
}
/** Classic screen: keep the used pads (the server switches the source), then show the same mega bank again. */
async function classicSwitch(id) {
  const live = current?.session;
  if (!live) { location.hash = `#/song/${id}`; return; }
  if (id === live.source) return;
  const out = await live.setSource(id);
  if (out?.ok) { stats.switches++; syncedHash = null; location.hash = `#/mega/${live.mega}/song/${id}`; }
}
function closeOpen() {
  if (!current) return;
  routeSeq++;
  stats.destroys++;
  current.destroy();
  current = null;
}
/** Id of the most recently updated mega bank whose source is `song` (GET /api/mega lists them; nothing is created), or null. */
async function megaOfSong(song) {
  try {
    const list = await fetch("/api/mega").then((r) => r.json());
    return list.filter((m) => m.source === song).sort((a, b) => String(b.updated).localeCompare(String(a.updated)))[0]?.id ?? null;
  } catch { return null; }
}

async function route() {
  const seq = ++routeSeq;
  if (location.hash && location.hash === syncedHash) return;
  syncedHash = null;
  const r = parseRoute(location.hash);
  // A change inside the open mega bank is a change of its source song, not of the page.
  const live = !classic ? current?.session : null;
  if (live && r) {
    if (r.mid && r.mid === live.mega) {
      if (r.song && r.song !== live.source) await chooseSong(r.song); else syncUrl();
      return;
    }
    if (!r.mid && r.song) {
      if (r.song === live.source) { syncUrl(); return; }
      const mid = await megaOfSong(r.song);
      if (seq !== routeSeq) return;
      if (mid && mid === live.mega) { await chooseSong(r.song); return; }
    }
  }
  // a real change of mega bank (or the first open): the screen is made again
  if (current) { stats.destroys++; current.destroy(); }
  current = null;
  library.setActive(r?.song ?? null);
  library.setActiveMega(r?.mid ?? null);
  if (!r && !classic) { current = ui.mountHome(mainEl, { audio, input, library: libraryRef, nav }); return; }
  if (!r) {
    mainEl.innerHTML = `<div class="welcome"><h1>ChopPad</h1><p>Paste a YouTube link on the left, or pick a song from your library.</p>
      <p class="muted">Pads: Z X C V · A S D F · Q W E R · 1 2 3 4 — banks: [ ] — play/pause song: Enter — stop all: Space</p></div>`;
    return;
  }
  const open = (opts) => { stats.mounts++; return ui.mountSong(mainEl, { ...opts, audio, input, library: classic ? null : libraryRef, nav }); };
  try {
    if (!classic) await library.megasReady();   // the header names the mega bank: have the list before the first paint
    let view;
    try {
      view = await open(r.mid ? { mega: r.mid } : { id: r.song });
    } catch (err) {
      if (!(r.mid && r.song)) throw err;
      view = await open({ id: r.song });   // the named mega bank is gone: the song's own
    }
    if (seq !== routeSeq) { stats.destroys++; view.destroy(); return; }
    current = view;
    // another tab (or an agent) changed the source: the address, the library marks and the mega list follow
    view.session.on("source", () => { syncUrl(); library.refreshMegas(); });
    if (!classic) {
      let t = 0;   // used pads and last change are shown in the mega list: refresh it a moment after the pattern or the pads change
      view.session.store.subscribe((s, prev) => {
        if (s.project.overrides !== prev.project.overrides || s.project.pattern !== prev.project.pattern) { clearTimeout(t); t = setTimeout(() => library.refreshMegas(), 1200); }
      });
    }
    syncUrl();
    library.refreshMegas();
  } catch (err) {
    if (seq === routeSeq) mainEl.innerHTML = `<div class="welcome"><h2>Couldn't open this song</h2><p class="error">${err.message}</p></div>`;
  }
}
window.addEventListener("hashchange", route);
window.choppad = { audio, input, library, stats, nav, get song() { return current; } }; // for debugging / e2e: song.session.store
route();
