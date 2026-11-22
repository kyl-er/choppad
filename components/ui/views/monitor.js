// FIG. 3: the muted source video in a framed monitor. The engine seeks it to the chop being played; this view only frames it
// and tags it with the chop label of the last hit.
import { createDisposer } from "./util.js";

export function mount(root, session, env) {
  const d = createDisposer();
  root.innerHTML = `<span class="fignum">FIG. 3 — SOURCE VIDEO</span><div class="frame"><span class="mon-tag" aria-live="off"></span><span class="mon-muted">MUTED</span></div>`;
  const frame = root.querySelector(".frame"), tag = root.querySelector(".mon-tag");
  frame.prepend(session.video);
  session.video.setAttribute("aria-label", "Source video, muted; it jumps to the chop being played");
  const onHit = (e) => {
    const c = session.engine.getSlice(session.store.get().bank, e.detail.pad);
    tag.textContent = c ? (c.custom ? "★" : session.chopLabel(c)) : "";
  };
  session.on("hit", onHit);
  d.add(() => session.off("hit", onHit));
  return { destroy() { d.run(); session.video.remove(); root.innerHTML = ""; } };
}
