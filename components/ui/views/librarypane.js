// Library pane: URL import, file drop, songs and kits. The library itself (library.js, shared with the classic screen) is
// created once by main.js and lives in env.libraryHost; this view only borrows the host while a song is open.
import { createDisposer } from "./util.js";

export function mount(root, session, env) {
  const d = createDisposer();
  root.innerHTML = `<span class="fignum">PARTS LIST — LIBRARY</span><div class="lib-slot"></div>`;
  const slot = root.querySelector(".lib-slot");
  if (env.libraryHost) slot.append(env.libraryHost);
  d.add(() => { if (env.libraryHost) { env.libraryHost.remove(); env.parkLibrary?.(); } });
  return { destroy() { d.run(); root.innerHTML = ""; } };
}
