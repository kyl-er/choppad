// LCD sub-pages for the drum pad mega banks (CHOP-13 step C), appended by lcdpages.js:
//   MAIN 2 mega banks   the three names (song, mega bank, bank), the tempo, and per drum pad bank: how many pads are kept / used / free
//   PAD  9 kept         the selected pad: kept or not, the song it came from, where in that song, its level; KEEP and RELEASE
// Declarative like the other pages; nothing here holds state. KEEP is session.keepPad, RELEASE is the `slice.clear` action (after a
// question when the sequence plays the pad), the question is the panel of songchoice.js (env.actions.ask).
import { PADS, bankCount, usedPads } from "/actions.js";
import { act, blank } from "./lcdpages.js";
import { bankName, esc, fmt, shortSong, tempoLabel } from "./util.js";

const letter = (b) => String.fromCharCode(65 + (b % 4));
const setHtml = (el, html) => { if (el._h !== html) { el._h = html; el.innerHTML = html; } };

export function megaSubpages(ctx) {
  const { session, st, dispatch, env, out } = ctx;
  const { engine, meta } = session;
  const songName = (id) => (id === session.source ? meta.title : env.nav.songInfo(id)?.title) ?? id;
  const ov = (bank = st().bank, pad = st().selected) => st().project.overrides[`${bank}:${pad}`];
  const megaName = () => env.nav.megaInfo(session.mega)?.name ?? "—";
  /** Hits of (bank, pad) in the pattern being edited, and in the other patterns of song mode. */
  const hitsOf = (bank, pad) => {
    const { project } = st();
    const mine = (e) => e.pad === pad && (Number.isInteger(e.bank) ? e.bank : 0) === bank;
    const here = project.pattern.events.filter(mine).length;
    const cur = project.song?.current;
    const other = (project.song?.patterns ?? []).filter((p) => p.id !== cur).reduce((n, p) => n + (p.events ?? []).filter(mine).length, 0);
    return { here, other };
  };
  const levelDb = (o) => 20 * Math.log10(o.gain ?? 1) + (o.params?.gainDb ?? 0);
  const sgn1 = (v) => `${v >= 0 ? "+" : ""}${v.toFixed(1)}`;

  // ------------------------------------------------------------------ PAD 9: kept
  async function keep() {
    const { bank, selected } = st();
    const r = await session.keepPad(bank, selected);
    out.kept = r.ok ? (r.kept.length ? `✓ PAD ${selected + 1} KEPT: IT STAYS WHEN THE SONG CHANGES` : `PAD ${selected + 1} WAS ALREADY KEPT`) : `<span class="error">${esc(r.error ?? "not kept")}</span>`;
    ctx.refresh();
  }
  async function release() {
    const { bank, selected } = st();
    const o = ov(bank, selected);
    if (!o?.kept) return;
    const { here, other } = hitsOf(bank, selected);
    if (here + other > 0) {
      const to = session.source ? shortSong(meta.title) : "no song";
      const n = here + other;
      const c = await env.actions.ask?.({
        title: `Release pad ${selected + 1}?`,
        text: `The sequence plays this pad (${n} hit${n === 1 ? "" : "s"}${other ? `, ${other} in other patterns` : ""}). After the release those hits play ${session.source ? `the chop of ${to}` : "nothing (no song)"} instead of the kept sound.`,
        choices: [{ id: "release", key: "R", label: "Release the pad", desc: "Drop the kept sound; the pad shows the current song's chop again." }],
        cancel: "Keep it", focusCancel: true,
      });
      if (c !== "release") { out.kept = "RELEASE CANCELLED"; ctx.refresh(); return; }
    }
    dispatch({ type: "slice.clear", bank, pad: selected });
    out.kept = `✓ PAD ${selected + 1} RELEASED: IT PLAYS ${session.source ? esc(shortSong(meta.title)).toUpperCase() : "NOTHING"} AGAIN`;
    ctx.refresh();
  }
  const keptInfo = () => {
    const { bank, selected } = st();
    const o = ov(bank, selected), c = engine.getSlice(bank, selected);
    const head = `PAD ${selected + 1} · BANK ${letter(bank)}`;
    if (o?.kept) {
      const f = o.from;
      const dur = `${(o.end - o.start).toFixed(2)} S`;
      return `${head} · <b>KEPT</b> · FROM <b>${f ? esc(songName(f.song)) : "?"}</b>${f ? ` AT ${fmt(f.start)}–${fmt(f.end)}` : ""} · ${dur} · LEVEL <b>${sgn1(levelDb(o))} DB</b>${out.kept ? `<br>${out.kept}` : ""}`;
    }
    const { here, other } = hitsOf(bank, selected);
    const why = !c ? "EMPTY PAD" : o ? "CUSTOM SLICE, NOT KEPT" : `PLAYS THE CHOP OF ${esc(shortSong(meta.title)).toUpperCase()}`;
    return `${head} · <b>NOT KEPT</b> · ${why}${here + other ? ` · IN THE SEQUENCE: A SONG CHANGE KEEPS IT` : ""}${out.kept ? `<br>${out.kept}` : ""}`;
  };
  const kept = { name: "kept", info: { cls: "kept-out", html: keptInfo }, fields: () => [
    act("Keep", { cls: "kp-keep", val: () => (ov()?.kept ? "kept" : "▸"), disabled: () => !engine.getSlice(st().bank, st().selected) || !!ov()?.kept, fn: keep }),
    act("Release", { cls: "kp-release", val: () => (ov()?.kept ? "▸" : "—"), disabled: () => !ov()?.kept, fn: release }),
    blank(), blank(), blank(), blank(),
  ] };

  // ------------------------------------------------------------------ MAIN 2: mega banks
  const bankRows = () => {
    const { project, chops, bank } = st();
    const used = new Set(usedPads(project).map(([b, p]) => `${b}:${p}`));
    const n = bankCount(project, chops);
    const rows = [];
    for (let b = 0; b < n; b++) {
      let k = 0, u = 0, f = 0;
      for (let p = 0; p < PADS; p++) {
        const key = `${b}:${p}`;
        if (project.overrides[key]?.kept) k++;
        else if (used.has(key)) u++;
        else if (engine.getSlice(b, p)) f++;
      }
      rows.push({ b, k, u, f, on: b === bank });
    }
    return rows;
  };
  const overview = () => {
    const el = document.createElement("div");
    el.className = "bankov";
    el.setAttribute("role", "group");
    el.setAttribute("aria-label", "Drum pad banks: pads kept, used and free");
    el.addEventListener("click", (e) => { const r = e.target.closest("[data-b]"); if (r) session.setBank(Number(r.dataset.b), true); });
    return { el, update() {
      const rows = bankRows();
      const head = `<div class="bo-row bo-head" aria-hidden="true"><span>BANK</span><span>KEPT</span><span>USED</span><span>FREE</span></div>`;
      setHtml(el, head + rows.map((r) => `<button type="button" class="bo-row${r.on ? " on" : ""}" data-b="${r.b}" aria-label="Bank ${letter(r.b)}, ${r.b + 1} of ${rows.length}: ${r.k} kept, ${r.u} used, ${r.f} free"${r.on ? ' aria-current="true"' : ""}>
          <span>${r.on ? "▸ " : ""}${letter(r.b)} ${r.b + 1}</span><span>${r.k}</span><span>${r.u}</span><span>${r.f}</span></button>`).join(""));
    } };
  };
  const mega = { name: "mega banks", cls: "cols2 megaov", fields: () => [
    act("Songs", { cls: "mo-songs", val: "▸", fn: () => env.nav.focusSongs() }),
    act("Mega banks", { cls: "mo-megas", val: "▸", fn: () => env.nav.focusMegas() }),
    blank(), blank(), blank(), blank(),
  ], extra: overview, info: { cls: "mega-path", html: () => {
    const { bank, chops, project } = st();
    const T = session.tempoBpm, user = project.tempo?.bpm != null;
    return `SONG <b>${esc(session.source ? meta.title : "—")}</b> ▸ MEGA BANK <b>${esc(megaName())}</b> ▸ BANK <b>${esc(bankName(bank, bankCount(project, chops)))}</b> · ${esc(tempoLabel({ T, user, source: session.source, title: meta.title }))}<br>KEPT = OWNS ITS SOUND · USED = IN THE SEQUENCE OR A CUSTOM SLICE, KEPT WHEN THE SONG CHANGES · FREE = PLAYS THE SONG`;
  } } };

  return { MAIN: [mega], PAD: [kept] };
}
