// Transport cluster: REC, PLAY (pattern), STOP, SONG, UNDO, CLEAR, CLICK, TAP, plus QUANT and BARS keys.
// Also the global shortcuts that belong to transport: Enter song, P pattern, B rec, T tap.
import { createDisposer } from "./util.js";

const KEYS = [
  ["rec", "rec", "●", "Rec", "Arm recording: hit pads to record them (B)"],
  ["seq-play", "pattern", "▶", "Play", "Play or stop the pattern loop (P)"],
  ["stop", "stop", "■", "Stop", "Stop everything (Space)"],
  ["play", "song", "▶▶", "Song", "Play or pause the song (Enter)"],
  ["seq-undo", "undo", "↶", "Undo", "Drop the latest recorded take"],
  ["seq-clear", "clear", "✕", "Clear", "Empty the pattern"],
  ["metro", "click", "♩", "Click", "Metronome on or off"],
  ["tapkey", "tap", "T", "Tap", "Tap the tempo (T), then apply it on the GRID page"],
];
const QUANTS = ["off", "1/8", "1/16"], BARS = [1, 2, 4];

export function mount(root, session, env) {
  const d = createDisposer();
  const { store, seq } = session;
  const st = () => store.get();
  root.setAttribute("role", "group");
  root.setAttribute("aria-label", "Transport");
  root.innerHTML = KEYS.map(([cls, act, icon, label, tip]) => `<button type="button" class="tkey ${cls}" data-act="${act}" title="${tip}"><i>${icon}</i><span>${label}</span></button>`).join("") +
    `<button type="button" class="tkey wide q-key" data-act="quant" title="Quantize: off, 1/8, 1/16"><i>⌗</i><span class="q-val"></span></button>` +
    `<button type="button" class="tkey wide bars-key" data-act="bars" title="Pattern length in bars"><i>↻</i><span class="bars-val"></span></button>`;
  const $ = (a) => root.querySelector(`[data-act="${a}"]`);
  const click = (a, fn) => $(a).addEventListener("click", fn);
  click("rec", () => session.toggleRecord());
  click("pattern", () => session.togglePattern());
  click("stop", () => session.panic());
  click("song", () => (session.songPlaying ? session.stopSong() : session.playSong()));
  click("undo", () => store.dispatch({ type: "pattern.undo" }));
  click("clear", () => store.dispatch({ type: "pattern.clear" }));
  click("click", () => store.dispatch({ type: "settings.set", params: { metronome: !st().settings.metronome } }));
  click("tap", () => env.actions.tap?.());
  click("quant", () => store.dispatch({ type: "settings.set", params: { quant: QUANTS[(QUANTS.indexOf(st().settings.quant) + 1) % QUANTS.length] } }));
  click("bars", () => store.dispatch({ type: "pattern.setBars", bars: BARS[(BARS.indexOf(st().project.pattern.bars) + 1) % BARS.length], beatsPerBar: st().chops.beats_per_bar }));

  const flag = (a, on) => { const b = $(a); b.classList.toggle("on", on); b.setAttribute("aria-pressed", String(on)); };
  function render() {
    flag("rec", seq.state.recording);
    flag("pattern", seq.state.playing);
    flag("song", session.songPlaying);
    flag("click", st().settings.metronome);
    const pat = $("pattern");
    pat.querySelector("i").textContent = seq.state.playing ? "■" : "▶";
    pat.querySelector("span").textContent = seq.state.playing ? "Stop" : "Play";
    $("song").querySelector("span").textContent = session.songPlaying ? "Pause" : "Song";
    $("quant").querySelector(".q-val").textContent = `Quant ${st().settings.quant}`;
    $("bars").querySelector(".bars-val").textContent = `${st().project.pattern.bars} bar${st().project.pattern.bars === 1 ? "" : "s"}`;
  }
  for (const t of ["seq", "song"]) { session.on(t, render); d.add(() => session.off(t, render)); }
  d.add(store.subscribe((s, prev) => { if (s.settings !== prev.settings || s.project.pattern.bars !== prev.project.pattern.bars) render(); }));
  render();

  // the click key blinks on every beat of the loop (blue: "in place")
  let lastBeat = -1;
  d.add(env.ticker.add(() => {
    if (!seq.state.playing || !st().settings.metronome) { lastBeat = -1; return; }
    const beat = Math.floor(seq.position());
    if (beat !== lastBeat) { lastBeat = beat; const k = $("click"); k.classList.add("tick"); setTimeout(() => k.classList.remove("tick"), 90); }
  }));

  d.on(document, "keydown", (e) => {
    if (e.metaKey || e.ctrlKey || e.altKey || e.repeat) return;
    const tg = e.target, tag = tg?.tagName ?? "";
    if (/^(INPUT|TEXTAREA|SELECT)$/.test(tag) && tg.type !== "range" && tg.type !== "checkbox") return;
    if (e.code === "Enter" && !tg?.closest?.("a[href]")) { e.preventDefault(); session.songPlaying ? session.stopSong() : session.playSong(); }
    else if (e.code === "KeyP") session.togglePattern();
    else if (e.code === "KeyB") session.toggleRecord();
    else if (e.code === "KeyT") env.actions.tap?.();
  });
  return { destroy() { d.run(); root.innerHTML = ""; } };
}
