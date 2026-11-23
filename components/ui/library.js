// Library sidebar: import a YouTube link or a dropped audio file (background job with progress) and pick songs.
const fmtTime = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

// "3 min ago" for the mega bank list (`updated` is epoch seconds)
const ago = (t) => {
  if (!Number.isFinite(t)) return "";
  const s = Math.max(0, Date.now() / 1000 - t);
  if (s < 45) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return new Date(t * 1000).toISOString().slice(0, 10);
};

/** `mega` (the new screen only; the classic screen passes none and gets no list): { open(mid), currentSource(), beforeRemove(mid),
 *  removed(mid, wasOpen) }. The library owns the lists and the requests behind them (GET/POST/PUT/DELETE /api/mega): the screen only
 *  hears `open(mid)` (a real change of mega bank). After every refresh of the list a `choppad:megas` event is sent on `window`. */
export function mountLibrary(root, { onOpen, mega = null }) {
  root.innerHTML = `
    <div class="brand">Chop<span>Pad</span></div>
    <form class="import">
      <input name="url" type="url" placeholder="Paste a YouTube link…" autocomplete="off" required>
      <button type="submit">Import</button>
    </form>
    <label class="file-pick muted small">…or drop an mp3 / wav anywhere, or <u>choose a file</u>
      <input type="file" accept="audio/*,video/*,.mp3,.wav,.flac,.ogg,.m4a,.aiff" multiple hidden></label>
    <div class="jobs"></div>
    <div class="section-h" id="lib-h-songs">${mega ? "Songs" : "Library"}</div>
    <ul class="songs" aria-labelledby="lib-h-songs"></ul>${mega ? `
    <div class="section-h" id="lib-h-megas">Drum pad mega banks</div>
    <ul class="megas" aria-labelledby="lib-h-megas"></ul>
    <button type="button" class="mega-new" aria-label="New drum pad mega bank on the current song">+ New mega bank</button>` : ""}
    <div class="section-h">Kits</div>
    <ul class="kits" style="list-style:none;padding:0;margin:0"></ul>`;
  const form = root.querySelector(".import");
  const jobsEl = root.querySelector(".jobs");
  const list = root.querySelector(".songs");
  const kitList = root.querySelector(".kits");
  const megaList = root.querySelector(".megas");
  let active = null;
  let songs = [];
  let megas = [];
  let activeMega = null;
  let megasReady = null;

  // Kits: 16 pads from any songs. Click one to put it on the open song's current bank.
  async function refreshKits() {
    let kits = [];
    try { kits = await fetch("/api/kits").then((r) => r.json()); } catch { kits = []; }
    if (!Array.isArray(kits)) kits = [];
    kitList.innerHTML = kits.length ? "" : `<li class="empty">No kits yet — “save bank as kit” on a song.</li>`;
    for (const k of kits) {
      const li = document.createElement("li");
      li.className = "kit";
      li.dataset.id = k.id;
      li.innerHTML = `<div class="title">${esc(k.name)}</div>
        <div class="meta">${k.pads} pads · ${k.songs.length} song${k.songs.length === 1 ? "" : "s"} <button class="kit-del" title="delete kit" type="button">×</button></div>`;
      li.addEventListener("click", async (e) => {
        if (e.target.closest(".kit-del")) return;
        const session = window.choppad?.song?.session;
        const meta = li.querySelector(".meta");
        if (!session) { meta.textContent = "open a song first"; return; }
        const r = await session.loadKit(k.id);
        meta.textContent = r.ok ? `loaded on bank ${session.store.get().bank + 1}` : r.error;
      });
      li.querySelector(".kit-del").addEventListener("click", async () => {
        await fetch(`/api/kits/${k.id}`, { method: "DELETE" });
        refreshKits();
      });
      kitList.append(li);
    }
  }
  window.addEventListener("choppad:kits", refreshKits);

  async function refresh() {
    try {
      songs = await fetch("/api/songs").then((r) => r.json());
    } catch {
      songs = [];
    }
    list.innerHTML = songs.length ? "" : `<li class="empty">No songs yet — import one above.</li>`;
    for (const s of songs) {
      const li = document.createElement("li");
      li.className = "song" + (s.id === active ? " active" : "");
      li.dataset.id = s.id;
      li.innerHTML = `<button type="button" class="row-open" aria-label="Chop ${esc(s.title)}"${s.id === active ? ' aria-current="true"' : ""}><span class="title">${esc(s.title)}</span>
        <span class="meta">${s.bpm.toFixed(1)} BPM · ${esc(s.meter)}${s.key ? ` · <span class="key">${esc(s.key.tonic)} ${s.key.mode === "minor" ? "min" : "maj"}</span>` : ""} · ${fmtTime(s.duration_s)} · ${s.banks} banks</span></button>`;
      li.addEventListener("click", () => onOpen(s.id));
      list.append(li);
    }
  }

  // ---- import jobs: every job the server runs is shown from the library stream (GET /api/events, `job` events {job, state, step,
  // progress, song?, error?}); polling GET /api/jobs/<id> is the fallback when there is no stream or it is disconnected ----
  const rows = new Map();    // job id -> { row, fill, stage, finished }
  const mine = new Map();    // jobs this tab started -> label; only those open the song when done
  const seen = new Map();    // job id -> time of the last news (event or poll)
  const last = new Map();    // job id -> latest normalized state
  const norm = (j) => ({ job: j.job, state: j.state, step: j.step ?? j.stage, progress: j.progress ?? 0, song: j.song ?? j.id, error: j.error });
  async function onJob(j) {
    seen.set(j.job, Date.now()); last.set(j.job, j);
    let r = rows.get(j.job);
    const over = j.state === "done" || j.state === "error";
    if (!r) {
      if (over && !mine.has(j.job)) { if (j.state === "done") refresh(); return; }   // somebody else's old news
      const row = document.createElement("div");
      row.className = "job"; row.dataset.job = j.job;
      row.innerHTML = `<div class="job-url"></div><div class="bar" role="progressbar" aria-valuemin="0" aria-valuemax="100"><i></i></div><div class="job-stage"></div>`;
      jobsEl.prepend(row);
      r = { row, fill: row.querySelector(".bar i"), bar: row.querySelector(".bar"), stage: row.querySelector(".job-stage"), url: row.querySelector(".job-url"), finished: false };
      rows.set(j.job, r);
    }
    r.url.textContent = mine.get(j.job) ?? "import";
    const pct = Math.round((j.progress ?? 0) * 100);
    r.fill.style.width = `${pct}%`;
    r.bar.setAttribute("aria-valuenow", String(pct));
    r.stage.textContent = j.state === "error" ? j.error : `${j.step ?? j.state} · ${pct}%`;
    r.row.dataset.state = j.state;
    if (r.finished) return;
    if (j.state === "done") {
      r.finished = true;
      r.row.classList.add("done");
      setTimeout(() => { r.row.remove(); rows.delete(j.job); }, 4000);
      await refresh();
      if (mine.has(j.job) && j.song) onOpen(j.song);
    } else if (j.state === "error") { r.finished = true; r.row.classList.add("error"); }
  }
  let es = null;
  if (typeof EventSource !== "undefined") {
    es = new EventSource("/api/events");
    es.addEventListener("job", (e) => { try { onJob(norm(JSON.parse(e.data))); } catch { /* a malformed event is skipped */ } });
  }
  function addJob(jobId, label) {
    mine.set(jobId, label);
    seen.set(jobId, Date.now());
    if (last.has(jobId)) onJob(last.get(jobId));        // the stream was faster than the POST answer
    else onJob({ job: jobId, state: "queued", step: "queued", progress: 0 });
    const poll = async () => {
      const r = rows.get(jobId);
      if (r?.finished) return;
      // Ask only when the stream cannot tell us: it is missing or not connected. A connected stream that is quiet just
      // means the current step is slow (analysing a long song takes more than a few seconds).
      const streamUp = es && es.readyState === 1;
      if (!streamUp && Date.now() - (seen.get(jobId) ?? 0) > 3000) {
        try { onJob(norm(await fetch(`/api/jobs/${jobId}`).then((x) => x.json()))); } catch { /* try again */ }
      }
      setTimeout(poll, 1000);
    };
    setTimeout(poll, 1000);
  }

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const url = form.url.value.trim();
    if (!url) return;
    const res = await fetch("/api/songs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url }),
    });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) {
      jobsEl.insertAdjacentHTML("afterbegin", `<div class="job error">${esc(body.error || res.status)}</div>`);
      return;
    }
    form.reset();
    addJob(body.job, url);
  });

  // ---- drag & drop / file picker -> POST /api/upload (raw body) -> same import job flow ----
  async function upload(file) {
    const res = await fetch("/api/upload", {
      method: "POST",
      headers: { "X-Filename": encodeURIComponent(file.name), "Content-Type": "application/octet-stream" },
      body: file,
    }).catch((err) => ({ ok: false, json: async () => ({ error: String(err) }) }));
    const body = await res.json().catch(() => ({}));
    if (!res.ok) {
      jobsEl.insertAdjacentHTML("afterbegin", `<div class="job error">${esc(file.name)}: ${esc(body.error || res.status)}</div>`);
      return;
    }
    addJob(body.job, file.name);
  }
  const picker = root.querySelector(".file-pick input");
  picker.addEventListener("change", () => { [...picker.files].forEach(upload); picker.value = ""; });
  const overlay = document.createElement("div");
  overlay.className = "drop-overlay";
  overlay.textContent = "Drop audio to chop it";
  overlay.hidden = true;
  document.body.append(overlay);
  let depth = 0;
  const hasFiles = (e) => [...(e.dataTransfer?.types ?? [])].includes("Files");
  document.addEventListener("dragenter", (e) => { if (hasFiles(e)) { depth++; overlay.hidden = false; } });
  document.addEventListener("dragleave", (e) => { if (hasFiles(e) && --depth <= 0) { depth = 0; overlay.hidden = true; } });
  document.addEventListener("dragover", (e) => { if (hasFiles(e)) e.preventDefault(); });
  document.addEventListener("drop", (e) => {
    if (!hasFiles(e)) return;
    e.preventDefault();
    depth = 0;
    overlay.hidden = true;
    [...e.dataTransfer.files].forEach(upload);
  });

  // ---- drum pad mega banks (GET /api/mega: name, source song, how many pads are used, when it last changed)
  function setActiveMega(mid) {
    activeMega = mid;
    for (const li of megaList?.querySelectorAll(".mega") ?? []) {
      const on = li.dataset.id === mid;
      li.classList.toggle("active", on);
      const b = li.querySelector(".mega-open");
      if (on) b?.setAttribute("aria-current", "true"); else b?.removeAttribute("aria-current");
      const tag = li.querySelector(".mega-tag");
      if (tag) tag.hidden = !on;
    }
  }
  async function refreshMegas() {
    if (!megaList) return [];
    const job = (async () => {
      try {
        const r = await fetch("/api/mega");
        const list = await r.json();
        megas = Array.isArray(list) ? list : [];
      } catch { /* keep the last list */ }
      // a row being renamed or asked about keeps its state: the list is redrawn only when nothing in it is being edited
      if (!megaList.querySelector(".mega.editing, .mega.asking")) drawMegas();
      window.dispatchEvent(new CustomEvent("choppad:megas", { detail: { megas } }));
      return megas;
    })();
    megasReady ??= job;
    return job;
  }
  function drawMegas() {
    megaList.innerHTML = megas.length ? "" : `<li class="empty">No mega banks yet. They are made when you open a song.</li>`;
    for (const m of megas) {
      const li = document.createElement("li");
      li.className = "mega" + (m.id === activeMega ? " active" : "");
      li.dataset.id = m.id;
      const song = m.source ? esc(m.source_title ?? m.source) : "no song yet";
      li.innerHTML = `<button type="button" class="row-open mega-open" title="${esc(m.name)}" aria-label="Open drum pad mega bank ${esc(m.name)}, ${m.source ? "song " + esc(m.source_title ?? m.source) : "no song"}, ${m.used_pads} pads used"${m.id === activeMega ? ' aria-current="true"' : ""}>
          <span class="title">${esc(m.name)}</span>
          <span class="meta"><span class="mega-song">♪ ${song}</span><span class="mega-used">${m.used_pads} pad${m.used_pads === 1 ? "" : "s"} used · ${ago(m.updated)}</span></span></button>
        <span class="mega-tag"${m.id === activeMega ? "" : " hidden"}>OPEN</span>
        <span class="mega-act"><button type="button" class="mega-ren" aria-label="Rename ${esc(m.name)}" title="Rename">✎</button><button type="button" class="mega-del" aria-label="Move ${esc(m.name)} to trash" title="Move to trash">×</button></span>`;
      li.querySelector(".mega-open").addEventListener("click", () => { if (m.id !== activeMega) mega.open(m.id); });
      li.querySelector(".mega-ren").addEventListener("click", () => rename(li, m));
      li.querySelector(".mega-del").addEventListener("click", () => askRemove(li, m));
      megaList.append(li);
    }
  }
  function rename(li, m) {
    li.classList.add("editing");
    const t = li.querySelector(".title");
    const input = document.createElement("input");
    input.type = "text"; input.className = "mega-name"; input.value = m.name; input.maxLength = 80; input.autocomplete = "off";
    input.setAttribute("aria-label", `New name for drum pad mega bank ${m.name}`);
    t.replaceWith(input);
    li.querySelector(".mega-open").disabled = true;
    input.focus(); input.select();
    let done = false;
    const finish = async (save) => {
      if (done) return;
      done = true;
      li.classList.remove("editing");
      const name = input.value.trim();
      if (save && name && name !== m.name) {
        try {
          const r = await fetch(`/api/mega/${m.id}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name }) });
          if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || `HTTP ${r.status}`);
        } catch (err) { say(`Couldn't rename: ${err.message}`); }
      }
      await refreshMegas();
    };
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); finish(true); }
      else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); finish(false).then(() => megaList.querySelector(`.mega[data-id="${m.id}"] .mega-ren`)?.focus()); }
    });
    input.addEventListener("blur", () => finish(true));
  }
  function askRemove(li, m) {
    if (li.classList.contains("asking")) return;
    li.classList.add("asking");
    const row = document.createElement("div");
    row.className = "mega-ask";
    row.setAttribute("role", "group");
    row.setAttribute("aria-label", `Move ${m.name} to trash?`);
    row.innerHTML = `<span>Move “${esc(m.name)}” to trash?</span><span class="mega-ask-b"><button type="button" class="mega-yes">Move to trash</button><button type="button" class="mega-no">Keep it</button></span>`;
    li.append(row);
    const no = row.querySelector(".mega-no");
    no.focus();
    const close = () => { li.classList.remove("asking"); row.remove(); li.querySelector(".mega-del")?.focus(); };
    no.addEventListener("click", close);
    row.addEventListener("keydown", (e) => { if (e.key === "Escape") { e.stopPropagation(); close(); } });
    row.querySelector(".mega-yes").addEventListener("click", async () => {
      const wasOpen = m.id === activeMega;
      try {
        await mega.beforeRemove?.(m.id);
        const r = await fetch(`/api/mega/${m.id}`, { method: "DELETE" });
        if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || `HTTP ${r.status}`);
        say(`“${m.name}” moved to trash (media/_mega/.trash).`);
      } catch (err) { say(`Couldn't move to trash: ${err.message}`); li.classList.remove("asking"); row.remove(); return; }
      li.classList.remove("asking");
      await refreshMegas();
      mega.removed?.(m.id, wasOpen);
    });
  }
  async function createMega({ source = null, name } = {}) {
    const r = await fetch("/api/mega", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ...(source ? { source } : {}), ...(name ? { name } : {}) }) });
    const body = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(body.error || `HTTP ${r.status}`);
    await refreshMegas();
    return body.mega;
  }
  // a line under the mega list: what just happened to a bank (moved to trash, rename failed)
  const note = document.createElement("div");
  note.className = "mega-note muted"; note.setAttribute("role", "status");
  let noteT = 0;
  function say(text) { note.textContent = text; clearTimeout(noteT); noteT = setTimeout(() => { note.textContent = ""; }, 9000); }
  if (mega) {
    megaList.after(note);
    root.querySelector(".mega-new").addEventListener("click", async () => {
      try {
        const m = await createMega({ source: mega.currentSource?.() ?? null });
        say(`New mega bank “${m.name}”.`);
        mega.open(m.id);
      } catch (err) { say(`Couldn't make a mega bank: ${err.message}`); }
    });
    refreshMegas();
  }

  refresh();
  refreshKits();
  return {
    refresh, refreshKits,
    setActive(id) {
      active = id;
      for (const li of list.querySelectorAll(".song")) {
        const on = li.dataset.id === id;
        li.classList.toggle("active", on);
        const b = li.querySelector(".row-open");
        if (on) b?.setAttribute("aria-current", "true"); else b?.removeAttribute("aria-current");
      }
    },
    songInfo: (id) => songs.find((s) => s.id === id) ?? null,
    focusSongs() { (list.querySelector(".song.active .row-open") ?? list.querySelector(".row-open"))?.focus(); },
    // ---- drum pad mega banks
    refreshMegas, megasReady: () => megasReady ?? Promise.resolve(),
    megas: () => megas,
    megaInfo: (mid) => megas.find((m) => m.id === mid) ?? null,
    setActiveMega,
    focusMegas() { (megaList?.querySelector(".mega.active .mega-open") ?? megaList?.querySelector(".mega-open") ?? root.querySelector(".mega-new"))?.focus(); },
    createMega,
  };
}
