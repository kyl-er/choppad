// Song screen, default view ("Blueprint" light / "Night shift" dark). Presentation only: this file opens the session and
// composes the view modules in views/ (each `mount(root, session, env) -> { destroy }` with its own store subscription).
// A view never owns song state, never creates an engine, sequencer or fetch of its own: it reads `session.store`,
// dispatches actions and calls `session` methods (contracts §8, §9). The previous screen is ui-classic.js (?ui=classic);
// ?mode=pad opens the phone pad screen.
import { openSong } from "/session.js";
import { createEnv, mountShell, mountWelcome } from "./views/shell.js";
import * as phone from "./views/phone.js";

/** Returns { session, state, destroy() }. Throws if the song can't be loaded. `library` is { host, park } from main.js. */
export async function mountSong(root, { id, audio, input, library = null }) {
  root.innerHTML = `<p class="loading">Loading song…</p>`;
  const session = await openSong({ id, audio, onProgress: (t) => { const el = root.querySelector(".loading"); if (el) el.textContent = t; } });
  const env = createEnv({ audio, input, library });
  root.innerHTML = "";
  const phoneMode = new URLSearchParams(location.search).get("mode") === "pad";
  const view = phoneMode ? phone.mount(root, session, env) : mountShell(root, session, env);
  const st = () => session.store.get();
  return {
    session,
    env,
    destroy() { view.destroy(); if (phoneMode) env.dispose(); session.destroy(); root.innerHTML = ""; },
    get state() {
      const { bank, chops } = st();
      return { bank, banks: chops.banks.length, grid: chops.grid, bpm: chops.bpm, playing: session.songPlaying, lastChop: session.lastChop };
    },
  };
}

/** The screen with no song open (library + welcome note). */
export function mountHome(root, { audio, input, library = null }) {
  const view = mountWelcome(root, createEnv({ audio, input, library }));
  return { destroy: () => view.destroy() };
}
