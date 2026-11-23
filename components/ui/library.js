// Library sidebar: import a YouTube link or a dropped audio file (background job with progress) and pick songs.
const fmtTime = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

export function mountLibrary(root, { onOpen }) {
  root.innerHTML = `
    <div class="brand">Chop<span>Pad</span></div>
    <form class="import">
      <input name="url" type="url" placeholder="Paste a YouTube link…" autocomplete="off" required>
      <button type="submit">Import</button>
    </form>
    <label class="file-pick muted small">…or drop an mp3 / wav anywhere, or <u>choose a file</u>
      <input type="file" accept="audio/*,video/*,.mp3,.wav,.flac,.ogg,.m4a,.aiff" multiple hidden></label>
    <div class="jobs"></div>
    <div class="section-h">Library</div>
    <ul class="songs"></ul>
    <div class="section-h">Kits</div>
    <ul class="kits" style="list-style:none;padding:0;margin:0"></ul>`;
  const form = root.querySelector(".import");
  const jobsEl = root.querySelector(".jobs");
  const list = root.querySelector(".songs");
  const kitList = root.querySelector(".kits");
  let active = null;
  let songs = [];

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
      li.innerHTML = `<div class="title">${esc(s.title)}</div>
        <div class="meta">${s.bpm.toFixed(1)} BPM · ${esc(s.meter)}${s.key ? ` · <span class="key">${esc(s.key.tonic)} ${s.key.mode === "minor" ? "min" : "maj"}</span>` : ""} · ${fmtTime(s.duration_s)} · ${s.banks} banks</div>`;
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

  refresh();
  refreshKits();
  return {
    refresh, refreshKits,
    setActive(id) {
      active = id;
      for (const li of list.querySelectorAll(".song")) li.classList.toggle("active", li.dataset.id === id);
    },
  };
}
