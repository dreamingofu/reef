(() => {
  "use strict";

  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const audio = $("#audio");
  const VIEWS = ["home", "search", "kept", "downloads"];
  const ACTIVE = ["queued", "downloading", "converting"];
  const FROM = { results: "From search", history: "Kept", recent: "Recently played" };
  const AUTO_LIMIT = 20; // autoplay tracks appended per radio fetch
  const PAGE_TTL = 5 * 60 * 1000; // browse pages (album, artist, playlist, moods) stay in memory this long
  // Ids the server accepts for artist / album pages; anything else isn't linked.
  const ARTIST_ID = /^UC[\w-]{22}$/;
  const ALBUM_ID = /^(?:MPRE|OLAK5uy_)[\w-]{6,60}$/;

  const store = {
    get(k, d) { try { const v = localStorage.getItem("reef." + k); return v ? JSON.parse(v) : d; } catch { return d; } },
    set(k, v) { try { localStorage.setItem("reef." + k, JSON.stringify(v)); } catch { /* storage unavailable */ } },
  };

  const state = {
    view: "home",
    parent: "home", // the main view a detail page (album, artist, ...) was opened from: its tab and bottom bar stay
    homeTab: "foryou",
    query: "",
    results: [],
    searching: false,
    searchError: "",
    link: null, // the current search is a pasted link: { kind: "track" | "playlist", title }
    filter: "top",
    history: [],
    library: null,
    jobs: [],
    queue: [],
    index: -1,
    from: "",
    shuffle: false,
    original: null, // play order before shuffling (snapshot restored when shuffle goes off)
    repeat: "off",
    health: null,
    blocked: null, // hidden artists from /api/blocklist (null until loaded)
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
    page: () => pageTracks(), // the open album / playlist / artist page
  };

  // ---------- helpers ----------
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const icon = (n, cls = "") => `<svg class="i ${cls}" aria-hidden="true"><use href="#i-${n}"/></svg>`;
  // Direct YouTube URLs, used only for mediaSession artwork (iOS wants absolute, fetchable URLs).
  const thumb = (id) => `https://i.ytimg.com/vi/${id}/mqdefault.jpg`;
  const bigThumb = (id) => `https://i.ytimg.com/vi/${id}/maxresdefault.jpg`;
  // Everything else goes through /api/art: the Navidrome cover for kept tracks; else, for a YouTube id
  // the server has seen in a YT Music response (search, radio, link), square art at exactly `size`;
  // else a redirect to YouTube (size <= 200 -> mqdefault 16:9, < 600 -> hqdefault 4:3 letterboxed,
  // >= 600 -> maxres). Sizes are [not kept, kept]: the client can't tell whether the square art is
  // known (the server forgets it on restart), so non-kept art stays <= 200 in square crops so
  // hqdefault's black bars never show; square art at 200 is still sharp enough for rows and cards.
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
  const xBadge = (t) => (t.explicit ? `<span class="xbadge" role="img" aria-label="Explicit">E</span>` : "");
  // "[E] Artist · Album · 3:45 · Source": the subtitle of search / link / queue rows.
  const trackSub = (t) => xBadge(t) + [artistLinks(t), t.album ? esc(t.album) : "", t.duration ? fmt(t.duration) : ""]
    .filter(Boolean).join(" · ") + srcBadge(t);
  // A track's artists that have a page ([{name, id}]).
  const linkable = (t) => (Array.isArray(t?.artists) ? t.artists : []).filter((a) => a && a.name && ARTIST_ID.test(a.id || ""));
  // The artist names, each one with a page as its own link (rows play on tap; the link sits above that).
  function artistLinks(t) {
    const all = Array.isArray(t.artists) ? t.artists.filter((a) => a && a.name) : [];
    if (!all.length || !linkable(t).length) return esc(t.channel);
    return all.map((a) => (ARTIST_ID.test(a.id || "")
      ? `<a class="row-link" href="#artist/${encodeURIComponent(a.id)}">${esc(a.name)}</a>` : esc(a.name))).join(", ");
  }
  // "1 hr 2 min" / "45 min" ("" for 0).
  function fmtLen(total) {
    const mins = Math.round((Number(total) || 0) / 60);
    if (!total) return "";
    return mins >= 60 ? `${Math.floor(mins / 60)} hr ${mins % 60} min` : `${Math.max(1, mins)} min`;
  }
  const songs = (n) => `${n} song${n === 1 ? "" : "s"}`;

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
    if (!r.ok) throw Object.assign(new Error(body.error || r.statusText || "Request failed"), { status: r.status, body });
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

  // ---------- queue core ----------
  // Pure functions over [track] arrays: no DOM, no state, inputs never mutated. Tracks are matched by id.
  // Unit-checked from node by slicing the text between these two sentinel comments.
  // ==== queue-core:start ====
  const Q = (() => {
    function shuffled(list, rand = Math.random) {
      const a = list.slice();
      for (let i = a.length - 1; i > 0; i--) {
        const j = Math.floor(rand() * (i + 1));
        [a[i], a[j]] = [a[j], a[i]];
      }
      return a;
    }
    // Shuffle only what comes after the current track; `original` is the snapshot unshuffle() restores.
    // Autoplay items (t.auto) aren't shuffled: they stay a block at the end, in their order.
    function shuffleUpcoming(queue, index, rand) {
      const head = queue.slice(0, index + 1), up = queue.slice(index + 1);
      return { queue: head.concat(shuffled(up.filter((t) => !t.auto), rand), up.filter((t) => t.auto)), original: queue.slice() };
    }
    // Start a fresh shuffled queue at list[index]: it plays first, everything else follows in random order.
    function shuffleFrom(list, index, rand) {
      const others = list.filter((_, k) => k !== index);
      return { queue: [list[index], ...shuffled(others, rand)], index: 0, original: list.slice() };
    }
    // Back to the snapshot order, keeping the current track current. Tracks removed while shuffled stay
    // removed; tracks added while shuffled are appended in their queue order.
    function unshuffle(queue, index, original) {
      const current = queue[index];
      const inQueue = new Set(queue.map((t) => t.id));
      const base = (original || []).filter((t) => inQueue.has(t.id));
      const inBase = new Set(base.map((t) => t.id));
      const out = base.concat(queue.filter((t) => !inBase.has(t.id)));
      const i = current ? out.findIndex((t) => t.id === current.id) : -1;
      return { queue: out, index: i >= 0 ? i : Math.min(index, out.length - 1) };
    }
    // Upcoming copy of `id` (after the current track), or -1.
    const upcomingAt = (queue, index, id) => queue.findIndex((t, k) => k > index && t.id === id);
    // Right after the current track; a copy already upcoming is moved rather than duplicated.
    function insertNext(queue, index, track) {
      const q = queue.slice();
      const at = upcomingAt(q, index, track.id);
      if (at >= 0) q.splice(at, 1);
      q.splice(index + 1, 0, track);
      return q;
    }
    // Play next while shuffled: insert into the queue and into the snapshot too, right after the current
    // track in each, so it stays next when shuffle goes off (Apple Music behaviour). "Add to queue" leaves
    // the snapshot alone, so those tracks end up at the end. If the current track isn't in the snapshot
    // (it was itself added while shuffled), the snapshot is left alone: unshuffle() then appends the
    // current track and this one together, in queue order, so it still follows directly.
    function playNext(queue, index, track, original) {
      const q = insertNext(queue, index, track);
      if (!original) return { queue: q, original };
      const p = queue[index] ? original.findIndex((t) => t.id === queue[index].id) : -1;
      return { queue: q, original: p >= 0 ? insertNext(original, p, track) : original.slice() };
    }
    // At the end of what the user queued: before the first upcoming autoplay item, else at the very end.
    // A copy already upcoming (autoplay or not) is moved there.
    function append(queue, index, track) {
      const q = queue.slice();
      const at = upcomingAt(q, index, track.id);
      if (at >= 0) q.splice(at, 1);
      const a = q.findIndex((t, k) => k > index && t.auto);
      if (a >= 0) q.splice(a, 0, track); else q.push(track);
      return q;
    }
    // Append the tracks not already anywhere in the queue (by id, deduped among themselves too), at most
    // `limit` of them; `auto` marks them as autoplay items ({...t, auto: true}).
    function appendNew(queue, tracks, { limit = Infinity, auto = false } = {}) {
      const seen = new Set(queue.map((t) => t.id));
      const out = queue.slice();
      for (const t of tracks || []) {
        if (out.length - queue.length >= limit) break;
        if (!t || !t.id || seen.has(t.id)) continue;
        seen.add(t.id);
        out.push(auto ? { ...t, auto: true } : t);
      }
      return out;
    }
    // Drop the upcoming autoplay items (autoplay turned off); history and the current track stay.
    const stripAuto = (queue, index) => queue.filter((t, k) => k <= index || !t.auto);
    // Upcoming tracks split for display: [manual, autoplay block]. `at` = queue index of the block's first item.
    function upcomingParts(queue, index) {
      const up = queue.slice(index + 1);
      const a = up.findIndex((t) => t.auto);
      return a < 0 ? { manual: up, auto: [], at: -1 } : { manual: up.slice(0, a), auto: up.slice(a), at: index + 1 + a };
    }
    function removeAt(queue, index, i) {
      if (!(i >= 0 && i < queue.length)) return { queue: queue.slice(), index };
      const q = queue.slice();
      q.splice(i, 1);
      // Removing the current track makes the one after it current (or the new last one).
      return { queue: q, index: i < index ? index - 1 : i === index ? Math.min(index, q.length - 1) : index };
    }
    const clearUpcoming = (queue, index) => queue.slice(0, index + 1);
    // -1 = nothing to advance to (stop). Repeat-one loops in the <audio> element; a manual skip still moves on.
    function nextIndex(len, index, repeat) {
      if (!len) return -1;
      if (index + 1 < len) return index + 1;
      return repeat === "all" ? 0 : -1;
    }
    // -1 = restart the current track.
    const prevIndex = (len, index) => (len && index > 0 ? Math.min(index, len) - 1 : -1);
    return { shuffled, shuffleUpcoming, shuffleFrom, unshuffle, insertNext, playNext, append, appendNew, stripAuto, upcomingParts, removeAt, clearUpcoming, nextIndex, prevIndex };
  })();
  // ==== queue-core:end ====

  const navidromeUrl = (path) => `${location.protocol}//${location.hostname}:4533${path}`;
  const cur = () => state.queue[state.index];
  // What a queued / remembered track keeps (queue, session, "plays"). `auto` (an autoplay item) is dropped
  // here; only the live queue and its saved session keep it, via qslim.
  const slim = (t) => ({
    id: t.id, title: t.title, channel: t.channel, duration: t.duration, source: t.source || srcKey(t.id),
    ...(t.codec ? { codec: t.codec } : {}), ...(t.album ? { album: t.album } : {}), ...(t.album_id ? { album_id: t.album_id } : {}),
    ...(Array.isArray(t.artists) && t.artists.length ? { artists: t.artists.map((a) => ({ name: a?.name, id: a?.id ?? null })) } : {}),
    ...(t.explicit ? { explicit: true } : {}),
  });
  const qslim = (t) => (t.auto ? { ...slim(t), auto: true } : slim(t));

  // YouTube serves a 120x90 placeholder (sometimes with status 404) when maxres art is missing.
  function fallbackImg(img) {
    if (img.tagName !== "IMG") return;
    // Browse art (mood tiles, covers) that fails just goes: the tile's gradient / the empty frame shows instead.
    if ("drop" in img.dataset) { img.parentElement?.classList.remove("has-art"); img.remove(); return; }
    if (img.dataset.fallback && img.src !== img.dataset.fallback) img.src = img.dataset.fallback;
  }
  document.addEventListener("error", (e) => fallbackImg(e.target), true);
  document.addEventListener("load", (e) => {
    const img = e.target;
    if (img.tagName === "IMG" && img.dataset.fallback && img.naturalWidth > 0 && img.naturalWidth <= 120) fallbackImg(img);
  }, true);

  // ---------- keep state ----------
  // /api/history is only the latest 50 keeps; lists from the server also mark `kept`, so older keeps count too.
  const keptIds = new Set();
  function noteKept(tracks) {
    for (const t of tracks || []) {
      if (!t || !t.id || typeof t.kept !== "boolean") continue;
      if (t.kept) keptIds.add(t.id); else keptIds.delete(t.id);
    }
  }
  function keepState(id) {
    if (!id) return "none";
    if (srcKey(id) === "nd") return "kept"; // Navidrome results are the library itself
    if (state.history.some((h) => h.id === id) || keptIds.has(id)) return "kept";
    if (state.jobs.some((j) => j.video_id === id && ACTIVE.includes(j.status))) return "pending";
    return "none";
  }

  function keepBtn(id, list) {
    const s = keepState(id);
    if (s !== "kept" && !canKeep(id)) return "";
    return `<button class="keep ${s}" data-keep="${esc(id)}" data-list="${list}" aria-label="${s === "kept" ? "In your library" : "Keep in library"}">${icon(s === "kept" ? "check" : "plus")}</button>`;
  }

  const moreBtn = (t, i, list) => `<button class="icon-btn sm more-btn" data-more="${esc(t.id)}" data-i="${i}" data-list="${list}" aria-label="More for ${esc(t.title)}">${icon("more")}</button>`;

  function refreshKeepButtons() {
    $$("[data-keep]").forEach((b) => {
      const s = keepState(b.dataset.keep);
      b.classList.remove("none", "pending", "kept");
      b.classList.add(s);
      b.setAttribute("aria-label", s === "kept" ? "In your library" : "Keep in library");
      const use = b.querySelector("use");
      if (use) use.setAttribute("href", `#i-${s === "kept" ? "check" : "plus"}`);
    });
    refreshKeepAll();
    if (!$("#act-sheet").hidden) renderActions();
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

  // A YouTube Music mood / genre ({label, color, params, art}) opens its page; the art is the cover of its
  // first playlist, over the color gradient (which shows on its own when there's no art or it fails).
  const moodTile = (t) => `<a class="tile${t.art ? " has-art" : ""}" style="--c:${esc(t.color)}" href="#mood/${encodeURIComponent(t.params)}">${t.art
    ? `<img src="${esc(t.art)}" alt="" loading="lazy" data-drop>` : ""}<span>${esc(t.label)}</span>${t.art ? "" : icon("note")}</a>`;
  const moodsOn = () => state.tiles.some((t) => t.params);

  function tiles(list = state.tiles) {
    return `<div class="tiles">${list.map((t) => {
      if (t.params) return moodTile(t);
      const id = (t.ids || [])[0];
      return id
        ? `<button class="tile has-art" style="--c:${esc(t.color)}" data-search="${esc(t.query)}"><img src="${artUrl(id, artSize(id, "tile"))}" alt="" loading="lazy"><span>${esc(t.label)}</span></button>`
        : `<button class="tile" style="--c:${esc(t.color)}" data-search="${esc(t.query)}"><span>${esc(t.label)}</span>${icon("note")}</button>`;
    }).join("")}</div>`;
  }

  // A track row. The title button's ::after covers the whole row (tap = play); the artist links, + and ⋯ sit
  // above it. `num` shows the track number instead of the art (album pages).
  function row(t, i, list, sub, { num } = {}) {
    const playing = cur()?.id === t.id;
    const subtitle = sub != null ? xBadge(t) + sub : trackSub(t);
    const lead = num != null
      ? `<span class="row-art row-num">${esc(num)}${eqIfPlaying(t.id)}</span>`
      : `<span class="row-art">${art(t.id)}${eqIfPlaying(t.id)}</span>`;
    return `<li class="row trk${playing ? " playing" : ""}" data-track="${esc(t.id)}">
      ${lead}
      <span class="row-text"><button class="row-hit" data-play="${i}" data-list="${list}"><span class="row-t">${esc(t.title)}</span></button><span class="row-s">${subtitle}</span></span>
      ${keepBtn(t.id, list)}
      ${moreBtn(t, i, list)}
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
      html += moodsOn()
        ? shelf({ title: "Browse moods & genres", ic: "trend", sub: "Playlists from YouTube Music", more: "#moods", body: tiles() })
        : shelf({ title: "Browse moods & genres", ic: "trend", sub: "Tap one to search YouTube", body: tiles() });
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

  // Suggestion rows: tap runs the search; ↖ puts the text in the box to keep typing.
  function suggestRows(typed) {
    const items = sug.list.length ? sug.list : [typed];
    const low = typed.toLowerCase();
    return `<ul class="rows sug">${items.map((s) => {
      const n = s.toLowerCase().startsWith(low) ? typed.length : 0;
      return `<li class="row sug-row"><button class="row-main" data-search="${esc(s)}"><span class="sug-ic">${icon("search")}</span>
        <span class="sug-t">${n ? `<b>${esc(s.slice(0, n))}</b>${esc(s.slice(n))}` : esc(s)}</span></button>
        <button class="icon-btn sm sug-fill" data-fill="${esc(s)}" aria-label="Edit “${esc(s)}”">${icon("fill")}</button></li>`;
    }).join("")}</ul>`;
  }

  function topCard(t) {
    return `<div class="top-card" data-track="${esc(t.id)}">
        <span class="top-art">${art(t.id)}</span>
        <div class="top-meta">
          <div class="top-t">${esc(t.title)}</div>
          <div class="top-s">Song · ${trackSub(t)}</div>
          <div class="top-actions">
            <button class="play-round" data-play="0" data-list="results" aria-label="Play">${icon("play", "fill")}</button>
            ${keepBtn(t.id, "results")}
            ${moreBtn(t, 0, "results")}
          </div>
        </div>
      </div>`;
  }

  // A pasted playlist / album: header, Play all + Shuffle, then its tracks.
  function playlistView(res) {
    const album = res.length > 0 && res.every((t) => t.album_id && t.album_id === res[0].album_id);
    const total = res.reduce((a, t) => a + (Number(t.duration) || 0), 0);
    const len = total ? ` · ${fmtLen(total)}` : "";
    const artist = album && res[0].channel ? `<span class="pl-a">${esc(res[0].channel)}</span>` : "";
    return `<div class="pl-head">
        <span class="pl-art">${res.length ? art(res[0].id, true) : icon("note")}</span>
        <div class="pl-meta"><span class="eyebrow">${album ? "Album" : "Playlist"}</span>
          <h2 class="pl-t">${esc(state.link.title || (album ? res[0].album : "") || "Playlist")}</h2>${artist}
          <span class="pl-s">${res.length} song${res.length === 1 ? "" : "s"}${len}</span></div>
      </div>
      ${res.length ? `<div class="actions">
        <button class="pill-btn" data-play-all="results">${icon("play", "fill")}Play</button>
        <button class="pill-btn" data-shuffle-all="results">${icon("shuffle")}Shuffle</button>
      </div>
      <ul class="rows" style="margin-top:12px">${res.map((r, i) => row(r, i, "results")).join("")}</ul>`
        : emptyState("note", "Nothing playable", "This playlist has no tracks Reef can play.")}`;
  }

  function renderSearch() {
    const el = $("#view-search");
    const typed = suggesting();
    searchMode = typed ? "suggest" : "results";
    if (typed) {
      el.innerHTML = suggestRows(typed);
      fitSuggest();
      return;
    }
    if (!state.query) {
      const searches = store.get("searches", []);
      el.innerHTML = sourceChips() + (searches.length ? `
        <div class="head-row"><h2 class="section-title">Recent</h2><button class="link-btn" data-clear-searches>Clear</button></div>
        <ul class="rows">${searches.map((q) => `<li class="row"><button class="row-main" data-search="${esc(q)}"><span class="row-art" style="display:grid;place-items:center;color:var(--faint)">${icon("clock")}</span><span class="row-text"><span class="row-t">${esc(q)}</span></span></button></li>`).join("")}</ul>` : "")
        + `<div class="head-row sec"><h2 class="section-title">Browse Categories</h2>${moodsOn() ? `<a class="link-btn" href="#moods">See all</a>` : ""}</div>${tiles()}`;
      return;
    }

    // Pasted links ignore the source and the filters: they always resolve on YouTube.
    const link = state.link;
    const chips = link ? "" : sourceChips() + `<div class="chips">${[["top", "Top Results"], ["songs", "Songs"], ["kept", "In library"]].map(([k, l]) =>
      `<button class="chip${state.filter === k ? " on" : ""}" data-filter="${k}">${l}</button>`).join("")}</div>`;

    if (state.searching) {
      el.innerHTML = chips + `<h2 class="section-title">${link ? "Opening link…" : "Top result"}</h2><div class="sk sk-top"></div>${link ? "" : `<h2 class="section-title">Songs</h2>`}`
        + `<div class="sk-row"><div class="sk a"></div><div class="sk l"></div></div>`.repeat(link ? 3 : 5);
      return;
    }
    if (state.searchError) {
      el.innerHTML = chips + emptyState("alert", link ? "Couldn't open link" : "Search failed", state.searchError);
      return;
    }
    const res = state.results;
    if (link?.kind === "playlist") { el.innerHTML = playlistView(res); return; }
    if (!res.length) {
      el.innerHTML = chips + emptyState("search", "No results", `Nothing found for “${state.query}”.`);
      return;
    }

    let html = chips;
    if (link) {
      html += `<h2 class="section-title">Top result</h2>${topCard(res[0])}`;
    } else if (state.filter === "top") {
      html += `<h2 class="section-title">Top result</h2>${topCard(res[0])}`;
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

  // ---------- browse pages: album, artist, playlist, mood, all moods ----------
  // #album/<id> #artist/<id> #playlist/<id> #mood/<params> #moods render into #view-detail. Page data stays
  // in memory per route for PAGE_TTL, so back / forward are instant; an older entry still shows at once and
  // is refreshed behind it. A failed first load shows an error with Retry.
  const detail = { kind: "", id: "", key: "", cache: new Map(), errors: new Map(), inflight: new Map(), restore: null, about: false };
  const releaseType = new Map(); // album id -> "Album" | "Single" | "EP", learned from artist pages
  const PAGE_API = {
    album: (id) => `/api/album/${encodeURIComponent(id)}`,
    artist: (id) => `/api/artist/${encodeURIComponent(id)}`,
    playlist: (id) => `/api/playlist/${encodeURIComponent(id)}`,
    mood: (id) => `/api/mood?params=${encodeURIComponent(id)}`,
    moods: () => "/api/moods",
  };

  const pageData = () => detail.cache.get(detail.key)?.data || null;
  function pageTracks() {
    const d = pageData();
    if (!d) return [];
    return (detail.kind === "artist" ? d.songs : detail.kind === "album" || detail.kind === "playlist" ? d.tracks : null) || [];
  }
  function moodName(params) {
    const t = state.tiles.find((x) => x.params === params);
    if (t) return t.label;
    for (const sec of detail.cache.get("moods")?.data?.sections || []) {
      const m = (sec.items || []).find((x) => x.params === params);
      if (m) return m.title;
    }
    return "";
  }
  // The page's name: the header title, and "Playing from" in the player.
  function pageTitle(kind = detail.kind, d = pageData(), id = detail.id) {
    if (kind === "moods") return "Moods & genres";
    if (kind === "mood") return d?.title || moodName(id) || "Mood";
    if (!d) return "";
    if (kind === "artist") return d.name || "Artist";
    return d.title || (kind === "album" ? "Album" : "Playlist");
  }

  function loadPage(key = detail.key, { force = false } = {}) {
    const hit = detail.cache.get(key);
    if (!force && hit && Date.now() - hit.at < PAGE_TTL) return null;
    if (detail.inflight.has(key)) return detail.inflight.get(key);
    const slash = key.indexOf("/");
    const kind = slash < 0 ? key : key.slice(0, slash), id = slash < 0 ? "" : key.slice(slash + 1);
    const p = (async () => {
      let changed = true;
      try {
        const data = await api(PAGE_API[kind](id));
        noteKept(kind === "artist" ? data.songs : data.tracks);
        if (kind === "artist") for (const r of [...(data.albums || []), ...(data.singles || [])]) if (r.type) releaseType.set(r.id, r.type);
        const old = detail.cache.get(key);
        changed = !old || JSON.stringify(old.data) !== JSON.stringify(data);
        detail.cache.delete(key); // re-insert: the Map's order is the eviction order
        detail.cache.set(key, { at: Date.now(), data });
        while (detail.cache.size > 40) detail.cache.delete(detail.cache.keys().next().value);
        detail.errors.delete(key);
      } catch (e) {
        if (detail.cache.has(key)) changed = false; // a failed refresh keeps what's shown
        else detail.errors.set(key, e);
      } finally {
        detail.inflight.delete(key);
      }
      if (changed && state.view === "detail" && detail.key === key) renderDetail();
    })();
    detail.inflight.set(key, p);
    return p;
  }

  function pageSkeleton(kind) {
    if (kind === "moods" || kind === "mood") {
      const grid = kind === "mood" ? `<div class="grid2">${'<div><div class="sk sk-sq"></div><div class="sk sk-line"></div></div>'.repeat(4)}</div>`
        : `<div class="tiles">${'<div class="sk sk-tile"></div>'.repeat(8)}</div>`;
      return `<div role="status" aria-label="Loading…"><div class="sk sk-title"></div>${grid}</div>`;
    }
    return `<div role="status" aria-label="Loading…"><div class="hero"><div class="hero-art${kind === "artist" ? " circle" : ""} sk"></div>
        <div class="sk sk-line" style="width:56%"></div><div class="sk sk-line sm" style="width:34%"></div></div>
      <div class="actions"><div class="sk sk-btn"></div><div class="sk sk-btn"></div></div>
      ${'<div class="sk-row"><div class="sk a"></div><div class="sk l"></div></div>'.repeat(5)}</div>`;
  }

  // Upstream failures can carry a parser dump; a short message (e.g. "YouTube Music is off") is shown as is.
  function pageError(e) {
    const msg = e.status === 404 ? "YouTube Music doesn't have this page."
      : e.message && e.message.length <= 90 ? e.message : "YouTube Music didn't answer. Try again in a moment.";
    return emptyState("alert", e.status === 404 ? "Not found" : "Couldn't load this page", msg,
      `<button class="pill-btn" data-retry>${icon("repeat")}Retry</button>`);
  }

  // The glyph sits under the cover, so it shows while the cover loads or if it fails.
  function heroArt(src, tracks, round = false) {
    const img = src ? `<img src="${esc(src)}" alt="" data-drop>` : tracks?.length ? art(tracks[0].id, true) : "";
    return `<span class="hero-art${round ? " circle" : ""}">${icon(round ? "person" : "note")}${img}</span>`;
  }
  const pageActions = () => `<div class="actions">
      <button class="pill-btn" data-play-all="page">${icon("play", "fill")}Play</button>
      <button class="pill-btn" data-shuffle-all="page">${icon("shuffle")}Shuffle</button>
    </div>`;
  const hiddenBanner = (unhide) => `<div class="banner">${icon("hide")}<span>You've hidden this artist</span>${unhide
    ? `<button class="chip sm" data-unhide-page>Unhide</button>` : ""}</div>`;

  // Keep album / Keep all: "none" (something to keep), "pending" (the rest is being kept), "kept", "" (nothing keepable).
  function keepAllState() {
    const st = pageTracks().filter((t) => canKeep(t.id) || keepState(t.id) === "kept").map((t) => keepState(t.id));
    if (!st.length) return "";
    return st.every((x) => x === "kept") ? "kept" : st.every((x) => x !== "none") ? "pending" : "none";
  }
  const keepAllInner = (s) => (s === "kept" ? `${icon("check")}In your library`
    : s === "pending" ? `${icon("download")}Keeping…` : `${icon("plus")}${detail.kind === "album" ? "Keep album" : "Keep all"}`);
  function keepAllBtn() {
    const s = keepAllState();
    return s ? `<button class="keep-all ${s}" data-keep-all data-state="${s}">${keepAllInner(s)}</button>` : "";
  }
  function refreshKeepAll() {
    const b = $("#view-detail [data-keep-all]");
    if (!b || state.view !== "detail") return;
    const s = keepAllState();
    if (b.dataset.state === s) return;
    b.dataset.state = s;
    b.className = `keep-all ${s}`;
    b.innerHTML = keepAllInner(s);
  }

  // Tags for keeps from an album page, so the files land as that album (the server validates them too).
  function albumHints(t, a) {
    const h = {};
    if (!a) return h;
    if (a.title) h.album = String(a.title).slice(0, 200);
    if (a.artist) h.album_artist = String(a.artist).slice(0, 200);
    const n = Number(t.track_number);
    if (Number.isInteger(n) && n > 0 && n < 1000) h.track_number = n;
    if (/^\d{4}$/.test(String(a.year || ""))) h.year = String(a.year);
    return h;
  }

  async function keepAll() {
    const d = pageData();
    if (!d || (detail.kind !== "album" && detail.kind !== "playlist")) return;
    const s = keepAllState();
    if (s === "kept") { toast("Already in your library"); return; }
    if (s === "pending") { location.hash = "#downloads"; return; }
    const album = detail.kind === "album";
    const tracks = pageTracks();
    const already = tracks.filter((t) => keepState(t.id) === "kept").length;
    const items = tracks.filter((t) => canKeep(t.id) && keepState(t.id) === "none").slice(0, 100)
      .map((t) => ({ id: t.id, title: t.title || "", channel: t.channel || "", ...(album ? albumHints(t, d) : {}) }));
    let r;
    try {
      r = await api("/api/download/batch", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ tracks: items }),
      });
    } catch (e) {
      toast(e.message);
      return;
    }
    const queued = Array.isArray(r?.queued) ? r.queued : [];
    const skipped = Array.isArray(r?.skipped) ? r.skipped : [];
    const kept = skipped.filter((x) => x.reason === "kept" && x.id);
    for (const x of kept) keptIds.add(x.id); // kept from another device meanwhile
    const known = new Set(state.jobs.map((j) => j.id));
    state.jobs = queued.filter((j) => !known.has(j.id)).concat(state.jobs);
    const m = already + kept.length;
    toast(queued.length ? `Keeping ${songs(queued.length)}${m ? ` (${m} already in library)` : ""}`
      : skipped.some((x) => x.reason === "keeping") ? "Already keeping these" : "Already in your library");
    refreshKeepButtons();
    renderBadges();
    if (queued.length) pollJobs();
    if (kept.length) loadHistory();
  }

  function albumPage(a) {
    const tracks = a.tracks || [];
    const total = a.duration || tracks.reduce((x, t) => x + (Number(t.duration) || 0), 0);
    const meta = [releaseType.get(a.id) || "Album", a.year, songs(tracks.length), fmtLen(total)].filter(Boolean).join(" · ");
    const by = (a.artists || []).filter((x) => x && x.name);
    const artists = by.length ? by.map((x) => (ARTIST_ID.test(x.id || "")
      ? `<a href="#artist/${encodeURIComponent(x.id)}">${esc(x.name)}</a>` : esc(x.name))).join(", ") : esc(a.artist);
    // Rows name the artist only when it isn't the album's (features, compilations).
    const sub = (t) => [t.channel && t.channel !== a.artist ? artistLinks(t) : "", t.duration ? fmt(t.duration) : ""].filter(Boolean).join(" · ");
    return `<div class="hero">${heroArt(a.thumb, tracks)}
        <h1 class="hero-t" data-anchor>${esc(a.title || "Album")}</h1>
        ${artists ? `<p class="hero-a">${artists}</p>` : ""}
        <p class="hero-s">${esc(meta)}</p>
      </div>
      ${a.blocked ? hiddenBanner(true) : ""}
      ${tracks.length ? `${pageActions()}${keepAllBtn()}
        <ul class="rows d-rows">${tracks.map((t, i) => row(t, i, "page", sub(t), { num: t.track_number || i + 1 })).join("")}</ul>`
        : emptyState("note", "Nothing playable", "This album has no tracks Reef can play.")}`;
  }

  function playlistPage(p) {
    const tracks = p.tracks || [];
    const total = tracks.reduce((x, t) => x + (Number(t.duration) || 0), 0);
    return `<div class="hero">${heroArt(p.thumb, tracks)}
        <h1 class="hero-t" data-anchor>${esc(pageTitle("playlist", p))}</h1>
        ${p.author ? `<p class="hero-a plain">${esc(p.author)}</p>` : ""}
        <p class="hero-s">${esc([songs(tracks.length), fmtLen(total)].filter(Boolean).join(" · "))}</p>
      </div>
      ${tracks.length ? `${pageActions()}${keepAllBtn()}
        <ul class="rows d-rows">${tracks.map((t, i) => row(t, i, "page")).join("")}</ul>`
        : emptyState("note", "Nothing playable", "This playlist has no tracks Reef can play.")}`;
  }

  const releaseCards = (items) => `<div class="scroller">${items.map((r) => `
      <a class="card" href="#album/${encodeURIComponent(r.id)}">
        <span class="card-art">${r.thumb ? `<img src="${esc(r.thumb)}" alt="" loading="lazy" data-drop>` : ""}</span>
        <div class="card-t">${esc(r.title)}</div>
        <div class="card-s">${esc([r.year, r.type].filter(Boolean).join(" · "))}</div>
      </a>`).join("")}</div>`;
  const artistCards = (items) => `<div class="scroller">${items.map((r) => `
      <a class="card av" href="#artist/${encodeURIComponent(r.id)}">
        <span class="card-art">${r.thumb ? `<img src="${esc(r.thumb)}" alt="" loading="lazy" data-drop>` : icon("person")}</span>
        <div class="card-t">${esc(r.name)}</div>
      </a>`).join("")}</div>`;

  function artistPage(a) {
    const list = a.songs || [];
    // With a full "Songs" playlist to go to, show the first five here (Apple Music style); else all of them.
    const top = a.songs_playlist_id ? list.slice(0, 5) : list;
    let html = `<div class="hero artist">${heroArt(a.thumb, null, true)}
        <h1 class="hero-t" data-anchor>${esc(a.name)}</h1>
        <div class="actions">
          ${list.length ? `<button class="pill-btn" data-play-all="page">${icon("play", "fill")}Play</button>
          <button class="pill-btn" data-page-radio>${icon("radio")}Radio</button>` : ""}
          <button class="pill-btn${a.blocked ? " on" : ""}" data-artist-hide aria-pressed="${a.blocked ? "true" : "false"}">${icon("hide")}${a.blocked ? "Unhide" : "Hide"}</button>
        </div>
      </div>
      ${a.blocked ? hiddenBanner(false) : ""}`;
    if (top.length) {
      html += shelf({ title: "Top songs", more: a.songs_playlist_id ? `#playlist/${encodeURIComponent(a.songs_playlist_id)}` : "",
        body: `<ul class="rows">${top.map((t, i) => row(t, i, "page")).join("")}</ul>` });
    }
    if (a.albums?.length) html += shelf({ title: "Albums", body: releaseCards(a.albums) });
    if (a.singles?.length) html += shelf({ title: "Singles & EPs", body: releaseCards(a.singles) });
    if (a.related?.length) html += shelf({ title: "Fans also like", body: artistCards(a.related) });
    if (a.description) {
      const long = a.description.length > 220;
      html += `<section class="shelf"><div class="shelf-head"><h2>About</h2></div>
        <p class="about${long && !detail.about ? " clamp" : ""}" id="about">${esc(a.description)}</p>
        ${long ? `<button class="link-btn about-more" data-about aria-expanded="${detail.about}">${detail.about ? "Less" : "More"}</button>` : ""}</section>`;
    }
    return html;
  }

  function moodsPage(d) {
    const secs = (d.sections || []).filter((x) => x.items?.length);
    return `<h1 class="page-title" data-anchor>Moods & genres</h1>${secs.length
      ? secs.map((x) => `<h2 class="section-title">${esc(x.title)}</h2>${tiles(x.items.map((m) => ({ label: m.title, color: m.color, params: m.params, art: m.art })))}`).join("")
      : emptyState("note", "Nothing to browse", "YouTube Music didn't send any moods or genres.")}`;
  }

  function moodPage(d) {
    const seen = new Set(); // a playlist can sit in several of the mood's shelves
    const pls = (d.playlists || []).filter((p) => p && p.id && !seen.has(p.id) && seen.add(p.id));
    return `<h1 class="page-title" data-anchor>${esc(pageTitle("mood", d))}</h1>${pls.length ? `<div class="grid2">${pls.map((p) => `
      <a class="pcard" href="#playlist/${encodeURIComponent(p.id)}">
        <span class="pcard-art">${p.thumb ? `<img src="${esc(p.thumb)}" alt="" loading="lazy" data-drop>` : icon("note")}</span>
        <span class="pcard-t">${esc(p.title)}</span>${p.subtitle ? `<span class="pcard-s">${esc(p.subtitle)}</span>` : ""}
      </a>`).join("")}</div>` : emptyState("note", "No playlists", "YouTube Music has nothing here right now.")}`;
  }

  const PAGES = { album: albumPage, artist: artistPage, playlist: playlistPage, mood: moodPage, moods: moodsPage };

  function renderDetail() {
    const el = $("#view-detail");
    const entry = detail.cache.get(detail.key);
    const err = detail.errors.get(detail.key);
    $("#top-title").textContent = pageTitle();
    if (entry) {
      el.innerHTML = PAGES[detail.kind](entry.data);
      if (detail.restore != null) { window.scrollTo(0, detail.restore); detail.restore = null; }
    } else {
      el.innerHTML = err ? pageError(err) : pageSkeleton(detail.kind);
      if (err) detail.restore = null;
    }
    syncTopTitle();
  }

  // The header shows the page's name once its big title has scrolled under it.
  function syncTopTitle() {
    let on = false;
    if (state.view === "detail") {
      const a = $("#view-detail [data-anchor]");
      on = !a || a.getBoundingClientRect().bottom < $("#top").offsetHeight;
    }
    $("#top").classList.toggle("title-on", on);
  }

  // ---------- hidden artists ----------
  // Comparable artist name, as the server's blocklist.key(): no " - Topic" / "VEVO", casefolded, no punctuation.
  function nameKey(n) {
    const s = String(n || "").trim().replace(/\s+-\s+Topic$/i, "").replace(/\s*VEVO$/, "").trim().toLowerCase();
    return s.replace(/[^\p{L}\p{N}]+/gu, "") || s;
  }
  function byArtist(t, a) {
    const k = nameKey(a.name);
    const all = Array.isArray(t.artists) ? t.artists.filter((x) => x && x.name) : [];
    if (all.length) return all.some((x) => (a.id && x.id === a.id) || nameKey(x.name) === k);
    return nameKey(t.channel) === k;
  }
  // Who "Hide artist" hides for a track: its first artist (with the channel id when known), else the channel.
  function hideTarget(t) {
    if (!t || srcKey(t.id) !== "yt") return null;
    const a = (Array.isArray(t.artists) ? t.artists : []).find((x) => x && x.name);
    if (a) return { name: a.name, id: ARTIST_ID.test(a.id || "") ? a.id : null };
    return t.channel ? { name: t.channel, id: null } : null;
  }

  async function loadBlocklist() {
    try {
      const list = await api("/api/blocklist");
      if (Array.isArray(list)) state.blocked = list;
    } catch { /* keep the last list */ }
    if (!$("#status-sheet").hidden) renderStatus();
    if (!$("#hidden-sheet").hidden) renderHidden();
  }

  async function hideArtist(a) {
    if (!a?.name) return;
    try {
      await api("/api/blocklist", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(a.id ? { name: a.name, id: a.id } : { name: a.name }),
      });
    } catch (e) {
      toast(`Couldn't hide: ${e.message}`);
      return;
    }
    toast(`Hidden “${a.name}”`);
    blockedChanged(a);
  }

  async function unhideArtists(list) {
    list = (list || []).filter((a) => a && (a.id || a.name));
    if (!list.length) return;
    for (const a of list) {
      const q = new URLSearchParams();
      if (a.id) q.set("id", a.id);
      if (a.name) q.set("name", a.name);
      try {
        await api(`/api/blocklist?${q}`, { method: "DELETE" });
      } catch (e) {
        if (e.status !== 404) { toast(`Couldn't unhide: ${e.message}`); blockedChanged(null); return; }
      }
    }
    toast(`Unhid “${list.map((a) => a.name).filter(Boolean).join(", ")}”`);
    blockedChanged(null);
  }

  function toggleArtistHidden() {
    const a = pageData();
    if (!a || detail.kind !== "artist") return;
    const who = { id: a.id, name: a.name };
    a.blocked = !a.blocked; // flip at once; the refetch confirms
    renderDetail();
    if (a.blocked) hideArtist(who); else unhideArtists([who]);
  }

  // The blocklist changed: what's on screen drops (hide) or may regain (unhide) their songs.
  function blockedChanged(hid) {
    for (const e of detail.cache.values()) e.at = 0; // any page may hold their songs: refetch on the next visit
    if (hid) {
      if (state.results.some((t) => byArtist(t, hid))) {
        state.results = state.results.filter((t) => !byArtist(t, hid));
        if (state.view === "search") renderSearch();
      }
      const d = pageData();
      if (detail.kind === "playlist" && d?.tracks) d.tracks = d.tracks.filter((t) => !byArtist(t, hid));
    } else if (state.query && !state.link && !state.searching) {
      refetchSearch();
    }
    if (state.view === "detail") { renderDetail(); loadPage(detail.key, { force: true }); }
    loadBlocklist();
  }

  // Re-run the current search in place (no skeleton, no navigation), e.g. after an artist was unhidden.
  async function refetchSearch() {
    const q = state.query, seq = ++searchSeq;
    try {
      const r = await api(`/api/search?q=${encodeURIComponent(q)}&src=${encodeURIComponent(currentSource())}`);
      if (seq !== searchSeq || !Array.isArray(r)) return;
      state.results = r;
      noteKept(r);
      if (state.view === "search") renderSearch();
    } catch { /* keep what's shown */ }
  }

  function renderHidden() {
    const list = state.blocked;
    $("#hidden-list").innerHTML = list == null
      ? '<div class="sk-row"><div class="sk a"></div><div class="sk l"></div></div>'.repeat(2)
      : !list.length
        ? emptyState("hide", "No hidden artists", "Hide an artist from a song's ⋯ menu to keep them out of search, radio and playlists.")
        : `<ul class="rows">${list.map((e) => `<li class="row hid-row">
            <span class="hid-ic">${icon("person")}</span>
            <span class="row-text">${ARTIST_ID.test(e.id || "")
              ? `<a class="row-t" href="#artist/${encodeURIComponent(e.id)}">${esc(e.name)}</a>` : `<span class="row-t">${esc(e.name)}</span>`}
              <span class="row-s">Hidden ${esc(timeAgo(e.at))}</span></span>
            <button class="chip sm" data-unhide="${esc(e.id || "")}" data-name="${esc(e.name)}" aria-label="Unhide ${esc(e.name)}">Unhide</button>
          </li>`).join("")}</ul>`;
  }

  function openHidden() {
    closeSheet("#status-sheet");
    renderHidden();
    openSheet("#hidden-sheet");
    loadBlocklist();
  }

  const RENDER = { home: renderHome, search: renderSearch, kept: renderKept, downloads: renderDownloads, detail: renderDetail };

  function renderBadges() {
    const n = state.jobs.filter((j) => ACTIVE.includes(j.status)).length;
    for (const el of [$("#tab-badge"), $("#hdr-badge")]) {
      el.hidden = !n;
      el.textContent = n;
    }
  }

  // ---------- routing ----------
  // Main views #home #search #kept #downloads; browse pages (#album/<id>, #artist/<id>, #playlist/<id>,
  // #mood/<params>, #moods) show in #view-detail on top of the main view they were opened from, whose tab
  // and bottom bar stay. Every history entry carries {reef: depth, parent}: depth > 0 means Back has an
  // in-app page to return to, and each depth's scroll position comes back on back / forward (iOS edge swipe).
  const DETAIL_RE = /^(album|artist|playlist|mood)\/(.+)$/;
  let navDepth = 0, navReplace = false, routeKey = "";
  const scrollAt = new Map(); // history depth -> scrollY
  if ("scrollRestoration" in history) history.scrollRestoration = "manual";

  function parseHash() {
    const h = location.hash.slice(1);
    if (VIEWS.includes(h)) return { view: h, key: h };
    if (h === "moods") return { view: "detail", kind: "moods", id: "", key: "moods" };
    const m = DETAIL_RE.exec(h);
    if (m) {
      let id = "";
      try { id = decodeURIComponent(m[2]); } catch { /* malformed escape */ }
      if (id) return { view: "detail", kind: m[1], id, key: `${m[1]}/${id}` };
    }
    return { view: "home", key: "home" };
  }

  // True when the entry was visited before (back / forward / reload); a new one gets the next depth.
  function trackNav() {
    const hs = history.state;
    if (hs && typeof hs.reef === "number") { navDepth = hs.reef; return true; }
    if (navReplace) { navReplace = false; return false; } // goBack()'s replace keeps the depth
    navDepth += 1;
    for (const k of [...scrollAt.keys()]) if (k >= navDepth) scrollAt.delete(k);
    return false;
  }

  function route() {
    const r = parseHash();
    const hs = history.state;
    const visited = trackNav();
    if (r.view !== "detail") state.parent = r.view;
    else if (visited && VIEWS.includes(hs?.parent)) state.parent = hs.parent;
    else if (VIEWS.includes(state.view)) state.parent = state.view; // opened from a main view (else: from a page, same parent)
    if (hs?.reef !== navDepth || hs?.parent !== state.parent) {
      try { history.replaceState({ ...(hs || {}), reef: navDepth, parent: state.parent }, ""); } catch { /* ignore */ }
    }
    const changed = r.key !== routeKey;
    routeKey = r.key;
    state.view = r.view;
    const tab = r.view === "detail" ? state.parent : r.view;
    document.body.dataset.view = tab;
    document.body.classList.toggle("in-detail", r.view === "detail");
    $$(".view").forEach((s) => { s.hidden = s.id !== `view-${r.view}`; });
    $$(".tab").forEach((t) => t.classList.toggle("active", t.dataset.view === tab));
    const y = visited ? scrollAt.get(navDepth) || 0 : 0;
    if (r.view === "detail") {
      if (changed) Object.assign(detail, { kind: r.kind, id: r.id, key: r.key, about: false });
      detail.restore = changed ? y : null; // applied once the page has content
      renderDetail();
      loadPage(r.key);
    } else {
      if (r.view === "home" && state.homeTab === "library" && state.library === null) loadLibrary();
      RENDER[r.view]();
      if (changed) window.scrollTo(0, y);
    }
    syncTopTitle();
    if (changed) {
      if (npOpen()) closeNP();
      $$(".sheet-wrap.open").forEach((w) => closeSheet(w));
    }
  }

  // The header's back button: the previous in-app page, else (opened from a link) the parent view.
  function goBack() {
    if (navDepth > 0) { history.back(); return; }
    navReplace = true;
    location.replace(`#${state.parent}`);
  }
  // Go to a route; already there, just close the player / sheets on top of it.
  function openRoute(hash) {
    if (location.hash === hash) { if (npOpen()) closeNP(); return; }
    location.hash = hash;
  }

  // ---------- data ----------
  async function loadHistory() {
    try { state.history = await api("/api/history"); } catch { /* keep previous */ }
    // Search and browse pages only need their + buttons updated (a re-render would reset their scrollers).
    if (state.view !== "search" && state.view !== "detail") RENDER[state.view]();
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
  // A YouTube / YT Music URL (with or without the scheme) or a bare 11-character video id. Bare ids need a
  // digit, - or _, or mixed case, so ordinary 11-letter words still search; the server has the final say.
  const LINK_RE = /^(?:https?:\/\/)?(?:[\w-]+\.)*(?:youtube\.com|youtu\.be)\/\S*$/i;
  const isBareId = (q) => /^[A-Za-z0-9_-]{11}$/.test(q) && (/[0-9_-]/.test(q) || (/[a-z]/.test(q) && /[A-Z]/.test(q)));
  const looksLikeLink = (q) => LINK_RE.test(q) || isBareId(q);

  let searchSeq = 0;
  // `link`: q is a link (from the share target); `plain`: search the words even if they look like an id.
  async function runSearch(q, { link = false, plain = false } = {}) {
    q = String(q || "").trim();
    if (!q) return;
    const asLink = !plain && (link || looksLikeLink(q));
    state.query = q;
    state.link = asLink ? { kind: "", title: "" } : null;
    state.searching = true;
    state.searchError = "";
    state.filter = "top";
    clearTimeout(sug.timer);
    sug.seq++;
    $("#q").value = q;
    $("#q-clear").hidden = false;
    $("#q").blur();
    if (!asLink) rememberSearch(q);
    if (location.hash !== "#search") location.hash = "#search"; else renderSearch();
    const seq = ++searchSeq;
    try {
      if (asLink) {
        let r;
        try {
          r = await api(`/api/link?url=${encodeURIComponent(q)}`);
        } catch (e) {
          // Something like "Beyonce2024" looks like a video id; if it isn't one, search for it instead.
          if (seq === searchSeq && !link && !LINK_RE.test(q)) { runSearch(q, { plain: true }); return; }
          throw e;
        }
        if (seq !== searchSeq) return;
        state.results = Array.isArray(r?.tracks) ? r.tracks : [];
        state.link = { kind: r?.kind === "playlist" ? "playlist" : "track", title: r?.title || "" };
        // Recent searches get a name, never the raw URL.
        const t = state.results[0];
        const name = state.link.kind === "playlist" ? state.link.title : t ? `${t.title} ${t.channel || ""}`.trim() : "";
        if (name) rememberSearch(name);
      } else {
        const r = await api(`/api/search?q=${encodeURIComponent(q)}&src=${encodeURIComponent(currentSource())}`);
        if (seq !== searchSeq) return;
        state.results = r;
      }
      noteKept(state.results);
    } catch (e) {
      if (seq !== searchSeq) return;
      state.results = [];
      state.searchError = e.message;
    }
    state.searching = false;
    if (state.view === "search") renderSearch();
  }

  function rememberSearch(q) {
    store.set("searches", [q, ...store.get("searches", []).filter((s) => s.toLowerCase() !== q.toLowerCase())].slice(0, 8));
  }

  // ---------- search suggestions ----------
  // Shown in place of Recent / the results while the box holds text that hasn't been searched yet.
  // Debounced; `seq` drops stale answers; `cache` is per typed text (lowercased), successes only.
  const sug = { seq: 0, timer: 0, cache: new Map(), list: [] };
  let searchMode = "";
  const suggesting = () => {
    const typed = $("#q").value.trim();
    return typed && typed !== state.query && !looksLikeLink(typed) ? typed : "";
  };

  function suggest() {
    clearTimeout(sug.timer);
    const typed = suggesting();
    if (typed) {
      const key = typed.toLowerCase();
      if (sug.cache.has(key)) {
        sug.seq++;
        sug.list = sug.cache.get(key);
      } else {
        const seq = ++sug.seq;
        sug.timer = setTimeout(async () => {
          let list;
          try { list = await api(`/api/suggest?q=${encodeURIComponent(typed)}`); } catch { list = null; }
          if (Array.isArray(list)) {
            list = list.filter((x) => typeof x === "string" && x.trim()).slice(0, 8);
            sug.cache.set(key, list);
            if (sug.cache.size > 200) sug.cache.delete(sug.cache.keys().next().value);
          }
          if (seq !== sug.seq || suggesting() !== typed) return;
          sug.list = list || [];
          if (state.view === "search") renderSearch();
        }, 250);
      }
    }
    // Re-render on every keystroke while suggesting (cheap), but the results only when the mode flips.
    if (state.view === "search" && (typed || searchMode === "suggest")) renderSearch();
  }

  // With the iOS keyboard up, Safari pans the page up to show the bottom search bar, which can hide the
  // top of the view (the suggestions). Push the list down into what's visible (visualViewport) if so.
  const vv = window.visualViewport;
  function fitSuggest() {
    const list = $("#view-search .sug");
    if (!list) return;
    list.style.marginTop = "";
    if (!vv || document.activeElement !== $("#q")) return;
    const top = list.getBoundingClientRect().top;
    const want = vv.offsetTop + 12;
    if (top < want) list.style.marginTop = `${Math.round(want - top)}px`;
  }
  if (vv) vv.addEventListener("resize", fitSuggest);

  // ---------- player ----------
  function play(listName, i, { shuffle = false } = {}) {
    const list = lists[listName]?.();
    if (!list || !list.length) return;
    const tracks = list.map(slim);
    i = Math.min(Math.max(i, 0), tracks.length - 1);
    if (shuffle) {
      state.shuffle = true;
      i = Math.floor(Math.random() * tracks.length);
    }
    if (state.shuffle) {
      const r = Q.shuffleFrom(tracks, i);
      state.queue = r.queue; state.index = r.index; state.original = r.original;
    } else {
      state.queue = tracks; state.index = i; state.original = null;
    }
    queueGen++;
    state.from = fromLabel(listName);
    prefetch([cur()?.id]);
    load();
  }

  // "Now playing from …": a pasted playlist and a browse page show their own name.
  const fromLabel = (listName) => (listName === "page" ? pageTitle()
    : listName === "results" && state.link?.kind === "playlist" ? state.link.title || "Playlist" : FROM[listName] || "");

  function rememberPlay(t) {
    store.set("plays", [slim(t), ...store.get("plays", []).filter((p) => p.id !== t.id)].slice(0, 12));
  }

  function setMediaMeta(t) {
    if (!("mediaSession" in navigator) || !t) return;
    const abs = (u) => new URL(u, location.href).href;
    navigator.mediaSession.metadata = new MediaMetadata({
      title: t.title, artist: t.channel, album: "Reef",
      artwork: srcKey(t.id) === "yt"
        ? [{ src: thumb(t.id), sizes: "320x180", type: "image/jpeg" }, { src: bigThumb(t.id), sizes: "1280x720", type: "image/jpeg" }]
        : [{ src: abs(artUrl(t.id, 600)), sizes: "600x600", type: "image/jpeg" }],
    });
  }

  const streamUrl = (t) => `/api/stream/${encodeURIComponent(t.id)}`;

  function load() {
    const t = cur();
    if (!t) return;
    resume = null;
    audio.src = streamUrl(t);
    audio.play().catch(() => {});
    // Resolve the next track once this one is playing, so it doesn't compete for the extractor.
    prefetched = null;
    prefetchNext = upcomingId();
    rememberPlay(t);
    updatePlayer();
    markPlaying();
    setProgress(0, t.duration || 0);
    resetLyrics();
    setMediaMeta(t);
    saveSession(0);
    renderQueue();
    autoFill();
  }

  // Play (or resume) whatever is current. A restored session has no src yet: set it on this first
  // play (inside the user's gesture, which iOS needs) and seek once metadata is in.
  function playAudio() {
    const t = cur();
    if (!t) return;
    if (!resume) { audio.play().catch(() => {}); return; }
    const pos = resume.position;
    resume = null;
    audio.src = streamUrl(t);
    if (pos > 0) {
      audio.addEventListener("loadedmetadata", () => {
        if (cur()?.id !== t.id || audio.getAttribute("src") !== streamUrl(t)) return;
        const d = audio.duration;
        if (!isFinite(d) || pos < d - 1) audio.currentTime = pos;
      }, { once: true });
    }
    prefetched = null;
    prefetchNext = upcomingId();
    audio.play().catch(() => {});
    autoFill(); // a restored queue near its end
  }

  function next() {
    const n = state.queue.length;
    if (!n) return;
    const i = Q.nextIndex(n, state.index, state.repeat);
    if (i < 0) {
      // End of the queue: if autoplay is fetching (or can fetch) similar tracks, wait and go on.
      const at = state.index, gen = queueGen, wait = autoFill();
      if (wait) {
        setLoading(true);
        wait.then(() => {
          if (gen !== queueGen || state.index !== at) return; // the user moved on meanwhile
          setLoading(false);
          if (at + 1 < state.queue.length) { state.index = at + 1; load(); } else stopAtEnd();
        });
        return;
      }
      stopAtEnd();
      return;
    }
    state.index = i;
    load();
  }

  function stopAtEnd() {
    audio.pause();
    if (resume) { resume.position = 0; setProgress(0, duration()); saveSession(); } else audio.currentTime = 0;
  }

  function prev() {
    const pos = resume ? resume.position : audio.currentTime;
    const i = Q.prevIndex(state.queue.length, state.index);
    if (pos > 3 || i < 0) {
      if (resume) { resume.position = 0; setProgress(0, duration()); saveSession(); } else audio.currentTime = 0;
      return;
    }
    state.index = i;
    load();
  }

  function togglePlay() {
    if (!cur()) return;
    if (audio.paused) playAudio(); else audio.pause();
  }

  // ---------- queue actions ----------
  // A queue change: persist it, re-plan the next-track prefetch, refresh the open queue sheet.
  function queueChanged() {
    saveSession();
    planPrefetch();
    renderQueue();
    autoFill();
  }

  // ---------- autoplay ----------
  // With repeat off and <= 2 tracks left, append up to AUTO_LIMIT tracks from the radio of the queue's last
  // YouTube track (each seed once per session), marked `auto`: the Up next sheet shows them as a block,
  // "Add to queue" goes before them, turning autoplay off removes them. Failures stay silent.
  // `queueGen` changes whenever the queue is replaced, so an answer for an old queue is dropped.
  let queueGen = 0;
  const autoplay = { on: store.get("autoplay", true), inflight: null, used: new Set(), failedAt: 0 };

  // The pending fill (a Promise of "appended anything?"), or null when there's nothing to do.
  function autoFill() {
    if (!autoplay.on || state.repeat !== "off" || !cur()) return null;
    if (autoplay.inflight) return autoplay.inflight;
    if (state.queue.length - state.index - 1 > 2) return null;
    const seed = state.queue.slice().reverse().find((t) => srcKey(t.id) === "yt");
    if (!seed || autoplay.used.has(seed.id) || Date.now() - autoplay.failedAt < 30000) return null;
    autoplay.used.add(seed.id);
    const gen = queueGen;
    const p = api(`/api/radio/${encodeURIComponent(seed.id)}`).then((list) => {
      if (gen !== queueGen || !autoplay.on || state.repeat !== "off" || !Array.isArray(list)) return false;
      const before = state.queue.length;
      state.queue = Q.appendNew(state.queue, list.map(slim), { limit: AUTO_LIMIT, auto: true });
      if (state.queue.length === before) return false;
      autoplay.inflight = null; // so queueChanged() may chain another fill if still short
      queueChanged();
      return true;
    }).catch((e) => {
      console.warn("autoplay: radio failed for", seed.id, e.message);
      autoplay.used.delete(seed.id); // a blip shouldn't end autoplay for good; retry after a pause
      autoplay.failedAt = Date.now();
      return false;
    }).finally(() => { if (autoplay.inflight === p) autoplay.inflight = null; });
    autoplay.inflight = p;
    renderQueue(); // "finding similar tracks…" while it loads
    return p;
  }

  function setAutoplay(on) {
    autoplay.on = on;
    store.set("autoplay", on);
    if (!on) state.queue = Q.stripAuto(state.queue, state.index);
    else autoplay.used.clear(); // switched back on: the seeds may fill again (their tracks were removed)
    renderQueueModes();
    queueChanged();
  }

  // ---------- radio ----------
  // Replace the queue with the seed + its YT Music radio. A seed that's already playing keeps playing
  // (only what comes after it is replaced); otherwise it starts at once and the radio fills in behind it.
  async function startRadio(t) {
    t = slim(t);
    if (srcKey(t.id) !== "yt") return;
    const gen = ++queueGen;
    autoplay.used.add(t.id); // its radio is the queue now; autoplay continues from the last track
    state.from = "Radio";
    if (state.shuffle) { state.shuffle = false; state.original = null; }
    if (cur()?.id !== t.id) {
      state.queue = [t]; state.index = 0;
      load();
    } else {
      updatePlayer();
    }
    toast(`Starting radio from “${t.title}”`);
    let list;
    try {
      list = await api(`/api/radio/${encodeURIComponent(t.id)}`);
    } catch (e) {
      if (gen === queueGen) toast(`Couldn't start radio: ${e.message}`);
      return;
    }
    if (gen !== queueGen || cur()?.id !== t.id || !Array.isArray(list)) return;
    state.queue = Q.appendNew(Q.clearUpcoming(state.queue, state.index), list.map(slim));
    queueChanged();
  }

  function addToQueue(t, listName, mode) {
    if (!t) return;
    t = slim(t);
    if (!cur()) {
      // Nothing loaded: either action just starts playing it.
      state.queue = [t]; state.index = 0; state.original = state.shuffle ? [t] : null;
      queueGen++;
      state.from = fromLabel(listName);
      load();
      return;
    }
    if (cur().id === t.id) { toast("Already playing"); return; }
    if (mode === "next") {
      const r = Q.playNext(state.queue, state.index, t, state.shuffle ? state.original : null);
      state.queue = r.queue;
      if (state.shuffle) state.original = r.original;
    } else {
      state.queue = Q.append(state.queue, state.index, t);
    }
    queueChanged();
    toast(mode === "next" ? `Playing next: “${t.title}”` : `Added to queue: “${t.title}”`);
  }

  function jumpTo(i) {
    if (!(i >= 0 && i < state.queue.length) || i === state.index) return;
    state.index = i;
    load();
  }

  function removeFromQueue(i) {
    if (i === state.index) return; // the sheet only offers upcoming tracks
    const r = Q.removeAt(state.queue, state.index, i);
    state.queue = r.queue; state.index = r.index;
    queueChanged();
  }

  function clearUpcoming() {
    state.queue = Q.clearUpcoming(state.queue, state.index);
    queueChanged();
  }

  function toggleShuffle() {
    if (!state.shuffle) {
      const r = Q.shuffleUpcoming(state.queue, state.index);
      state.queue = r.queue; state.original = r.original; state.shuffle = true;
    } else {
      const r = Q.unshuffle(state.queue, state.index, state.original);
      state.queue = r.queue; state.index = r.index; state.original = null; state.shuffle = false;
    }
    updateModes();
    queueChanged();
  }

  function cycleRepeat() {
    state.repeat = { off: "all", all: "one", one: "off" }[state.repeat] || "off";
    updateModes();
    queueChanged();
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
    $("#np-s").disabled = !linkable(t).length; // tapping the artist opens their page
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
    // "End of track" sleep needs `ended` to fire, which a looping element never does.
    audio.loop = state.repeat === "one" && sleep.mode !== "end";
    renderQueueModes();
  }

  // ---------- prefetch ----------
  // prefetchNext waits for the current track's `playing`; `prefetched` stops re-warming the same id.
  let prefetchNext = null, prefetched = null;
  function upcomingId() {
    const i = Q.nextIndex(state.queue.length, state.index, state.repeat);
    return i >= 0 && i !== state.index ? state.queue[i].id : null;
  }
  function planPrefetch() {
    const id = upcomingId();
    prefetchNext = null;
    if (!id || id === prefetched) return;
    if (!resume && !audio.paused && audio.readyState >= 3) { prefetch([id]); prefetched = id; }
    else prefetchNext = id;
  }

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

  // ---------- session (queue persistence + resume) ----------
  // store "session": the queue, where we are in it and the modes, so a reload or a killed Home Screen
  // app comes back paused where it was. `resume` is set while a restored track has no src yet.
  let resume = null;
  let sessionRev = Number(store.get("session", null)?.rev) || 0;
  let posSavedAt = 0;
  function saveSession(position) {
    const t = cur();
    if (position == null) position = resume ? resume.position : (t && audio.getAttribute("src") ? audio.currentTime || 0 : 0);
    posSavedAt = Date.now();
    store.set("session", {
      v: 1, rev: ++sessionRev,
      queue: state.queue.map(qslim), index: state.index, from: state.from,
      shuffle: state.shuffle, original: state.shuffle && state.original ? state.original.map(qslim) : null,
      repeat: state.repeat, position: Math.round(position * 10) / 10, savedAt: Date.now(),
    });
  }

  const validTrack = (t) => t && typeof t === "object" && typeof t.id === "string" && t.id !== "";
  function restoreSession() {
    if (state.queue.length) return; // something already started; never clobber it
    const s = store.get("session", null);
    if (!s || s.v !== 1 || !Array.isArray(s.queue) || !s.queue.length || !s.queue.every(validTrack)) return;
    const index = Number(s.index);
    if (!Number.isInteger(index) || index < 0 || index >= s.queue.length) return;
    state.queue = s.queue.map(qslim);
    state.index = index;
    state.from = typeof s.from === "string" ? s.from : "";
    state.repeat = ["off", "all", "one"].includes(s.repeat) ? s.repeat : "off";
    state.shuffle = !!s.shuffle;
    state.original = state.shuffle && Array.isArray(s.original) ? s.original.filter(validTrack).map(qslim) : null;
    const t = cur();
    let pos = Number(s.position);
    if (!isFinite(pos) || pos < 0 || (t.duration && pos >= t.duration - 1)) pos = 0;
    resume = { position: pos };
    updatePlayer();
    markPlaying();
    setProgress(pos, t.duration || 0);
    setMediaMeta(t);
    updatePlayState();
  }

  // ---------- sleep timer ----------
  // A wall-clock deadline checked every few seconds and on timeupdate/visibilitychange: Safari throttles
  // (or freezes) timers in the background, so one long setTimeout could fire far too late.
  const sleep = { mode: "off", minutes: 0, deadline: 0, timer: 0, fade: 0, fadeFrom: 1 };
  const SLEEP_OPTS = [["off", "Off"], ["15", "15 minutes"], ["30", "30 minutes"], ["45", "45 minutes"], ["60", "1 hour"], ["end", "End of track"]];
  const sleepLeft = () => Math.max(1, Math.ceil((sleep.deadline - Date.now()) / 60000));

  function setSleep(choice, { quiet = false } = {}) {
    clearInterval(sleep.timer);
    sleep.timer = 0;
    stopFade();
    if (choice === "end") {
      Object.assign(sleep, { mode: "end", minutes: 0, deadline: 0 });
      if (!quiet) toast("Pausing after this track");
    } else if (Number(choice) > 0) {
      Object.assign(sleep, { mode: "time", minutes: Number(choice), deadline: Date.now() + Number(choice) * 60000 });
      sleep.timer = setInterval(checkSleep, 5000);
      if (!quiet) toast(`Sleep timer: ${choice} min`);
    } else {
      Object.assign(sleep, { mode: "off", minutes: 0, deadline: 0 });
      if (!quiet) toast("Sleep timer off");
    }
    updateModes();
    renderSleepBadge();
  }

  function checkSleep() {
    if (sleep.mode !== "time") return;
    if (Date.now() < sleep.deadline) { renderSleepBadge(); return; }
    setSleep("off", { quiet: true });
    if (audio.paused) return;
    // iOS ignores audio.volume (reads back 1), so fade only where the element really takes it; and not
    // in the background, where the fade's timer could stall.
    if (!document.hidden && volumeWritable()) fadeOutAndPause(); else audio.pause();
  }

  function volumeWritable() {
    const v = audio.volume, probe = v > 0.5 ? 0.25 : 0.75;
    try { audio.volume = probe; } catch { return false; }
    const ok = Math.abs(audio.volume - probe) < 0.01;
    audio.volume = v;
    return ok;
  }

  function fadeOutAndPause() {
    const v0 = audio.volume, steps = 30;
    let k = 0;
    sleep.fade = setInterval(() => {
      k += 1;
      audio.volume = Math.max(0, v0 * (1 - k / steps));
      if (k >= steps) { clearInterval(sleep.fade); sleep.fade = 0; audio.pause(); audio.volume = v0; }
    }, 100);
    sleep.fadeFrom = v0;
  }
  function stopFade() {
    if (!sleep.fade) return;
    clearInterval(sleep.fade);
    sleep.fade = 0;
    audio.volume = sleep.fadeFrom;
  }

  function renderSleepBadge() {
    const b = $("#np-sleep"), badge = $("#np-sleep-badge");
    b.classList.toggle("on", sleep.mode !== "off");
    const txt = sleep.mode === "time" ? String(sleepLeft()) : "";
    if (badge.textContent !== txt) badge.textContent = txt;
    badge.hidden = !txt;
    b.setAttribute("aria-label", sleep.mode === "time" ? `Sleep timer: ${txt} min left`
      : sleep.mode === "end" ? "Sleep timer: end of track" : "Sleep timer");
    if (!$("#sleep-sheet").hidden) renderSleep();
  }

  function renderSleep() {
    $("#sleep-menu").innerHTML = SLEEP_OPTS.map(([v, label]) => {
      const on = v === "off" ? sleep.mode === "off" : v === "end" ? sleep.mode === "end" : sleep.mode === "time" && sleep.minutes === Number(v);
      const aside = on && sleep.mode === "time" ? `${sleepLeft()} min left` : "";
      return `<button class="menu-item${on ? " on" : ""}" data-sleep="${v}" aria-pressed="${on}">
        <span class="menu-l">${esc(label)}</span>${aside ? `<span class="aside">${aside}</span>` : ""}${on ? icon("check") : ""}</button>`;
    }).join("");
  }

  // ---------- audio events ----------
  audio.addEventListener("timeupdate", () => {
    setProgress(audio.currentTime, duration());
    if (lyr.times.length) syncLyrics();
    if (sleep.mode === "time") checkSleep();
    if (!audio.paused && Date.now() - posSavedAt >= 5000) saveSession();
  });
  audio.addEventListener("loadedmetadata", () => setProgress(audio.currentTime, duration()));
  audio.addEventListener("play", updatePlayState);
  audio.addEventListener("pause", () => { updatePlayState(); if (cur()) saveSession(); });
  audio.addEventListener("ended", () => {
    updatePlayState();
    if (sleep.mode === "end") { setSleep("off", { quiet: true }); saveSession(); return; } // pause instead of advancing
    next();
  });
  audio.addEventListener("error", () => { setLoading(false); if (audio.getAttribute("src")) toast("Couldn't play this one"); updatePlayState(); });
  audio.addEventListener("loadstart", () => { if (audio.getAttribute("src")) setLoading(true); });
  audio.addEventListener("waiting", () => setLoading(true));
  audio.addEventListener("playing", () => {
    setLoading(false);
    if (prefetchNext) { prefetch([prefetchNext]); prefetched = prefetchNext; prefetchNext = null; }
    if (lyr.on && cur()) fetchLyrics(cur()); // warm, so opening the player shows them at once
  });
  audio.addEventListener("pause", () => setLoading(false));
  audio.addEventListener("ended", () => setLoading(false));
  audio.addEventListener("emptied", () => { if (!audio.getAttribute("src")) setLoading(false); });

  document.addEventListener("visibilitychange", () => {
    checkSleep();
    if (document.visibilityState === "hidden" && cur()) saveSession();
  });
  window.addEventListener("pagehide", () => { if (cur()) saveSession(); });

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
    seeking = false;
    if (!d) return;
    const t = (seek.value / 1000) * d;
    if (resume) { resume.position = t; setProgress(t, d); saveSession(); } else audio.currentTime = t;
  });

  if ("mediaSession" in navigator) {
    const ms = navigator.mediaSession;
    const set = (a, f) => { try { ms.setActionHandler(a, f); } catch { /* unsupported action */ } };
    set("play", playAudio);
    set("pause", () => audio.pause());
    set("previoustrack", prev);
    set("nexttrack", next);
    set("seekto", (e) => {
      if (e.seekTime == null) return;
      if (resume) { resume.position = e.seekTime; setProgress(e.seekTime, duration()); } else audio.currentTime = e.seekTime;
    });
  }

  // ---------- now playing sheet ----------
  function openNP() {
    if (!cur()) return;
    const np = $("#np");
    np.classList.add("open");
    np.setAttribute("aria-hidden", "false");
    syncLock();
    if (lyr.on) showLyrics();
  }
  function closeNP() {
    const np = $("#np");
    np.classList.remove("open");
    np.style.transform = "";
    np.setAttribute("aria-hidden", "true");
    syncLock();
  }
  // Page scroll stays locked while the player or any sheet is up.
  const syncLock = () => document.documentElement.classList.toggle("lock", npOpen() || $$(".sheet-wrap.open").length > 0);

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

  // ---------- lyrics ----------
  // LRC text -> [{ t: seconds, text }] sorted by time. Handles several stamps per line, [mm:ss(.x|.xx|.xxx)],
  // [offset:±ms] (positive = sooner), skips [ar:]/[ti:]-style metadata and strips enhanced <mm:ss.xx> word tags.
  function parseLRC(lrc) {
    const STAMP = /^\[(\d{1,3}):(\d{1,2})(?:[.:](\d{1,3}))?\]\s*/;
    const out = [];
    let offset = 0;
    for (const raw of String(lrc || "").split(/\r?\n/)) {
      let line = raw.trim(), m;
      const off = /^\[offset:\s*([+-]?\d+)\s*\]$/i.exec(line);
      if (off) { offset = Number(off[1]) / 1000; continue; }
      const times = [];
      while ((m = STAMP.exec(line))) {
        times.push(Number(m[1]) * 60 + Number(m[2]) + (m[3] ? Number(m[3]) / 10 ** m[3].length : 0));
        line = line.slice(m[0].length);
      }
      if (!times.length) continue;
      const text = line.replace(/<\d{1,3}:\d{1,2}(?:[.:]\d{1,3})?>/g, "").replace(/\s+/g, " ").trim();
      for (const t of times) out.push({ t, text });
    }
    for (const l of out) l.t = Math.max(0, l.t - offset); // [offset:] may come after the lines it shifts
    return out.sort((a, b) => a.t - b.t);
  }

  const LYRIC_LEAD = 0.2; // Safari fires timeupdate ~4x/s; showing a line a beat early reads as on time
  const lyr = {
    on: store.get("lyricsOn", false),
    cache: new Map(), // track id -> Promise of {synced, plain, instrumental} | null (misses too)
    seq: 0, id: null, lines: [], times: [], els: [], active: -1, held: false, holdTimer: 0,
  };
  const lyricsPanel = $("#np-lyrics");
  const npOpen = () => $("#np").classList.contains("open");

  function fetchLyrics(t) {
    let p = lyr.cache.get(t.id);
    if (!p) {
      const q = new URLSearchParams();
      for (const [k, v] of [["title", t.title], ["artist", t.channel], ["album", t.album], ["duration", t.duration]]) if (v) q.set(k, v);
      p = api(`/api/lyrics/${encodeURIComponent(t.id)}?${q}`).catch((e) => {
        if (e.status !== 404) lyr.cache.delete(t.id); // a network blip shouldn't stick; a real miss should
        return null;
      });
      lyr.cache.set(t.id, p);
    }
    return p;
  }

  function lyricsMsg(text, cls = "") {
    lyricsPanel.innerHTML = `<div class="lyr-msg ${cls}">${cls ? icon("note") : ""}${esc(text)}</div>`;
  }

  function renderLyrics(data) {
    const lines = data?.synced ? parseLRC(data.synced) : [];
    lyr.lines = lines;
    lyr.times = lines.map((l) => l.t);
    lyr.active = -1;
    if (lines.length) {
      lyricsPanel.innerHTML = `<div class="lyr synced">${lines.map((l, i) => l.text
        ? `<button class="lyr-line" data-i="${i}">${esc(l.text)}</button>`
        : `<button class="lyr-line gap" data-i="${i}" aria-label="Instrumental break">♪</button>`).join("")}</div>`;
    } else if (data?.plain || data?.synced) {
      const text = data.plain || data.synced.replace(/^(\s*\[[^\]]*\])+/gm, ""); // unparseable synced: show its words
      lyricsPanel.innerHTML = `<div class="lyr plain">${text.split(/\r?\n/).map((l) => (l.trim()
        ? `<p>${esc(l.trim())}</p>` : `<p class="gap"></p>`)).join("")}</div>`;
    } else if (data?.instrumental) {
      lyricsMsg("Instrumental", "instrumental");
    } else {
      lyricsMsg("No lyrics for this track");
    }
    lyr.els = $$(".lyr-line", lyricsPanel);
    lyricsPanel.scrollTop = 0;
    syncLyrics(true);
  }

  // Show the current track's lyrics in the panel (no-op when they're already there).
  async function showLyrics() {
    const t = cur();
    if (!lyr.on || !t) return;
    if (lyr.id === t.id) { syncLyrics(true); return; }
    lyr.id = t.id;
    const seq = ++lyr.seq;
    lyr.lines = []; lyr.times = []; lyr.els = []; lyr.active = -1;
    lyricsPanel.innerHTML = `<div class="lyr-sk" role="status" aria-label="Loading lyrics…"><i class="sk"></i><i class="sk"></i><i class="sk"></i><i class="sk"></i></div>`;
    const data = await fetchLyrics(t);
    if (seq !== lyr.seq || cur()?.id !== t.id) return; // the track changed while this was loading
    renderLyrics(data);
  }

  // New track: drop what the panel shows, then load if it's visible.
  function resetLyrics() {
    lyr.id = null; lyr.seq++;
    lyr.lines = []; lyr.times = []; lyr.els = []; lyr.active = -1;
    if (lyr.on && npOpen()) showLyrics();
  }

  function scrollToLine(el, smooth) {
    if (!el || lyr.held || !npOpen() || !lyr.on) return;
    const top = el.offsetTop + el.offsetHeight / 2 - lyricsPanel.clientHeight / 3;
    const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
    lyricsPanel.scrollTo({ top: Math.max(0, top), behavior: smooth && !reduce ? "smooth" : "auto" });
  }

  // Highlight the line for audio.currentTime: binary search, and the DOM is touched only when the line changes.
  function syncLyrics(force = false, smooth = !force) {
    const ts = lyr.times;
    if (!ts.length) return;
    const now = audio.currentTime + LYRIC_LEAD;
    let lo = 0, hi = ts.length - 1, i = -1;
    while (lo <= hi) {
      const mid = (lo + hi) >> 1;
      if (ts[mid] <= now) { i = mid; lo = mid + 1; } else hi = mid - 1;
    }
    if (i === lyr.active && !force) return;
    lyr.els[lyr.active]?.classList.remove("on");
    lyr.els[i]?.classList.add("on");
    lyr.active = i;
    if (i < 0) { if (force && !lyr.held) lyricsPanel.scrollTop = 0; return; }
    scrollToLine(lyr.els[i], smooth);
  }

  function setLyricsOn(on) {
    lyr.on = on;
    store.set("lyricsOn", on);
    $("#np").classList.toggle("show-lyrics", on);
    $("#np-lyr").setAttribute("aria-pressed", String(on));
    if (on) showLyrics();
  }

  // Reading ahead or back: let the user scroll for 4 s before following the song again.
  function holdLyrics() {
    lyr.held = true;
    clearTimeout(lyr.holdTimer);
    lyr.holdTimer = setTimeout(() => { lyr.held = false; syncLyrics(true, true); }, 4000);
  }
  for (const ev of ["touchstart", "touchmove", "wheel", "pointerdown"]) lyricsPanel.addEventListener(ev, holdLyrics, { passive: true });
  lyricsPanel.addEventListener("click", (e) => {
    const line = e.target.closest(".lyr-line");
    if (!line || !lyr.lines[line.dataset.i]) return;
    clearTimeout(lyr.holdTimer);
    lyr.held = false;
    const at = lyr.lines[line.dataset.i].t;
    if (resume) resume.position = at; else audio.currentTime = at;
    if (audio.paused) playAudio();
    syncLyrics(true, true);
  });

  // ---------- keep ----------
  async function keep(id, listName) {
    const s = keepState(id);
    if (s === "kept") { toast("Already in your library"); return; }
    if (s === "pending") { location.hash = "#downloads"; return; }
    const t = (lists[listName]?.() || []).find((x) => x.id === id) || state.queue.find((x) => x.id === id) || { id, title: "" };
    // From an album page, the keep carries the album's tags (as Keep album does).
    const hints = listName === "page" && detail.kind === "album" ? albumHints(t, pageData()) : {};
    try {
      const job = await api("/api/download", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id, title: t.title, channel: t.channel || "", ...hints }),
      });
      state.jobs.unshift(job);
      refreshKeepButtons();
      renderBadges();
      updatePlayer();
      toast(`Keeping “${t.title || "song"}”`);
      pollJobs();
    } catch (e) {
      if (e.status === 409) {
        // The server already has it (another tab or device kept it): catch up before saying so.
        await loadHistory();
        if (e.body?.job) await pollJobs();
        refreshKeepButtons();
      }
      toast(e.message);
    }
  }

  // ---------- share ----------
  async function share(t = cur()) {
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
    const n = state.blocked?.length;
    $("#status-list").innerHTML = rows.map(([k, v, c]) => `<li><span class="k">${esc(k)}</span><span class="v ${c}">${esc(v)}</span></li>`).join("")
      + `<li class="st-link"><button data-open-hidden><span class="k">Hidden artists</span><span class="v">${n == null ? "" : n}${icon("chev-right")}</span></button></li>`;
  }
  async function openStatus() {
    renderStatus();
    openSheet("#status-sheet");
    loadBlocklist();
    await loadHealth();
    renderStatus();
  }

  // ---------- sheets ----------
  function openSheet(sel) {
    const w = $(sel);
    w.hidden = false;
    $(".sheet", w).style.transform = "";
    requestAnimationFrame(() => requestAnimationFrame(() => { w.classList.add("open"); syncLock(); }));
  }
  function closeSheet(w) {
    if (typeof w === "string") w = $(w);
    if (!w || w.hidden) return;
    w.classList.remove("open");
    $(".sheet", w).style.transform = "";
    syncLock();
    setTimeout(() => { if (!w.classList.contains("open")) w.hidden = true; }, 380);
  }
  const topSheet = () => $$(".sheet-wrap.open").pop();

  // Drag a sheet down by its top (grabber + heading) to dismiss it, like the player.
  for (const w of $$(".sheet-wrap")) {
    const sheet = $(".sheet", w), zone = $(".sheet-top", w);
    let y0 = null, dy = 0;
    zone.addEventListener("touchstart", (e) => { y0 = e.touches[0].clientY; dy = 0; sheet.style.transition = "none"; }, { passive: true });
    zone.addEventListener("touchmove", (e) => {
      if (y0 == null) return;
      dy = Math.max(0, e.touches[0].clientY - y0);
      sheet.style.transform = `translateY(${dy}px)`;
    }, { passive: true });
    zone.addEventListener("touchend", () => {
      sheet.style.transition = "";
      if (dy > 80) closeSheet(w); else sheet.style.transform = "";
      y0 = null;
    });
  }

  // ---------- track actions sheet ----------
  let actTarget = null; // { t, list }
  function openActions(id, listName, i) {
    const list = lists[listName]?.() || [];
    const t = (list[i]?.id === id ? list[i] : list.find((x) => x.id === id)) || state.queue.find((x) => x.id === id);
    if (!t) return;
    actTarget = { t: slim(t), list: listName };
    renderActions();
    openSheet("#act-sheet");
  }
  function renderActions() {
    if (!actTarget) return;
    const { t } = actTarget;
    const ks = keepState(t.id);
    $("#act-head").innerHTML = `<span class="row-art">${art(t.id)}</span>
      <span class="row-text"><span class="row-t">${esc(t.title)}</span><span class="row-s">${xBadge(t)}${esc(t.channel)}${srcBadge(t)}</span></span>`;
    const item = (act, ic, label, extra = "") => `<button class="menu-item" data-act="${act}"${extra}>${icon(ic)}<span class="menu-l">${label}</span></button>`;
    const keepItem = ks === "kept" ? item("keep", "check", "In your library", " disabled")
      : ks === "pending" ? item("keep", "download", "Keeping…")
      : canKeep(t.id) ? item("keep", "plus", "Keep in library") : "";
    const radioItem = srcKey(t.id) === "yt" ? item("radio", "radio", "Start radio") : "";
    // One "Go to artist", or one per artist ("Go to Daft Punk", "Go to Pharrell Williams") when there are several.
    const as = linkable(t).slice(0, 4);
    const artistItems = as.length === 1 ? item("artist", "person", "Go to artist", ` data-arg="${esc(as[0].id)}"`)
      : as.map((a) => item("artist", "person", `Go to ${esc(a.name)}`, ` data-arg="${esc(a.id)}"`)).join("");
    const albumItem = ALBUM_ID.test(t.album_id || "") ? item("album", "disc", "Go to album", ` data-arg="${esc(t.album_id)}"`) : "";
    const ha = hideTarget(t);
    const hideItem = ha ? item("hide", "hide", as.length > 1 ? `Hide ${esc(ha.name)}` : "Hide artist") : "";
    $("#act-menu").innerHTML = item("next", "play-next", "Play next") + item("queue", "queue-add", "Add to queue") + radioItem + keepItem
      + artistItems + albumItem + item("share", "share", "Share") + hideItem;
  }
  function runAction(act, arg) {
    const a = actTarget;
    closeSheet("#act-sheet");
    if (!a) return;
    if (act === "next" || act === "queue") addToQueue(a.t, a.list, act);
    else if (act === "keep") keep(a.t.id, a.list);
    else if (act === "share") share(a.t);
    else if (act === "radio") startRadio(a.t);
    else if (act === "artist" && arg) openRoute(`#artist/${encodeURIComponent(arg)}`);
    else if (act === "album" && arg) openRoute(`#album/${encodeURIComponent(arg)}`);
    else if (act === "hide") hideArtist(hideTarget(a.t));
  }

  // ---------- queue sheet ----------
  function openQueue() {
    openSheet("#queue-sheet");
    renderQueue();
    renderQueueModes();
    $("#q-body").scrollTop = 0;
  }
  function renderQueueModes() {
    const el = $("#q-modes");
    if (!el || $("#queue-sheet").hidden) return;
    const rep = { off: "Repeat", all: "Repeat all", one: "Repeat one" }[state.repeat];
    el.innerHTML = `<button class="chip sm${state.shuffle ? " on" : ""}" data-q-mode="shuffle" aria-pressed="${state.shuffle}">${icon("shuffle")}Shuffle</button>
      <button class="chip sm${state.repeat !== "off" ? " on" : ""}" data-q-mode="repeat" aria-pressed="${state.repeat !== "off"}">${icon("repeat")}${rep}</button>
      <button class="chip sm${autoplay.on ? " on" : ""}" data-q-mode="autoplay" aria-pressed="${autoplay.on}">${icon("infinity")}Autoplay</button>`;
  }
  function qRow(t, i, now) {
    const sub = `<span class="row-s">${trackSub(t)}</span>`;
    return now
      ? `<li class="row playing" data-track="${esc(t.id)}"><span class="row-main"><span class="row-art">${art(t.id)}${eqIfPlaying(t.id)}</span>
          <span class="row-text"><span class="row-t">${esc(t.title)}</span>${sub}</span></span></li>`
      : `<li class="row trk q-row"><span class="row-art">${art(t.id)}</span>
          <span class="row-text"><button class="row-hit" data-q-jump="${i}" aria-label="Play ${esc(t.title)}"><span class="row-t">${esc(t.title)}</span></button>${sub}</span>
          <button class="icon-btn sm" data-q-remove="${i}" aria-label="Remove ${esc(t.title)} from queue">${icon("x")}</button></li>`;
  }
  function renderQueue() {
    if ($("#queue-sheet").hidden) return;
    const t = cur();
    if (!t) { $("#q-body").innerHTML = emptyState("note", "Nothing playing", "Play a song to start a queue."); return; }
    const { manual, auto, at } = Q.upcomingParts(state.queue, state.index);
    const any = manual.length + auto.length;
    const empty = state.repeat === "all" && state.queue.length > 1 ? "Repeat is on: the queue starts over after this."
      : autoplay.on && state.repeat === "off" && autoplay.inflight ? "Autoplay is finding similar tracks…"
      : "Nothing queued. Use ⋯ on a song to add it.";
    $("#q-body").innerHTML = `<h4 class="q-label">Now playing</h4><ul class="rows">${qRow(t, state.index, true)}</ul>
      <div class="head-row q-up"><h4 class="q-label">Up next${manual.length ? ` · ${manual.length}` : ""}</h4>${any ? `<button class="link-btn" data-q-clear>Clear</button>` : ""}</div>
      ${manual.length ? `<ul class="rows">${manual.map((x, k) => qRow(x, state.index + 1 + k)).join("")}</ul>`
        : auto.length ? "" : `<p class="q-empty">${empty}</p>`}
      ${auto.length ? `<h4 class="q-auto">${icon("infinity")}Autoplay · similar tracks</h4>
        <ul class="rows">${auto.map((x, k) => qRow(x, at + k)).join("")}</ul>` : ""}`;
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
    const t = e.target.closest("[data-keep],[data-play],[data-search],[data-source],[data-home-tab],[data-filter],[data-dismiss],[data-play-all],[data-shuffle-all],[data-close],[data-clear-finished],[data-clear-searches],[data-more],[data-act],[data-q-jump],[data-q-remove],[data-q-clear],[data-q-mode],[data-sleep],[data-fill],[data-keep-all],[data-retry],[data-page-radio],[data-artist-hide],[data-unhide],[data-unhide-page],[data-open-hidden],[data-about]");
    if (!t) return;
    const d = t.dataset;
    if ("keep" in d) { e.preventDefault(); if (d.keep) keep(d.keep, d.list); return; }
    if ("more" in d) { openActions(d.more, d.list, Number(d.i)); return; }
    if ("act" in d) { if (!t.disabled) runAction(d.act, d.arg); return; }
    if ("keepAll" in d) { keepAll(); return; }
    if ("retry" in d) { detail.errors.delete(detail.key); renderDetail(); loadPage(detail.key, { force: true }); return; }
    if ("pageRadio" in d) { const s = pageTracks()[0]; if (s) startRadio(s); return; }
    if ("artistHide" in d) { toggleArtistHidden(); return; }
    if ("unhidePage" in d) {
      const a = pageData();
      if (a) unhideArtists(detail.kind === "artist" ? [{ id: a.id, name: a.name }] : a.artists || []);
      return;
    }
    if ("unhide" in d) { unhideArtists([{ id: d.unhide, name: d.name }]); return; }
    if ("openHidden" in d) { openHidden(); return; }
    if ("about" in d) {
      detail.about = !detail.about;
      $("#about")?.classList.toggle("clamp", !detail.about);
      t.textContent = detail.about ? "Less" : "More";
      t.setAttribute("aria-expanded", String(detail.about));
      return;
    }
    if ("qJump" in d) { jumpTo(Number(d.qJump)); return; }
    if ("qRemove" in d) { removeFromQueue(Number(d.qRemove)); return; }
    if ("qClear" in d) { clearUpcoming(); return; }
    if ("qMode" in d) {
      if (d.qMode === "shuffle") toggleShuffle(); else if (d.qMode === "autoplay") setAutoplay(!autoplay.on); else cycleRepeat();
      return;
    }
    if ("fill" in d) {
      const q = $("#q");
      q.value = d.fill + " ";
      q.focus();
      q.setSelectionRange(q.value.length, q.value.length);
      suggest();
      return;
    }
    if ("sleep" in d) { setSleep(d.sleep); closeSheet("#sleep-sheet"); return; }
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
    if ("close" in d) { closeSheet(t.closest(".sheet-wrap")); }
  });

  $("#search-bar").addEventListener("submit", (e) => { e.preventDefault(); runSearch($("#q").value); });
  $("#q").addEventListener("input", () => { $("#q-clear").hidden = !$("#q").value; suggest(); });
  $("#q").addEventListener("focus", () => {
    document.body.classList.add("typing");
    if (state.view === "detail") location.hash = "#search"; // a page opened from Search: typing goes back to searching
    setTimeout(fitSuggest, 350);
  });
  $("#q").addEventListener("blur", () => { document.body.classList.remove("typing"); fitSuggest(); });
  // Keep the keyboard up when ↖ is tapped: don't let the button take focus from the box.
  $("#view-search").addEventListener("mousedown", (e) => { if (e.target.closest("[data-fill]")) e.preventDefault(); });
  $("#q-clear").addEventListener("click", () => {
    $("#q").value = "";
    $("#q-clear").hidden = true;
    state.query = "";
    state.link = null;
    state.results = [];
    searchSeq++;
    sug.seq++;
    clearTimeout(sug.timer);
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
  $("#np-share").addEventListener("click", () => share());
  $("#np-queue").addEventListener("click", openQueue);
  $("#np-sleep").addEventListener("click", () => { renderSleep(); openSheet("#sleep-sheet"); });
  $("#np-dl").addEventListener("click", closeNP);
  $("#np-lyr").addEventListener("click", () => setLyricsOn(!lyr.on));
  $("#np-shuffle").addEventListener("click", toggleShuffle);
  $("#np-repeat").addEventListener("click", cycleRepeat);
  $("#status-btn").addEventListener("click", openStatus);
  $("#top-back").addEventListener("click", goBack);
  $("#np-s").addEventListener("click", () => {
    const a = linkable(cur())[0];
    if (a) openRoute(`#artist/${encodeURIComponent(a.id)}`);
  });
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    if (topSheet()) closeSheet(topSheet());
    else if ($("#np").classList.contains("open")) closeNP();
  });

  const top = $("#top");
  window.addEventListener("scroll", () => {
    top.classList.toggle("scrolled", window.scrollY > 4);
    scrollAt.set(navDepth, window.scrollY);
    if (state.view === "detail") syncTopTitle();
  }, { passive: true });
  window.addEventListener("hashchange", route);

  // ---------- boot ----------
  state.view = "";
  restoreSession();
  // Share target / deep links: /?link=<url>#search or /?q=<text>#search. Drop the query string first.
  const bootArgs = new URLSearchParams(location.search);
  const bootLink = bootArgs.get("link")?.trim(), bootQ = bootArgs.get("q")?.trim();
  if (location.search) history.replaceState({ reef: 0 }, "", location.pathname + (bootLink || bootQ ? "#search" : location.hash));
  else if (typeof history.state?.reef !== "number") history.replaceState({ reef: 0 }, ""); // the app's first entry
  route();
  if (bootLink) runSearch(bootLink, { link: true });
  else if (bootQ) runSearch(bootQ);
  setLyricsOn(lyr.on);
  loadSources();
  loadHealth();
  loadHistory();
  loadTiles();
  pollJobs();
})();
