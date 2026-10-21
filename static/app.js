(() => {
  "use strict";

  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const audio = $("#audio");
  const VIEWS = ["home", "search", "kept", "downloads"];
  const ACTIVE = ["queued", "downloading", "converting"];
  const FROM = { results: "From search", history: "Kept", recent: "Recently played" };
  const TILES = [
    ["Hits", "#d9a90a", "top hits this week"],
    ["Pop", "#e2577f", "pop hits"],
    ["Hip-Hop", "#4f7fe0", "hip hop hits"],
    ["Country", "#d9801c", "country hits"],
    ["Dance", "#22a874", "dance hits"],
    ["Rock", "#d9603f", "rock classics"],
    ["Latin", "#df2f78", "latin hits"],
    ["R&B", "#7a63d6", "r&b hits"],
    ["Chill", "#2f8f9e", "chill lofi"],
    ["Throwback", "#b8336a", "2000s hits"],
  ];

  const store = {
    get(k, d) { try { const v = localStorage.getItem("reef." + k); return v ? JSON.parse(v) : d; } catch { return d; } },
    set(k, v) { try { localStorage.setItem("reef." + k, JSON.stringify(v)); } catch { /* storage unavailable */ } },
  };

  const state = {
    view: "home",
    homeTab: "foryou",
    query: "",
    results: [],
    searching: false,
    searchError: "",
    filter: "top",
    history: [],
    library: null,
    jobs: [],
    queue: [],
    index: -1,
    from: "",
    shuffle: false,
    repeat: "off",
    health: null,
  };

  const lists = {
    results: () => state.results,
    history: () => state.history,
    recent: () => store.get("plays", []),
  };

  // ---------- helpers ----------
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const icon = (n, cls = "") => `<svg class="i ${cls}" aria-hidden="true"><use href="#i-${n}"/></svg>`;
  const thumb = (id) => `https://i.ytimg.com/vi/${id}/mqdefault.jpg`;
  const bigThumb = (id) => `https://i.ytimg.com/vi/${id}/maxresdefault.jpg`;
  const art = (id, big = false) => big
    ? `<img src="${bigThumb(id)}" data-fallback="${thumb(id)}" alt="" loading="lazy">`
    : `<img src="${thumb(id)}" alt="" loading="lazy">`;

  function fmt(sec) {
    if (!isFinite(sec) || sec < 0) sec = 0;
    sec = Math.floor(sec);
    const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
    return (h ? `${h}:${String(m).padStart(2, "0")}` : m) + ":" + String(s).padStart(2, "0");
  }

  function timeAgo(ts) {
    if (!ts) return "";
    const d = Date.now() / 1000 - ts;
    if (d < 60) return "just now";
    if (d < 3600) return `${Math.floor(d / 60)}m ago`;
    if (d < 86400) return `${Math.floor(d / 3600)}h ago`;
    return `${Math.floor(d / 86400)}d ago`;
  }

  function greeting() {
    const h = new Date().getHours();
    return h < 5 ? "Good night" : h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening";
  }

  async function api(path, opts) {
    const r = await fetch(path, opts);
    if (r.status === 204) return null;
    const body = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(body.error || r.statusText || "Request failed");
    return body;
  }

  const navidromeUrl = (path) => `${location.protocol}//${location.hostname}:4533${path}`;
  const cur = () => state.queue[state.index];
  const slim = (t) => ({ id: t.id, title: t.title, channel: t.channel, duration: t.duration });

  // YouTube serves a 120x90 placeholder (sometimes with status 404) when maxres art is missing.
  function fallbackImg(img) {
    if (img.tagName === "IMG" && img.dataset.fallback && img.src !== img.dataset.fallback) img.src = img.dataset.fallback;
  }
  document.addEventListener("error", (e) => fallbackImg(e.target), true);
  document.addEventListener("load", (e) => {
    const img = e.target;
    if (img.tagName === "IMG" && img.dataset.fallback && img.naturalWidth > 0 && img.naturalWidth <= 120) fallbackImg(img);
  }, true);

  // ---------- keep state ----------
  function keepState(id) {
    if (!id) return "none";
    if (state.history.some((h) => h.id === id)) return "kept";
    if (state.jobs.some((j) => j.video_id === id && ACTIVE.includes(j.status))) return "pending";
    return "none";
  }

  function keepBtn(id, list) {
    const s = keepState(id);
    return `<button class="keep ${s}" data-keep="${esc(id)}" data-list="${list}" aria-label="${s === "kept" ? "In your library" : "Keep in library"}">${icon(s === "kept" ? "check" : "plus")}</button>`;
  }

  function refreshKeepButtons() {
    $$("[data-keep]").forEach((b) => {
      const s = keepState(b.dataset.keep);
      b.classList.remove("none", "pending", "kept");
      b.classList.add(s);
      b.setAttribute("aria-label", s === "kept" ? "In your library" : "Keep in library");
      const use = b.querySelector("use");
      if (use) use.setAttribute("href", `#i-${s === "kept" ? "check" : "plus"}`);
    });
  }

  function markPlaying() {
    const id = cur()?.id;
    $$("[data-track]").forEach((el) => {
      const on = el.dataset.track === id;
      el.classList.toggle("playing", on);
      const holder = el.querySelector(".row-art, .card-art");
      const eq = holder?.querySelector(".eq");
      if (on && holder && !eq) holder.insertAdjacentHTML("beforeend", `<span class="eq"><i></i><i></i><i></i></span>`);
      if (!on && eq) eq.remove();
    });
  }

  const eqIfPlaying = (id) => (cur()?.id === id ? `<span class="eq"><i></i><i></i><i></i></span>` : "");

  // ---------- pieces ----------
  function shelf({ title, ic, sub, more, body }) {
    return `<section class="shelf">
      <div class="shelf-head"><h2>${ic ? icon(ic) : ""}${esc(title)}</h2>${more ? `<a class="more" href="${more}" aria-label="See all">${icon("chev-right")}</a>` : ""}</div>
      ${sub ? `<p class="shelf-sub">${esc(sub)}</p>` : ""}
      ${body}
    </section>`;
  }

  function posters(items, list) {
    if (!items.length) {
      return `<div class="scroller"><a class="poster poster-grad" href="#search">
        <span class="poster-glyph">${icon("note")}</span>
        <span class="poster-meta"><span class="eyebrow">Reef</span><span class="t">Find something new</span><span class="s">Search YouTube and keep what you love</span></span>
      </a></div>`;
    }
    return `<div class="scroller">${items.map((t, i) => `
      <button class="poster" data-play="${i}" data-list="${list}" data-track="${esc(t.id)}">
        ${art(t.id, true)}
        <span class="poster-meta"><span class="eyebrow">${i === 0 ? "Last played" : "Played recently"}</span><span class="t">${esc(t.title)}</span><span class="s">${esc(t.channel)}</span></span>
      </button>`).join("")}</div>`;
  }

  function squares(items, list) {
    return `<div class="scroller">${items.map((t, i) => `
      <div class="card" data-track="${esc(t.id)}">
        <button class="card-art" data-play="${i}" data-list="${list}" aria-label="Play ${esc(t.title)}">${art(t.id)}${eqIfPlaying(t.id)}</button>
        <div class="card-t">${esc(t.title)}</div>
        <div class="card-s">${esc(t.channel)}</div>
      </div>`).join("")}</div>`;
  }

  function albumCards(albums) {
    return `<div class="scroller">${albums.map((a) => `
      <a class="card" href="${esc(navidromeUrl(`/app/#/album/${encodeURIComponent(a.id)}/show`))}" target="_blank" rel="noopener">
        <span class="card-art">${a.cover ? `<img src="/api/cover/${encodeURIComponent(a.cover)}?size=320" alt="" loading="lazy">` : ""}</span>
        <div class="card-t">${esc(a.name)}</div>
        <div class="card-s">${esc(a.artist)}${a.year ? ` · ${a.year}` : ""}</div>
      </a>`).join("")}</div>`;
  }

  function tiles() {
    return `<div class="tiles">${TILES.map(([label, c, q]) =>
      `<button class="tile" style="--c:${c}" data-search="${esc(q)}"><span>${esc(label)}</span>${icon("note")}</button>`).join("")}</div>`;
  }

  function row(t, i, list, sub) {
    const playing = cur()?.id === t.id;
    const subtitle = sub ?? `${esc(t.channel)}${t.duration ? ` · ${fmt(t.duration)}` : ""}`;
    return `<li class="row${playing ? " playing" : ""}" data-track="${esc(t.id)}">
      <button class="row-main" data-play="${i}" data-list="${list}">
        <span class="row-art">${art(t.id)}${eqIfPlaying(t.id)}</span>
        <span class="row-text"><span class="row-t">${esc(t.title)}</span><span class="row-s">${subtitle}</span></span>
      </button>
      ${keepBtn(t.id, list)}
    </li>`;
  }

  function emptyState(ic, title, text, action) {
    return `<div class="empty">${icon(ic)}<h3>${esc(title)}</h3><p>${esc(text)}</p>${action || ""}</div>`;
  }

  // ---------- views ----------
  function renderHome() {
    const plays = store.get("plays", []);
    let html = `<h1 class="page-title">${greeting()}</h1>
      <div class="seg" role="tablist">
        <button role="tab" class="${state.homeTab === "foryou" ? "on" : ""}" data-home-tab="foryou">For you</button>
        <button role="tab" class="${state.homeTab === "library" ? "on" : ""}" data-home-tab="library">Library</button>
      </div>`;

    if (state.homeTab === "foryou") {
      html += shelf({ title: "Jump back in", ic: "clock", body: posters(plays, "recent") });
      if (state.history.length) {
        html += shelf({ title: "Recently kept", ic: "sparkle", sub: "Saved to Navidrome by Reef", more: "#kept", body: squares(state.history.slice(0, 15), "history") });
      }
      const searches = store.get("searches", []);
      if (searches.length) {
        html += shelf({ title: "Recent searches", ic: "search", body: `<div class="chips wrap">${searches.map((q) => `<button class="chip" data-search="${esc(q)}">${esc(q)}</button>`).join("")}</div>` });
      }
      html += shelf({ title: "Browse moods & genres", ic: "trend", sub: "Tap one to search YouTube", body: tiles() });
    } else if (state.library === null) {
      html += `<div class="shelf"><div class="scroller">${'<div class="card"><div class="card-art sk"></div></div>'.repeat(4)}</div></div>`;
    } else if (!state.library.length) {
      const ok = state.health?.navidrome?.ok;
      html += emptyState("disc", ok ? "Your library is empty" : "Can't reach Navidrome",
        ok ? "Songs you keep will show up here." : "Open the server sheet (pink button) to see what's wrong.");
    } else {
      html += shelf({ title: "New in your library", ic: "disc", sub: "Latest albums in Navidrome", body: albumCards(state.library.slice(0, 10)) });
      if (state.library.length > 10) html += shelf({ title: "More albums", ic: "note", body: albumCards(state.library.slice(10)) });
      if (state.history.length) html += shelf({ title: "Kept with Reef", ic: "sparkle", more: "#kept", body: squares(state.history.slice(0, 15), "history") });
    }
    $("#view-home").innerHTML = html;
  }

  function renderSearch() {
    const el = $("#view-search");
    if (!state.query) {
      const searches = store.get("searches", []);
      el.innerHTML = (searches.length ? `
        <div class="head-row"><h2 class="section-title">Recent</h2><button class="link-btn" data-clear-searches>Clear</button></div>
        <ul class="rows">${searches.map((q) => `<li class="row"><button class="row-main" data-search="${esc(q)}"><span class="row-art" style="display:grid;place-items:center;color:var(--faint)">${icon("clock")}</span><span class="row-text"><span class="row-t">${esc(q)}</span></span></button></li>`).join("")}</ul>` : "")
        + `<h2 class="section-title">Browse Categories</h2>${tiles()}`;
      return;
    }

    const chips = `<div class="chips">${[["top", "Top Results"], ["songs", "Songs"], ["kept", "In library"]].map(([k, l]) =>
      `<button class="chip${state.filter === k ? " on" : ""}" data-filter="${k}">${l}</button>`).join("")}</div>`;

    if (state.searching) {
      el.innerHTML = chips + `<h2 class="section-title">Top result</h2><div class="sk sk-top"></div><h2 class="section-title">Songs</h2>`
        + `<div class="sk-row"><div class="sk a"></div><div class="sk l"></div></div>`.repeat(5);
      return;
    }
    if (state.searchError) {
      el.innerHTML = chips + emptyState("alert", "Search failed", state.searchError);
      return;
    }
    const res = state.results;
    if (!res.length) {
      el.innerHTML = chips + emptyState("search", "No results", `Nothing found for “${state.query}”.`);
      return;
    }

    let html = chips;
    if (state.filter === "top") {
      const t = res[0];
      html += `<h2 class="section-title">Top result</h2>
        <div class="top-card" data-track="${esc(t.id)}">
          <span class="top-art">${art(t.id)}</span>
          <div class="top-meta">
            <div class="top-t">${esc(t.title)}</div>
            <div class="top-s">Song · ${esc(t.channel)}${t.duration ? ` · ${fmt(t.duration)}` : ""}</div>
            <div class="top-actions">
              <button class="play-round" data-play="0" data-list="results" aria-label="Play">${icon("play", "fill")}</button>
              ${keepBtn(t.id, "results")}
            </div>
          </div>
        </div>`;
      if (res.length > 1) html += `<h2 class="section-title">Songs</h2><ul class="rows">${res.slice(1).map((r, i) => row(r, i + 1, "results")).join("")}</ul>`;
    } else {
      const items = res.map((r, i) => [r, i]).filter(([r]) => state.filter === "songs" || keepState(r.id) === "kept");
      html += items.length
        ? `<ul class="rows" style="margin-top:14px">${items.map(([r, i]) => row(r, i, "results")).join("")}</ul>`
        : emptyState("disc", "None kept yet", "Tap + on a song to keep it in your library.");
    }
    el.innerHTML = html;
  }

  function renderKept() {
    const n = state.history.length;
    let html = `<h1 class="page-title">Kept</h1><p class="page-sub">${n} song${n === 1 ? "" : "s"} saved to Navidrome</p>`;
    if (!n) {
      html += emptyState("disc", "Nothing kept yet", "Search for a song and tap + to save it to your library.",
        `<a class="pill-btn" href="#search">${icon("search")}Search</a>`);
    } else {
      html += `<div class="actions">
          <button class="pill-btn" data-play-all="history">${icon("play", "fill")}Play</button>
          <button class="pill-btn" data-shuffle-all="history">${icon("shuffle")}Shuffle</button>
        </div>
        <ul class="rows" style="margin-top:12px">${state.history.map((t, i) => row(t, i, "history", `${esc(t.channel)} · ${timeAgo(t.kept_at)}`)).join("")}</ul>`;
    }
    $("#view-kept").innerHTML = html;
  }

  function jobLabel(j) {
    switch (j.status) {
      case "queued": return "Waiting…";
      case "downloading": return `Downloading · ${j.percent}%`;
      case "converting": return "Converting to Opus…";
      case "done": return j.rescanned ? "Saved · Navidrome is rescanning" : "Saved to library";
      case "error": return j.error || "Failed";
      default: return j.status;
    }
  }

  function renderDownloads() {
    const jobs = state.jobs;
    const finished = jobs.some((j) => !ACTIVE.includes(j.status));
    let html = `<div class="head-row"><h1 class="page-title">Downloads</h1>${finished ? `<button class="link-btn" data-clear-finished>Clear</button>` : ""}</div>
      <p class="page-sub">Songs being saved to your Navidrome library</p>`;
    if (!jobs.length) {
      html += emptyState("download", "No downloads", "Tap + on any song to keep it.", `<a class="pill-btn" href="#search">${icon("search")}Search</a>`);
    } else {
      html += `<ul class="rows" style="margin-top:14px">${jobs.map((j) => `
        <li class="row job ${j.status}">
          <span class="row-main" style="cursor:default">
            <span class="row-art">${art(j.video_id)}</span>
            <span class="row-text">
              <span class="row-t">${esc(j.title || j.video_id)}</span>
              <span class="row-s">${esc(jobLabel(j))}</span>
              ${j.status === "downloading" ? `<span class="bar"><i style="width:${j.percent}%"></i></span>` : ""}
            </span>
          </span>
          ${ACTIVE.includes(j.status)
            ? `<span class="keep pending" aria-label="Working">${icon("plus")}</span>`
            : `<button class="icon-btn sm" data-dismiss="${esc(j.id)}" aria-label="Dismiss">${icon("x")}</button>`}
        </li>`).join("")}</ul>`;
    }
    $("#view-downloads").innerHTML = html;
  }

  const RENDER = { home: renderHome, search: renderSearch, kept: renderKept, downloads: renderDownloads };

  function renderBadges() {
    const n = state.jobs.filter((j) => ACTIVE.includes(j.status)).length;
    for (const el of [$("#tab-badge"), $("#hdr-badge")]) {
      el.hidden = !n;
      el.textContent = n;
    }
  }

  // ---------- routing ----------
  function route() {
    const v = location.hash.slice(1);
    const view = VIEWS.includes(v) ? v : "home";
    const changed = view !== state.view;
    state.view = view;
    document.body.dataset.view = view;
    $$(".view").forEach((s) => { s.hidden = s.id !== `view-${view}`; });
    $$(".tab").forEach((t) => t.classList.toggle("active", t.dataset.view === view));
    if (view === "home" && state.homeTab === "library" && state.library === null) loadLibrary();
    RENDER[view]();
    if (changed) window.scrollTo(0, 0);
    if ($("#np").classList.contains("open") && changed) closeNP();
  }

  // ---------- data ----------
  async function loadHistory() {
    try { state.history = await api("/api/history"); } catch { /* keep previous */ }
    if (state.view !== "search") RENDER[state.view]();
    refreshKeepButtons();
    updatePlayer();
  }

  async function loadLibrary() {
    try { state.library = await api("/api/library/recent"); } catch { state.library = []; }
    if (state.view === "home") renderHome();
  }

  async function loadHealth() {
    try { state.health = await api("/api/health"); } catch { state.health = null; }
    const dot = $("#status-dot");
    dot.className = "dot " + (state.health?.navidrome?.ok ? "ok" : "bad");
  }

  let pollTimer;
  async function pollJobs() {
    clearTimeout(pollTimer);
    try {
      const jobs = await api("/api/jobs");
      const prev = new Map(state.jobs.map((j) => [j.id, j.status]));
      state.jobs = jobs;
      const done = jobs.filter((j) => j.status === "done" && prev.has(j.id) && prev.get(j.id) !== "done");
      const failed = jobs.filter((j) => j.status === "error" && prev.has(j.id) && prev.get(j.id) !== "error");
      if (done.length) {
        toast(done.length === 1 ? `Saved “${done[0].title}”` : `Saved ${done.length} songs`);
        await loadHistory();
        if (state.homeTab === "library") loadLibrary();
      }
      if (failed.length) toast(`Couldn't keep “${failed[0].title}”`);
      refreshKeepButtons();
      renderBadges();
      if (state.view === "downloads") renderDownloads();
      if (jobs.some((j) => ACTIVE.includes(j.status))) pollTimer = setTimeout(pollJobs, 1500);
    } catch {
      pollTimer = setTimeout(pollJobs, 5000);
    }
  }

  // ---------- search ----------
  let searchSeq = 0;
  async function runSearch(q) {
    q = String(q || "").trim();
    if (!q) return;
    state.query = q;
    state.searching = true;
    state.searchError = "";
    state.filter = "top";
    $("#q").value = q;
    $("#q-clear").hidden = false;
    $("#q").blur();
    store.set("searches", [q, ...store.get("searches", []).filter((s) => s.toLowerCase() !== q.toLowerCase())].slice(0, 8));
    if (location.hash !== "#search") location.hash = "#search"; else renderSearch();
    const seq = ++searchSeq;
    try {
      const r = await api(`/api/search?q=${encodeURIComponent(q)}`);
      if (seq !== searchSeq) return;
      state.results = r;
    } catch (e) {
      if (seq !== searchSeq) return;
      state.results = [];
      state.searchError = e.message;
    }
    state.searching = false;
    if (state.view === "search") renderSearch();
  }

  // ---------- player ----------
  function play(listName, i, { shuffle = false } = {}) {
    const list = lists[listName]?.();
    if (!list || !list.length) return;
    state.queue = list.map(slim);
    state.from = FROM[listName] || "";
    if (shuffle) {
      state.shuffle = true;
      i = Math.floor(Math.random() * list.length);
    }
    state.index = Math.min(Math.max(i, 0), list.length - 1);
    load();
  }

  function rememberPlay(t) {
    store.set("plays", [slim(t), ...store.get("plays", []).filter((p) => p.id !== t.id)].slice(0, 12));
  }

  function load() {
    const t = cur();
    if (!t) return;
    audio.src = `/api/stream/${encodeURIComponent(t.id)}`;
    audio.play().catch(() => {});
    rememberPlay(t);
    updatePlayer();
    markPlaying();
    setProgress(0, t.duration || 0);
    if ("mediaSession" in navigator) {
      navigator.mediaSession.metadata = new MediaMetadata({
        title: t.title, artist: t.channel, album: "Reef",
        artwork: [{ src: thumb(t.id), sizes: "320x180", type: "image/jpeg" }, { src: bigThumb(t.id), sizes: "1280x720", type: "image/jpeg" }],
      });
    }
  }

  function next() {
    const n = state.queue.length;
    if (!n) return;
    let i;
    if (state.shuffle && n > 1) {
      do { i = Math.floor(Math.random() * n); } while (i === state.index);
    } else {
      i = state.index + 1;
    }
    if (i >= n) {
      if (state.repeat === "all") i = 0;
      else { audio.pause(); audio.currentTime = 0; return; }
    }
    state.index = i;
    load();
  }

  function prev() {
    if (audio.currentTime > 3 || state.index <= 0) { audio.currentTime = 0; return; }
    state.index -= 1;
    load();
  }

  function togglePlay() {
    if (!cur()) return;
    if (audio.paused) audio.play().catch(() => {}); else audio.pause();
  }

  function updatePlayer() {
    const t = cur();
    const mini = $("#mini");
    if (!t) { mini.hidden = true; document.body.classList.remove("has-mini"); return; }
    mini.hidden = false;
    document.body.classList.add("has-mini");
    $("#mini-art").src = thumb(t.id);
    $("#mini-t").textContent = t.title;
    $("#mini-s").textContent = t.channel;

    const artImg = $("#np-art");
    if (artImg.dataset.id !== t.id) {
      artImg.dataset.id = t.id;
      artImg.dataset.fallback = thumb(t.id);
      artImg.src = bigThumb(t.id);
      $("#np-bg").src = thumb(t.id);
    }
    $("#np-t").textContent = t.title;
    $("#np-s").textContent = t.channel;
    $("#np-from").textContent = state.from;
    $("#np-yt").href = `https://www.youtube.com/watch?v=${encodeURIComponent(t.id)}`;
    $("#np-keep").dataset.keep = t.id;
    $("#np-keep2").dataset.keep = t.id;
    refreshKeepButtons();
    const s = keepState(t.id);
    $("#np-keep2").classList.toggle("kept", s === "kept");
    $("#np-keep2").classList.toggle("pending", s === "pending");
    updateModes();
  }

  function updatePlayState() {
    const playing = !audio.paused && !audio.ended;
    document.body.classList.toggle("is-playing", playing);
    for (const id of ["#mini-play", "#np-play"]) {
      const b = $(id);
      b.querySelector("use").setAttribute("href", playing ? "#i-pause" : "#i-play");
      b.setAttribute("aria-label", playing ? "Pause" : "Play");
    }
    if ("mediaSession" in navigator) navigator.mediaSession.playbackState = playing ? "playing" : "paused";
  }

  function updateModes() {
    $("#np-shuffle").classList.toggle("on", state.shuffle);
    const r = $("#np-repeat");
    r.classList.toggle("on", state.repeat !== "off");
    r.classList.toggle("one", state.repeat === "one");
    audio.loop = state.repeat === "one";
  }

  let seeking = false;
  function duration() {
    return isFinite(audio.duration) && audio.duration > 0 ? audio.duration : (cur()?.duration || 0);
  }
  function setProgress(t, d) {
    const p = d ? Math.min(100, (t / d) * 100) : 0;
    $("#mini-bar").style.width = p + "%";
    if (!seeking) {
      const seek = $("#np-seek");
      seek.value = Math.round(p * 10);
      seek.style.setProperty("--p", p + "%");
      $("#np-cur").textContent = fmt(t);
      $("#np-rem").textContent = "-" + fmt(Math.max(0, d - t));
    }
  }

  audio.addEventListener("timeupdate", () => setProgress(audio.currentTime, duration()));
  audio.addEventListener("loadedmetadata", () => setProgress(audio.currentTime, duration()));
  audio.addEventListener("play", updatePlayState);
  audio.addEventListener("pause", updatePlayState);
  audio.addEventListener("ended", () => { updatePlayState(); next(); });
  audio.addEventListener("error", () => { if (audio.getAttribute("src")) toast("Couldn't play this one"); updatePlayState(); });

  const seek = $("#np-seek");
  seek.addEventListener("input", () => {
    seeking = true;
    const p = seek.value / 10;
    seek.style.setProperty("--p", p + "%");
    const d = duration();
    $("#np-cur").textContent = fmt((p / 100) * d);
    $("#np-rem").textContent = "-" + fmt(d - (p / 100) * d);
  });
  seek.addEventListener("change", () => {
    const d = duration();
    if (d) audio.currentTime = (seek.value / 1000) * d;
    seeking = false;
  });

  if ("mediaSession" in navigator) {
    const ms = navigator.mediaSession;
    const set = (a, f) => { try { ms.setActionHandler(a, f); } catch { /* unsupported action */ } };
    set("play", () => audio.play());
    set("pause", () => audio.pause());
    set("previoustrack", prev);
    set("nexttrack", next);
    set("seekto", (e) => { if (e.seekTime != null) audio.currentTime = e.seekTime; });
  }

  // ---------- now playing sheet ----------
  function openNP() {
    if (!cur()) return;
    const np = $("#np");
    np.classList.add("open");
    np.setAttribute("aria-hidden", "false");
    document.documentElement.classList.add("lock");
  }
  function closeNP() {
    const np = $("#np");
    np.classList.remove("open");
    np.style.transform = "";
    np.setAttribute("aria-hidden", "true");
    document.documentElement.classList.remove("lock");
  }

  (function swipeToClose() {
    const np = $("#np");
    let y0 = null, dy = 0;
    for (const zone of [$("#np-grab"), $(".np-art-wrap")]) {
      zone.addEventListener("touchstart", (e) => { y0 = e.touches[0].clientY; dy = 0; np.style.transition = "none"; }, { passive: true });
      zone.addEventListener("touchmove", (e) => {
        if (y0 == null) return;
        dy = Math.max(0, e.touches[0].clientY - y0);
        np.style.transform = `translateY(${dy}px)`;
      }, { passive: true });
      zone.addEventListener("touchend", () => {
        np.style.transition = "";
        if (dy > 110) closeNP(); else np.style.transform = "";
        y0 = null;
      });
    }
  })();

  // ---------- keep ----------
  async function keep(id, listName) {
    const s = keepState(id);
    if (s === "kept") { toast("Already in your library"); return; }
    if (s === "pending") { location.hash = "#downloads"; return; }
    const t = (lists[listName]?.() || []).find((x) => x.id === id) || state.queue.find((x) => x.id === id) || { id, title: "" };
    try {
      const job = await api("/api/download", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id, title: t.title }),
      });
      state.jobs.unshift(job);
      refreshKeepButtons();
      renderBadges();
      updatePlayer();
      toast(`Keeping “${t.title || "song"}”`);
      pollJobs();
    } catch (e) {
      toast(e.message);
    }
  }

  // ---------- share ----------
  async function share() {
    const t = cur();
    if (!t) return;
    const url = `https://www.youtube.com/watch?v=${t.id}`;
    try {
      if (navigator.share) { await navigator.share({ title: t.title, url }); return; }
      if (navigator.clipboard && window.isSecureContext) { await navigator.clipboard.writeText(url); toast("Link copied"); return; }
    } catch (e) {
      if (e && e.name === "AbortError") return;
    }
    const ta = document.createElement("textarea");
    ta.value = url;
    ta.setAttribute("readonly", "");
    ta.style.cssText = "position:fixed;opacity:0;top:0";
    document.body.appendChild(ta);
    ta.select();
    ta.setSelectionRange(0, url.length);
    let ok = false;
    try { ok = document.execCommand("copy"); } catch { ok = false; }
    ta.remove();
    toast(ok ? "Link copied" : url);
  }

  // ---------- status sheet ----------
  function renderStatus() {
    const h = state.health;
    const nd = h?.navidrome;
    const rows = [
      ["Reef", h ? "Running" : "Unreachable", h ? "ok" : "bad"],
      ["Navidrome", nd?.ok ? `Connected · ${nd.version}` : (nd?.error || "Unknown"), nd?.ok ? "ok" : "bad"],
      ["Saves to", h?.download_dir || "—", ""],
      ["yt-dlp", h?.ytdlp || "—", ""],
      ["Web player", navidromeUrl("/app"), ""],
    ];
    $("#status-list").innerHTML = rows.map(([k, v, c]) => `<li><span class="k">${esc(k)}</span><span class="v ${c}">${esc(v)}</span></li>`).join("");
  }
  async function openStatus() {
    const w = $("#status-sheet");
    renderStatus();
    w.hidden = false;
    requestAnimationFrame(() => requestAnimationFrame(() => w.classList.add("open")));
    await loadHealth();
    renderStatus();
  }
  function closeStatus() {
    const w = $("#status-sheet");
    w.classList.remove("open");
    setTimeout(() => { w.hidden = true; }, 380);
  }

  // ---------- toast ----------
  let toastTimer;
  function toast(msg) {
    const el = $("#toast");
    el.textContent = msg;
    el.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.classList.remove("show"), 2600);
  }

  // ---------- events ----------
  document.addEventListener("click", (e) => {
    const t = e.target.closest("[data-keep],[data-play],[data-search],[data-home-tab],[data-filter],[data-dismiss],[data-play-all],[data-shuffle-all],[data-close],[data-clear-finished],[data-clear-searches]");
    if (!t) return;
    const d = t.dataset;
    if ("keep" in d) { e.preventDefault(); if (d.keep) keep(d.keep, d.list); return; }
    if ("play" in d) { play(d.list, Number(d.play)); return; }
    if ("search" in d) { runSearch(d.search); return; }
    if ("homeTab" in d) {
      state.homeTab = d.homeTab;
      if (state.homeTab === "library" && state.library === null) loadLibrary();
      renderHome();
      return;
    }
    if ("filter" in d) { state.filter = d.filter; renderSearch(); return; }
    if ("dismiss" in d) {
      fetch(`/api/jobs/${encodeURIComponent(d.dismiss)}`, { method: "DELETE" }).finally(pollJobs);
      state.jobs = state.jobs.filter((j) => j.id !== d.dismiss);
      renderDownloads();
      return;
    }
    if ("clearFinished" in d) {
      const ids = state.jobs.filter((j) => !ACTIVE.includes(j.status)).map((j) => j.id);
      state.jobs = state.jobs.filter((j) => ACTIVE.includes(j.status));
      renderDownloads();
      Promise.all(ids.map((id) => fetch(`/api/jobs/${encodeURIComponent(id)}`, { method: "DELETE" }))).finally(pollJobs);
      return;
    }
    if ("clearSearches" in d) { store.set("searches", []); renderSearch(); return; }
    if ("playAll" in d) { state.shuffle = false; play(d.playAll, 0); return; }
    if ("shuffleAll" in d) { play(d.shuffleAll, 0, { shuffle: true }); return; }
    if ("close" in d) { closeStatus(); }
  });

  $("#search-bar").addEventListener("submit", (e) => { e.preventDefault(); runSearch($("#q").value); });
  $("#q").addEventListener("input", () => { $("#q-clear").hidden = !$("#q").value; });
  $("#q-clear").addEventListener("click", () => {
    $("#q").value = "";
    $("#q-clear").hidden = true;
    state.query = "";
    state.results = [];
    searchSeq++;
    renderSearch();
    $("#q").focus();
  });

  $("#mini-open").addEventListener("click", openNP);
  $("#mini-play").addEventListener("click", togglePlay);
  $("#mini-next").addEventListener("click", next);
  $("#np-close").addEventListener("click", closeNP);
  $("#np-play").addEventListener("click", togglePlay);
  $("#np-next").addEventListener("click", next);
  $("#np-prev").addEventListener("click", prev);
  $("#np-share").addEventListener("click", share);
  $("#np-share2").addEventListener("click", share);
  $("#np-dl").addEventListener("click", closeNP);
  $("#np-shuffle").addEventListener("click", () => { state.shuffle = !state.shuffle; updateModes(); });
  $("#np-repeat").addEventListener("click", () => {
    state.repeat = { off: "all", all: "one", one: "off" }[state.repeat];
    updateModes();
  });
  $("#status-btn").addEventListener("click", openStatus);
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    if (!$("#status-sheet").hidden) closeStatus();
    else if ($("#np").classList.contains("open")) closeNP();
  });

  const top = $("#top");
  window.addEventListener("scroll", () => top.classList.toggle("scrolled", window.scrollY > 4), { passive: true });
  window.addEventListener("hashchange", route);

  // ---------- boot ----------
  state.view = "";
  route();
  loadHealth();
  loadHistory();
  pollJobs();
})();
