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

  function addJob(jobId, url) {
    const row = document.createElement("div");
    row.className = "job";
    row.innerHTML = `<div class="job-url">${esc(url)}</div><div class="bar"><i></i></div><div class="job-stage">queued…</div>`;
    jobsEl.prepend(row);
    const fill = row.querySelector(".bar i");
    const stage = row.querySelector(".job-stage");
    const poll = async () => {
      let j;
      try {
        j = await fetch(`/api/jobs/${jobId}`).then((r) => r.json());
      } catch {
        setTimeout(poll, 1500);
        return;
      }
      fill.style.width = `${Math.round((j.progress ?? 0) * 100)}%`;
      stage.textContent = j.state === "error" ? j.error : j.stage;
      if (j.state === "done") {
        row.classList.add("done");
        setTimeout(() => row.remove(), 4000);
        await refresh();
        onOpen(j.id);
      } else if (j.state === "error") {
        row.classList.add("error");
      } else {
        setTimeout(poll, 700);
      }
    };
    poll();
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
