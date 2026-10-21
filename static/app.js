const $ = (sel) => document.querySelector(sel);
const resultsEl = $("#results");
const jobsEl = $("#jobs");
const audio = $("#audio");
const player = $("#player");

let items = [];
let current = null;
let pollTimer = null;

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function fmtDuration(sec) {
  if (sec == null) return "";
  sec = Math.round(sec);
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
  return (h ? `${h}:${String(m).padStart(2, "0")}` : m) + ":" + String(s).padStart(2, "0");
}

async function api(path, opts) {
  const r = await fetch(path, opts);
  if (r.status === 204) return null;
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(body.error || r.statusText);
  return body;
}

$("#search-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const q = $("#q").value.trim();
  if (!q) return;
  $("#q").blur();
  resultsEl.innerHTML = `<li class="msg">Searching…</li>`;
  try {
    items = await api(`/api/search?q=${encodeURIComponent(q)}`);
    renderResults();
  } catch (err) {
    resultsEl.innerHTML = `<li class="msg err">${esc(err.message)}</li>`;
  }
});

function renderResults() {
  if (!items.length) {
    resultsEl.innerHTML = `<li class="msg">No results.</li>`;
    return;
  }
  resultsEl.innerHTML = items.map((it) => `
    <li data-id="${it.id}" class="${current && current.id === it.id ? "playing" : ""}">
      <img src="${esc(it.thumb)}" alt="" loading="lazy">
      <div class="meta">
        <div class="title">${esc(it.title)}</div>
        <div class="sub">${esc(it.channel)}${it.duration ? ` · ${fmtDuration(it.duration)}` : ""}</div>
      </div>
      <button class="btn add" aria-label="Keep in library">+</button>
    </li>`).join("");
}

resultsEl.addEventListener("click", (e) => {
  const li = e.target.closest("li[data-id]");
  if (!li) return;
  const it = items.find((i) => i.id === li.dataset.id);
  if (!it) return;
  const addBtn = e.target.closest(".add");
  if (addBtn) keep(it, addBtn);
  else play(it);
});

function play(it) {
  current = it;
  player.hidden = false;
  document.body.classList.add("has-player");
  $("#np-thumb").src = it.thumb;
  $("#np-title").textContent = it.title;
  $("#np-channel").textContent = it.channel;
  audio.src = `/api/stream/${it.id}`;
  audio.play().catch(() => {});
  renderResults();
  if ("mediaSession" in navigator) {
    navigator.mediaSession.metadata = new MediaMetadata({ title: it.title, artist: it.channel, artwork: [{ src: it.thumb }] });
  }
}

$("#np-add").addEventListener("click", () => current && keep(current, $("#np-add")));

async function keep(it, btn) {
  btn.disabled = true;
  btn.textContent = "…";
  try {
    await api("/api/download", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: it.id, title: it.title }),
    });
    btn.textContent = "✓";
    pollJobs();
  } catch (err) {
    btn.textContent = "!";
    btn.title = err.message;
    btn.disabled = false;
  }
}

function jobLabel(j) {
  switch (j.status) {
    case "queued": return "Queued";
    case "downloading": return `Downloading ${j.percent}%`;
    case "converting": return "Converting…";
    case "done": return j.rescanned ? "Saved · Navidrome rescanning" : "Saved";
    case "error": return `Error: ${j.error}`;
    default: return j.status;
  }
}

async function pollJobs() {
  clearTimeout(pollTimer);
  try {
    const jobs = await api("/api/jobs");
    $("#downloads").hidden = !jobs.length;
    jobsEl.innerHTML = jobs.map((j) => `
      <li class="job ${j.status}" data-id="${j.id}">
        <div class="meta">
          <div class="title">${esc(j.title || j.video_id)}</div>
          <div class="sub">${esc(jobLabel(j))}</div>
          ${j.status === "downloading" ? `<progress max="100" value="${j.percent}"></progress>` : ""}
        </div>
        ${j.status === "done" || j.status === "error" ? `<button class="btn dismiss" aria-label="Dismiss">×</button>` : ""}
      </li>`).join("");
    const active = jobs.some((j) => ["queued", "downloading", "converting"].includes(j.status));
    if (active) pollTimer = setTimeout(pollJobs, 1500);
  } catch {
    pollTimer = setTimeout(pollJobs, 5000);
  }
}

jobsEl.addEventListener("click", async (e) => {
  const btn = e.target.closest(".dismiss");
  if (!btn) return;
  await fetch(`/api/jobs/${btn.closest("li").dataset.id}`, { method: "DELETE" });
  pollJobs();
});

async function health() {
  const el = $("#nd-status");
  try {
    const h = await api("/api/health");
    el.textContent = h.navidrome.ok ? `Navidrome ${h.navidrome.version}` : `Navidrome: ${h.navidrome.error}`;
    el.className = `pill ${h.navidrome.ok ? "ok" : "bad"}`;
  } catch {
    el.textContent = "Reef offline";
    el.className = "pill bad";
  }
}

health();
pollJobs();
