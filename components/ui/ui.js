// Song screen, default view ("Blueprint" light / "Night shift" dark). Presentation only: this file opens the session and
// composes the view modules in views/ (each `mount(root, session, env) -> { destroy }` with its own store subscription).
// A view never owns song state, never creates an engine, sequencer or fetch of its own: it reads `session.store`,
// dispatches actions and calls `session` methods (contracts §8, §9). The previous screen is ui-classic.js (?ui=classic);
// ?mode=pad opens the phone pad screen.
import { openMega, openSong } from "/session.js";
import { createEnv, mountShell, mountWelcome } from "./views/shell.js";
import * as phone from "./views/phone.js";
import { bankCount } from "/actions.js";

/** Returns { session, state, destroy() }. Opens the mega bank `mega`, or the mega bank of song `id`. Throws if it can't be loaded.
 *  `library` is { host, park } from main.js. The screen it builds lives as long as the mega bank is open: a change of the source
 *  song goes to `session.setSource` and every view updates itself (see views/*: the `source` event). */
export async function mountSong(root, { id, mega = null, audio, input, library = null, nav = null }) {
  root.innerHTML = `<p class="loading">Loading song…</p>`;
  const onProgress = (t) => { const el = root.querySelector(".loading"); if (el) el.textContent = t; };
  const session = mega ? await openMega({ mid: mega, audio, onProgress }) : await openSong({ id, audio, onProgress });
  const env = createEnv({ audio, input, library, nav });
  root.innerHTML = "";
  const phoneMode = new URLSearchParams(location.search).get("mode") === "pad";
  const view = phoneMode ? phone.mount(root, session, env) : mountShell(root, session, env);
  const st = () => session.store.get();
  return {
    session,
    env,
    destroy() { view.destroy(); if (phoneMode) env.dispose(); session.destroy(); root.innerHTML = ""; },
    get state() {
      const { bank, chops, project } = st();
      return { bank, banks: bankCount(project, chops), grid: chops.grid, bpm: chops.bpm, playing: session.songPlaying, lastChop: session.lastChop };
    },
  };
}

/** The screen with no song open (library + welcome note). */
export function mountHome(root, { audio, input, library = null, nav = null }) {
  const view = mountWelcome(root, createEnv({ audio, input, library, nav }));
  return { destroy: () => view.destroy() };
}
