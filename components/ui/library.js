// Library sidebar: import a YouTube link (background job with progress) and pick songs.
const fmtTime = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

export function mountLibrary(root, { onOpen }) {
  root.innerHTML = `
    <div class="brand">Chop<span>Pad</span></div>
    <form class="import">
      <input name="url" type="url" placeholder="Paste a YouTube link…" autocomplete="off" required>
      <button type="submit">Import</button>
    </form>
    <div class="jobs"></div>
    <div class="section-h">Library</div>
    <ul class="songs"></ul>`;
  const form = root.querySelector(".import");
  const jobsEl = root.querySelector(".jobs");
  const list = root.querySelector(".songs");
  let active = null;
  let songs = [];

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
        <div class="meta">${s.bpm.toFixed(1)} BPM · ${esc(s.meter)} · ${fmtTime(s.duration_s)} · ${s.banks} banks</div>`;
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

  refresh();
  return {
    refresh,
    setActive(id) {
      active = id;
      for (const li of list.querySelectorAll(".song")) li.classList.toggle("active", li.dataset.id === id);
    },
  };
}
