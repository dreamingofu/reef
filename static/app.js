(() => {
  "use strict";

  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const audio = $("#audio");
  const VIEWS = ["home", "search", "kept", "downloads"];
  const ACTIVE = ["queued", "downloading", "converting"];
  const FROM = { results: "From search", history: "Kept", recent: "Recently played", quick: "Quick picks", on_repeat: "On repeat",
    jump_back: "Jump back in", liked: "Liked songs" };
  const HOME_TTL = 5 * 60 * 1000; // re-ask /api/home when Home is shown and the copy is older than this
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
    home: store.get("home", null), // the last /api/home body: Home paints from it at once, then refreshes
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
    settings: {}, // server-side settings (/api/settings `values`); missing keys mean the defaults
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
    quick: () => homeItems("quick"),
    on_repeat: () => homeItems("on_repeat"),
    jump_back: () => homeItems("jump_back"),
    liked: () => homeItems("liked"),
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
  const art = (id, big = false) => /^pq:/.test(id || "") ? `<img src="/static/icon.svg" alt="">` : big
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
  const srcBadge = (t) => (srcKey(t.id) === "yt" || t.q ? "" : ` · ${esc(srcInfo(srcKey(t.id)).name)}`);
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

  // ---------- recovery core ----------
  // The player's recovery decisions as pure functions (no DOM, no state); the machine that acts on them is in
  // "recovery" below. Unit-checked from node by slicing the text between these two sentinel comments.
  // ==== recovery-core:start ====
  const R = (() => {
    const MAX_TRIES = 3; // reloads per track before giving up on it
    const MAX_DEAD = 3; // tracks given up on in a row before stopping
    const SETTLE_S = 15; // seconds a recovered track must play past where it resumed before its tries reset
    // Blocks where reloading the same track can't help: skip it at once.
    const FINAL = ["GONE", "REGION", "MEMBERS_OR_PAID", "LOGIN_REQUIRED"];
    const isFinal = (block) => FINAL.includes(block);
    const FALLBACK = "Couldn't play this one";
    const OFFLINE = "Check your connection";
    // Wait before reload #attempt: 1 s, 2 s, 4 s (capped at 10 s).
    const backoff = (attempt) => Math.min(10000, 1000 * 2 ** Math.max(0, attempt - 1));
    // How long the playhead may stand still while playing before it counts as a failure. Once the track has metadata, 8 s;
    // before that the server may still be resolving a cold stream (yt-dlp), and a reload would only restart that.
    const stallLimit = (readyState) => (readyState >= 1 ? 8000 : 20000);
    // A probe's answer (`Range: bytes=0-1` on the stream) -> {ok, block, hint}. ok: the stream answers again.
    // status 0 = the fetch itself failed (offline, timeout).
    function probeVerdict(status, body) {
      if (status === 200 || status === 206) return { ok: true, block: null, hint: "" };
      if (!status) return { ok: false, block: "NETWORK", hint: "" };
      if (status === 404) return { ok: false, block: "GONE", hint: "" }; // the server doesn't know this id
      const block = typeof body?.block === "string" && body.block ? body.block : "UNKNOWN";
      return { ok: false, block, hint: typeof body?.hint === "string" ? body.hint : "" };
    }
    // The current track failed. `block`: from the probe (null when the stream answers again); `tries`: reloads
    // already made for it; `online`: navigator.onLine; `early`: a stall before the element had metadata.
    // -> {do: "retry", attempt, delay} | {do: "wait"} (offline: pause until `online`) | {do: "give-up"}
    //  | {do: "keep"} (an early stall on a stream that answers: it's slow, not dead; a reload would start over)
    function onFailure({ block = null, tries = 0, online = true, early = false } = {}) {
      if (isFinal(block)) return { do: "give-up" };
      if (online === false) return { do: "wait" };
      if (early && !block) return { do: "keep" };
      if (tries < MAX_TRIES) return { do: "retry", attempt: tries + 1, delay: backoff(tries + 1) };
      return { do: "give-up" };
    }
    // The reloads counted against a track after the playhead reached `at`; its last recovery resumed it at `from`.
    // `playing` alone proves nothing (a stream can play a few seconds after each reload and fail again, forever):
    // only SETTLE_S of progress past the resume point clears them.
    const settledTries = ({ tries = 0, from = 0, at = 0 } = {}) => (tries > 0 && at - from >= SETTLE_S ? 0 : tries);
    // After giving up on a track. `dead`: tracks given up on in a row, this one included; `content`: how many of
    // them failed for a FINAL block. Stop after MAX_DEAD in a row, or once every track in the queue failed (so
    // repeat-all can't spin). A run of only removed / blocked tracks isn't a connection problem: it says so.
    // -> {do: "next", message} | {do: "stop", outage, message}; `outage`: `online` may resume it.
    function afterGiveUp({ dead = 1, content = 0, queueLength = 1, block = null, hint = "" } = {}) {
      if (dead < MAX_DEAD && dead < queueLength) return { do: "next", message: hint || FALLBACK };
      if (block === "BOT_WALL") return { do: "stop", outage: true, message: hint || OFFLINE };
      if (content >= dead) return { do: "stop", outage: false, message: dead > 1 ? `Couldn't play ${dead} songs in a row` : hint || FALLBACK };
      return { do: "stop", outage: true, message: OFFLINE };
    }
    return { MAX_TRIES, MAX_DEAD, SETTLE_S, FALLBACK, OFFLINE, isFinal, backoff, stallLimit, probeVerdict, onFailure, settledTries, afterGiveUp };
  })();
  // ==== recovery-core:end ====

  // ---------- library + listen core ----------
  // Pure helpers for playing kept tracks from Navidrome (production plan P1.5) and for the listen log
  // (POST /api/listens). Unit-checked from node by slicing the text between these two sentinel comments.
  // ==== library-core:start ====
  const L = (() => {
    const COUNT_S = 30; // the server's counting rule, mirrored for display; the server decides what's stored
    const COUNT_SHARE = 0.5;
    const MIN_REPORT_S = 1; // shorter plays aren't reported at all
    const ND_ID = /^[\w.-]{1,128}$/;
    // The id the player streams for track `t`: the Navidrome copy (`nd:<ndId>`) of a kept track when the
    // library is on, the setting allows it and that copy hasn't failed for this play; else the track's own id.
    function streamId(t, { ndId = null, ndOn = false, prefer = true, failed = false } = {}) {
      if (!t || typeof t.id !== "string" || !t.id) return null;
      if (!ndOn || !prefer || failed || /^nd:/.test(t.id) || typeof ndId !== "string" || !ND_ID.test(ndId)) return t.id;
      return `nd:${ndId}`;
    }
    const counts = (played, duration) => played >= COUNT_S || (duration > 0 && played >= duration * COUNT_SHARE);
    // Seconds actually played between two timeupdates: the playhead's advance, as long as the wall clock allows
    // it (a seek jumps further than that and counts nothing; going back counts nothing either).
    function playedDelta(prevT, prevWall, t, wall, rate = 1) {
      if (prevT == null || prevWall == null) return 0;
      const d = t - prevT;
      if (!(d > 0)) return 0;
      const allowed = (Math.max(0, wall - prevWall) / 1000) * Math.max(1, Number(rate) || 1) + 1.5;
      return d <= allowed ? d : 0;
    }
    // The POST /api/listens body for listen `l` ({track, startedAt (epoch s), played (s), via, sent}), or null when
    // there's nothing new to say: under MIN_REPORT_S, or already reported with the same time and skip flag.
    // `l.sent` is what went out last ({played, skipped}); a later, longer report of the same play updates it
    // server-side (entries are keyed by id + started_at).
    function listenReport(l, { skipped = false, now = Date.now() } = {}) {
      if (!l || !l.track || !(l.played >= MIN_REPORT_S)) return null;
      const played = Math.round(l.played * 10) / 10;
      if (l.sent && l.sent.played >= played - 0.5 && l.sent.skipped === skipped) return null;
      const t = l.track;
      return {
        id: t.id, title: t.title || "", channel: t.channel || "", duration: t.duration || null,
        ...(t.artists ? { artists: t.artists } : {}), ...(t.album ? { album: t.album } : {}), ...(t.album_id ? { album_id: t.album_id } : {}),
        played_s: played, started_at: Math.floor(l.startedAt), ended_at: Math.floor(now / 1000),
        skipped: !!skipped, source: t.source || "", ...(l.via ? { via: l.via } : {}),
      };
    }
    return { COUNT_S, COUNT_SHARE, MIN_REPORT_S, streamId, counts, playedDelta, listenReport };
  })();
  // ==== library-core:end ====

  // ---------- match core ----------
  // Pure helpers for placeholder queue entries and dead-track replacement (production plan P1.8) and for skipping
  // SponsorBlock's non-music segments at play time (P1.9). Unit-checked from node by slicing the text between
  // these two sentinel comments.
  // ==== match-core:start ====
  const M = (() => {
    const WINDOW = 2; // placeholders are looked up only this close to the current track (and before they play)
    // Blocks a replacement can fix: this upload is gone or walled, the song itself usually isn't.
    const REPLACE_BLOCKS = ["GONE", "REGION", "MEMBERS_OR_PAID"];
    const SEG_EARLY = 0.05; // a segment starting at 0 is entered by the first timeupdate
    const SEG_TAIL = 0.5; // this close to a segment's end, let it play out instead of seeking
    const isPlaceholder = (t) => !!(t && t.q === true);
    // A queue entry known only by name ({q: true, title, artist, duration}, from imports / charts). Its id is local
    // ("pq:…", never a playable id); /api/match swaps it for a real track before it plays.
    function placeholder({ title = "", artist = "", duration = null, album = "", key = "" } = {}, id) {
      return {
        id, q: true, title: String(title), artist: String(artist), channel: String(artist),
        duration: Number(duration) > 0 ? Number(duration) : null, ...(album ? { album: String(album) } : {}), ...(key ? { key: String(key) } : {}),
      };
    }
    // Queue indexes of placeholders to look up now: the current entry and the next `window` after it.
    function toResolve(queue, index, window = WINDOW) {
      const out = [];
      for (let k = Math.max(0, index); k < queue.length && k <= index + window; k++) if (isPlaceholder(queue[k])) out.push(k);
      return out;
    }
    // The queue with the entry `id` swapped for `track` (an autoplay entry stays one). A track already elsewhere in
    // the queue isn't added twice (ids are unique in a queue): the entry is dropped instead, the next one taking
    // its place. -> {queue, index, at (where it was, -1 = not found), dropped}
    function swap(queue, index, id, track) {
      const at = queue.findIndex((t) => t && t.id === id);
      if (at < 0 || !track || !track.id) return { queue: queue.slice(), index, at: -1, dropped: false };
      const q = queue.slice();
      if (q.some((t, k) => k !== at && t && t.id === track.id)) {
        q.splice(at, 1);
        return { queue: q, index: at < index ? index - 1 : at === index ? Math.min(index, q.length - 1) : index, at, dropped: true };
      }
      q[at] = queue[at].auto ? { ...track, auto: true } : track;
      return { queue: q, index, at, dropped: false };
    }
    // Whether a track recovery gave up on gets a replacement lookup: a YouTube / Monochrome track that failed for a
    // block another upload can fix, and isn't itself a replacement (one swap per song, never a chain).
    function canReplace(t, block, { enabled = true } = {}) {
      if (!enabled || !t || isPlaceholder(t) || t.replaced_from || !REPLACE_BLOCKS.includes(block)) return false;
      return /^mc:./.test(t.id || "") || /^[A-Za-z0-9_-]{11}$/.test(t.id || "");
    }
    // SponsorBlock segments [{start, end}] -> the index of the one to jump out of at playhead `t`, or -1. Each segment
    // is skipped once; `done` holds the ones already skipped or entered on purpose (a seek into one plays it).
    function segmentAt(segs, done, t) {
      for (let i = 0; i < (segs || []).length; i++) {
        const s = segs[i];
        if (!done.has(i) && t >= s.start - SEG_EARLY && t < s.end - SEG_TAIL) return i;
      }
      return -1;
    }
    // Where to land after segment `seg`: its end, kept inside the track when the length is known.
    const segmentTarget = (seg, duration) => (duration > 0 ? Math.min(seg.end, Math.max(0, duration - 0.25)) : seg.end);
    // Segments a seek to `t` lands in (the user wants to hear them: mark them done).
    function entered(segs, t) {
      const out = [];
      (segs || []).forEach((s, i) => { if (t >= s.start && t < s.end) out.push(i); });
      return out;
    }
    // Valid segments from /api/segments, sorted by start.
    const cleanSegments = (list) => (Array.isArray(list) ? list : [])
      .filter((s) => s && Number.isFinite(s.start) && Number.isFinite(s.end) && s.end > s.start && s.start >= 0)
      .map((s) => ({ start: s.start, end: s.end })).sort((a, b) => a.start - b.start);
    return { WINDOW, REPLACE_BLOCKS, isPlaceholder, placeholder, toResolve, swap, canReplace, segmentAt, segmentTarget, entered, cleanSegments };
  })();
  // ==== match-core:end ====

  // ---------- lyrics core ----------
  // Pure helpers for the lyrics view (production plan P1.3): LRC parsing, word timing from /api/lyrics `words`,
  // and the per-track timing offset. Unit-checked from node by slicing the text between these sentinel comments.
  // ==== lyrics-core:start ====
  const LY = (() => {
    const OFFSET_MAX = 10000; // ms either way, as the server allows
    const NUDGE = 500;
    const SOURCES = { lrclib: "LRCLIB", "lrclib-pinned": "LRCLIB (your pick)", simpmusic: "SimpMusic",
      youtube: "YouTube Music", betterlyrics: "BetterLyrics", navidrome: "your library" };
    // LRC text -> [{ t: seconds, text }] sorted by time. Handles several stamps per line, [mm:ss(.x|.xx|.xxx)],
    // [offset:±ms] (positive = sooner), skips [ar:]/[ti:]-style metadata and strips enhanced <mm:ss.xx> word tags.
    // The server's word timing (`words`) is aligned with exactly these lines (lyrics_providers.parse_lrc).
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
    // Lines with `words` ([{t, d?, text}] sorted) where the server sent word timing for them. Pairs by index, and
    // only when there is one entry per line: misaligned timing is worse than none (the line mode stays).
    function attachWords(lines, words) {
      if (!Array.isArray(words) || words.length !== lines.length) return lines;
      return lines.map((l, i) => {
        const w = (Array.isArray(words[i]) ? words[i] : [])
          .filter((x) => x && Number.isFinite(x.t) && typeof x.text === "string" && x.text)
          .map((x) => ({ t: x.t, d: Number.isFinite(x.d) ? x.d : null, text: x.text }))
          .sort((a, b) => a.t - b.t);
        return w.length ? { ...l, words: w } : l;
      });
    }
    // Index of the last entry of sorted `times` at or before `now`, else -1 (binary search).
    function indexAt(times, now) {
      let lo = 0, hi = times.length - 1, i = -1;
      while (lo <= hi) {
        const mid = (lo + hi) >> 1;
        if (times[mid] <= now) { i = mid; lo = mid + 1; } else hi = mid - 1;
      }
      return i;
    }
    // The word being sung in a line at `now`: the last one started, -1 before the first.
    const wordAt = (words, now) => indexAt((words || []).map((w) => w.t), now);
    // offset_ms: + = the lyrics come later. The playhead in lyrics time, and the playhead for a line's time.
    const lyricTime = (audioTime, offsetMs) => audioTime - (Number(offsetMs) || 0) / 1000;
    const seekTime = (lineTime, offsetMs) => Math.max(0, lineTime + (Number(offsetMs) || 0) / 1000);
    const nudge = (offsetMs, delta) => Math.max(-OFFSET_MAX, Math.min(OFFSET_MAX, Math.round((Number(offsetMs) || 0) + delta)));
    // "+0.5 s" / "0 s" / "−1.5 s".
    function fmtOffset(ms) {
      const s = Math.round((Number(ms) || 0) / 100) / 10;
      return s === 0 ? "0 s" : `${s > 0 ? "+" : "−"}${Math.abs(s).toFixed(1)} s`;
    }
    const sourceLabel = (p) => SOURCES[p] || p || "";
    return { OFFSET_MAX, NUDGE, parseLRC, attachWords, indexAt, wordAt, lyricTime, seekTime, nudge, fmtOffset, sourceLabel };
  })();
  // ==== lyrics-core:end ====

  const navidromeUrl = (path) => `${location.protocol}//${location.hostname}:4533${path}`;
  const cur = () => state.queue[state.index];
  // What a queued / remembered track keeps (queue, session, "plays"). `auto` (an autoplay item) is dropped
  // here; only the live queue and its saved session keep it, via qslim.
  const slim = (t) => (M.isPlaceholder(t) ? M.placeholder(t, t.id) : {
    id: t.id, title: t.title, channel: t.channel, duration: t.duration, source: t.source || srcKey(t.id),
    ...(t.codec ? { codec: t.codec } : {}), ...(t.album ? { album: t.album } : {}), ...(t.album_id ? { album_id: t.album_id } : {}),
    ...(Array.isArray(t.artists) && t.artists.length ? { artists: t.artists.map((a) => ({ name: a?.name, id: a?.id ?? null })) } : {}),
    ...(t.explicit ? { explicit: true } : {}), ...(t.nd_id ? { nd_id: t.nd_id } : {}),
    ...(t.replaced_from ? { replaced_from: t.replaced_from } : {}),
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
  // Kept tracks Navidrome has scanned: track id -> Navidrome song id, from server lists (`nd_id`) and history.
  const ndIds = new Map();
  function noteKept(tracks) {
    for (const t of tracks || []) {
      if (!t || !t.id || typeof t.kept !== "boolean") continue;
      if (t.kept) keptIds.add(t.id); else keptIds.delete(t.id);
      if (t.kept && t.nd_id) ndIds.set(t.id, t.nd_id); else if (!t.kept) ndIds.delete(t.id);
    }
  }
  function keepState(id) {
    if (!id) return "none";
    if (srcKey(id) === "nd") return "kept"; // Navidrome results are the library itself
    if (state.history.some((h) => h.id === id || h.upgraded_from === id) || keptIds.has(id)) return "kept";
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
  function shelf({ title, ic, sub, more, action, body }) {
    return `<section class="shelf">
      <div class="shelf-head"><h2>${ic ? icon(ic) : ""}${esc(title)}</h2>${more ? `<a class="more" href="${more}" aria-label="See all">${icon("chev-right")}</a>` : ""}${action || ""}</div>
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

  // ---------- Home shelves (/api/home) ----------
  const homeShelf = (id) => (state.home?.shelves || []).find((s) => s.id === id && Array.isArray(s.items) && s.items.length);
  const homeItems = (id) => homeShelf(id)?.items || [];
  const skeletonCards = () => `<div class="scroller">${'<div class="card"><div class="card-art sk"></div></div>'.repeat(4)}</div>`;

  // Quick picks: columns of 4 rows, scrolled sideways one column at a time; the next column peeks in.
  const quickRows = (items) => `<ul class="qp" data-shelf="quick">${items.map((t, i) => row(t, i, "quick")).join("")}</ul>`;

  function trackCards(items, list) {
    return `<div class="scroller" data-shelf="${list}">${items.map((t, i) => `
      <div class="card" data-track="${esc(t.id)}">
        <button class="card-art" data-play="${i}" data-list="${list}" aria-label="Play ${esc(t.title)}">${art(t.id)}${eqIfPlaying(t.id)}</button>
        <div class="card-meta"><div class="card-text"><div class="card-t">${esc(t.title)}</div>
          <div class="card-s">${esc(t.channel)}${t.plays > 1 ? ` · ${t.plays} plays` : ""}</div></div>${moreBtn(t, i, list)}</div>
      </div>`).join("")}</div>`;
  }

  // Top artists: the artist page when the id is known, else a search for the name.
  function topArtistCards(items) {
    return `<div class="scroller" data-shelf="top_artists">${items.map((a) => {
      const inner = `<span class="card-art">${a.cover_id ? art(a.cover_id) : icon("person")}</span>
        <div class="card-t">${esc(a.name)}</div><div class="card-s">${a.count} play${a.count === 1 ? "" : "s"}</div>`;
      return ARTIST_ID.test(a.id || "") ? `<a class="card av" href="#artist/${encodeURIComponent(a.id)}">${inner}</a>`
        : `<button class="card av" data-search="${esc(a.name)}">${inner}</button>`;
    }).join("")}</div>`;
  }

  // Mixed for you: one card per seed artist; a tap starts radio from that seed.
  function mixCards(items) {
    return `<div class="scroller" data-shelf="mixed">${items.map((m, i) => `
      <button class="card mix" data-mix="${i}" aria-label="Start radio: ${esc(m.name)} mix">
        <span class="card-art">${art(m.seed.id)}<span class="mix-tag">${icon("radio")}Radio</span></span>
        <div class="card-t">${esc(m.name)} mix</div>
        <div class="card-s">${esc(m.with?.length ? `With ${m.with.join(", ")}` : `From “${m.seed.title}”`)}</div>
      </button>`).join("")}</div>`;
  }

  // ---------- views ----------
  function renderHome() {
    const plays = store.get("plays", []);
    const view = $("#view-home");
    // Re-rendering (a dislike, a refresh) keeps each shelf's sideways scroll.
    const scrolled = new Map($$("[data-shelf]", view).map((el) => [el.dataset.shelf, el.scrollLeft]));
    let html = `<h1 class="page-title">${greeting()}</h1>
      <div class="seg" role="tablist">
        <button role="tab" class="${state.homeTab === "foryou" ? "on" : ""}" data-home-tab="foryou">For you</button>
        <button role="tab" class="${state.homeTab === "library" ? "on" : ""}" data-home-tab="library">Library</button>
      </div>`;

    if (state.homeTab === "foryou") {
      let s;
      if (!state.home) html += shelf({ title: "Quick picks", ic: "sparkle", body: skeletonCards() });
      if ((s = homeShelf("quick"))) {
        html += shelf({ title: s.title, ic: "sparkle", sub: "Based on what you've been playing", body: quickRows(s.items),
          action: `<button class="link-btn shelf-act" data-play-all="quick">Play all</button>` });
      }
      if ((s = homeShelf("on_repeat"))) html += shelf({ title: s.title, ic: "repeat", sub: "Your most played this month", body: trackCards(s.items, "on_repeat") });
      if ((s = homeShelf("mixed"))) html += shelf({ title: s.title, ic: "radio", sub: "Radio from artists you play", body: mixCards(s.items) });
      // Old favourites once the log reaches back far enough; until then, what was played on this device.
      html += (s = homeShelf("jump_back"))
        ? shelf({ title: s.title, ic: "clock", sub: "Songs you loved a while ago", body: trackCards(s.items, "jump_back") })
        : shelf({ title: "Jump back in", ic: "clock", body: posters(plays, "recent") });
      if ((s = homeShelf("liked"))) html += shelf({ title: s.title, ic: "heart", body: trackCards(s.items, "liked") });
      if ((s = homeShelf("top_artists"))) html += shelf({ title: s.title, ic: "person", body: topArtistCards(s.items) });
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
    view.innerHTML = html;
    for (const [k, x] of scrolled) { const el = $(`[data-shelf="${k}"]`, view); if (el) el.scrollLeft = x; }
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
      case "converting": return j.lossless ? "Saving FLAC…" : "Converting to Opus…";
      case "done": return j.rescanned ? "Saved · Navidrome is rescanning" : "Saved to library";
      case "error": return j.hint || j.error || "Failed";
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
              <span class="row-t">${esc(j.title || j.video_id)}${j.lossless ? ` <span class="lbadge">Lossless</span>` : ""}</span>
              <span class="row-s"${j.status === "error" && j.error ? ` title="${esc(j.error)}"` : ""}>${esc(jobLabel(j))}</span>
              ${j.status === "error" && j.hint && j.error && j.error !== j.hint ? `<span class="row-e">${esc(j.error)}</span>` : ""}
              ${j.status === "done" && j.warning ? `<span class="row-e">${esc(j.warning)}</span>` : ""}
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
      dropFromHome((x, kind) => (kind === "artists" ? (hid.id && x.id === hid.id) || nameKey(x.name) === nameKey(hid.name) : byArtist(x, hid)));
    } else {
      loadHome();
    }
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
      if (r.view === "home" && homeDue()) loadHome();
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
    for (const h of state.history) if (h && h.id && h.nd_id) ndIds.set(h.id, h.nd_id);
    // Search and browse pages only need their + buttons updated (a re-render would reset their scrollers).
    if (state.view !== "search" && state.view !== "detail") RENDER[state.view]();
    refreshKeepButtons();
    updatePlayer();
  }

  async function loadLibrary() {
    try { state.library = await api("/api/library/recent"); } catch { state.library = []; }
    if (state.view === "home") renderHome();
  }

  let homeAt = 0, homeTries = 0, homeTimer;
  async function loadHome({ again = false } = {}) {
    clearTimeout(homeTimer);
    if (!again) homeTries = 0;
    homeAt = Date.now();
    let body;
    try { body = await api("/api/home"); } catch { homeAt = 0; return; }
    if (!body || !Array.isArray(body.shelves)) return;
    for (const s of body.shelves) if (s.kind === "tracks") noteKept(s.items);
    const moods = body.shelves.find((s) => s.kind === "moods");
    if (moods?.items?.length) state.tiles = moods.items;
    const same = JSON.stringify(body.shelves) === JSON.stringify(state.home?.shelves);
    state.home = body;
    store.set("home", body);
    if (!same && state.view === "home") renderHome();
    // The server is building (first start, or the copy was due): look again once the build had time to land.
    if (body.stale && homeTries < 3) homeTimer = setTimeout(() => { homeTries++; loadHome({ again: true }); }, 8000 * (homeTries + 1));
  }
  const homeDue = () => Date.now() - homeAt > HOME_TTL;

  // "Not interested": drop it from Home at once, then tell the server (14-day cooldown; a second song by the
  // same artist hides the artist, which the answer's `blocked` says).
  function dropFromHome(drop) {
    if (!state.home) return;
    let changed = false;
    for (const s of state.home.shelves) {
      if (!["tracks", "seeds", "artists"].includes(s.kind)) continue;
      const left = s.items.filter((x) => !drop(s.kind === "seeds" ? x.seed : x, s.kind));
      if (left.length !== s.items.length) { s.items = left; changed = true; }
    }
    if (!changed) return;
    state.home.shelves = state.home.shelves.filter((s) => s.items.length);
    store.set("home", state.home);
    if (state.view === "home") renderHome();
  }
  async function notInterested(t) {
    dropFromHome((x, kind) => kind !== "artists" && x.id === t.id);
    let r;
    try {
      r = await api(`/api/dislikes/${encodeURIComponent(t.id)}`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(slim(t)),
      });
    } catch (e) {
      toast(`Couldn't save that: ${e.message}`);
      loadHome();
      return;
    }
    if (r?.blocked) {
      toast(`Hidden “${r.blocked.name}”: that's two of their songs you weren't into`);
      blockedChanged(r.blocked);
    } else {
      toast("Got it. You won't see this song for a while");
    }
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

  async function loadSettings() {
    try { state.settings = (await api("/api/settings")).values || {}; } catch { /* keep the defaults */ }
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
    prefetch([cur() && playId(cur())]);
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

  // P1.5: a kept track Navidrome has scanned streams from the library (faster, and it works while YouTube
  // walls us); the UI keeps showing the original track. If that stream fails, recovery plays the original once.
  const libraryOn = () => state.sources.some((x) => x.key === "nd") && state.settings.prefer_library !== false;
  const ndIdOf = (t) => t?.nd_id || ndIds.get(t?.id) || null;
  const playId = (t) => L.streamId(t, { ndId: ndIdOf(t), ndOn: libraryOn(), failed: t === cur() && rec.ndFailed });
  const streamUrl = (t) => `/api/stream/${encodeURIComponent(playId(t))}`;
  // The current src is the Navidrome copy of a non-library track.
  const viaLibrary = () => srcKey(cur()?.id) !== "nd" && /^\/api\/stream\/nd%3A/.test(audio.getAttribute("src") || "");

  function load() {
    const t = cur();
    if (!t) return;
    if (M.isPlaceholder(t)) { loadPlaceholder(t); return; }
    pendingPh = null;
    closeListen();
    resume = null;
    newTrack(""); // first: it clears the last track's library fallback, which streamUrl reads
    loadSegments(t);
    rec.src = streamUrl(t);
    audio.src = rec.src;
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
    resolveAhead();
  }

  // Play (or resume) whatever is current. A restored session has no src yet: set it on this first
  // play (inside the user's gesture, which iOS needs) and seek once metadata is in.
  function playAudio() {
    const t = cur();
    if (!t) return;
    // A placeholder (e.g. a restored queue stopped on one): look it up first; a tap during the lookup waits for it.
    if (M.isPlaceholder(t)) { if (pendingPh !== t.id) loadPlaceholder(t); return; }
    if (!resume) {
      // A src that failed and wasn't reloaded (Reef stopped on it, or it's mid-recovery): retry it now.
      if (rec.broken && !rec.busy) { retryNow(true); return; }
      if (rec.busy) { rec.want = true; setLoading(true); } // the pending reload will play
      audio.play().catch(() => {});
      return;
    }
    const pos = resume.position;
    resume = null;
    loadSegments(t);
    rec.pos = pos;
    rec.seekTo = pos > 0 ? pos : null; // applied once metadata is in (applySeek)
    rec.src = streamUrl(t);
    audio.src = rec.src;
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
    seekTo(0);
  }

  function prev() {
    const pos = playPos();
    const i = Q.prevIndex(state.queue.length, state.index);
    if (pos > 3 || i < 0) { seekTo(0); return; }
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
    resolveAhead();
  }

  // ---------- placeholders (P1.8) ----------
  // Placeholder entries ({q: true, title, artist}) are looked up with /api/match only once they come within
  // M.WINDOW of the current track, and swapped for the song in place (queue and shuffle snapshot). Not found ->
  // dropped with a toast; a failed lookup (server / network) is retried after 30 s, or skipped when it's due to play.
  const resolving = new Map(); // placeholder id -> Promise of the track (null when not found / failed)
  const resolveFailedAt = new Map(); // placeholder id -> when its lookup last failed (not a 404)
  let pendingPh = null; // the placeholder load() is waiting on; any other load() cancels the wait

  function swapEverywhere(id, track) {
    const r = M.swap(state.queue, state.index, id, track);
    state.queue = r.queue; state.index = r.index;
    if (state.original) state.original = M.swap(state.original, -1, id, track).queue;
    return r;
  }
  function dropEverywhere(id) {
    const i = state.queue.findIndex((x) => x.id === id);
    if (i >= 0) { const r = Q.removeAt(state.queue, state.index, i); state.queue = r.queue; state.index = r.index; }
    if (state.original) state.original = state.original.filter((x) => x.id !== id);
  }

  function resolvePlaceholder(t) {
    if (resolving.has(t.id)) return resolving.get(t.id);
    const body = { title: t.title, artist: t.artist || t.channel || "", duration: t.duration || null,
      ...(t.album ? { album: t.album } : {}), ...(t.key ? { key: t.key } : {}) };
    const p = api("/api/match", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })
      .then((r) => {
        if (!r?.track?.id) throw Object.assign(new Error("No match found"), { status: 404 });
        noteKept([r.track]);
        const track = slim(r.track);
        if (state.queue.some((x) => x.id === t.id)) { swapEverywhere(t.id, track); queueChanged(); }
        return track;
      })
      .catch((e) => {
        if (e.status === 404) {
          if (state.queue.some((x) => x.id === t.id)) {
            const wasCurrent = cur()?.id === t.id;
            dropEverywhere(t.id);
            if (!wasCurrent) queueChanged(); // loadPlaceholder moves on from the current one
            toast(`Skipped “${t.title}”: not found`);
          }
        } else {
          resolveFailedAt.set(t.id, Date.now());
          console.warn("match: lookup failed for", t.title, e.message);
        }
        return null;
      })
      .finally(() => resolving.delete(t.id));
    resolving.set(t.id, p);
    return p;
  }

  function resolveAhead() {
    for (const i of M.toResolve(state.queue, state.index)) {
      const t = state.queue[i];
      if (pendingPh === t.id || resolving.has(t.id) || Date.now() - (resolveFailedAt.get(t.id) || 0) < 30000) continue;
      resolvePlaceholder(t);
    }
  }

  // The current entry is a placeholder: stop what was playing, look it up, then play the song (or move on).
  function loadPlaceholder(t) {
    closeListen();
    resume = null;
    newTrack("");
    loadSegments(t); // clears the last track's segments
    audio.pause();
    pendingPh = t.id;
    const wasLast = state.index === state.queue.length - 1;
    updatePlayer();
    markPlaying();
    setProgress(0, t.duration || 0);
    setLoading(true);
    renderQueue();
    resolvePlaceholder(t).then((track) => {
      if (pendingPh !== t.id) return; // the user moved on meanwhile
      pendingPh = null;
      setLoading(false);
      if (track && cur()?.id === track.id) { load(); return; }
      if (state.queue.some((x) => x.id === t.id)) { // the lookup failed (not "not found"): skip it for now
        toast(`Couldn't look up “${t.title}”`);
        rec.skipping = true; next(); rec.skipping = false;
        return;
      }
      // Not found: dropEverywhere made the following entry current (or the previous one, if this was the last).
      if (wasLast || !cur()) { updatePlayer(); markPlaying(); saveSession(0); renderQueue(); return; }
      rec.skipping = true; load(); rec.skipping = false;
    });
  }

  // ---------- SponsorBlock at play time (P1.9) ----------
  // Segments of the current YouTube track (not its library copy: a keep may have cut them already). Each one is
  // jumped over once on timeupdate, never while recovery holds the playhead (a pending seek, a reload); a seek
  // into one (seekTo) plays it. `token` ties the list to the play token, so a late answer for an old track is dropped.
  const seg = { token: -1, list: [], done: new Set(), toasted: false };
  function loadSegments(t) {
    Object.assign(seg, { token: rec.token, list: [], done: new Set(), toasted: false });
    if (!t || M.isPlaceholder(t) || srcKey(t.id) !== "yt" || state.settings.sponsorblock_play === false) return;
    const token = rec.token;
    api(`/api/segments/${encodeURIComponent(t.id)}`).then((list) => {
      if (token !== rec.token || seg.token !== token) return;
      seg.list = M.cleanSegments(list);
      skipSegment();
    }).catch(() => { /* SponsorBlock down: play everything */ });
  }
  function skipSegment() {
    if (!seg.list.length || seg.token !== rec.token || !cur() || resume || rec.busy || rec.broken || rec.seekTo != null) return;
    if (audio.paused || audio.readyState < 1 || viaLibrary()) return;
    const i = M.segmentAt(seg.list, seg.done, audio.currentTime);
    if (i < 0) return;
    seg.done.add(i);
    audio.currentTime = M.segmentTarget(seg.list[i], isFinite(audio.duration) ? audio.duration : 0);
    if (!seg.toasted) { seg.toasted = true; toast("Skipped non-music part"); }
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
    return i >= 0 && i !== state.index && !M.isPlaceholder(state.queue[i]) ? playId(state.queue[i]) : null;
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

  // ---------- listen log ----------
  // Each play is reported to /api/listens when it ends or is left (another track, the page hidden or closed):
  // the seconds actually played, added up from timeupdate deltas while playing (seeks don't count, see
  // L.playedDelta), and whether it was skipped. Reporting the same play again (hidden, then skipped) updates the
  // server's entry: it's keyed by id + started_at. The "plays" list in localStorage (Recently played) is separate.
  let lis = null; // the current play: {track, startedAt (epoch s), played (s), lastT, lastWall, via, sent}
  function sendListen(body) {
    const json = JSON.stringify(body);
    try {
      if (navigator.sendBeacon && navigator.sendBeacon("/api/listens", new Blob([json], { type: "application/json" }))) return;
    } catch { /* a browser that refuses JSON beacons: fall back */ }
    try {
      fetch("/api/listens", { method: "POST", keepalive: true, headers: { "Content-Type": "application/json" }, body: json }).catch(() => {});
    } catch { /* best effort */ }
  }
  function reportListen(skipped) {
    const body = L.listenReport(lis, { skipped });
    if (!body) return;
    lis.sent = { played: body.played_s, skipped };
    sendListen(body);
  }
  // The current play is over: report it and forget it. Leaving a track early is a skip, unless recovery moved
  // past it (rec.skipping: the track couldn't play).
  function closeListen(skipped = !rec.skipping) {
    if (!lis) return;
    reportListen(skipped);
    lis = null;
  }
  // On timeupdate: add what was played since the last one (only while really playing the current src).
  function trackListen() {
    const t = cur();
    if (!t || resume || audio.paused || rec.seekTo != null || rec.busy || rec.broken) { if (lis) lis.lastT = null; return; }
    if (!lis || lis.track.id !== t.id) {
      closeListen();
      lis = { track: slim(t), startedAt: Date.now() / 1000, played: 0, lastT: null, lastWall: null, via: "", sent: null };
    }
    const now = Date.now();
    lis.played += L.playedDelta(lis.lastT, lis.lastWall, audio.currentTime, now, audio.playbackRate);
    lis.lastT = audio.currentTime;
    lis.lastWall = now;
    if (viaLibrary()) lis.via = "nd";
  }

  // ---------- session (queue persistence + resume) ----------
  // store "session": the queue, where we are in it and the modes, so a reload or a killed Home Screen
  // app comes back paused where it was. `resume` is set while a restored track has no src yet.
  let resume = null;
  let sessionRev = Number(store.get("session", null)?.rev) || 0;
  let posSavedAt = 0;
  function saveSession(position) {
    const t = cur();
    if (position == null) position = t ? playPos() : 0;
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

  // ---------- recovery ----------
  // `rec.token` (the play token) changes on every track change; each async step of a recovery checks it and
  // drops stale work, so only the track that failed is ever retried. A failure is an `error`, an `ended` well
  // before the track's length, or a playhead that stood still past R.stallLimit while playing (`waiting` /
  // `stalled`, or a silent freeze, which WebKit does when a mid-song request fails): remember the position,
  // probe the stream for its block, then (after a backoff) reload it with ?fresh=<token>-<n> (the server
  // re-resolves once per distinct token), seek back once metadata is in and play if it was playing. R decides
  // when to give up, skip, or stop.
  // Prefetches go through /api/prefetch, never the element, so their failures never land here.
  const rec = {
    token: Math.floor(Math.random() * 1e6), // the play token (random start: ?fresh= tokens differ across page loads)
    tries: 0, // reloads made for the current track (R.onFailure); cleared by R.settledTries, not by `playing`
    from: 0, // where the last reload resumed the track (R.settledTries)
    reloads: 0, // ?fresh= counter within a token (never reused, unlike `tries`, which a user retry resets)
    dead: 0, content: 0, // tracks given up on in a row / of those, for a FINAL block (R.afterGiveUp)
    busy: false, // probing, or waiting out the backoff before a reload
    broken: false, // the element's src failed and hasn't been reloaded yet (play reloads it)
    outage: false, // Reef paused because of a failure: `online` (or a YouTube reset) resumes
    want: false, // the user wants playback (follows play / pause); a reload plays only if set
    pos: 0, // last known position (timeupdate / seeking); a reload seeks back to it
    seekTo: null, // position still to restore once the current src has metadata
    src: "", // the src the player set for the current track
    timer: 0,
    lastT: 0, movedAt: 0, // the stall watchdog: the playhead seen last, and when it last moved
    skipping: false, // the next load() is a recovery skip: keep the dead-track count
    fromEnd: false, // the failure was an early `ended`: if the reloaded src really ends there, move on
    ndFailed: false, // P1.5: the library copy failed for this play; the original id streams from now on
  };

  // Where playback is (or will resume from): a restored session, a pending seek, a broken src, the element.
  function playPos() {
    if (resume) return resume.position;
    if (!audio.getAttribute("src")) return 0;
    if (rec.seekTo != null) return rec.seekTo;
    return rec.busy || rec.broken ? rec.pos : audio.currentTime || 0;
  }

  function seekTo(t) {
    if (seg.token === rec.token) for (const i of M.entered(seg.list, t)) seg.done.add(i); // a seek into a segment plays it
    if (resume) { resume.position = t; setProgress(t, duration()); saveSession(); return; }
    if (rec.busy || rec.broken || rec.seekTo != null) {
      rec.pos = t;
      if (rec.seekTo != null) rec.seekTo = t;
      setProgress(t, duration());
      saveSession();
      return;
    }
    audio.currentTime = t;
  }

  // load(): a new track gets a new token; everything pending for the old one is dropped.
  function newTrack(src) {
    rec.token++;
    clearTimeout(rec.timer);
    Object.assign(rec, { tries: 0, from: 0, reloads: 0, busy: false, broken: false, outage: false, pos: 0, seekTo: null, src, timer: 0, movedAt: Date.now(), fromEnd: false, ndFailed: false });
    if (!rec.skipping) { rec.dead = 0; rec.content = 0; } // the user picked it: a fresh run
    rec.skipping = false;
  }

  // Once the src the player set has metadata, seek back to where it was.
  function applySeek() {
    if (rec.seekTo == null || audio.getAttribute("src") !== rec.src || audio.readyState < 1) return;
    const pos = rec.seekTo, d = audio.duration, fromEnd = rec.fromEnd;
    rec.seekTo = null;
    rec.fromEnd = false;
    if (pos > 0 && (!isFinite(d) || pos < d - 1)) audio.currentTime = pos;
    else if (fromEnd && pos > 0) next(); // the stream really is that short (its listed length was off)
  }

  async function probe(t) {
    const url = audio.getAttribute("src") || streamUrl(t); // same ?fresh= token: the probe never evicts
    const ctl = typeof AbortController === "function" ? new AbortController() : null;
    const timer = ctl ? setTimeout(() => ctl.abort(), 15000) : 0;
    try {
      const r = await fetch(url, { headers: { Range: "bytes=0-1" }, cache: "no-store", ...(ctl ? { signal: ctl.signal } : {}) });
      let body = null;
      if (r.ok) { try { r.body?.cancel().catch(() => {}); } catch { /* nothing to cancel */ } } else body = await r.json().catch(() => null);
      return R.probeVerdict(r.status, body);
    } catch {
      return R.probeVerdict(0, null);
    } finally {
      clearTimeout(timer);
    }
  }

  async function fail(why) {
    const t = cur();
    if (!t || resume || !audio.getAttribute("src") || rec.busy) return;
    if (why === "error" && (!audio.error || audio.error.code === 1)) return; // stale (src replaced) or aborted
    const token = rec.token;
    if (why === "ended") { rec.want = true; rec.fromEnd = true; } // it was playing: `pause` fired just before `ended`
    // Some engines reset currentTime on an error: fall back to the last position seen.
    if (rec.seekTo != null) rec.pos = rec.seekTo; else if (audio.currentTime > 0) rec.pos = audio.currentTime;
    if (viaLibrary() && !rec.ndFailed) {
      // The library copy failed (Navidrome down, file moved): play the original instead, once, from here.
      console.info("recovery:", why, t.id, "library stream failed, trying the original");
      rec.ndFailed = true;
      rec.seekTo = null;
      reload(token);
      return;
    }
    rec.busy = true;
    rec.broken = true;
    rec.seekTo = null;
    setLoading(true);
    updatePlayState();
    const v = await probe(t);
    if (token !== rec.token || !rec.busy) return;
    console.info("recovery:", why, t.id, `try ${rec.tries}/${R.MAX_TRIES}`, v.ok ? "stream answers" : v.block);
    const d = R.onFailure({ block: v.ok ? null : v.block, tries: rec.tries, online: navigator.onLine, early: why === "stall" && audio.readyState < 1 });
    if (d.do === "keep") { rec.busy = false; rec.broken = false; rec.movedAt = Date.now(); return; }
    if (d.do === "retry") {
      rec.tries = d.attempt;
      rec.timer = setTimeout(() => reload(token), d.delay);
      return;
    }
    rec.busy = false;
    if (d.do === "wait") { // offline: stop here, `online` picks it up
      if (rec.want) { rec.outage = true; audio.pause(); toast(R.OFFLINE); }
      setLoading(false);
      return;
    }
    giveUp(v);
  }

  function giveUp(v) {
    setLoading(false);
    // Paused by the user, or the sleep timer's "end of track": don't start the next one, just say why.
    if (!rec.want || sleep.mode === "end") {
      if (sleep.mode === "end" && rec.want) { setSleep("off", { quiet: true }); audio.pause(); }
      toast(v.hint || R.FALLBACK);
      return;
    }
    if (M.canReplace(cur(), v.block, { enabled: state.settings.replace_dead_tracks !== false })) { replaceDead(cur(), v); return; }
    moveOn(v);
  }

  // P1.8: the same song from another upload (POST /api/replace), swapped in at the same place in the queue (and the
  // session); history, likes and playlists keep the original. `replaced_from` marks it, so it's never replaced in turn.
  const deadIds = []; // ids already replaced this page load: never offered back as a replacement
  async function replaceDead(t, v) {
    const token = rec.token;
    rec.busy = true; // holds off the stall watchdog and a play tap (which only sets rec.want) while we look
    setLoading(true);
    let r = null;
    try {
      r = await api("/api/replace", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ track: slim(t), exclude: deadIds.slice(-20) }) });
    } catch (e) {
      console.info("recovery:", t.id, "no replacement", e.status || e.message);
    }
    if (token !== rec.token || cur()?.id !== t.id) return; // the user moved on meanwhile
    rec.busy = false;
    setLoading(false);
    if (!deadIds.includes(t.id)) deadIds.push(t.id);
    if (!r?.track?.id) { moveOn(v); return; }
    noteKept([r.track]);
    swapEverywhere(t.id, { ...slim(r.track), replaced_from: t.id });
    console.info("recovery:", t.id, "replaced by", r.track.id, r.source, r.score);
    toast(`Played a replacement for “${t.title}”`);
    rec.skipping = true; load(); rec.skipping = false;
  }

  function moveOn(v) {
    rec.dead++;
    if (R.isFinal(v.block)) rec.content++;
    const d = R.afterGiveUp({ dead: rec.dead, content: rec.content, queueLength: state.queue.length, block: v.block, hint: v.hint });
    toast(d.message);
    if (d.do === "next") { rec.skipping = true; next(); rec.skipping = false; return; }
    audio.pause(); // stop on the failed track; play (or `online`, if it's an outage) retries it
    rec.outage = d.outage;
  }

  // Reload the current track's src with a fresh token, seek back, play if wanted.
  function reload(token) {
    const t = cur();
    if (!t || token !== rec.token) return;
    clearTimeout(rec.timer);
    rec.timer = 0;
    rec.busy = false;
    rec.broken = false;
    rec.reloads++;
    const src = `${streamUrl(t)}?fresh=${token}-${rec.reloads}`;
    rec.src = src;
    rec.seekTo = rec.pos > 0 ? rec.pos : null;
    rec.from = rec.pos > 0 ? rec.pos : 0;
    rec.movedAt = Date.now();
    audio.src = src;
    setLoading(true);
    if (!rec.want) return; // canplay clears the spinner; play reuses this src
    audio.play().catch((e) => {
      if (token !== rec.token || audio.getAttribute("src") !== src) return;
      // iOS refusing a play() outside a gesture: show the paused state (a tap plays this src) instead of looping.
      if (e && e.name === "NotAllowedError") { rec.want = false; setLoading(false); updatePlayState(); }
    });
  }

  // A user (play on a broken src), `online`, or a YouTube reset: reload the current track now.
  function retryNow(play) {
    if (!cur() || resume) return;
    clearTimeout(rec.timer);
    Object.assign(rec, { tries: 0, busy: false, outage: false, timer: 0 });
    if (play) rec.want = true;
    if (rec.seekTo == null && audio.getAttribute("src")) rec.pos = playPos();
    reload(rec.token);
  }

  // `ended` well before the track's known length, e.g. at 1:10 of a 5:21 song. The element's own duration
  // can't tell: WebKit shortens it to what it managed to load when the rest of the stream fails.
  function endedEarly() {
    const len = Number(cur()?.duration) || 0;
    return len > 0 && audio.currentTime < len - Math.max(10, len * 0.05);
  }

  // The stall watchdog. `waiting` / `stalled` aren't reliable: WebKit can also freeze the playhead with
  // readyState 4 and fire nothing when a mid-song request fails. So: while playing, a playhead that hasn't moved
  // for R.stallLimit is a failure. Wall-clock based, so a throttled (background) timer only fires late.
  setInterval(() => {
    const now = Date.now();
    if (audio.currentTime !== rec.lastT) { rec.lastT = audio.currentTime; rec.movedAt = now; return; }
    if (!cur() || resume || rec.busy || rec.broken || audio.paused || audio.ended || !audio.getAttribute("src")) { rec.movedAt = now; return; }
    if (now - rec.movedAt > R.stallLimit(audio.readyState)) fail("stall");
  }, 2000);

  // Back online: resume only what Reef itself paused for the outage.
  window.addEventListener("online", () => { if (rec.outage && cur() && !resume) retryNow(true); });

  // ---------- audio events ----------
  audio.addEventListener("timeupdate", () => {
    trackListen();
    if (rec.seekTo == null && !rec.busy && !rec.broken) {
      rec.pos = audio.currentTime;
      if (rec.tries) rec.tries = R.settledTries({ tries: rec.tries, from: rec.from, at: rec.pos });
      setProgress(audio.currentTime, duration());
      if (lyr.times.length) syncLyrics();
      skipSegment();
    }
    if (sleep.mode === "time") checkSleep();
    if (!audio.paused && Date.now() - posSavedAt >= 5000) saveSession();
  });
  audio.addEventListener("seeking", () => {
    if (lis) lis.lastT = null; // a jump isn't listening time
    if (rec.seekTo != null || rec.busy || rec.broken) return;
    rec.pos = audio.currentTime;
    if (rec.tries) rec.from = rec.pos; // a jump isn't progress: count it from where the playhead landed
  });
  audio.addEventListener("loadedmetadata", () => { applySeek(); setProgress(playPos(), duration()); });
  audio.addEventListener("canplay", () => { applySeek(); if (audio.paused) setLoading(false); });
  audio.addEventListener("play", () => { rec.want = true; updatePlayState(); });
  audio.addEventListener("pause", () => { rec.want = false; updatePlayState(); if (cur()) saveSession(); });
  audio.addEventListener("ended", () => {
    updatePlayState();
    if (!endedEarly()) closeListen(false); // played to the end (an early end is a failure: recovery below)
    if (sleep.mode === "end") { setSleep("off", { quiet: true }); saveSession(); return; } // pause instead of advancing
    // WebKit can end a track early when its stream dies mid-song (it gives up after the 403s): recover instead.
    if (endedEarly()) { fail("ended"); return; }
    next();
  });
  audio.addEventListener("error", () => { fail("error"); });
  audio.addEventListener("loadstart", () => { if (audio.getAttribute("src")) setLoading(true); });
  audio.addEventListener("waiting", () => setLoading(true));
  audio.addEventListener("playing", () => {
    setLoading(false);
    rec.movedAt = Date.now();
    if (rec.busy) { clearTimeout(rec.timer); rec.timer = 0; rec.busy = false; } // a stall that sorted itself out
    // It plays: the run of unplayable tracks is over. Its own tries wait for sustained progress (timeupdate).
    Object.assign(rec, { dead: 0, content: 0, outage: false, broken: false });
    if (prefetchNext) { prefetch([prefetchNext]); prefetched = prefetchNext; prefetchNext = null; }
    if (lyr.on && cur()) fetchLyrics(cur()); // warm, so opening the player shows them at once
  });
  audio.addEventListener("pause", () => setLoading(false));
  audio.addEventListener("ended", () => setLoading(false));
  audio.addEventListener("emptied", () => { if (!audio.getAttribute("src")) setLoading(false); });

  document.addEventListener("visibilitychange", () => {
    checkSleep();
    if (document.visibilityState === "visible" && state.view === "home" && homeDue()) loadHome();
    if (document.visibilityState === "hidden" && cur()) saveSession();
    // iOS may kill a hidden Home Screen app without a pagehide: report the play so far (updated later if it goes on).
    if (document.visibilityState === "hidden" && lis) reportListen(false);
  });
  window.addEventListener("pagehide", () => {
    if (cur()) saveSession();
    if (lis) reportListen(false);
  });

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
    seekTo((seek.value / 1000) * d);
  });

  if ("mediaSession" in navigator) {
    const ms = navigator.mediaSession;
    const set = (a, f) => { try { ms.setActionHandler(a, f); } catch { /* unsupported action */ } };
    set("play", playAudio);
    set("pause", () => audio.pause());
    set("previoustrack", prev);
    set("nexttrack", next);
    set("seekto", (e) => {
      if (e.seekTime != null) seekTo(e.seekTime);
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
  const LYRIC_LEAD = 0.2; // Safari fires timeupdate ~4x/s; showing a line (or word) a beat early reads as on time
  const lyr = {
    on: store.get("lyricsOn", false),
    cache: new Map(), // track id -> Promise of the /api/lyrics body | null (misses too)
    seq: 0, id: null, data: null, lines: [], times: [], els: [], wordEls: [], active: -1, word: -1,
    held: false, holdTimer: 0, offset: 0, offsetTimer: 0,
  };
  const lyricsPanel = $("#np-lyrics");
  const lyrPop = $("#lyr-pop");
  const npOpen = () => $("#np").classList.contains("open");

  function fetchLyrics(t) {
    let p = lyr.cache.get(t.id);
    if (!p) {
      p = api(`/api/lyrics/${encodeURIComponent(t.id)}?${lyricsHints(t)}`).catch((e) => {
        if (e.status !== 404) lyr.cache.delete(t.id); // a network blip shouldn't stick; a real miss should
        return null;
      });
      lyr.cache.set(t.id, p);
    }
    return p;
  }
  function lyricsHints(t) {
    const q = new URLSearchParams();
    for (const [k, v] of [["title", t.title], ["artist", t.channel], ["album", t.album], ["duration", t.duration]]) if (v) q.set(k, v);
    return q;
  }

  function lyricsMsg(text, cls = "") {
    lyricsPanel.innerHTML = `<div class="lyr-msg ${cls}">${cls ? icon("note") : ""}${esc(text)}</div>`;
  }

  function renderLyrics(data) {
    lyr.data = data;
    lyr.offset = Number(data?.offset_ms) || 0;
    const lines = data?.synced ? LY.attachWords(LY.parseLRC(data.synced), data.words) : [];
    lyr.lines = lines;
    lyr.times = lines.map((l) => l.t);
    lyr.active = -1;
    lyr.word = -1;
    if (lines.length) {
      // Word-timed lines hold one span per word; syncWords lights them up as they're sung.
      const body = (l) => (l.words ? l.words.map((w) => `<span class="w">${esc(w.text)}</span>`).join("") : esc(l.text));
      lyricsPanel.innerHTML = `<div class="lyr synced${lines.some((l) => l.words) ? " wordy" : ""}">${lines.map((l, i) => l.text
        ? `<button class="lyr-line" data-i="${i}">${body(l)}</button>`
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
    lyr.wordEls = lyr.els.map((el) => $$(".w", el));
    lyricsPanel.scrollTop = 0;
    renderLyrTools();
    syncLyrics(true);
  }

  // Show the current track's lyrics in the panel (no-op when they're already there).
  async function showLyrics() {
    const t = cur();
    if (!lyr.on || !t) return;
    if (lyr.id === t.id) { syncLyrics(true); return; }
    lyr.id = t.id;
    const seq = ++lyr.seq;
    lyr.data = null; lyr.lines = []; lyr.times = []; lyr.els = []; lyr.wordEls = []; lyr.active = -1; lyr.word = -1;
    lyricsPanel.innerHTML = `<div class="lyr-sk" role="status" aria-label="Loading lyrics…"><i class="sk"></i><i class="sk"></i><i class="sk"></i><i class="sk"></i></div>`;
    renderLyrTools();
    const data = await fetchLyrics(t);
    if (seq !== lyr.seq || cur()?.id !== t.id) return; // the track changed while this was loading
    renderLyrics(data);
  }

  // New track: drop what the panel shows, then load if it's visible.
  function resetLyrics() {
    lyr.id = null; lyr.seq++;
    lyr.data = null; lyr.lines = []; lyr.times = []; lyr.els = []; lyr.wordEls = []; lyr.active = -1; lyr.word = -1;
    lyr.offset = 0;
    showLyrTools(false);
    if (lyr.on && npOpen()) showLyrics();
  }

  function scrollToLine(el, smooth) {
    if (!el || lyr.held || !npOpen() || !lyr.on) return;
    const top = el.offsetTop + el.offsetHeight / 2 - lyricsPanel.clientHeight / 3;
    const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
    lyricsPanel.scrollTo({ top: Math.max(0, top), behavior: smooth && !reduce ? "smooth" : "auto" });
  }

  // Highlight the line for the playhead (in lyrics time: the track's offset applied): binary search, and the DOM
  // is touched only when the line (or, in a word-timed line, the word) changes.
  function syncLyrics(force = false, smooth = !force) {
    if (!lyr.times.length) return;
    const now = LY.lyricTime(audio.currentTime, lyr.offset) + LYRIC_LEAD;
    const i = LY.indexAt(lyr.times, now);
    if (i !== lyr.active || force) {
      for (const w of lyr.wordEls[lyr.active] || []) w.classList.remove("on");
      lyr.word = -1;
      lyr.els[lyr.active]?.classList.remove("on");
      lyr.els[i]?.classList.add("on");
      lyr.active = i;
      if (i < 0) { if (force && !lyr.held) lyricsPanel.scrollTop = 0; } else scrollToLine(lyr.els[i], smooth);
    }
    syncWords(now);
  }
  // Word-timed line: every word sung so far is lit (a seek back unlights the ones after the playhead).
  function syncWords(now) {
    const words = lyr.lines[lyr.active]?.words;
    if (!words) return;
    const k = LY.wordAt(words, now);
    if (k === lyr.word) return;
    (lyr.wordEls[lyr.active] || []).forEach((el, j) => el.classList.toggle("on", j <= k));
    lyr.word = k;
  }

  function setLyricsOn(on) {
    lyr.on = on;
    store.set("lyricsOn", on);
    $("#np").classList.toggle("show-lyrics", on);
    $("#np-lyr").setAttribute("aria-pressed", String(on));
    if (!on) showLyrTools(false);
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
    seekTo(LY.seekTime(lyr.lines[line.dataset.i].t, lyr.offset));
    if (audio.paused) playAudio();
    syncLyrics(true, true);
  });

  // ---------- lyrics tools: timing nudge, source, "Wrong lyrics?" ----------
  // A small ⋯ over the panel's top corner opens them; the full lyrics UI (UI_REMAKE_PLAN §5) comes later.
  function renderLyrTools() {
    const synced = lyr.times.length > 0, d = lyr.data;
    const src = d?.provider ? `From ${esc(LY.sourceLabel(d.provider))}${lyr.lines.some((l) => l.words) ? " · word timing" : ""}` : "";
    lyrPop.innerHTML = `
      <div class="lyr-pop-row"><span class="k">Timing</span>
        <button class="lyr-nudge" data-lyr-nudge="${-LY.NUDGE}" aria-label="Show lyrics half a second sooner" ${synced ? "" : "disabled"}>−0.5</button>
        <button class="lyr-nudge lyr-off" data-lyr-nudge="reset" aria-label="Reset lyrics timing" ${synced && lyr.offset ? "" : "disabled"}>${esc(LY.fmtOffset(lyr.offset))}</button>
        <button class="lyr-nudge" data-lyr-nudge="${LY.NUDGE}" aria-label="Show lyrics half a second later" ${synced ? "" : "disabled"}>+0.5</button></div>
      ${synced ? `<p class="lyr-hint">Lyrics ahead of the song? Tap +0.5.</p>` : ""}
      <button class="lyr-pop-item" data-lyr-search>${icon("search")}<span>Wrong lyrics? Search</span></button>
      ${src ? `<p class="lyr-src">${src}</p>` : ""}`;
  }
  function showLyrTools(open) {
    lyrPop.hidden = !open;
    $("#lyr-more").setAttribute("aria-expanded", String(open));
  }
  function setLyricsOffset(ms) {
    const id = lyr.id;
    lyr.offset = ms;
    if (lyr.data) lyr.data.offset_ms = ms; // the cached promise holds this object: reopening keeps the nudge
    renderLyrTools();
    syncLyrics(true, true);
    clearTimeout(lyr.offsetTimer);
    lyr.offsetTimer = setTimeout(() => {
      api(`/api/lyrics/${encodeURIComponent(id)}/offset`, {
        method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ms }),
      }).catch(() => toast("Couldn't save the lyrics timing"));
    }, 600); // several taps, one save
  }
  $("#lyr-more").addEventListener("click", (e) => { e.stopPropagation(); renderLyrTools(); showLyrTools(lyrPop.hidden); });
  lyrPop.addEventListener("click", (e) => {
    e.stopPropagation(); // re-rendering detaches the target: the outside-click check below would misread it
    const b = e.target.closest("[data-lyr-nudge],[data-lyr-search]");
    if (!b || b.disabled) return;
    if ("lyrSearch" in b.dataset) { showLyrTools(false); openLyricsSearch(); return; }
    const v = b.dataset.lyrNudge;
    setLyricsOffset(v === "reset" ? 0 : LY.nudge(lyr.offset, Number(v)));
  });
  document.addEventListener("click", (e) => {
    if (!lyrPop.hidden && !e.target.closest("#lyr-pop,#lyr-more")) showLyrTools(false);
  });

  // "Wrong lyrics?": search LRCLIB and pin a result to this track (the server never replaces a pin).
  const lyrSearch = { seq: 0, id: null };
  async function runLyricsSearch(q) {
    const t = sheetTrack(lyrSearch.id);
    if (!t) return;
    const seq = ++lyrSearch.seq;
    const hints = lyricsHints(t);
    if (q) hints.set("q", q);
    $("#lyr-results").innerHTML = `<p class="st-note">Searching LRCLIB…</p>`;
    try {
      const r = await api(`/api/lyrics/search?${hints}`);
      if (seq !== lyrSearch.seq) return;
      if (!q) $("#lyr-q").value = r.q;
      $("#lyr-results").innerHTML = r.results.length ? r.results.map((x) => `
        <button class="lyr-res" data-pin="${x.id}">
          <span class="row-text"><span class="row-t">${esc(x.track)}</span><span class="row-s">${esc([x.artist, x.album].filter(Boolean).join(" · "))}</span></span>
          <span class="lyr-badge${x.synced ? " on" : ""}">${x.synced ? "Synced" : x.instrumental ? "Instrumental" : "Plain"}</span>
          <span class="hl-ms">${x.duration ? fmt(x.duration) : ""}</span>
        </button>`).join("") : `<p class="st-note">Nothing on LRCLIB for that. Try fewer words, or the artist's spelling there.</p>`;
    } catch (e) {
      if (seq === lyrSearch.seq) $("#lyr-results").innerHTML = `<p class="st-note bad">${esc(e.message)}</p>`;
    }
  }
  // The track the sheet was opened for, while it's still the one playing (the sheet pins to that id only).
  const sheetTrack = (id) => (cur()?.id === id ? cur() : null);
  function openLyricsSearch() {
    const t = cur();
    if (!t) return;
    lyrSearch.id = t.id;
    $("#lyr-q").value = "";
    $("#lyr-unpin").hidden = lyr.data?.provider !== "lrclib-pinned";
    openSheet("#lyrics-sheet");
    runLyricsSearch("");
  }
  $("#lyr-search-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const q = $("#lyr-q").value.trim();
    if (q) { $("#lyr-q").blur(); runLyricsSearch(q); }
  });
  $("#lyr-results").addEventListener("click", async (e) => {
    const b = e.target.closest("[data-pin]");
    const id = lyrSearch.id;
    if (!b || !id) return;
    b.disabled = true;
    try {
      const data = await api(`/api/lyrics/${encodeURIComponent(id)}/pin`, {
        method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ lrclib_id: Number(b.dataset.pin) }),
      });
      lyr.cache.set(id, Promise.resolve(data));
      if (lyr.id === id) renderLyrics(data);
      closeSheet("#lyrics-sheet");
      toast("These lyrics will show for this song from now on");
    } catch (err) {
      b.disabled = false;
      toast(err.message || "Couldn't use those lyrics");
    }
  });
  $("#lyr-unpin").addEventListener("click", async () => {
    const id = lyrSearch.id;
    try {
      await api(`/api/lyrics/${encodeURIComponent(id)}/pin`, { method: "DELETE" });
    } catch (err) {
      if (err.status !== 404) { toast(err.message || "Couldn't reset the lyrics"); return; }
    }
    lyr.cache.delete(id);
    closeSheet("#lyrics-sheet");
    if (lyr.id === id) { lyr.id = null; showLyrics(); }
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
        body: JSON.stringify({ id, title: t.title, channel: t.channel || "", duration: t.duration || null, ...hints }),
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
      + stLink("sources", "Sources", sourcesSummary())
      + stLink("updates", "Updates", updatesSummary(), updateAvailable())
      + stLink("scrobble", "Scrobbling", scrobbleSummary())
      + stLink("backup", "Backup", backupSummary())
      + `<li class="st-link"><button data-open-hidden><span class="k">Hidden artists</span><span class="v">${n == null ? "" : n}${icon("chev-right")}</span></button></li>`
      + `<li class="st-link"><button data-yt-reset${ytResetting ? " disabled" : ""}><span class="k">Reset YouTube session</span><span class="v">${ytResetting ? "Resetting…" : icon("repeat")}</span></button></li>`
      + `<li class="st-link"><button data-copy-diag><span class="k">Copy diagnostics</span><span class="v">${icon("copy")}</span></button></li>`
      + (streamNote(h) ? `<li class="st-note">${esc(streamNote(h))}</li>` : "");
  }
  // "Last stream: visionos · 2 clients gated (tv, web)": the client of the last play and the gated ones.
  function streamNote(h) {
    const client = h?.stream_timing?.client;
    const gated = Object.keys(h?.yt_gates?.global || {});
    const parts = [];
    if (client && client !== "-") parts.push(`Last stream: ${client}`);
    if (h?.yt_gates) parts.push(gated.length ? `${gated.length} client${gated.length === 1 ? "" : "s"} gated (${gated.join(", ")})` : "No clients gated");
    return parts.join(" · ");
  }
  // POST /api/yt/reset: yt-dlp's cache, client gates, the resolve cache. A failing track gets another go.
  let ytResetting = false;
  async function resetYouTube() {
    if (ytResetting) return;
    ytResetting = true;
    renderStatus();
    try {
      await api("/api/yt/reset", { method: "POST" });
      toast("YouTube session reset");
      if (cur() && !resume && (rec.broken || rec.busy || rec.outage)) retryNow(rec.want || rec.outage);
    } catch (e) {
      toast(`Couldn't reset: ${e.message}`);
    }
    ytResetting = false;
    await loadHealth();
    renderStatus();
  }
  // GET /api/diagnostics (versions, config without secrets, caches, gates, recent redacted log lines) as JSON text.
  // Fetched when the sheet opens so the tap can copy at once: iOS only lets a page copy inside the tap itself,
  // and over plain http (the tailnet address) there is no navigator.clipboard, only the textarea fallback.
  let diagText = null;
  async function loadDiagnostics() {
    try { diagText = JSON.stringify(await api("/api/diagnostics"), null, 2); } catch { diagText = null; }
  }
  function copyFallback(text) {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.style.cssText = "position:fixed;opacity:0;top:0";
    document.body.appendChild(ta);
    ta.select();
    ta.setSelectionRange(0, text.length);
    let ok = false;
    try { ok = document.execCommand("copy"); } catch { ok = false; }
    ta.remove();
    return ok;
  }
  async function copyDiagnostics() {
    if (!diagText) await loadDiagnostics();  // not loaded yet: the copy may then need a second tap on iOS
    const text = diagText;
    if (!text) { toast("Couldn't load diagnostics"); return; }
    let ok = false;
    if (navigator.clipboard && window.isSecureContext) {
      try { await navigator.clipboard.writeText(text); ok = true; } catch { ok = false; }
    }
    if (!ok) ok = copyFallback(text);
    toast(ok ? "Diagnostics copied" : "Couldn't copy, tap again");
    loadDiagnostics();  // fresh numbers for the next copy
  }
  async function openStatus() {
    renderStatus();
    openSheet("#status-sheet");
    loadBlocklist();
    loadDiagnostics();
    loadVersion();
    loadScrobble();
    loadBackupStatus();
    await loadHealth();
    renderStatus();
  }

  // ---------- status sub-sheets: Sources, Updates, Scrobbling, Backup ----------
  // Each opens from a Server row (the Server sheet closes, like Hidden artists) and has a back arrow to it.
  const st = {
    sources: null, sourcesBusy: false, version: null, versionBusy: false, upd: null, updTimer: 0, updDown: false,
    scrob: null, tokenBusy: false, backup: null, restoreMode: "merge", restoreFile: null, restoreBusy: false, restored: null,
  };
  const SUBS = { sources: renderSourcesSheet, updates: renderUpdatesSheet, scrobble: renderScrobbleSheet, backup: renderBackupSheet };
  const subOpen = (k) => !$(`#${k}-sheet`).hidden;
  const rerender = (k) => { if (subOpen(k)) SUBS[k](); if (!$("#status-sheet").hidden) renderStatus(); };
  const ON = (b) => (b ? " disabled" : "");
  function stLink(key, label, value, badge) {
    return `<li class="st-link"><button data-st="${key}"><span class="k">${esc(label)}</span><span class="v">${badge ? '<i class="st-badge"></i>' : ""}${esc(value || "")}${icon("chev-right")}</span></button></li>`;
  }
  function openSub(k) {
    closeSheet("#status-sheet");
    SUBS[k]();
    openSheet(`#${k}-sheet`);
    if (k === "sources") loadSourcesHealth(false);
    if (k === "updates") { loadVersion(); loadUpdateStatus(); }
    if (k === "scrobble") loadScrobble();
    if (k === "backup") loadBackupStatus();
  }
  function backToStatus(w) {
    closeSheet(w);
    openStatus();
  }
  const shortDate = (ts) => (ts ? new Date(typeof ts === "number" ? ts * 1000 : ts).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" }) : "");
  const plural = (n, one, many = one + "s") => `${n} ${n === 1 ? one : many}`;
  const skel = (n) => '<div class="sk-row"><div class="sk l"></div></div>'.repeat(n);

  // Sources: /api/health/sources (6 s at most, cached 60 s on the server; Refresh asks again).
  const HL = { ONLINE: "Online", REACHABLE: "Reachable, not working", OFFLINE: "Offline", DISABLED: "Off" };
  function sourcesSummary() {
    const r = st.sources;
    if (!r) return "";
    const on = r.filter((x) => x.status === "ONLINE").length, bad = r.filter((x) => x.status === "OFFLINE" || x.status === "REACHABLE").length;
    return bad ? `${on} online · ${bad} down` : `${on} online`;
  }
  async function loadSourcesHealth(force) {
    if (st.sourcesBusy) return;
    st.sourcesBusy = true;
    rerender("sources");
    try { st.sources = await api(`/api/health/sources${force ? "?refresh=1" : ""}`); } catch (e) { toast(`Couldn't check sources: ${e.message}`); }
    st.sourcesBusy = false;
    rerender("sources");
  }
  function renderSourcesSheet() {
    const r = st.sources, busy = st.sourcesBusy;
    const when = r?.[0]?.checked_at ? `Checked ${timeAgo(r[0].checked_at)}` : busy ? "Checking every source…" : "";
    $("#sources-body").innerHTML = `<div class="st-head"><span>${esc(when)}</span>
        <button class="chip sm" data-st="refresh-sources"${ON(busy)}>${icon("repeat")}${busy ? "Checking…" : "Refresh"}</button></div>`
      + (!r ? skel(6) : `<ul class="hl-list">${r.map((x) => `<li class="hl-row">
          <i class="hl-dot ${esc(x.status.toLowerCase())}" title="${esc(HL[x.status] || x.status)}"></i>
          <span class="row-text"><span class="row-t">${esc(x.name)}</span>
            <span class="row-s">${esc(HL[x.status] || x.status)}${x.detail ? ` · ${esc(x.detail)}` : ""}</span></span>
          <span class="hl-ms">${x.latency_ms != null && x.status !== "DISABLED" ? `${x.latency_ms} ms` : ""}</span></li>`).join("")}</ul>`);
  }

  // Updates: /api/version (cached 6 h on the server; "Check now" asks GitHub/PyPI again), POST /api/update.
  const updateAvailable = () => !!(st.version && ((st.version.behind && !st.version.skipped) || st.version.ytdlp_outdated));
  function updatesSummary() {
    const v = st.version;
    if (st.upd?.running) return "Updating…";
    if (!v) return "";
    if (v.behind && !v.skipped) return v.behind_count ? `${plural(v.behind_count, "commit")} behind` : "Update available";
    if (v.ytdlp_outdated) return "yt-dlp update";
    return v.behind === false ? "Up to date" : (v.reef?.short || "");
  }
  async function loadVersion(force) {
    if (force) { st.versionBusy = true; rerender("updates"); }
    try { st.version = await api(`/api/version${force ? "?refresh=1" : ""}`); } catch (e) { if (force) toast(`Couldn't check: ${e.message}`); }
    st.versionBusy = false;
    rerender("updates");
  }
  async function loadUpdateStatus() {
    try { st.upd = await api("/api/update/status"); } catch { return; }
    rerender("updates");
    if (st.upd.running && !st.updTimer) watchUpdate();
  }
  function renderUpdatesSheet() {
    const v = st.version, u = st.upd, running = !!u?.running || st.updDown;
    if (!v) { $("#updates-body").innerHTML = skel(4); return; }
    const L = v.latest_reef;
    const reefState = v.behind === true ? (v.behind_count ? `${plural(v.behind_count, "commit")} behind` : "Behind")
      : v.behind === false ? "Up to date" : "Unknown (this commit isn't on GitHub)";
    const rows = [
      ["Reef", `${v.reef?.short || "?"}${v.reef?.date ? ` · ${shortDate(v.reef.date)}` : ""}`, ""],
      ["Newest", L ? `${L.short} · ${shortDate(L.date)}` : "—", ""],
      ["Status", reefState + (v.behind && v.skipped ? " (skipped)" : ""), v.behind && !v.skipped ? "bad" : v.behind === false ? "ok" : ""],
      ["yt-dlp", v.ytdlp || "—", ""],
      ["Newest yt-dlp", v.latest_ytdlp || "—", v.ytdlp_outdated ? "bad" : v.latest_ytdlp ? "ok" : ""],
    ];
    const allowed = v.self_update?.allowed;
    const label = { reef: "Reef", ytdlp: "yt-dlp", both: "Reef and yt-dlp" }[u?.what] || "";
    const progress = u && (running || u.finished_at) ? `<div class="upd-run">
        <div class="upd-state ${running ? "" : u.result === "ok" ? "ok" : "bad"}">${running ? '<span class="spin"></span>' : ""}${esc(
          st.updDown ? `Restarting Reef…` : running ? `Updating ${label}…` : u.result === "ok" ? `${label} updated ${timeAgo(u.finished_at)}` : `${label} update failed ${timeAgo(u.finished_at)}`)}</div>
        ${u.log_tail?.length ? `<pre class="upd-log">${esc(u.log_tail.slice(-8).join("\n"))}</pre>` : ""}</div>` : "";
    $("#updates-body").innerHTML = `<ul class="status-list">${rows.map(([k, val, c]) => `<li><span class="k">${esc(k)}</span><span class="v ${c}">${esc(val)}</span></li>`).join("")}</ul>`
      + (L?.message ? `<p class="st-note">Newest: “${esc(L.message)}”</p>` : "")
      + progress
      + (allowed ? `<div class="st-btns">
          ${v.behind ? `<button class="btn-wide" data-st="update-reef"${ON(running)}>Update Reef</button>` : ""}
          ${v.ytdlp_outdated ? `<button class="btn-wide alt" data-st="update-ytdlp"${ON(running)}>Update yt-dlp to ${esc(v.latest_ytdlp)}</button>` : ""}
        </div>` : `<p class="st-note">${esc(v.self_update?.reason || "Updates can't run here.")}</p>`)
      + `<div class="st-actions">
          <button class="chip sm" data-st="check-updates"${ON(st.versionBusy)}>${icon("repeat")}${st.versionBusy ? "Checking…" : "Check now"}</button>
          ${v.behind && !v.skipped && L ? `<button class="chip sm" data-st="skip-version">Skip this version</button>` : ""}
          ${v.skipped ? `<button class="chip sm" data-st="unskip-version">Stop skipping</button>` : ""}
        </div>`
      + `<p class="st-note">${v.checked_at ? `Checked ${esc(timeAgo(v.checked_at))}` : "Not checked yet"}${v.errors?.length ? ` · ${esc(v.errors.join("; "))}` : ""}</p>`;
    const lg = $("#updates-body .upd-log");
    if (lg) lg.scrollTop = lg.scrollHeight; // the newest lines
  }
  async function startUpdate(what) {
    const label = what === "reef" ? "Reef" : "yt-dlp";
    if (!confirm(`Update ${label} now? Reef restarts, so playback stops for a few seconds.`)) return;
    try {
      st.upd = await api("/api/update", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ what, confirm: true }) });
    } catch (e) {
      toast(`Can't update: ${e.message}`);
      return;
    }
    rerender("updates");
    watchUpdate();
  }
  // Poll /api/update/status every 2 s. While Reef restarts nothing answers: poll /api/health until it's back,
  // then read the result. A finished Reef update reloads the page so the new app.js runs.
  async function watchUpdate() {
    clearTimeout(st.updTimer);
    st.updTimer = 0;
    let u = null;
    if (st.updDown) {
      try { await api("/api/health"); st.updDown = false; } catch { /* still restarting */ }
    }
    if (!st.updDown) {
      try { u = await api("/api/update/status"); } catch { st.updDown = true; }
    }
    if (u) st.upd = u;
    rerender("updates");
    if (st.updDown || u?.running) { st.updTimer = setTimeout(watchUpdate, 2000); return; }
    if (!u) return;
    if (u.result === "ok") {
      toast(`${u.what === "ytdlp" ? "yt-dlp" : "Reef"} updated`);
      if (u.what !== "ytdlp") { setTimeout(() => location.reload(), 1500); return; }
      loadVersion();
      loadHealth();
    } else {
      toast("Update failed. The log is in Server → Updates.");
    }
  }

  // Scrobbling: ListenBrainz token (write-only; the field never shows the saved one), on/off, queue.
  function scrobbleSummary() {
    const s = st.scrob;
    if (!s) return "";
    if (!s.token_set) return "Off";
    if (s.token_valid === false) return "Token not valid";
    if (!s.scrobble_enabled) return "Paused";
    return (s.user_name || "On") + (s.pending ? ` · ${s.pending} waiting` : "");
  }
  async function loadScrobble() {
    try { st.scrob = await api("/api/scrobble/status"); } catch { /* keep the last */ }
    rerender("scrobble");
  }
  function renderScrobbleSheet() {
    const s = st.scrob;
    if (!s) { $("#scrobble-body").innerHTML = skel(3); return; }
    const who = !s.token_set ? ["No token", ""] : s.user_name ? [s.user_name, "ok"] : s.token_valid === false ? ["Token not valid", "bad"] : ["Not checked yet", ""];
    $("#scrobble-body").innerHTML = `<p class="st-note st-lead">Counted listens (30 s or half the song) go to ListenBrainz. Get your user token at
        <a href="https://listenbrainz.org/settings/" target="_blank" rel="noopener">listenbrainz.org/settings</a>.</p>
      <form class="st-form" id="lb-form" autocomplete="off">
        <input class="st-input" id="lb-token" type="password" autocomplete="off" autocapitalize="off" spellcheck="false"
          placeholder="${s.token_set ? "Saved · paste to replace" : "Paste your ListenBrainz token"}" aria-label="ListenBrainz token">
        <button class="chip" type="submit"${ON(st.tokenBusy)}>${st.tokenBusy ? "Checking…" : "Save"}</button>
      </form>
      <ul class="status-list">
        <li><span class="k">Account</span><span class="v ${who[1]}">${esc(who[0])}</span></li>
        <li class="st-toggle"><label for="lb-on"><span class="k">Scrobble listens</span>
          <input type="checkbox" class="sw" id="lb-on"${s.scrobble_enabled ? " checked" : ""}${s.token_set ? "" : " disabled"}></label></li>
        <li><span class="k">Waiting to send</span><span class="v">${s.pending}</span></li>
        <li><span class="k">Sent</span><span class="v">${s.submitted}${s.last_ok_at ? ` · last ${esc(timeAgo(s.last_ok_at))}` : ""}</span></li>
      </ul>
      ${s.last_error ? `<p class="st-note bad">${esc(s.last_error)}${s.retry_at ? ` · retrying ${esc(untilText(s.retry_at))}` : ""}</p>` : ""}
      ${s.token_set ? `<div class="st-actions"><button class="chip sm" data-st="validate-token"${ON(st.tokenBusy)}>${icon("repeat")}Check token</button>
        <button class="chip sm" data-st="clear-token"${ON(st.tokenBusy)}>${icon("x")}Remove token</button></div>` : ""}`;
  }
  function untilText(ts) {
    const d = Math.max(0, Math.round(ts - Date.now() / 1000));
    return d < 60 ? "in a moment" : d < 3600 ? `in ${Math.round(d / 60)} min` : `in ${Math.round(d / 3600)} h`;
  }
  async function saveToken(value) {
    st.tokenBusy = true;
    rerender("scrobble");
    try {
      await api("/api/settings", { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ listenbrainz_token: value }) });
      await loadScrobble();
      const s = st.scrob;
      toast(!value ? "Token removed" : s?.user_name ? `Scrobbling as ${s.user_name}` : s?.token_valid === false ? "ListenBrainz says that token isn't valid" : (s?.last_error || "Token saved"));
    } catch (e) {
      toast(`Couldn't save: ${e.message}`);
    }
    st.tokenBusy = false;
    rerender("scrobble");
  }
  async function setScrobbling(on) {
    try {
      await api("/api/settings", { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ scrobble_enabled: on }) });
    } catch (e) { toast(`Couldn't change it: ${e.message}`); }
    loadScrobble();
  }
  async function validateToken() {
    st.tokenBusy = true;
    rerender("scrobble");
    try { st.scrob = await api("/api/scrobble/validate", { method: "POST" }); } catch (e) { toast(`Couldn't check: ${e.message}`); }
    st.tokenBusy = false;
    rerender("scrobble");
  }

  // Backup: download (GET /api/backup), restore (POST /api/restore, merge or replace), daily snapshots.
  function backupSummary() {
    const b = st.backup;
    return b?.last_auto_at ? `Saved ${timeAgo(b.last_auto_at)}` : b ? "No backup yet" : "";
  }
  async function loadBackupStatus() {
    try { st.backup = await api("/api/backup/status"); } catch { /* keep the last */ }
    rerender("backup");
  }
  function renderBackupSheet() {
    const b = st.backup, r = st.restored, f = st.restoreFile;
    const autos = (b?.snapshots || []).filter((x) => x.kind === "auto").length;
    $("#backup-body").innerHTML = `<p class="st-note st-lead">Your kept-song list, listens, likes, playlists, hidden artists, lyrics and
        preferences in one file. Never your ListenBrainz token or passwords.</p>
      <a class="btn-wide alt" href="/api/backup" download>${icon("download")}Download backup</a>
      <h4 class="st-sub">Restore</h4>
      <label class="st-file">${icon("note")}<span>${esc(f ? f.name : "Choose a backup file")}</span>
        <input type="file" id="restore-file" accept=".json,application/json"></label>
      <div class="seg st-seg">
        <button data-st="mode-merge" class="${st.restoreMode === "merge" ? "on" : ""}">Merge</button>
        <button data-st="mode-replace" class="${st.restoreMode === "replace" ? "on" : ""}">Replace</button>
      </div>
      <p class="st-note">${st.restoreMode === "merge" ? "Merge adds what's missing; where both have an item, the newer copy wins." : "Replace swaps each section for the backup's copy."}
        Reef saves a copy of the current data first.</p>
      <button class="btn-wide" data-st="restore"${ON(!f || st.restoreBusy)}>${st.restoreBusy ? "Restoring…" : "Restore"}</button>
      ${r ? `<p class="st-note ok">Restored (${esc(r.mode)}): ${esc(Object.entries(r.sections).map(([k, c]) => `${k} ${c.after}`).join(", "))}</p>` : ""}
      <ul class="status-list">
        <li><span class="k">Last automatic backup</span><span class="v">${b?.last_auto_at ? esc(timeAgo(b.last_auto_at)) : b ? "Not yet" : ""}</span></li>
        <li><span class="k">Daily copies kept</span><span class="v">${b ? `${autos} of 7` : ""}</span></li>
      </ul>`;
  }
  async function restoreBackup() {
    const f = st.restoreFile, mode = st.restoreMode;
    if (!f || st.restoreBusy) return;
    if (!confirm(`${mode === "replace" ? "Replace" : "Merge"} Reef's data with “${f.name}”? A copy of the current data is saved first.`)) return;
    st.restoreBusy = true;
    rerender("backup");
    const fd = new FormData();
    fd.append("file", f);
    fd.append("mode", mode);
    try {
      st.restored = await api("/api/restore", { method: "POST", body: fd });
      st.restoreFile = null;
      toast("Backup restored");
      loadSettings();
      loadHistory();
      loadBlocklist();
      loadHome();
    } catch (e) {
      toast(`Couldn't restore: ${e.message}`);
    }
    st.restoreBusy = false;
    loadBackupStatus();
  }

  function statusAction(a, el) {
    if (SUBS[a]) { openSub(a); return; }
    if (a === "back") { backToStatus(el.closest(".sheet-wrap")); return; }
    if (a === "refresh-sources") { loadSourcesHealth(true); return; }
    if (a === "check-updates") { loadVersion(true); return; }
    if (a === "update-reef") { startUpdate("reef"); return; }
    if (a === "update-ytdlp") { startUpdate("ytdlp"); return; }
    if (a === "skip-version" || a === "unskip-version") {
      const sha = a === "skip-version" ? st.version?.latest_reef?.sha : null;
      api("/api/settings", { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ skipped_version: sha }) })
        .then(() => { toast(sha ? "Skipped this version" : "Showing updates again"); loadVersion(); })
        .catch((e) => toast(`Couldn't save: ${e.message}`));
      return;
    }
    if (a === "validate-token") { validateToken(); return; }
    if (a === "clear-token") { if (confirm("Remove the ListenBrainz token? Listens stop being sent.")) saveToken(""); return; }
    if (a === "mode-merge" || a === "mode-replace") { st.restoreMode = a.slice(5); rerender("backup"); return; }
    if (a === "restore") restoreBackup();
  }
  $("#scrobble-body").addEventListener("submit", (e) => {
    e.preventDefault();
    const v = $("#lb-token").value.trim();
    if (!v) { toast("Paste the token first"); return; }
    saveToken(v);
  });
  $("#scrobble-body").addEventListener("change", (e) => { if (e.target.id === "lb-on") setScrobbling(e.target.checked); });
  $("#backup-body").addEventListener("change", (e) => {
    if (e.target.id !== "restore-file") return;
    st.restoreFile = e.target.files?.[0] || null;
    st.restored = null;
    renderBackupSheet();
  });

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
    const dislikeItem = srcKey(t.id) !== "nd" ? item("dislike", "thumb-down", "Not interested") : "";
    $("#act-menu").innerHTML = item("next", "play-next", "Play next") + item("queue", "queue-add", "Add to queue") + radioItem + keepItem
      + artistItems + albumItem + item("share", "share", "Share") + dislikeItem + hideItem;
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
    else if (act === "dislike") notInterested(a.t);
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
    toastTimer = setTimeout(() => el.classList.remove("show"), Math.max(2600, String(msg).length * 55)); // long hints stay longer
  }

  // ---------- events ----------
  document.addEventListener("click", (e) => {
    const t = e.target.closest("[data-keep],[data-play],[data-search],[data-source],[data-home-tab],[data-filter],[data-dismiss],[data-play-all],[data-shuffle-all],[data-close],[data-clear-finished],[data-clear-searches],[data-more],[data-act],[data-q-jump],[data-q-remove],[data-q-clear],[data-q-mode],[data-sleep],[data-fill],[data-keep-all],[data-retry],[data-page-radio],[data-artist-hide],[data-unhide],[data-unhide-page],[data-open-hidden],[data-yt-reset],[data-copy-diag],[data-about],[data-mix],[data-st]");
    if (!t) return;
    const d = t.dataset;
    if ("keep" in d) { e.preventDefault(); if (d.keep) keep(d.keep, d.list); return; }
    if ("more" in d) { openActions(d.more, d.list, Number(d.i)); return; }
    if ("act" in d) { if (!t.disabled) runAction(d.act, d.arg); return; }
    if ("keepAll" in d) { keepAll(); return; }
    if ("retry" in d) { detail.errors.delete(detail.key); renderDetail(); loadPage(detail.key, { force: true }); return; }
    if ("mix" in d) { const m = homeItems("mixed")[Number(d.mix)]; if (m?.seed) startRadio(m.seed); return; }
    if ("pageRadio" in d) { const s = pageTracks()[0]; if (s) startRadio(s); return; }
    if ("artistHide" in d) { toggleArtistHidden(); return; }
    if ("unhidePage" in d) {
      const a = pageData();
      if (a) unhideArtists(detail.kind === "artist" ? [{ id: a.id, name: a.name }] : a.artists || []);
      return;
    }
    if ("unhide" in d) { unhideArtists([{ id: d.unhide, name: d.name }]); return; }
    if ("openHidden" in d) { openHidden(); return; }
    if ("ytReset" in d) { resetYouTube(); return; }
    if ("copyDiag" in d) { copyDiagnostics(); return; }
    if ("st" in d) { if (!t.disabled) statusAction(d.st, t); return; }
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
  loadSettings();
  loadHealth();
  loadHistory();
  loadTiles();
  pollJobs();
})();
