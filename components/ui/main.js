// ChopPad app bootstrap: one AudioContext + master bus, one MIDI/keyboard input, hash router.
import { createInput } from "/midi.js";
import { mountLibrary } from "/library.js";
import { mountSong } from "/ui.js";
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

const library = mountLibrary(document.getElementById("sidebar"), {
  onOpen: (id) => { location.hash = `#/song/${id}`; },
});

// Old links used ?id=<id>
const legacy = new URLSearchParams(location.search).get("id");
if (legacy && !location.hash) history.replaceState(null, "", `/#/song/${legacy}`);

const mainEl = document.getElementById("main");
let current = null;
let routeSeq = 0;
async function route() {
  const seq = ++routeSeq;
  current?.destroy();
  current = null;
  const m = location.hash.match(/^#\/song\/([\w-]+)/);
  library.setActive(m ? m[1] : null);
  if (!m) {
    mainEl.innerHTML = `<div class="welcome"><h1>ChopPad</h1><p>Paste a YouTube link on the left, or pick a song from your library.</p>
      <p class="muted">Pads: Z X C V · A S D F · Q W E R · 1 2 3 4 — banks: [ ] — play/pause song: Enter — stop all: Space</p></div>`;
    return;
  }
  try {
    const view = await mountSong(mainEl, { id: m[1], audio, input });
    if (seq !== routeSeq) view.destroy(); else current = view;
  } catch (err) {
    if (seq === routeSeq) mainEl.innerHTML = `<div class="welcome"><h2>Couldn't open this song</h2><p class="error">${err.message}</p></div>`;
  }
}
window.addEventListener("hashchange", route);
route();
window.choppad = { audio, input, library, get song() { return current; } }; // for debugging / e2e: song.session.store
