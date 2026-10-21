(() => {
  "use strict";

  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const audio = $("#audio");
  const VIEWS = ["home", "search", "kept", "downloads"];
  const ACTIVE = ["queued", "downloading", "converting"];
  const FROM = { results: "From search", history: "Kept", recent: "Recently played" };

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
    // Search sources from /api/sources; `source` is the chip picked for searching.
    sources: [{ key: "yt", name: "YouTube", can_keep: true, codec_label: "AAC" }],
    source: store.get("source", null),
    // Mood/genre tiles; seeded by the server-rendered page, refreshed from /api/tiles.
    tiles: (() => { try { return JSON.parse($("#tiles-data").textContent); } catch { return []; } })(),
  };

  const lists = {
    results: () => state.results,
    history: () => state.history,
    recent: () => store.get("plays", []),
  };

  // ---------- helpers ----------
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const icon = (n, cls = "") => `<svg class="i ${cls}" aria-hidden="true"><use href="#i-${n}"/></svg>`;
  // Direct YouTube URLs, used only for mediaSession artwork (iOS wants absolute, fetchable URLs).
  const thumb = (id) => `https://i.ytimg.com/vi/${id}/mqdefault.jpg`;
  const bigThumb = (id) => `https://i.ytimg.com/vi/${id}/maxresdefault.jpg`;
  // Everything else goes through /api/art: the Navidrome cover for kept tracks, else a redirect to
  // YouTube (size <= 200 -> mqdefault 16:9, < 600 -> hqdefault 4:3 letterboxed, >= 600 -> maxres).
  // Sizes are [not kept, kept]: YouTube art stays <= 200 in square crops so hqdefault's black bars
  // never show; kept tracks are served from Navidrome (no bars), so they get sharper sizes.
  const ART = { small: [200, 480], big: [720, 720], fallback: [200, 480], tile: [320, 320], mini: [160, 320], bg: [160, 160] };
  // encodeURIComponent turns "mc:123" into "mc%3A123", which Flask decodes back before routing.
  const artUrl = (id, size) => `/api/art/${encodeURIComponent(id)}?size=${size}`;
  const artSize = (id, kind) => ART[kind][keepState(id) === "kept" ? 1 : 0];
  // Only YouTube has a smaller second-choice thumbnail; other sources fall back to the app icon.
  const artFallback = (id) => (srcKey(id) === "yt" ? artUrl(id, artSize(id, "fallback")) : "/static/icon.svg");
  const art = (id, big = false) => big
    ? `<img src="${artUrl(id, artSize(id, "big"))}" data-fallback="${artFallback(id)}" alt="" loading="lazy">`
    : `<img src="${artUrl(id, artSize(id, "small"))}" alt="" loading="lazy">`;

  // ---------- sources ----------
  // Ids are "<key>:<native>" except YouTube's bare ones. KNOWN covers sources that are off right now
  // (e.g. a Navidrome track in "recently played" while Navidrome is unconfigured).
  const KNOWN = {
    yt: { key: "yt", name: "YouTube", can_keep: true, codec_label: "AAC" },
    mc: { key: "mc", name: "Monochrome", can_keep: true, codec_label: "FLAC" },
    nd: { key: "nd", name: "Library", can_keep: false, codec_label: "" },
  };
  const srcKey = (id) => (/^[a-z]{2}:/.test(id || "") ? id.slice(0, 2) : "yt");
  const srcInfo = (key) => state.sources.find((x) => x.key === key) || KNOWN[key] || { key, name: key, can_keep: false, codec_label: "" };
  const canKeep = (id) => srcInfo(srcKey(id)).can_keep !== false;
  const currentSource = () => (state.sources.some((x) => x.key === state.source) ? state.source
    : (state.sources.find((x) => x.default) || state.sources[0] || KNOWN.yt).key);
  // " · Monochrome" after a non-YouTube track's subtitle; YouTube is the default, so it stays unlabeled.
  const srcBadge = (t) => (srcKey(t.id) === "yt" ? "" : ` · ${esc(srcInfo(srcKey(t.id)).name)}`);
  const codecLabel = (t) => (t.codec ? String(t.codec).toUpperCase() : srcInfo(srcKey(t.id)).codec_label);

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

  // Warm the server's stream-URL cache; best effort, never blocks or surfaces errors.
  function prefetch(ids) {
    ids = ids.filter(Boolean);
    if (!ids.length) return;
    try {
      fetch("/api/prefetch", {
        method: "POST", keepalive: true,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ids }),
      }).catch(() => {});
    } catch {}
  }

  const navidromeUrl = (path) => `${location.protocol}//${location.hostname}:4533${path}`;
  const cur = () => state.queue[state.index];
  const slim = (t) => ({ id: t.id, title: t.title, channel: t.channel, duration: t.duration, source: t.source || srcKey(t.id), ...(t.codec ? { codec: t.codec } : {}) });

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
    if (srcKey(id) === "nd") return "kept"; // Navidrome results are the library itself
    if (state.history.some((h) => h.id === id)) return "kept";
    if (state.jobs.some((j) => j.video_id === id && ACTIVE.includes(j.status))) return "pending";
    return "none";
  }

  function keepBtn(id, list) {
    const s = keepState(id);
    if (s !== "kept" && !canKeep(id)) return "";
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
    return `<div class="tiles">${state.tiles.map((t) => {
      const id = (t.ids || [])[0];
      return id
        ? `<button class="tile has-art" style="--c:${esc(t.color)}" data-search="${esc(t.query)}"><img src="${artUrl(id, artSize(id, "tile"))}" alt="" loading="lazy"><span>${esc(t.label)}</span></button>`
        : `<button class="tile" style="--c:${esc(t.color)}" data-search="${esc(t.query)}"><span>${esc(t.label)}</span>${icon("note")}</button>`;
    }).join("")}</div>`;
  }

  function row(t, i, list, sub) {
    const playing = cur()?.id === t.id;
    const subtitle = sub ?? `${esc(t.channel)}${t.duration ? ` · ${fmt(t.duration)}` : ""}${srcBadge(t)}`;
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

  // Source picker, shown only when there is more than one enabled source.
  function sourceChips() {
    if (state.sources.length < 2) return "";
    const on = currentSource();
    return `<div class="chips src-chips">${state.sources.map((x) =>
      `<button class="chip${x.key === on ? " on" : ""}" data-source="${esc(x.key)}">${esc(x.name)}</button>`).join("")}</div>`;
  }

  function renderSearch() {
    const el = $("#view-search");
    if (!state.query) {
      const searches = store.get("searches", []);
      el.innerHTML = sourceChips() + (searches.length ? `
        <div class="head-row"><h2 class="section-title">Recent</h2><button class="link-btn" data-clear-searches>Clear</button></div>
        <ul class="rows">${searches.map((q) => `<li class="row"><button class="row-main" data-search="${esc(q)}"><span class="row-art" style="display:grid;place-items:center;color:var(--faint)">${icon("clock")}</span><span class="row-text"><span class="row-t">${esc(q)}</span></span></button></li>`).join("")}</ul>` : "")
        + `<h2 class="section-title">Browse Categories</h2>${tiles()}`;
      return;
    }

    const chips = sourceChips() + `<div class="chips">${[["top", "Top Results"], ["songs", "Songs"], ["kept", "In library"]].map(([k, l]) =>
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
            <div class="top-s">Song · ${esc(t.channel)}${t.duration ? ` · ${fmt(t.duration)}` : ""}${srcBadge(t)}</div>
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

  async function loadTiles() {
    let list;
    try { list = await api("/api/tiles"); } catch { return; }
    if (!Array.isArray(list) || JSON.stringify(list) === JSON.stringify(state.tiles)) return;
    state.tiles = list;
    if (state.view === "home") renderHome();
    else if (state.view === "search" && !state.query) renderSearch();
  }

  async function loadSources() {
    let list;
    try { list = await api("/api/sources"); } catch { return; }
    if (!Array.isArray(list) || !list.length) return;
    state.sources = list;
    if (state.view === "search") renderSearch();
    refreshKeepButtons();
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
      const r = await api(`/api/search?q=${encodeURIComponent(q)}&src=${encodeURIComponent(currentSource())}`);
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
    prefetch([cur()?.id]);
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
    // Resolve the next track once this one is playing, so it doesn't compete for the extractor.
    prefetchNext = state.queue[state.index + 1]?.id || null;
    rememberPlay(t);
    updatePlayer();
    markPlaying();
    setProgress(0, t.duration || 0);
    if ("mediaSession" in navigator) {
      const abs = (u) => new URL(u, location.href).href;
      navigator.mediaSession.metadata = new MediaMetadata({
        title: t.title, artist: t.channel, album: "Reef",
        artwork: srcKey(t.id) === "yt"
          ? [{ src: thumb(t.id), sizes: "320x180", type: "image/jpeg" }, { src: bigThumb(t.id), sizes: "1280x720", type: "image/jpeg" }]
          : [{ src: abs(artUrl(t.id, 600)), sizes: "600x600", type: "image/jpeg" }],
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
    $("#mini-art").src = artUrl(t.id, artSize(t.id, "mini"));
    $("#mini-t").textContent = t.title;
    $("#mini-s").textContent = t.channel;

    const artImg = $("#np-art");
    if (artImg.dataset.id !== t.id) {
      artImg.dataset.id = t.id;
      artImg.dataset.fallback = artFallback(t.id);
      artImg.src = artUrl(t.id, artSize(t.id, "big"));
      $("#np-bg").src = artUrl(t.id, artSize(t.id, "bg"));
    }
    $("#np-t").textContent = t.title;
    $("#np-s").textContent = t.channel;
    $("#np-from").textContent = state.from;
    const yt = srcKey(t.id) === "yt";
    $("#np-yt").style.visibility = yt ? "" : "hidden"; // keeps the header centered
    if (yt) $("#np-yt").href = `https://www.youtube.com/watch?v=${encodeURIComponent(t.id)}`;
    const codec = codecLabel(t);
    $("#np-src").textContent = srcInfo(srcKey(t.id)).name + (codec ? ` · ${codec}` : "");
    $("#np-keep").dataset.keep = t.id;
    $("#np-keep2").dataset.keep = t.id;
    const keepable = canKeep(t.id) || keepState(t.id) === "kept";
    $("#np-keep").hidden = !keepable;
    $("#np-keep2").hidden = !keepable;
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

  let prefetchNext = null;
  function setLoading(on) {
    document.body.classList.toggle("is-loading", on);
    for (const id of ["#mini-play", "#np-play"]) $(id).setAttribute("aria-busy", on ? "true" : "false");
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
  audio.addEventListener("error", () => { setLoading(false); if (audio.getAttribute("src")) toast("Couldn't play this one"); updatePlayState(); });
  audio.addEventListener("loadstart", () => { if (audio.getAttribute("src")) setLoading(true); });
  audio.addEventListener("waiting", () => setLoading(true));
  audio.addEventListener("playing", () => {
    setLoading(false);
    if (prefetchNext) { prefetch([prefetchNext]); prefetchNext = null; }
  });
  audio.addEventListener("pause", () => setLoading(false));
  audio.addEventListener("ended", () => setLoading(false));
  audio.addEventListener("emptied", () => { if (!audio.getAttribute("src")) setLoading(false); });

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
        body: JSON.stringify({ id, title: t.title, channel: t.channel || "" }),
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
    // Only YouTube tracks have a public page; others share "Title – Artist" as text.
    const url = srcKey(t.id) === "yt" ? `https://www.youtube.com/watch?v=${t.id}` : `${t.title}${t.channel ? ` – ${t.channel}` : ""}`;
    try {
      if (navigator.share) { await navigator.share(srcKey(t.id) === "yt" ? { title: t.title, url } : { title: t.title, text: url }); return; }
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
    const t = e.target.closest("[data-keep],[data-play],[data-search],[data-source],[data-home-tab],[data-filter],[data-dismiss],[data-play-all],[data-shuffle-all],[data-close],[data-clear-finished],[data-clear-searches]");
    if (!t) return;
    const d = t.dataset;
    if ("keep" in d) { e.preventDefault(); if (d.keep) keep(d.keep, d.list); return; }
    if ("play" in d) { play(d.list, Number(d.play)); return; }
    if ("search" in d) { runSearch(d.search); return; }
    if ("source" in d) {
      state.source = d.source;
      store.set("source", d.source);
      if (state.query) runSearch(state.query); else renderSearch();
      return;
    }
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
  loadSources();
  loadHealth();
  loadHistory();
  loadTiles();
  pollJobs();
})();
