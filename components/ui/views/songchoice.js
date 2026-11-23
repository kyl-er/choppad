// The choice on a song change, the line that says what happened, and the small questions the screen asks (contracts §15 UI).
// A non-modal panel in the instrument's style: it floats over the right-hand column, never over the pads, the pattern keeps playing
// while it is open. Presentation only: the choice goes to env.nav (a new mega bank) or session.setSource (keep the drum pads).
//   env.actions.chooseSong(id) -> Promise: asks (A / B / Esc) when the open mega bank has used pads, else switches at once
//   env.actions.ask({ title, text, choices, cancel }) -> Promise<choice id | null>: the same panel for other questions (release a pad)
//   env.actions.say(text, { failed? })               -> the one-line result
import { usedPads, PADS } from "/actions.js";
import { createDisposer, esc, shortSong } from "./util.js";

// "remember my choice for this session": "a" | "b" | null, kept for the browser tab (sessionStorage), so it survives a change of mega bank
const KEY = "choppad.megaChoice";
let remembered = null;
try { const s = sessionStorage.getItem(KEY); if (s === "a" || s === "b") remembered = s; } catch { /* private mode */ }
const setRemembered = (v) => { remembered = v; try { if (v) sessionStorage.setItem(KEY, v); else sessionStorage.removeItem(KEY); } catch { /* private mode */ } };

export function mount(root, session, env) {
  const d = createDisposer();
  const { store, meta } = session;
  const st = () => store.get();
  root.innerHTML = `<div class="mchoice" role="dialog" aria-modal="false" aria-labelledby="mc-title" aria-describedby="mc-sub" hidden></div>
    <div class="mline" role="status" aria-live="polite" hidden></div>`;
  const dlg = root.querySelector(".mchoice"), line = root.querySelector(".mline");
  const megaName = () => env.nav.megaInfo(session.mega)?.name ?? "this mega bank";
  const letter = (b) => String.fromCharCode(65 + (b % 4));
  let pending = null;   // { resolve, choices, prevFocus, onKey }
  let lineT = 0;

  // ---------------------------------------------------------------- the line: what happened
  function say(text, { failed = [], action = null, forget = false, sticky = false } = {}) {
    clearTimeout(lineT);
    line.hidden = false;
    line.dataset.kind = failed.length ? "warn" : "info";
    line.innerHTML = `<span class="ml-text">${esc(text)}</span>${failed.length ? `<span class="ml-failed">${failed.map((f) => esc(`Pad ${f.pad + 1} (bank ${letter(f.bank)}) not kept: ${f.reason}`)).join(" · ")}</span>` : ""}
      <span class="ml-b">${action ? `<button type="button" class="ml-act">${esc(action.label)}</button>` : ""}${forget ? `<button type="button" class="ml-forget" title="Stop remembering my choice">Ask me again</button>` : ""}<button type="button" class="ml-x" aria-label="Dismiss this message" title="Dismiss">×</button></span>`;
    line.querySelector(".ml-x").addEventListener("click", clearLine);
    line.querySelector(".ml-act")?.addEventListener("click", action?.run);
    line.querySelector(".ml-forget")?.addEventListener("click", () => { setRemembered(null); clearLine(); env.toast("I will ask again on the next song change"); });
    if (!sticky && !failed.length) lineT = setTimeout(clearLine, 30000);
  }
  function clearLine() { clearTimeout(lineT); line.hidden = true; line.innerHTML = ""; }
  env.actions.say = say;

  // ---------------------------------------------------------------- the panel
  function closeAsk(value) {
    if (!pending) return;
    const p = pending;
    pending = null;
    document.removeEventListener("keydown", p.docKey, true);
    dlg.hidden = true; dlg.innerHTML = "";
    if (p.prevFocus && document.body.contains(p.prevFocus)) { try { p.prevFocus.focus(); } catch { /* gone */ } }
    p.resolve(value);
  }
  /** choices: [{ id, key, label, desc }] -> resolves { choice, remember } or null (Cancel / Esc / replaced by another question). */
  function ask({ title, text, choices, cancel = "Cancel", rememberable = false, focusCancel = false }) {
    if (pending) closeAsk(null);
    return new Promise((resolve) => {
      const prevFocus = document.activeElement;
      dlg.hidden = false;
      dlg.innerHTML = `<div class="mc-head"><b id="mc-title">${esc(title)}</b></div>
        <p class="mc-sub" id="mc-sub">${esc(text)}</p>
        <div class="mc-opts">${choices.map((c) => `<button type="button" class="mc-opt" data-choice="${c.id}" aria-keyshortcuts="${c.key}"><kbd>${c.key}</kbd><span><b>${esc(c.label)}</b>${c.desc ? `<em>${esc(c.desc)}</em>` : ""}</span></button>`).join("")}</div>
        <div class="mc-foot">${rememberable ? `<label class="mc-remember"><input type="checkbox" class="mc-rem"> Remember my choice for this session</label>` : "<span></span>"}<button type="button" class="mc-cancel" aria-keyshortcuts="Escape">${esc(cancel)} <kbd>Esc</kbd></button></div>`;
      const finish = (id) => closeAsk(id ? { choice: id, remember: !!dlg.querySelector(".mc-rem")?.checked } : null);
      dlg.querySelectorAll(".mc-opt").forEach((b) => b.addEventListener("click", () => finish(b.dataset.choice)));
      dlg.querySelector(".mc-cancel").addEventListener("click", () => finish(null));
      // A / B / Esc work with the focus anywhere on the page while the question is open (the pads keep their own keys: only these)
      const docKey = (e) => {
        if (e.metaKey || e.ctrlKey || e.altKey) return;
        const k = e.key.length === 1 ? e.key.toUpperCase() : e.key;
        const hit = choices.find((c) => c.key === k);
        if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); finish(null); }
        else if (hit && dlg.contains(document.activeElement) && document.activeElement?.tagName !== "INPUT") { e.preventDefault(); e.stopPropagation(); finish(hit.id); }
      };
      document.addEventListener("keydown", docKey, true);   // capture: before the pad keys and the LCD keys see it
      pending = { resolve, prevFocus, docKey };
      (focusCancel ? dlg.querySelector(".mc-cancel") : dlg.querySelector(".mc-opt"))?.focus();
    });
  }
  env.actions.ask = async (o) => (await ask({ ...o, choices: o.choices }))?.choice ?? null;

  // ---------------------------------------------------------------- choosing a song
  const freePads = () => { const { bank, project } = st(); let n = 0; for (let p = 0; p < PADS; p++) if (session.engine.getSlice(bank, p) && !project.overrides[`${bank}:${p}`]) n++; return n; };
  async function keepDrumPads(id, via) {
    const fromTitle = session.source ? meta.title : null;
    const out = await env.nav.switchSource(id);
    if (!out) return null;
    if (!out.ok) return out;   // source-error shows the reason and a retry
    if (out.same) return out;
    // every kept pad of the bank stays (those kept on an earlier change too); "from <old song>" only when all of them were cut from it
    const kept = Object.values(st().project.overrides).filter((o) => o?.kept).length, fresh = out.kept?.length ?? 0, to = meta.title;
    const head = kept ? `Kept ${kept} pad${kept === 1 ? "" : "s"}${fromTitle && fresh === kept ? ` from ${shortSong(fromTitle)}` : ""}; ${freePads()} free pads now play ${shortSong(to)}.` : `Now chopping ${shortSong(to)}: ${freePads()} free pads play it.`;
    say(head, { failed: out.failed ?? [], forget: via === "remembered" });
    return out;
  }
  async function newMegaBank(id, info, via) {
    const was = megaName();
    try {
      const m = await env.nav.newMega(id, { song: shortSong(info.title), from: was });
      return { ok: true, opened: !!m, mega: m, via };
    } catch (err) {
      say(`Couldn't make a mega bank: ${err.message}`, { sticky: true });
      return { ok: false, error: String(err.message ?? err) };
    }
  }
  async function chooseSong(id) {
    if (id === session.source) return null;
    const info = env.nav.songInfo(id);
    const used = usedPads(st().project);
    if (!info || !used.length) return keepDrumPads(id, "direct");   // nothing to lose (or not a song of the library: the session reports it)
    if (remembered) return remembered === "a" ? newMegaBank(id, info, "remembered") : keepDrumPads(id, "remembered");
    const song = shortSong(info.title);
    const r = await ask({
      title: `Switching to ${song}`,
      text: `“${megaName()}” has ${used.length} used pad${used.length === 1 ? "" : "s"}. Your pattern keeps playing while you choose.`,
      rememberable: true,
      choices: [
        { id: "a", key: "A", label: `New mega bank for ${song}`, desc: `“${megaName()}” stays as it is; the new one starts empty.` },
        { id: "b", key: "B", label: "Keep current drum pads", desc: `Keep the ${used.length} pad${used.length === 1 ? "" : "s"} you used, bring ${song} into the free pads.` },
      ],
    });
    if (!r) return null;   // cancelled: nothing changed
    if (r.remember) setRemembered(r.choice);
    return r.choice === "a" ? newMegaBank(id, info, "asked") : keepDrumPads(id, "asked");
  }
  env.actions.chooseSong = chooseSong;

  // ---------------------------------------------------------------- a failed load: the error stays until it is dealt with
  const onError = (e) => {
    const t = env.nav.songInfo(e.detail.song)?.title ?? e.detail.song;
    say(`Couldn't load ${shortSong(t)}: ${e.detail.error}. Still on ${session.source ? shortSong(meta.title) : "no song"}.`, { failed: [], sticky: true, action: { label: "Retry", run: async () => {
      clearLine();
      const out = await session.retrySource();
      if (out?.ok) say(`Now chopping ${shortSong(meta.title)}.`);
    } } });
    line.dataset.kind = "error";
  };
  const onSource = () => { if (line.dataset.kind === "error") clearLine(); };
  session.on("source-error", onError); session.on("source", onSource);
  d.add(() => { session.off("source-error", onError); session.off("source", onSource); });

  // what the screen before this one left for us (a new mega bank was made and opened)
  const n = env.nav.takeNote();
  if (n) say(`New mega bank “${n.mega}” for ${n.song}. “${n.from}” is untouched: open it from the list.`);
  else if (session.lastSourceError) onError({ detail: session.lastSourceError });

  return { destroy() { closeAsk(null); clearTimeout(lineT); d.run(); delete env.actions.chooseSong; delete env.actions.ask; delete env.actions.say; root.innerHTML = ""; } };
}
