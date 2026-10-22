(() => {
  "use strict";

  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const audio = $("#audio");
  const VIEWS = ["home", "search", "kept", "downloads"];
  const ACTIVE = ["queued", "downloading", "converting"];
  const FROM = { results: "From search", history: "Kept", recent: "Recently played" };
  const AUTO_LIMIT = 20; // autoplay tracks appended per radio fetch

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
  const trackSub = (t) => xBadge(t) + [esc(t.channel), t.album ? esc(t.album) : "", t.duration ? fmt(t.duration) : ""]
    .filter(Boolean).join(" · ") + srcBadge(t);

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

  const moreBtn = (t, i, list) => `<button class="icon-btn sm more-btn" data-more="${esc(t.id)}" data-i="${i}" data-list="${list}" aria-label="More for ${esc(t.title)}">${icon("more")}</button>`;

  function refreshKeepButtons() {
    $$("[data-keep]").forEach((b) => {
      const s = keepState(b.dataset.keep);
      b.classList.remove("none", "pending", "kept");
      b.classList.add(s);
      b.setAttribute("aria-label", s === "kept" ? "In your library" : "Keep in library");
      const use = b.querySelector("use");
      if (use) use.setAttribute("href", `#i-${s === "kept" ? "check" : "plus"}`);
    });    if (!$("#act-sheet").hidden) renderActions();
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
    const subtitle = sub != null ? xBadge(t) + sub : trackSub(t);
    return `<li class="row${playing ? " playing" : ""}" data-track="${esc(t.id)}">
      <button class="row-main" data-play="${i}" data-list="${list}">
        <span class="row-art">${art(t.id)}${eqIfPlaying(t.id)}</span>
        <span class="row-text"><span class="row-t">${esc(t.title)}</span><span class="row-s">${subtitle}</span></span>
      </button>
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
    const mins = Math.round(total / 60);
    const len = total ? ` · ${mins >= 60 ? `${Math.floor(mins / 60)} hr ${mins % 60} min` : `${mins} min`}` : "";
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
        + `<h2 class="section-title">Browse Categories</h2>${tiles()}`;
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

  // "Now playing from …": a pasted playlist shows its own name.
  const fromLabel = (listName) => (listName === "results" && state.link?.kind === "playlist"
    ? state.link.title || "Playlist" : FROM[listName] || "");

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
    $("#status-list").innerHTML = rows.map(([k, v, c]) => `<li><span class="k">${esc(k)}</span><span class="v ${c}">${esc(v)}</span></li>`).join("");
  }
  async function openStatus() {
    renderStatus();
    openSheet("#status-sheet");
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
    $("#act-menu").innerHTML = item("next", "play-next", "Play next") + item("queue", "queue-add", "Add to queue") + radioItem + keepItem + item("share", "share", "Share");
  }
  function runAction(act) {
    const a = actTarget;
    closeSheet("#act-sheet");
    if (!a) return;
    if (act === "next" || act === "queue") addToQueue(a.t, a.list, act);
    else if (act === "keep") keep(a.t.id, a.list);
    else if (act === "share") share(a.t);
    else if (act === "radio") startRadio(a.t);
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
    const body = `<span class="row-art">${art(t.id)}${now ? eqIfPlaying(t.id) : ""}</span>
      <span class="row-text"><span class="row-t">${esc(t.title)}</span><span class="row-s">${trackSub(t)}</span></span>`;
    return now
      ? `<li class="row playing" data-track="${esc(t.id)}"><span class="row-main">${body}</span></li>`
      : `<li class="row q-row"><button class="row-main" data-q-jump="${i}" aria-label="Play ${esc(t.title)}">${body}</button>
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
    const t = e.target.closest("[data-keep],[data-play],[data-search],[data-source],[data-home-tab],[data-filter],[data-dismiss],[data-play-all],[data-shuffle-all],[data-close],[data-clear-finished],[data-clear-searches],[data-more],[data-act],[data-q-jump],[data-q-remove],[data-q-clear],[data-q-mode],[data-sleep],[data-fill]");
    if (!t) return;
    const d = t.dataset;
    if ("keep" in d) { e.preventDefault(); if (d.keep) keep(d.keep, d.list); return; }
    if ("more" in d) { openActions(d.more, d.list, Number(d.i)); return; }
    if ("act" in d) { if (!t.disabled) runAction(d.act); return; }
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
  $("#q").addEventListener("focus", () => { document.body.classList.add("typing"); setTimeout(fitSuggest, 350); });
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
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    if (topSheet()) closeSheet(topSheet());
    else if ($("#np").classList.contains("open")) closeNP();
  });

  const top = $("#top");
  window.addEventListener("scroll", () => top.classList.toggle("scrolled", window.scrollY > 4), { passive: true });
  window.addEventListener("hashchange", route);

  // ---------- boot ----------
  state.view = "";
  restoreSession();
  // Share target / deep links: /?link=<url>#search or /?q=<text>#search. Drop the query string first.
  const bootArgs = new URLSearchParams(location.search);
  const bootLink = bootArgs.get("link")?.trim(), bootQ = bootArgs.get("q")?.trim();
  if (location.search) history.replaceState(null, "", location.pathname + (bootLink || bootQ ? "#search" : location.hash));
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
