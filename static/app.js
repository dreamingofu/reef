(() => {
  "use strict";

  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const audio = $("#audio");
  const VIEWS = ["home", "search", "library"];
  // Old tabs folded into Library (UI remake step 1): their hashes still work and land there.
  const OLD_VIEWS = { kept: "library", downloads: "library" };
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
    home: store.get("home", null), // the last /api/home body: Home paints from it at once, then refreshes
    query: "",
    results: [],
    searching: false,
    searchError: null, // the failed search's error (status, body {block, hint})
    link: null, // the current search is a pasted link: { kind: "track" | "playlist", title }
    filter: "top",
    history: [],
    historyLoaded: false, // /api/history answered at least once (the Library shows a skeleton until then)
    historyErr: null,
    homeErr: null, // the last /api/home failure while there's nothing to show
    library: null, // Navidrome's newest albums (Library page), null until loaded
    jobs: [],
    queue: [],
    index: -1,
    from: "",
    fromRoute: "", // where "Playing from …" leads: the hash of the page / view the queue came from ("" = nowhere)
    shuffle: false,
    original: null, // play order before shuffling (snapshot restored when shuffle goes off)
    repeat: "off",
    health: null,
    settings: {}, // server-side settings (/api/settings `values`); missing keys mean the defaults
    blocked: null, // hidden artists from /api/blocklist (null until loaded)
    playlists: null, // Reef playlists (/api/playlists summaries), null until loaded
    playlistsErr: null,
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
    queue: () => state.queue, // Up next rows (the track menu, §8.1)
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
  // `bg` (Now Playing's blurred backdrop) is the same 64 px copy the cover palette samples: one fetch for both.
  const ART = { small: [200, 480], big: [720, 720], fallback: [200, 480], tile: [320, 320], mini: [160, 320], bg: [64, 64] };
  // encodeURIComponent turns "mc:123" into "mc%3A123", which Flask decodes back before routing.
  // A placeholder (an import row not matched yet, "pq:…") has no art: the app icon.
  const artUrl = (id, size) => (/^pq:/.test(id || "") ? "/static/icon.svg" : `/api/art/${encodeURIComponent(id)}?size=${size}`);
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

  // ---------- lyrics view core ----------
  // Timing and selection maths for the full-screen lyrics view (UI_REMAKE_PLAN §5): the rows it shows (lines plus
  // interlude gaps), the active row / word at a time, word fill, interlude dot progress, depth by distance from the
  // active row, the auto-scroll anchor and stagger, long-line shrink. Pure, no DOM; unit-checked from node by slicing
  // between these sentinels (scratchpad u3/lyrics_view_core_check.js).
  // ==== lyrics-view-core:start ====
  const LV = (() => {
    const GAP_MIN = 4; // s: a break at least this long shows interlude dots
    const GAP_FADE = 0.65; // s: the dots fade out this long before the next line
    const STEPS = 4; // depth classes d0 (active) .. d4 by exact distance; then d5 (5..FAR away: blurless, near-invisible)
    const FAR = 8; // ... and d9 beyond FAR rows (hidden outside a manual scroll), so few rows ever carry a blur
    const ANCHOR = 0.35; // the active row sits at 35% of the view's height
    const STAGGER = 20, STAGGER_MAX = 200; // ms per row when rows move to a new active line, capped
    const HOLD_MS = 3500; // a manual scroll pauses auto-follow this long
    const GLOW_HELD = 1.1; // s: words held longer glow
    const clamp = (x, a, b) => Math.min(b, Math.max(a, x));
    // A line with nothing to sing ("", "♪", "♫ ♫") is a break, not a lyric.
    const isBreak = (text) => !/[^\s♪♫♬♩·•.…-]/.test(String(text || ""));
    // Index of the last item of sorted `times` at or before `now`, else -1 (binary search).
    function indexAt(times, now) {
      let lo = 0, hi = times.length - 1, i = -1;
      while (lo <= hi) {
        const mid = (lo + hi) >> 1;
        if (times[mid] <= now) { i = mid; lo = mid + 1; } else hi = mid - 1;
      }
      return i;
    }
    // When a word-timed line's last word ends (s), or null when the timing doesn't say.
    function lineEnd(words) {
      const w = words && words.length ? words[words.length - 1] : null;
      return w && Number.isFinite(w.d) && w.d >= 0 ? w.t + w.d : null;
    }
    // Parsed LRC lines ([{t, text, words?}] sorted) -> rows [{kind: "line", t, text, words, src} | {kind: "gap", t, end}].
    // Gaps: an intro before a first line at >= minGap s, a blank line followed by >= minGap s of nothing, and (word
    // timing only) the silence after a line's last word when the next line starts >= minGap s later. Shorter blanks are
    // dropped (the line before stays active); touching gaps merge.
    function rows(lines, { minGap = GAP_MIN } = {}) {
      const out = [];
      const push = (r) => {
        const last = out[out.length - 1];
        if (r.kind === "gap" && last && last.kind === "gap" && r.t <= last.end + 1e-6) { last.end = Math.max(last.end, r.end); return; }
        out.push(r);
      };
      const list = (lines || []).filter((l) => l && Number.isFinite(l.t));
      for (let i = 0; i < list.length; i++) {
        const l = list[i];
        let j = i + 1;
        while (j < list.length && list[j].t <= l.t) j++; // the next entry that comes later
        const nextT = j < list.length ? list[j].t : null;
        if (isBreak(l.text)) {
          if (nextT != null && nextT - l.t >= minGap) push({ kind: "gap", t: l.t, end: nextT });
          continue;
        }
        push({ kind: "line", t: l.t, text: l.text, words: l.words && l.words.length ? l.words : null, src: i });
        const end = lineEnd(l.words);
        const next = list[i + 1];
        if (end != null && next && !isBreak(next.text) && next.t - end >= minGap) push({ kind: "gap", t: Math.max(l.t, end), end: next.t });
      }
      if (out.length) {
        if (out[0].kind === "gap") out[0].t = Math.min(out[0].t, 0);
        else if (out[0].t >= minGap) out.unshift({ kind: "gap", t: 0, end: out[0].t });
      }
      return out;
    }
    // The word being sung in a word-timed line at `now`: the last one started, -1 before the first.
    function wordAt(words, now) {
      let lo = 0, hi = (words ? words.length : 0) - 1, i = -1;
      while (lo <= hi) {
        const mid = (lo + hi) >> 1;
        if (words[mid].t <= now) { i = mid; lo = mid + 1; } else hi = mid - 1;
      }
      return i;
    }
    // How much of word k is filled at `now` (0..1): over its own duration, else until the next word (or `end`, the
    // line's end / the next row), else half a second.
    function wordFill(words, k, now, end = null) {
      const w = words && words[k];
      if (!w) return 0;
      const nx = words[k + 1];
      const dur = Number.isFinite(w.d) && w.d > 0 ? w.d : nx ? nx.t - w.t : Number.isFinite(end) && end > w.t ? end - w.t : 0.5;
      return dur > 0 ? clamp((now - w.t) / dur, 0, 1) : 1;
    }
    // Words held long enough to glow while they're sung.
    const glows = (w, next) => {
      const d = Number.isFinite(w?.d) && w.d > 0 ? w.d : next ? next.t - w.t : 0;
      return d > GLOW_HELD;
    };
    // Interlude progress: p 0..1 (dot k is filled by clamp(3p - k, 0, 1)) over the gap less its fade-out, and `out`
    // once the dots should fade (GAP_FADE before the next line).
    function gapProgress(gap, now, fade = GAP_FADE) {
      if (!gap) return { p: 0, out: false };
      const span = Math.max(0.1, gap.end - gap.t - fade);
      return { p: clamp((now - gap.t) / span, 0, 1), out: now >= gap.end - fade };
    }
    const dotFill = (p, k) => clamp(3 * p - k, 0, 1);
    // Depth level for a distance: 0..4 as is, 5 for 5..FAR, 9 beyond FAR.
    const level = (d) => (d <= STEPS ? d : d <= FAR ? 5 : 9);
    // Depth class of row i when `active` is the active row: "d0".."d4", "d5", "d9" by distance (-1 = nothing active
    // yet: row 0 is one step away), " past" for rows above the active one (they fade out slower).
    function depth(i, active) {
      const d = active < 0 ? i + 1 : Math.abs(i - active);
      return "d" + level(d) + (active >= 0 && i < active ? " past" : "");
    }
    // The rows whose depth class can differ between active rows `a` and `b` (n rows): [lo, hi], or null.
    function depthRange(a, b, n, reach = FAR + 1) {
      if (!(n > 0)) return null;
      const lo = Math.max(0, Math.min(a, b) - reach), hi = Math.min(n - 1, Math.max(a, b, 0) + reach);
      return lo <= hi ? [lo, hi] : null;
    }
    // Where the view scrolls for an active row whose top is `top` px down the content: that top at 35% of `viewH`.
    const anchorTop = (top, viewH, frac = ANCHOR) => Math.max(0, Math.round(top - frac * viewH));
    // Rows move to a new active line as a wave: rows above it first, 20 ms per row after, at most 200 ms.
    const stagger = (i, active) => clamp((i - active + 2) * STAGGER, 0, STAGGER_MAX);
    // An auto-scroll animates (rows slide by `delta`) only when it's a real move that stays near the screen.
    const glides = (delta, viewH) => Math.abs(delta) >= 1 && Math.abs(delta) <= 1.5 * viewH;
    // The rAF loop is needed only while the active row fills smoothly: a word-timed line or an interlude (a line-timed
    // row in mixed lyrics follows timeupdate like any other).
    const needsFrames = (row) => !!row && (row.kind === "gap" || (row.kind === "line" && !!(row.words && row.words.length)));
    // Long lines shrink so they don't wrap into walls of text (>42 chars ×.9, >54 ×.8, >72 ×.72, >92 ×.66).
    function sizeFactor(len) {
      const n = Number(len) || 0;
      return n > 92 ? 0.66 : n > 72 ? 0.72 : n > 54 ? 0.8 : n > 42 ? 0.9 : 1;
    }
    // Word-timed spans grouped into words: syllables with no space between stay together (one unbreakable group).
    // -> [{from, to (inclusive), space (a space follows the group)}]
    function groups(words) {
      const out = [];
      let from = 0;
      for (let i = 0; i < (words ? words.length : 0); i++) {
        const t = String(words[i].text || ""), nx = words[i + 1];
        const space = /\s$/.test(t) || (nx && /^\s/.test(String(nx.text || "")));
        if (space || !nx) { out.push({ from, to: i, space: !!space && !!nx }); from = i + 1; }
      }
      return out;
    }
    return { GAP_MIN, GAP_FADE, STEPS, FAR, ANCHOR, level, HOLD_MS, isBreak, indexAt, lineEnd, rows, wordAt, wordFill, glows,
      gapProgress, dotFill, depth, depthRange, anchorTop, stagger, glides, needsFrames, sizeFactor, groups };
  })();
  // ==== lyrics-view-core:end ====

  // ---------- palette core ----------
  // Cover colours (UI_REMAKE_PLAN §1.1) from an image's RGBA pixels (a 64 px canvas of /api/art): pure
  // functions, no DOM. Pixels go into coarse RGB buckets ranked by population, vivid ones (HSV saturation > .3,
  // brightness .2-.9) counting ×1.3; a mean saturation under .22 (near-black pixels left out) makes the image grey. Roles: `base` the darkest
  // muted colour (lightness ≤ 26%), `accent` the most vibrant one at ~80% lightness with ≥ 4.5:1 contrast on base,
  // `onAccent` ~20% for text on the accent. Unit-checked from node by slicing between these sentinels.
  // ==== palette-core:start ====
  const P = (() => {
    const DEFAULT = Object.freeze({ base: "#121212", accent: "#fb2c5a", onAccent: "#ffffff", grey: false });
    const BLACK = Object.freeze({ base: "#000000", accent: "#fb2c5a", onAccent: "#ffffff", grey: false });
    const VIVID_BOOST = 1.3;
    const GREY_SAT = 0.22;
    const BASE_MAX_L = 0.26, BASE_MIN_L = 0.08, BASE_MAX_S = 0.55;
    const ACCENT_L = 0.8, ON_ACCENT_L = 0.2, MIN_CONTRAST = 4.5;
    const clamp = (x, a, b) => Math.min(b, Math.max(a, x));

    // h in [0, 360), s and l in [0, 1]
    function rgbToHsl(r, g, b) {
      r /= 255; g /= 255; b /= 255;
      const mx = Math.max(r, g, b), mn = Math.min(r, g, b), l = (mx + mn) / 2, d = mx - mn;
      if (!d) return [0, 0, l];
      const s = d / (1 - Math.abs(2 * l - 1));
      const h = mx === r ? ((g - b) / d) % 6 : mx === g ? (b - r) / d + 2 : (r - g) / d + 4;
      return [(h * 60 + 360) % 360, clamp(s, 0, 1), l];
    }
    function hslToRgb(h, s, l) {
      const c = (1 - Math.abs(2 * l - 1)) * s, x = c * (1 - Math.abs(((h / 60) % 2) - 1)), m = l - c / 2;
      const [r, g, b] = h < 60 ? [c, x, 0] : h < 120 ? [x, c, 0] : h < 180 ? [0, c, x] : h < 240 ? [0, x, c] : h < 300 ? [x, 0, c] : [c, 0, x];
      return [r, g, b].map((v) => Math.round(clamp(v + m, 0, 1) * 255));
    }
    const hex = (rgb) => "#" + rgb.map((v) => Math.round(v).toString(16).padStart(2, "0")).join("");
    function fromHex(h) {
      const m = /^#?([0-9a-f]{6})$/i.exec(String(h || ""));
      if (!m) return null;
      const n = parseInt(m[1], 16);
      return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
    }
    // WCAG relative luminance and contrast ratio
    function luminance(rgb) {
      const [r, g, b] = rgb.map((v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; });
      return 0.2126 * r + 0.7152 * g + 0.0722 * b;
    }
    function contrast(a, b) {
      const x = luminance(a), y = luminance(b);
      return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05);
    }

    // RGBA bytes -> buckets (3 bits per channel), each {rgb (mean), h, s, l, sv, v, n, share, vivid, score}, best first
    function buckets(data, { minAlpha = 125 } = {}) {
      const map = new Map();
      let total = 0, satSum = 0, satN = 0;
      for (let i = 0; i + 3 < (data?.length || 0); i += 4) {
        if (data[i + 3] < minAlpha) continue;
        const r = data[i], g = data[i + 1], b = data[i + 2];
        const key = ((r >> 5) << 6) | ((g >> 5) << 3) | (b >> 5);
        let e = map.get(key);
        if (!e) map.set(key, (e = { n: 0, r: 0, g: 0, b: 0 }));
        e.n++; e.r += r; e.g += g; e.b += b;
        const mx = Math.max(r, g, b), mn = Math.min(r, g, b);
        // near-black pixels have no meaningful saturation: they don't vote on "grey"
        if (mx >= 31) { satSum += (mx - mn) / mx; satN++; }
        total++;
      }
      const list = [...map.values()].map((e) => {
        const rgb = [e.r / e.n, e.g / e.n, e.b / e.n].map(Math.round);
        const [h, s, l] = rgbToHsl(...rgb);
        const mx = Math.max(...rgb), mn = Math.min(...rgb);
        const v = mx / 255, sv = mx ? (mx - mn) / mx : 0;
        const vivid = sv > 0.3 && v >= 0.2 && v <= 0.9;
        return { rgb, h, s, l, sv, v, n: e.n, share: e.n / total, vivid, score: e.n * (vivid ? VIVID_BOOST : 1) };
      }).sort((a, b) => b.score - a.score || b.n - a.n);
      return { list, total, meanSat: satN ? satSum / satN : 0 };
    }

    // The best bucket for a target lightness / saturation, weighing population like a palette swatch search.
    function best(list, { l, s, minL = 0, maxL = 1, filter = () => true }) {
      const top = list[0]?.score || 1;
      let pick = null, score = -Infinity;
      for (const c of list) {
        if (c.l < minL || c.l > maxL || !filter(c)) continue;
        const v = 0.24 * (1 - Math.abs(c.s - s)) + 0.52 * (1 - Math.abs(c.l - l)) + 0.24 * (c.score / top);
        if (v > score) { score = v; pick = c; }
      }
      return pick;
    }

    function derive(data) {
      const { list, total, meanSat } = buckets(data);
      if (!total) return null;
      const grey = meanSat < GREY_SAT;
      const kept = list.filter((c) => c.share >= 0.01);
      const cands = kept.length ? kept : list;
      // base: the darkest muted colour, clamped dark; near-black / near-white buckets carry no hue
      const b = best(cands, { l: 0.26, s: 0.3, minL: 0.04, maxL: 0.6 }) || cands[0];
      const baseHsl = [b.h, grey ? 0 : Math.min(b.s, BASE_MAX_S), clamp(b.l, BASE_MIN_L, BASE_MAX_L)];
      const base = hslToRgb(...baseHsl);
      // accent: the most vibrant colour, lifted to ~80% lightness until it reads on the base
      const a = grey ? null : best(cands, { l: 0.5, s: 1, minL: 0.15, maxL: 0.92, filter: (c) => c.sv > 0.3 && c.v >= 0.2 })
        || cands.filter((c) => c.l > 0.1 && c.l < 0.95).sort((x, y) => y.s - x.s)[0] || b;
      const aH = a ? a.h : b.h, aS = grey ? 0 : clamp(a.s, 0.35, 0.9);
      let aL = ACCENT_L, accent = hslToRgb(aH, aS, aL);
      while (contrast(accent, base) < MIN_CONTRAST && aL < 0.97) { aL += 0.02; accent = hslToRgb(aH, aS, aL); }
      // on-accent: the same hue, dark enough to read on the accent
      let oL = ON_ACCENT_L, onAccent = hslToRgb(aH, Math.min(aS, 0.6), oL);
      while (contrast(onAccent, accent) < MIN_CONTRAST && oL > 0.02) { oL -= 0.03; onAccent = hslToRgb(aH, Math.min(aS, 0.6), oL); }
      return { base: hex(base), accent: hex(accent), onAccent: hex(onAccent), grey };
    }

    // The CSS custom properties for a palette (DEFAULT when it's missing or malformed).
    function vars(p) {
      const ok = p && fromHex(p.base) && fromHex(p.accent) && fromHex(p.onAccent);
      const q = ok ? p : DEFAULT;
      return { "--c-base": q.base, "--c-accent": q.accent, "--c-on-accent": q.onAccent };
    }

    // A bounded {id: palette} cache: `id` moves to the end (newest); the oldest go past `max`. Returns a new object.
    function remember(cache, id, p, max = 150) {
      const out = {};
      if (!(max > 0)) return out; // nothing may be kept, not even the new one
      const keys = Object.keys(cache && typeof cache === "object" ? cache : {}).filter((k) => k !== id);
      for (const k of keys.slice(Math.max(0, keys.length - (max - 1)))) out[k] = cache[k];
      out[id] = p;
      return out;
    }

    return { DEFAULT, BLACK, MIN_CONTRAST, rgbToHsl, hslToRgb, hex, fromHex, luminance, contrast, buckets, derive, vars, remember };
  })();
  // ==== palette-core:end ====

  // ---------- player core ----------
  // Gesture, geometry and timing maths for the mini player, Now Playing and the Up next sheet (UI_REMAKE_PLAN §3.2,
  // §4.3-4.7, §7): pure functions, no DOM, no state. Unit-checked from node by slicing between these sentinels.
  // ==== player-core:start ====
  const PC = (() => {
    const EDGE = 24; // px: a horizontal drag starting this close to the left edge is Safari's back swipe (§0)
    const SLOP = 10; // px a pointer moves before a drag picks its axis
    const BGS = ["ambient", "immersive", "tint", "black"]; // Now Playing background styles (§4.1)
    const clamp = (x, a, b) => Math.min(b, Math.max(a, x));
    const sign = (x) => (x > 0 ? 1 : x < 0 ? -1 : 0);
    // The axis a drag follows once it has moved `slop` px: "x", "y", null (not yet) or "none" (not ours: a
    // horizontal drag from the left edge zone, or an axis the surface doesn't take).
    function axisOf(dx, dy, { slop = SLOP, x0 = Infinity, edge = EDGE, allowX = true, allowY = true } = {}) {
      if (Math.hypot(dx, dy) < slop) return null;
      if (Math.abs(dx) > Math.abs(dy)) return allowX && x0 >= edge ? "x" : "none";
      return allowY ? "y" : "none";
    }
    // Rubber band: the first `free` px of a drag move at most `cap` px, then 1:1 (mini player 100 -> 30, rows 60 -> 20).
    function rubber(d, free = 100, cap = 30) {
      const a = Math.abs(d);
      return sign(d) * (a <= free ? a * (cap / free) : cap + (a - free));
    }
    // A drag towards nothing (no neighbour) moves `k` of the finger's distance.
    const resist = (d, k = 0.3) => d * k;
    // px/s from pointer samples [{t (ms), x, y}], measured over the last `win` ms before the newest one.
    function velocity(samples, win = 100) {
      const n = samples ? samples.length : 0;
      if (n < 2) return { vx: 0, vy: 0 };
      const last = samples[n - 1];
      let first = samples[n - 2];
      for (let i = n - 2; i >= 0 && last.t - samples[i].t <= win; i--) first = samples[i];
      const dt = (last.t - first.t) / 1000;
      return dt > 0 ? { vx: (last.x - first.x) / dt, vy: (last.y - first.y) / dt } : { vx: 0, vy: 0 };
    }
    // Release of a horizontal swipe: the direction it commits to (-1 left, 1 right) or 0 (spring back). Past `frac` of
    // `width`, or a flick of `vmin` px/s in the direction it was dragged.
    function swipeCommit(dx, vx, width, { frac = 1 / 3, vmin = 1000, minDx = 8 } = {}) {
      if (width > 0 && Math.abs(dx) >= frac * width) return sign(dx);
      if (Math.abs(vx) >= vmin && sign(vx) === sign(dx) && Math.abs(dx) >= minDx) return sign(vx);
      return 0;
    }
    // Release of a vertical drag on the mini player: "open" (swiped up), "dismiss" (pulled down > 32 px or 800 px/s), null.
    function miniVertical(dy, vy, { up = 24, vUp = 500, down = 32, vDown = 800 } = {}) {
      if (dy <= -up || (vy <= -vUp && dy < 0)) return "open";
      if (dy >= down || (vy >= vDown && dy > 0)) return "dismiss";
      return null;
    }
    // Now Playing pulled down by its top bar or the art: close past `dist` px, or on a downward flick.
    const npClose = (dy, vy, { dist = 110, vmin = 800 } = {}) => dy >= dist || (vy >= vmin && dy > 16);
    // The Up next pull-up's drag threshold: clamp(5% of the height, 14, 56) px.
    const pullThreshold = (h) => clamp(0.05 * h, 14, 56);
    // Release of the Up next sheet. `open`: where the drag started; `dist`: px dragged (negative = up); `vy`: px/s; `h`:
    // the player's height. A flick up (< -520 px/s) opens, a flick down (> 450 px/s) closes; else it opens once dragged
    // up past the threshold, and closes once dragged down past max(threshold, a quarter of the height). -> open?
    function pullSettle(open, dist, vy, h) {
      if (vy < -520) return true;
      if (vy > 450) return false;
      return open ? !(dist > Math.max(pullThreshold(h), 0.25 * h)) : -dist > pullThreshold(h);
    }
    // Pull-up progress f (0 = peek, 1 = open) -> the player under it: opacity 1 - f/.4, art scale 1 - .1f, the
    // sheet's top radius 28 -> 0.
    function pullLook(f) {
      f = clamp(Number(f) || 0, 0, 1);
      return { fade: clamp(1 - f / 0.4, 0, 1), scale: 1 - 0.1 * f, radius: 28 * (1 - f) };
    }
    // A no-bounce settle (critically damped spring): progress 0..1 at `t` ms of a move that lasts `ms`.
    function settle(t, ms = 380) {
      if (!(t > 0)) return 0;
      if (t >= ms) return 1;
      const k = 7, x = (k * t) / ms;
      return Math.min(1, (1 - (1 + x) * Math.exp(-x)) / (1 - (1 + k) * Math.exp(-k)));
    }
    // Seek bar: pointer x -> fraction of the bar [0, 1].
    const seekFrac = (x, left, width) => (width > 0 ? clamp((x - left) / width, 0, 1) : 0);
    // The progress ring's stroke-dashoffset at progress `p` for a circle of circumference `c` (c = empty, 0 = full).
    const ringOffset = (p, c) => c * (1 - clamp(Number(p) || 0, 0, 1));
    // Double-tap seek on the art (§4.3): a second tap on the same half within `double` ms seeks `step` s; each further
    // tap there within `chain` ms of the last one adds `step`. `side`: -1 left half, 1 right half.
    // -> {at, side, total (s so far in this run), seek (signed s to move now; 0 = nothing yet)}
    function tapSeek(prev, now, side, { double = 350, chain = 1000, step = 5 } = {}) {
      if (prev && prev.side === side) {
        if (prev.total > 0 && now - prev.at <= chain) return { at: now, side, total: prev.total + step, seek: side * step };
        if (!(prev.total > 0) && now - prev.at <= double) return { at: now, side, total: step, seek: side * step };
      }
      return { at: now, side, total: 0, seek: 0 };
    }
    // The current track's neighbours for swipes and peeks: queue indexes (-1 = none). Like Q.nextIndex, repeat-all
    // wraps to the start; previous never wraps.
    function neighbours(len, index, repeat = "off") {
      if (!(len > 0) || !(index >= 0) || index >= len) return { prev: -1, next: -1 };
      const next = index + 1 < len ? index + 1 : repeat === "all" && len > 1 ? 0 : -1;
      return { prev: index > 0 ? index - 1 : -1, next };
    }
    // Move the entry at `from` to `to` (drag to reorder). -> {queue, index}: the current track stays current.
    function moveTrack(queue, index, from, to) {
      const n = queue.length;
      if (!(from >= 0 && from < n && to >= 0 && to < n) || from === to) return { queue: queue.slice(), index };
      const q = queue.slice();
      const [t] = q.splice(from, 1);
      q.splice(to, 0, t);
      let i = index;
      if (from === index) i = to;
      else if (from < index && to >= index) i = index - 1;
      else if (from > index && to <= index) i = index + 1;
      return { queue: q, index: i };
    }
    // Where a dragged row lands: `from` moved by `dy` px over rows `rowH` apart, kept inside [lo, hi].
    const dropIndex = (from, dy, rowH, lo, hi) => (rowH > 0 ? clamp(from + Math.round(dy / rowH), lo, hi) : from);
    // Undo a removal: put `track` back where it was relative to the current track. `rel` = its old index minus the old
    // current index (> 0 upcoming, < 0 history). Nothing happens when it's in the queue again. -> {queue, index}
    function reinsert(queue, index, track, rel) {
      if (!track || !track.id || queue.some((t) => t && t.id === track.id) || !rel) return { queue: queue.slice(), index };
      const q = queue.slice();
      const at = clamp(rel > 0 ? index + rel : index + rel + 1, 0, q.length);
      q.splice(at, 0, track);
      return { queue: q, index: at <= index ? index + 1 : index };
    }
    // The queue header's numbers: how many songs, and their known length in seconds.
    function queueStats(queue) {
      let secs = 0;
      for (const t of queue || []) if (Number(t?.duration) > 0) secs += Number(t.duration);
      return { count: (queue || []).length, seconds: Math.round(secs) };
    }
    const bgStyle = (v) => (BGS.includes(v) ? v : "ambient");
    // Delayed moves (a skip after the squish, a swipe after its slide) act only on what was playing when they were
    // asked for: `then` and `now` are {token (the play token), id (the current track's id)}. Any track change in
    // between (another skip, a song ending, recovery moving on, a placeholder swap) drops the move.
    const sameTrack = (then, now) => !!then && !!now && then.id != null && then.token === now.token && then.id === now.id;
    // The queue index of the track `id` now (a target picked before the queue changed), or -1.
    const indexOfId = (queue, id) => (id == null ? -1 : (queue || []).findIndex((t) => t && t.id === id));
    // A title wider than its box scrolls as a seamless loop (the text twice, `gap` apart): wait `delay`, move one
    // text-plus-gap at `speed` px/s. null = it fits.
    function marquee(textW, boxW, { delay = 3000, speed = 30, gap = 40 } = {}) {
      if (!(textW - boxW > 1)) return null;
      const dist = Math.ceil(textW + gap);
      const travel = Math.round((dist / speed) * 1000);
      return { dist, delay, travel, total: delay + travel };
    }
    return { EDGE, SLOP, BGS, clamp, axisOf, rubber, resist, velocity, swipeCommit, miniVertical, npClose, pullThreshold,
      pullSettle, pullLook, settle, seekFrac, ringOffset, tapSeek, neighbours, moveTrack, dropIndex, reinsert, queueStats,
      bgStyle, marquee, sameTrack, indexOfId };
  })();
  // ==== player-core:end ====

  // ---------- sheet core ----------
  // The track menu's item rules, row swipe thresholds, sheet drag-to-dismiss, the sleep timer's numbers and Song info's
  // formatting (UI_REMAKE_PLAN §8): pure functions, no DOM, no state. Unit-checked from node by slicing between these
  // sentinels.
  // ==== sheet-core:start ====
  const SH = (() => {
    const ROW_COMMIT = 115; // px a row swipe travels before it acts (§8.2), at most ROW_FRAC of the row
    const ROW_FRAC = 0.45;
    const ROW_BAND = 60; // the coloured band under a swiped row; a flick must at least uncover it
    const ROW_FLICK = 900; // px/s
    const SWIPE_ACTIONS = ["next", "queue", "like", "keep", "off"];
    const SWIPE_DEFAULT = { right: "next", left: "queue" };
    const SLEEP_PRESETS = [15, 30, 45, 60];
    const SLEEP_MAX = 12 * 60; // minutes
    const sign = (x) => (x > 0 ? 1 : x < 0 ? -1 : 0);
    const clamp = (x, a, b) => Math.min(b, Math.max(a, x));

    // ---- track menu (§8.1) ----
    // What the menu shows for a track. `c`: {placeholder, src ("yt" | "mc" | "nd" | …), playing (it's the current
    // track), context ("list" | "player" | "queue"), keep ("kept" | "pending" | "none"), canKeep, artists (how many
    // have a page), album (has an album page), hide (an artist "Hide" can name), matched (a placeholder, a song an
    // import or a replacement picked: it carries a match key or `replaced_from`)}.
    // -> {head: [...], quick: [...], goto: [...], recs: [...], more: [...]} (item keys, in order; empty groups drop)
    function menuModel(c = {}) {
      const ph = !!c.placeholder, yt = c.src === "yt" && !ph;
      const quick = [];
      if (!c.playing) quick.push("next", c.context === "queue" ? "remove" : "queue");
      if (yt) quick.push("radio");
      if (!ph && (c.keep === "kept" || c.keep === "pending" || c.canKeep)) quick.push("keep");
      if (!ph) quick.push("playlist", "like");
      const goto = [];
      if (c.artists > 1) goto.push("artists"); else if (c.artists === 1) goto.push("artist");
      if (c.album) goto.push("album");
      const recs = [];
      if (!ph && c.src !== "nd") recs.push("dislike");
      if (c.hide && !ph) recs.push("hide");
      const more = ["info"];
      if (canFix(c)) more.push("fix");
      if (yt) more.push("copy", "youtube");
      if (c.context === "player") more.push("npbg");
      return { head: ph ? ["info"] : ["share", "info"], quick, goto, recs, more };
    }
    // "Fix wrong match" (§8.4): where Reef picked the song (a placeholder, an import's or a replacement's match), or the
    // song in the player / queue (it swaps in place there). Never a library track: that file is what it is.
    function canFix(c = {}) {
      if (c.src === "nd") return false;
      if (c.placeholder || c.matched) return true;
      return (c.context === "player" || c.context === "queue") && (c.src === "yt" || c.src === "mc");
    }
    // The match key a fix pins: a placeholder's own (imports), the one a matched song carries, else null (the server
    // derives it from title + artist).
    const fixKey = (t) => (t && typeof t.key === "string" && t.key ? t.key : t && typeof t.mkey === "string" && t.mkey ? t.mkey : null);
    // Quick tiles lay out in 3 columns; 2 or 4 of them look better as 2.
    const gridCols = (n) => (n === 2 || n === 4 ? 2 : 3);

    // ---- row swipes (§8.2) ----
    // Row offset for a finger at `dx`: 1:1 up to the commit point, then a third of the rest.
    function rowOffset(dx, width = Infinity) {
      const need = commitAt(width), a = Math.abs(dx);
      return sign(dx) * (a <= need ? a : need + (a - need) / 3);
    }
    const commitAt = (width) => (width > 0 && isFinite(width) ? Math.min(ROW_COMMIT, ROW_FRAC * width) : ROW_COMMIT);
    const rowArmed = (dx, width) => Math.abs(dx) >= commitAt(width);
    // Release: 1 (swiped right), -1 (left) or 0 (spring back). Past the commit point, or a flick that uncovered the band.
    function rowCommit(dx, vx, width) {
      if (rowArmed(dx, width)) return sign(dx);
      if (Math.abs(vx) >= ROW_FLICK && sign(vx) === sign(dx) && Math.abs(dx) >= ROW_BAND) return sign(dx);
      return 0;
    }
    // The saved per-direction actions (a UI pref), with anything unknown back to the default.
    function swipePrefs(raw) {
      const ok = (v) => SWIPE_ACTIONS.includes(v);
      return { right: ok(raw?.right) ? raw.right : SWIPE_DEFAULT.right, left: ok(raw?.left) ? raw.left : SWIPE_DEFAULT.left };
    }
    const swipeAction = (prefs, dir) => (dir > 0 ? prefs.right : dir < 0 ? prefs.left : "off");
    // Whether a swipe action can act on a track ({placeholder, playing, keep, canKeep}); else the row only resists.
    function swipeUsable(action, c = {}) {
      if (action === "next" || action === "queue") return !c.playing;
      if (action === "like") return !c.placeholder;
      if (action === "keep") return !c.placeholder && c.keep === "none" && !!c.canKeep;
      return false;
    }

    // ---- sheets ----
    // A sheet dragged down by its top: closes past min(140 px, 35% of its height), or on a downward flick.
    function sheetDismiss(dy, vy, h) {
      if (vy >= 700 && dy > 12) return true;
      if (vy <= -300) return false;
      return dy >= Math.min(140, 0.35 * (h > 0 ? h : 400));
    }
    // Where the sheet sits while dragged: down 1:1, up only a little (rubber, at most 24 px).
    const sheetPull = (dy) => (dy >= 0 ? dy : -Math.min(24, Math.sqrt(-dy) * 2));

    // ---- sleep timer (§8.5) ----
    // ms -> {h, m, s} as two-digit strings (h may grow past 99 never: the timer caps at 12 h).
    function clockParts(ms) {
      const t = Math.max(0, Math.ceil((Number(ms) || 0) / 1000));
      const p = (n) => String(n).padStart(2, "0");
      return { h: p(Math.floor(t / 3600)), m: p(Math.floor((t % 3600) / 60)), s: p(t % 60) };
    }
    // Remaining share of the timer (1 = just set, 0 = due).
    const sleepFrac = (leftMs, totalMin) => (totalMin > 0 ? clamp(leftMs / (totalMin * 60000), 0, 1) : 0);
    // The Custom steppers: hours 0..12, minutes 0..55 in 5s (wrapping), never past 12 h.
    function stepCustom(v, part, delta) {
      let h = clamp(Math.round(Number(v?.h) || 0), 0, 12), m = clamp(Math.round(Number(v?.m) || 0), 0, 55);
      if (part === "h") h = clamp(h + delta, 0, 12);
      else m = (((m + delta * 5) % 60) + 60) % 60;
      if (h === 12) m = 0;
      return { h, m };
    }
    const customMinutes = (v) => clamp((Number(v?.h) || 0) * 60 + (Number(v?.m) || 0), 0, SLEEP_MAX);
    // "1 h 20 min", "45 min", "2 h".
    function durLabel(min) {
      min = Math.max(0, Math.round(Number(min) || 0));
      const h = Math.floor(min / 60), m = min % 60;
      return h ? `${h} h${m ? ` ${m} min` : ""}` : `${m} min`;
    }
    // +5 min on a running timer, capped at SLEEP_MAX from now. -> {deadline, total}
    function extend(deadline, total, now, add = 5) {
      const d = Math.min(Math.max(deadline, now) + add * 60000, now + SLEEP_MAX * 60000);
      return { deadline: d, total: Math.max(total, Math.ceil((d - now) / 60000)) };
    }

    // ---- Song info (§8.3) ----
    const fmtRate = (hz) => (Number(hz) > 0 ? `${String(Math.round(Number(hz) / 100) / 10).replace(/\.0$/, "")} kHz` : "");
    const fmtKbps = (k) => (Number(k) > 0 ? `${Math.round(Number(k))} kbps` : "");
    function fmtSize(bytes) {
      const b = Number(bytes);
      if (!(b > 0)) return "";
      if (b < 1024 * 1024) return `${Math.max(1, Math.round(b / 1024))} KB`;
      return `${(b / 1024 / 1024).toFixed(b < 10 * 1024 * 1024 ? 1 : 0)} MB`;
    }
    const CODECS = { aac: "AAC", mp4a: "AAC", opus: "Opus", flac: "FLAC", mp3: "MP3", vorbis: "Vorbis", alac: "ALAC", wave: "WAV", wav: "WAV" };
    const codecName = (c) => (c ? CODECS[String(c).toLowerCase()] || String(c).toUpperCase() : "");
    // "FLAC · 16-bit · 44.1 kHz · 912 kbps" from whatever is known (bit depth only means something when lossless).
    const LOSSLESS = ["FLAC", "ALAC", "WAV"];
    function techLine(x = {}) {
      const c = codecName(x.codec);
      return [c, x.bits && LOSSLESS.includes(c) ? `${x.bits}-bit` : "", fmtRate(x.sample_rate), fmtKbps(x.bitrate)].filter(Boolean).join(" · ");
    }
    const scorePct = (s) => (Number.isFinite(Number(s)) && s !== null ? `${Math.round(clamp(Number(s), 0, 1) * 100)}%` : "");

    return { ROW_COMMIT, ROW_BAND, ROW_FLICK, SWIPE_ACTIONS, SWIPE_DEFAULT, SLEEP_PRESETS, SLEEP_MAX, menuModel, canFix, fixKey,
      gridCols, rowOffset, commitAt, rowArmed, rowCommit, swipePrefs, swipeAction, swipeUsable, sheetDismiss, sheetPull,
      clockParts, sleepFrac, stepCustom, customMinutes, durLabel, extend, fmtRate, fmtKbps, fmtSize, codecName, techLine, scorePct };
  })();
  // ==== sheet-core:end ====

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
    ...(typeof t.mkey === "string" && t.mkey ? { mkey: t.mkey } : {}), // the match key Reef found it by (Fix wrong match)
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
  // Liked track ids (/api/likes, and `liked` on every server list): Now Playing's Like button.
  const likedIds = new Set();
  function noteKept(tracks) {
    for (const t of tracks || []) {
      if (!t || !t.id) continue;
      if (typeof t.liked === "boolean") { if (t.liked) likedIds.add(t.id); else likedIds.delete(t.id); }
      if (typeof t.kept !== "boolean") continue;
      if (t.kept) keptIds.add(t.id); else keptIds.delete(t.id);
      if (t.kept && t.nd_id) ndIds.set(t.id, t.nd_id); else if (!t.kept) ndIds.delete(t.id);
    }
    if (cur()) paintLike();
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

  // ---------- empty and error states (§1.5) ----------
  // One component: a 40 px icon, a short title, a line of text, one action. Errors read the server's typed
  // failure ({block, hint} from errors.py) so the text says what to do ("YouTube wants a bot check: try mobile data").
  function emptyState(ic, title, text, action) {
    return `<div class="empty" role="status">${icon(ic)}${title ? `<h3>${esc(title)}</h3>` : ""}${text ? `<p>${esc(text)}</p>` : ""}${action || ""}</div>`;
  }
  const BLOCK_TITLE = {
    BOT_WALL: "YouTube wants a bot check", NETWORK: "Network problem", RATE_LIMITED: "YouTube is rate-limiting",
    REGION: "Not available in your region", LOGIN_REQUIRED: "Sign-in required", MEMBERS_OR_PAID: "Members-only",
    GONE: "Not available",
  };
  // {ic, title, text} for a failed api() call: offline, server unreachable, a typed block, or the message itself.
  function errorInfo(e, title = "Something went wrong", fallback = "Try again in a moment.") {
    if (navigator.onLine === false) return { ic: "wifi-off", title: "You're offline", text: "This loads again once you're back online." };
    if (!e || !e.status) return { ic: "wifi-off", title: "Can't reach Reef", text: "The server didn't answer. Is it running, and is Tailscale on?" };
    const block = e.body?.block;
    if (block && block !== "UNKNOWN" && BLOCK_TITLE[block]) return { ic: "alert", title: BLOCK_TITLE[block], text: e.body.hint || fallback };
    const msg = e.message && e.message.length <= 90 ? e.message : fallback;
    return { ic: "alert", title, text: msg };
  }
  // `retry` is the attribute the Retry button carries (handled in the click listener); `notFound` the 404 text.
  function errorState(e, { title, fallback, retry = "", notFound = "" } = {}) {
    const x = e?.status === 404 && notFound ? { ic: "alert", title: "Not found", text: notFound } : errorInfo(e, title, fallback);
    return `<div class="empty err" role="alert">${icon(x.ic)}<h3>${esc(x.title)}</h3><p>${esc(x.text)}</p>${retry
      ? `<button class="pill-btn" ${retry}>${icon("repeat")}Retry</button>` : ""}</div>`;
  }

  // ---------- skeletons + list-in (§1.4) ----------
  // Skeletons stay invisible for 250 ms, then pulse (CSS); a container that held one animates its real content in.
  const SK_LABEL = `role="status" aria-label="Loading…"`;
  const skRows = (n, { art = true } = {}) => `<div class="skel" ${SK_LABEL}>${`<div class="sk-row">${art ? '<div class="sk a"></div>' : ""}<div class="l2"><i class="sk"></i><i class="sk"></i></div></div>`.repeat(n)}</div>`;
  const skCards = (n = 4) => `<div class="scroller skel short" ${SK_LABEL}>${'<div class="card"><div class="card-art sk"></div><div class="sk sk-line"></div></div>'.repeat(n)}</div>`;
  const LIST_SEL = ".rows, .scroller, .tiles, .grid2, .qp, .joined, .chips.wrap";
  function animateIn(root) {
    for (const el of $$(LIST_SEL, root)) if (!el.classList.contains("skel")) el.classList.add("list-in");
  }
  // Set innerHTML; lists fade in when the view was just opened (route) or this element was showing a skeleton.
  function paint(el, html) {
    if (!el) return;
    const fresh = el.closest(".view")?.dataset.anim === "1" || !!el.querySelector(".skel");
    el.innerHTML = html;
    if (fresh) animateIn(el);
  }

  // ---------- Home shelves (/api/home) ----------
  const homeShelf = (id) => (state.home?.shelves || []).find((s) => s.id === id && Array.isArray(s.items) && s.items.length);
  const homeItems = (id) => homeShelf(id)?.items || [];
  const skeletonCards = () => skCards(4);

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
    let html = `<h1 class="page-title">${greeting()}</h1>`;
    {
      let s;
      if (!state.home) {
        html += state.homeErr
          ? errorState(state.homeErr, { title: "Couldn't load Home", retry: "data-retry-home" })
          : shelf({ title: "Quick picks", ic: "sparkle", body: skeletonCards() });
      }
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
        html += shelf({ title: "Recently kept", ic: "sparkle", sub: "Saved to Navidrome by Reef", more: "#library", body: squares(state.history.slice(0, 15), "history") });
      }
      const searches = store.get("searches", []);
      if (searches.length) {
        html += shelf({ title: "Recent searches", ic: "search", body: `<div class="chips wrap">${searches.map((q) => `<button class="chip" data-search="${esc(q)}">${esc(q)}</button>`).join("")}</div>` });
      }
      html += moodsOn()
        ? shelf({ title: "Browse moods & genres", ic: "trend", sub: "Playlists from YouTube Music", more: "#moods", body: tiles() })
        : shelf({ title: "Browse moods & genres", ic: "trend", sub: "Tap one to search YouTube", body: tiles() });
    }
    paint(view, html);
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
      ${res.length ? `<div class="actions joined h">
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
      paint(el, sourceChips() + (searches.length ? `
        <div class="head-row"><h2 class="section-title">Recent</h2><button class="link-btn" data-clear-searches>Clear</button></div>
        <ul class="rows">${searches.map((q) => `<li class="row"><button class="row-main" data-search="${esc(q)}"><span class="row-art" style="display:grid;place-items:center;color:var(--faint)">${icon("clock")}</span><span class="row-text"><span class="row-t">${esc(q)}</span></span></button></li>`).join("")}</ul>` : "")
        + `<div class="head-row sec"><h2 class="section-title">Browse Categories</h2>${moodsOn() ? `<a class="link-btn" href="#moods">See all</a>` : ""}</div>${tiles()}`);
      return;
    }

    // Pasted links ignore the source and the filters: they always resolve on YouTube.
    const link = state.link;
    const chips = link ? "" : sourceChips() + `<div class="chips">${[["top", "Top Results"], ["songs", "Songs"], ["kept", "In library"]].map(([k, l]) =>
      `<button class="chip${state.filter === k ? " on" : ""}" data-filter="${k}">${l}</button>`).join("")}</div>`;

    if (state.searching) {
      el.innerHTML = chips + `<h2 class="section-title">${link ? "Opening link…" : "Top result"}</h2><div class="skel short" ${SK_LABEL}><div class="sk sk-top"></div></div>${link ? "" : `<h2 class="section-title">Songs</h2>`}`
        + skRows(link ? 3 : 5);
      return;
    }
    if (state.searchError) {
      paint(el, chips + errorState(state.searchError, { title: link ? "Couldn't open link" : "Search failed", retry: "data-retry-search" }));
      return;
    }
    const res = state.results;
    if (link?.kind === "playlist") { paint(el, playlistView(res)); return; }
    if (!res.length) {
      paint(el, chips + emptyState("search", "No results", `Nothing found for “${state.query}”.`));
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
    paint(el, html);
  }

  // ---------- Library (UI remake step 1) ----------
  // One page for what used to be the Kept and Downloads tabs: active keeps (Downloads), Reef's own playlists
  // (+ Import), Navidrome's newest albums and the Kept songs. The Library hub (§6) replaces it in step 6.
  // Each part paints into its own box, so the 1.5 s jobs poll repaints Downloads only.
  async function loadPlaylists() {
    try {
      const list = await api("/api/playlists");
      if (Array.isArray(list)) { state.playlists = list; state.playlistsErr = null; }
    } catch (e) {
      if (!state.playlists) state.playlistsErr = e; // keep the last list
    }
    if (state.view === "library") { renderLibPlaylists(); renderLibSub(); }
  }
  const libHead = (title, count, action = "") => `<div class="lib-head"><h2>${esc(title)}</h2>${count ? `<span class="lib-count">${count}</span>` : ""}${action}</div>`;
  function renderLibPlaylists() {
    const el = $("#lib-pl");
    if (!el) return;
    const list = state.playlists;
    const body = list === null
      ? (state.playlistsErr ? errorState(state.playlistsErr, { title: "Couldn't load playlists", retry: "data-retry-playlists" }) : skRows(2))
      : list.length ? `<ul class="joined">${list.map((p) => `<li class="row pl-row"><a class="pl-link" href="#local/${encodeURIComponent(p.id)}">
          <span class="row-art">${p.cover_id ? art(p.cover_id) : `<span class="pl-ic">${icon("note")}</span>`}</span>
          <span class="row-text"><span class="row-t">${esc(p.name)}</span><span class="row-s">${esc([songs(p.count), p.unmatched ? `${p.unmatched} to match` : "", p.not_found ? `${p.not_found} not found` : ""].filter(Boolean).join(" · "))}</span></span>
          ${icon("chev-right")}</a></li>`).join("")}</ul>`
        : `<p class="st-note">Import a CSV from Exportify, TuneMyMusic or Google Takeout to make one.</p>`;
    paint(el, `<section class="lib-sec">${libHead("Playlists", list?.length || "", `<button class="chip sm shelf-act" data-st="import">${icon("plus")}Import</button>`)}${body}</section>`);
  }
  function renderLibNavidrome() {
    const el = $("#lib-nd");
    if (!el) return;
    const albums = state.library;
    if (albums === null) { paint(el, `<section class="lib-sec">${libHead("New in Navidrome")}${skCards(3)}</section>`); return; }
    if (!albums.length) {
      // Nothing to show: say so only when Navidrome itself is the problem.
      const nd = state.health?.navidrome;
      paint(el, nd && !nd.ok && nd.configured !== false ? `<section class="lib-sec">${libHead("New in Navidrome")}${errorState(
        { status: 502, message: state.health.navidrome?.error || "" }, { title: "Can't reach Navidrome", fallback: "Open the server sheet (your avatar) to see what's wrong.", retry: "data-retry-library" })}</section>` : "");
      return;
    }
    paint(el, `<section class="lib-sec">${libHead("New in Navidrome")}${albumCards(albums.slice(0, 20))}</section>`);
  }
  function renderLibSub() {
    const el = $("#lib-sub");
    if (!el) return;
    const n = state.history.length, p = state.playlists?.length || 0;
    el.textContent = state.historyLoaded ? [`${songs(n)} kept`, p ? `${p} playlist${p === 1 ? "" : "s"}` : ""].filter(Boolean).join(" · ") : " ";
  }
  function renderKept() {
    const el = $("#lib-songs");
    if (!el) return;
    const n = state.history.length;
    let body;
    if (!state.historyLoaded) {
      body = state.historyErr ? errorState(state.historyErr, { title: "Couldn't load your keeps", retry: "data-retry-history" }) : skRows(6);
    } else if (!n) {
      body = emptyState("disc", "Nothing kept yet", "Search for a song and tap + to keep it in your library.",
        `<a class="pill-btn" href="#search">${icon("search")}Search</a>`);
    } else {
      body = `<div class="actions joined h">
          <button class="pill-btn" data-play-all="history">${icon("play", "fill")}Play</button>
          <button class="pill-btn" data-shuffle-all="history">${icon("shuffle")}Shuffle</button>
        </div>
        <ul class="rows">${state.history.map((t, i) => row(t, i, "history", `${esc(t.channel)} · ${timeAgo(t.kept_at)}`)).join("")}</ul>`;
    }
    paint(el, `<section class="lib-sec">${libHead("Kept songs", n || "")}${body}</section>`);
    renderLibSub();
  }
  function renderLibrary() {
    const el = $("#view-library");
    if (!$("#lib-songs", el)) {
      el.innerHTML = `<h1 class="page-title">Library</h1><p class="page-sub" id="lib-sub"> </p>
        <div id="lib-dl"></div><div id="lib-pl"></div><div id="lib-nd"></div><div id="lib-songs"></div>`;
    }
    renderDownloads();
    renderLibPlaylists();
    renderLibNavidrome();
    renderKept();
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

  // Downloads: on top of the Library while something is being kept, at the bottom once it's all finished.
  function renderDownloads() {
    const el = $("#lib-dl");
    if (!el) return;
    const jobs = state.jobs;
    const active = jobs.some((j) => ACTIVE.includes(j.status));
    const finished = jobs.some((j) => !ACTIVE.includes(j.status));
    el.classList.toggle("active", active);
    const head = libHead("Downloads", active ? jobs.filter((j) => ACTIVE.includes(j.status)).length : "",
      finished ? `<button class="link-btn shelf-act" data-clear-finished>Clear</button>` : "");
    if (!jobs.length) {
      paint(el, `<section class="lib-sec">${head}<p class="st-note">Nothing downloading. Tap + on any song to keep it in your library.</p></section>`);
      return;
    }
    paint(el, `<section class="lib-sec">${head}<ul class="joined">${jobs.map((j) => `
        <li class="row job ${j.status}">
          <span class="row-main" style="cursor:default">
            <span class="row-art">${art(j.video_id)}</span>
            <span class="row-text">
              <span class="row-t">${esc(j.title || j.video_id)}${j.lossless ? ` <span class="lbadge">Lossless</span>` : ""}</span>
              <span class="row-s num"${j.status === "error" && j.error ? ` title="${esc(j.error)}"` : ""}>${esc(jobLabel(j))}</span>
              ${j.status === "error" && j.hint && j.error && j.error !== j.hint ? `<span class="row-e">${esc(j.error)}</span>` : ""}
              ${j.status === "done" && j.warning ? `<span class="row-e">${esc(j.warning)}</span>` : ""}
              ${j.status === "downloading" ? `<span class="bar"><i style="width:${j.percent}%"></i></span>` : ""}
            </span>
          </span>
          ${ACTIVE.includes(j.status)
            ? `<span class="keep pending" aria-label="Working">${icon("plus")}</span>`
            : `<button class="icon-btn sm" data-dismiss="${esc(j.id)}" aria-label="Dismiss">${icon("x")}</button>`}
        </li>`).join("")}</ul></section>`);
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
    local: (id) => `/api/playlists/${encodeURIComponent(id)}`, // a Reef playlist (e.g. an import)
  };

  const pageData = () => detail.cache.get(detail.key)?.data || null;
  function pageTracks() {
    const d = pageData();
    if (!d) return [];
    return (detail.kind === "artist" ? d.songs : ["album", "playlist", "local"].includes(detail.kind) ? d.tracks : null) || [];
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
    if (kind === "local") return d.name || "Playlist";
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
        if (kind === "local") for (const t of data.tracks || []) if (!t.title) t.title = `YouTube video ${t.id}`; // a Takeout row not looked up yet
        noteKept(kind === "artist" ? data.songs : data.tracks);
        if (kind === "local" && (data.import || (data.tracks || []).some(M.isPlaceholder))) loadImportStatus(id);
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
      return `<div class="skel" ${SK_LABEL}><div class="sk sk-title"></div>${grid}</div>`;
    }
    return `<div class="skel" ${SK_LABEL}><div class="hero"><div class="hero-art${kind === "artist" ? " circle" : ""} sk"></div>
        <div class="sk sk-line" style="width:56%"></div><div class="sk sk-line sm" style="width:34%"></div></div>
      <div class="actions joined h"><div class="sk sk-btn"></div><div class="sk sk-btn"></div></div>
      ${'<div class="sk-row"><div class="sk a"></div><div class="l2"><i class="sk"></i><i class="sk"></i></div></div>'.repeat(5)}</div>`;
  }

  // Upstream failures can carry a parser dump; a short message (e.g. "YouTube Music is off") is shown as is,
  // a typed one (bot wall, rate limit, network) as its hint.
  function pageError(e) {
    return errorState(e, { title: "Couldn't load this page", fallback: "YouTube Music didn't answer. Try again in a moment.",
      retry: "data-retry", notFound: "YouTube Music doesn't have this page." });
  }

  // The glyph sits under the cover, so it shows while the cover loads or if it fails.
  function heroArt(src, tracks, round = false) {
    const img = src ? `<img src="${esc(src)}" alt="" data-drop>` : tracks?.length ? art(tracks[0].id, true) : "";
    return `<span class="hero-art${round ? " circle" : ""}">${icon(round ? "person" : "note")}${img}</span>`;
  }
  const pageActions = () => `<div class="actions joined h">
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
    if (!d || !["album", "playlist", "local"].includes(detail.kind)) return;
    const s = keepAllState();
    if (s === "kept") { toast("Already in your library"); return; }
    if (s === "pending") { libFocus = "lib-dl"; location.hash = "#library"; return; }
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
        <div class="actions joined h">
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

  // A Reef playlist (#local/<id>). An import's rows that aren't matched to a song yet are placeholders: they play
  // (looked up on the way, match-core) but can't be kept; the server matches them in the background meanwhile.
  const imp = { status: new Map(), timer: 0, busy: false, file: null, name: "", result: null };
  function importLine(pid) {
    const s = imp.status.get(pid);
    if (!s || !s.total) return "";
    const missing = s.missing?.length || 0;
    const text = s.running
      ? (s.rate_limited ? `YouTube Music asked to slow down · ${s.resolved} of ${s.total} matched` : `Matching songs… ${s.resolved} of ${s.total}`)
      : `${s.resolved} of ${s.total} matched${missing ? ` · ${missing} not found` : ""}${s.error ? ` · ${s.error}` : ""}`;
    const btn = s.running ? "" : s.pending ? `<button class="chip sm" data-import-resolve="${esc(pid)}">${icon("repeat")}Match ${s.pending} more</button>`
      : missing ? `<button class="chip sm" data-import-resolve="${esc(pid)}" data-retry-missing>${icon("repeat")}Try missing again</button>` : "";
    return `<div class="imp-line" role="status"><span>${s.running ? '<i class="spin"></i>' : ""}${esc(text)}</span>${btn}</div>`;
  }
  function localPage(p) {
    const tracks = p.tracks || [];
    const total = tracks.reduce((x, t) => x + (Number(t.duration) || 0), 0);
    const real = tracks.filter((t) => !M.isPlaceholder(t));
    const sub = (t) => (M.isPlaceholder(t) ? `${esc(t.artist || t.channel || "")}${t.artist || t.channel ? " · " : ""}${t.miss ? "Not found" : "Not matched yet"}` : null);
    return `<div class="hero">${heroArt(null, real)}
        <h1 class="hero-t" data-anchor>${esc(p.name || "Playlist")}</h1>
        ${p.description ? `<p class="hero-a plain">${esc(p.description)}</p>` : ""}
        <p class="hero-s">${esc([songs(tracks.length), fmtLen(total)].filter(Boolean).join(" · "))}</p>
      </div>
      <div id="imp-line">${importLine(p.id)}</div>
      ${tracks.length ? `${pageActions()}${keepAllBtn()}
        <ul class="rows d-rows">${tracks.map((t, i) => row(t, i, "page", sub(t))).join("")}</ul>`
        : emptyState("note", "Empty playlist", "Nothing in this playlist yet.")}`;
  }
  // GET /api/import/<id>/status; polled every 2 s while the background pass runs, refreshing the page as songs match.
  async function loadImportStatus(pid) {
    clearTimeout(imp.timer);
    let s;
    try { s = await api(`/api/import/${encodeURIComponent(pid)}/status`); } catch { return; }
    const old = imp.status.get(pid);
    imp.status.set(pid, s);
    const here = state.view === "detail" && detail.kind === "local" && detail.id === pid;
    if (!here) return;
    const line = $("#imp-line");
    if (line) line.innerHTML = importLine(pid);
    if (old && (old.resolved !== s.resolved || old.missing?.length !== s.missing?.length)) loadPage(detail.key, { force: true });
    if (s.running) imp.timer = setTimeout(() => loadImportStatus(pid), 2000);
  }
  async function resolveImport(pid, retryMissing) {
    try {
      const s = await api(`/api/import/${encodeURIComponent(pid)}/resolve`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ retry_missing: !!retryMissing }),
      });
      imp.status.set(pid, s);
    } catch (e) {
      toast(`Couldn't start matching: ${e.message}`);
    }
    loadImportStatus(pid);
  }

  const PAGES = { album: albumPage, artist: artistPage, playlist: playlistPage, mood: moodPage, moods: moodsPage, local: localPage };

  function renderDetail() {
    const el = $("#view-detail");
    const entry = detail.cache.get(detail.key);
    const err = detail.errors.get(detail.key);
    $("#top-title").textContent = pageTitle();
    if (entry) {
      paint(el, PAGES[detail.kind](entry.data));
      if (detail.restore != null) { window.scrollTo(0, detail.restore); detail.restore = null; }
    } else {
      paint(el, err ? pageError(err) : pageSkeleton(detail.kind));
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

  async function hideArtist(a, { undo = false } = {}) {
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
    toast(`Hidden “${a.name}”`, undo ? { action: "Undo", onAction: () => unhideArtists([a]) } : {});
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
      ? skRows(2)
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

  const RENDER = { home: renderHome, search: renderSearch, library: renderLibrary, detail: renderDetail };

  function renderBadges() {
    const n = state.jobs.filter((j) => ACTIVE.includes(j.status)).length;
    for (const el of [$("#tab-badge")]) {
      el.hidden = !n;
      el.textContent = n;
    }
  }

  // ---------- routing ----------
  // Main views #home #search #library (#kept / #downloads redirect there); browse pages (#album/<id>, #artist/<id>, #playlist/<id>,
  // #mood/<params>, #moods) show in #view-detail on top of the main view they were opened from, whose tab
  // and bottom bar stay. Every history entry carries {reef: depth, parent}: depth > 0 means Back has an
  // in-app page to return to, and each depth's scroll position comes back on back / forward (iOS edge swipe).
  const DETAIL_RE = /^(album|artist|playlist|mood|local)\/(.+)$/;
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

  let libFocus = ""; // "#downloads" landed on the Library: scroll to its Downloads once painted
  function route() {
    const old = OLD_VIEWS[location.hash.slice(1)];
    if (old) {
      libFocus = location.hash === "#downloads" ? "lib-dl" : "";
      navReplace = true;
      location.replace(`#${old}`);
      return;
    }
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
      const box = $("#view-detail");
      if (changed) box.dataset.anim = "1";
      renderDetail();
      box.dataset.anim = "";
      loadPage(r.key);
    } else {
      if (r.view === "home" && homeDue()) loadHome();
      if (r.view === "library") {
        if (state.library === null) loadLibrary();
        loadPlaylists();
      }
      const box = $(`#view-${r.view}`);
      if (changed) box.dataset.anim = "1"; // the first paint after opening a view animates its lists in
      RENDER[r.view]();
      box.dataset.anim = "";
      if (changed) window.scrollTo(0, y);
      const focus = r.view === "library" && libFocus && $(`#${libFocus}`);
      libFocus = "";
      if (focus && focus.offsetHeight) window.scrollTo(0, focus.getBoundingClientRect().top + window.scrollY - $("#top").offsetHeight - 8);
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
    try {
      state.history = await api("/api/history");
      state.historyLoaded = true;
      state.historyErr = null;
    } catch (e) {
      if (!state.historyLoaded) state.historyErr = e; // else keep the previous list
    }
    for (const h of state.history) if (h && h.id && h.nd_id) ndIds.set(h.id, h.nd_id);
    // Search and browse pages only need their + buttons updated (a re-render would reset their scrollers).
    if (state.view !== "search" && state.view !== "detail") RENDER[state.view]();
    refreshKeepButtons();
    updatePlayer();
  }

  async function loadLibrary() {
    try { state.library = await api("/api/library/recent"); } catch { state.library = []; }
    if (!Array.isArray(state.library)) state.library = [];
    if (state.view === "library") renderLibNavidrome();
  }

  let homeAt = 0, homeTries = 0, homeTimer;
  async function loadHome({ again = false } = {}) {
    clearTimeout(homeTimer);
    if (!again) homeTries = 0;
    homeAt = Date.now();
    let body;
    try {
      body = await api("/api/home");
    } catch (e) {
      homeAt = 0;
      if (!state.home) { state.homeErr = e; if (state.view === "home") renderHome(); }
      return;
    }
    state.homeErr = null;
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
    // Undo takes the mark back (and the artist, if this one hid them).
    const undo = async () => {
      try { await api(`/api/dislikes/${encodeURIComponent(t.id)}`, { method: "DELETE" }); } catch (e) { if (e.status !== 404) { toast(`Couldn't undo: ${e.message}`); return; } }
      if (r?.blocked) unhideArtists([r.blocked]); else { loadHome(); toast("Undone"); }
    };
    if (r?.blocked) {
      toast(`Hidden “${r.blocked.name}”: that's two of their songs you weren't into`, { action: "Undo", onAction: undo });
      blockedChanged(r.blocked);
    } else {
      toast("Got it. You won't see this song for a while", { action: "Undo", onAction: undo });
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
    const ok = !!state.health?.navidrome?.ok;
    dot.className = "dot " + (ok ? "ok" : "bad");
    $("#status-btn").setAttribute("aria-label", `Server and settings: ${state.health ? (ok ? "all good" : "Navidrome unreachable") : "Reef unreachable"}`);
    if (cur()) updatePlayer();
    if (state.view === "library" && state.library && !state.library.length) renderLibNavidrome();
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
        if (state.library !== null) loadLibrary(); // the Library's "New in Navidrome" picks up the keep
      }
      if (failed.length) toast(`Couldn't keep “${failed[0].title}”`);
      refreshKeepButtons();
      renderBadges();
      if (state.view === "library") renderDownloads();
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
    state.searchError = null;
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
      state.searchError = e;
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
    state.fromRoute = fromRoute(listName);
    prefetch([cur() && playId(cur())]);
    load();
  }

  // "Now playing from …": a pasted playlist and a browse page show their own name.
  const fromLabel = (listName) => (listName === "page" ? pageTitle()
    : listName === "results" && state.link?.kind === "playlist" ? state.link.title || "Playlist" : FROM[listName] || "");
  // "Playing from search" / "Playing from Discovery" ("" when the queue has no source).
  const playingFrom = () => (state.from ? `Playing from ${state.from.replace(/^From /, "")}` : "");
  // The page a list lives on, for "Playing from …" in Now Playing and Up next (a tap goes back there).
  const HOME_LISTS = ["quick", "on_repeat", "jump_back", "liked", "recent"];
  const fromRoute = (listName) => (listName === "page" ? (state.view === "detail" ? location.hash : "")
    : listName === "results" ? "#search" : listName === "history" ? "#library" : HOME_LISTS.includes(listName) ? "#home" : "");

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
        const mkey = r.key || t.key;
        const track = { ...slim(r.track), ...(mkey ? { mkey } : {}) };
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
    state.fromRoute = "";
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

  function addToQueue(t, listName, mode, { quiet = false } = {}) {
    if (!t) return;
    t = slim(t);
    if (!cur()) {
      // Nothing loaded: either action just starts playing it.
      state.queue = [t]; state.index = 0; state.original = state.shuffle ? [t] : null;
      queueGen++;
      state.from = fromLabel(listName);
      state.fromRoute = fromRoute(listName);
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
    if (!quiet) toast(mode === "next" ? `Playing next: “${t.title}”` : `Added to queue: “${t.title}”`);
  }

  function jumpTo(i) {
    if (!(i >= 0 && i < state.queue.length) || i === state.index) return;
    state.index = i;
    load();
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

  // ---------- cover palette (§1.1) ----------
  // The mini player and Now Playing (.cover-tint) take their colours from the artwork: a 64 px copy of /api/art
  // (same origin, so the canvas can be read) through palette-core, cached per track id in memory and (bounded)
  // in localStorage. The three roles live on :root, registered with @property, so they glide over 800 ms.
  // UI pref (localStorage "reef.theme"): "cover" (Cover colours), "accent" (Accent only), "black" (Pure black).
  const THEMES = { cover: "Cover colours", accent: "Accent only", black: "Pure black" };
  const PALETTE_SIZE = 64, PALETTE_KEEP = 150;
  const pal = { theme: THEMES[store.get("theme")] ? store.get("theme") : "cover", mem: new Map(), loading: new Map(), seq: 0 };
  function paletteFor(id) {
    if (pal.mem.has(id)) return Promise.resolve(pal.mem.get(id));
    const saved = store.get("palettes", {})?.[id];
    if (saved && P.vars(saved)["--c-base"] === saved.base) { pal.mem.set(id, saved); return Promise.resolve(saved); }
    if (pal.loading.has(id)) return pal.loading.get(id);
    const p = new Promise((resolve) => {
      const img = new Image();
      img.decoding = "async";
      img.onload = () => {
        let out = null;
        try {
          const c = document.createElement("canvas");
          c.width = c.height = PALETTE_SIZE;
          const ctx = c.getContext("2d", { willReadFrequently: true });
          ctx.drawImage(img, 0, 0, PALETTE_SIZE, PALETTE_SIZE);
          out = P.derive(ctx.getImageData(0, 0, PALETTE_SIZE, PALETTE_SIZE).data);
        } catch { out = null; } // a tainted canvas (a cross-origin redirect) or no 2D context
        resolve(out);
      };
      img.onerror = () => resolve(null);
      img.src = artUrl(id, PALETTE_SIZE);
    }).then((out) => {
      pal.loading.delete(id);
      if (out) {
        pal.mem.set(id, out);
        store.set("palettes", P.remember(store.get("palettes", {}), id, out, PALETTE_KEEP));
      }
      return out;
    });
    pal.loading.set(id, p);
    return p;
  }
  function setPaletteVars(p) {
    const root = document.documentElement.style;
    for (const [k, v] of Object.entries(P.vars(p))) root.setProperty(k, v);
  }
  function applyPalette(t = cur()) {
    document.body.dataset.theme = pal.theme;
    const seq = ++pal.seq;
    if (pal.theme !== "cover" || !t || M.isPlaceholder(t)) { setPaletteVars(pal.theme === "black" ? P.BLACK : P.DEFAULT); return; }
    // Until the new cover is read, the previous colours stay (then glide over); unreadable art -> the defaults.
    paletteFor(t.id).then((p) => { if (seq === pal.seq) setPaletteVars(p || P.DEFAULT); });
  }
  function setTheme(theme) {
    if (!THEMES[theme] || theme === pal.theme) return;
    pal.theme = theme;
    store.set("theme", theme);
    applyPalette();
    applyBg();
    if (!$("#status-sheet").hidden) renderStatus();
    if (!$("#bg-sheet").hidden) renderBgPicker();
  }
  const themePicker = () => `<li class="st-theme"><span class="k">Player colours</span>
      <div class="seg" role="radiogroup" aria-label="Player colours">${Object.entries(THEMES).map(([k, label]) =>
        `<button role="radio" aria-checked="${k === pal.theme}" class="${k === pal.theme ? "on" : ""}" data-set-theme="${k}">${label}</button>`).join("")}</div>
      <span class="hint">Colours the mini player and Now Playing. Saved on this device.</span></li>`;

  // Re-run an element's cross-fade (titles and art on track change, §1.4 "cross").
  function crossFade(...els) {
    for (const el of els) {
      if (!el) continue;
      el.classList.remove("xfade");
      void el.offsetWidth;
      el.classList.add("xfade");
    }
  }

  let shownId = null; // the track the player last painted (cross-fade + palette only when it changes)
  let miniGone = false; // the mini player was pulled down (§3.2): hidden until something plays again
  function updatePlayer() {
    const t = cur();
    const mini = $("#mini");
    if (!t) { mini.hidden = true; document.body.classList.remove("has-mini"); shownId = null; applyPalette(null); paintUpNext(); return; }
    mini.hidden = miniGone;
    document.body.classList.toggle("has-mini", !miniGone);
    const changed = shownId !== t.id;
    shownId = t.id;
    $("#mini-art").src = artUrl(t.id, artSize(t.id, "mini"));
    $("#mini-s").textContent = t.channel;

    const artImg = $("#np-art");
    if (artImg.dataset.id !== t.id) {
      artImg.dataset.id = t.id;
      artImg.dataset.fallback = artFallback(t.id);
      $("#np-art-box").classList.remove("wide");
      artImg.src = artUrl(t.id, artSize(t.id, "big"));
      $("#np-art-fill").src = artUrl(t.id, artSize(t.id, "bg"));
      setNpBg(t);
    }
    if (changed) {
      crossFade($("#mini-art"), $(".mini-text"), artImg, $(".np-titles"));
      applyPalette(t);
      setMarquee($("#mini-t"), t.title, MQ_MINI);
      setMarquee($("#np-t"), t.title, MQ_NP);
    }
    $("#np-s").textContent = t.channel;
    $("#np-s").disabled = !linkable(t).length; // tapping the artist opens their page
    $("#np-from").textContent = playingFrom();
    $("#np-from-btn").disabled = !state.fromRoute;
    const codec = codecLabel(t);
    const testAudio = !!state.health?.test_audio && srcKey(t.id) === "yt"; // REEF_TEST_AUDIO: a local file, not YouTube
    $("#np-src").textContent = testAudio ? "Test audio" : srcInfo(srcKey(t.id)).name + (codec ? ` · ${codec}` : "");
    $("#np-src").classList.toggle("test", testAudio);
    // Quality badge on the art (§4.2): only when it says something (a lossless source, or a codec the server named).
    const q = !testAudio && (t.codec || (srcKey(t.id) === "mc" ? codec : "")) ? codec : "";
    $("#np-qbadge").textContent = q;
    $("#np-qbadge").hidden = !q;
    $("#np-keep").dataset.keep = t.id;
    const keepable = canKeep(t.id) || keepState(t.id) === "kept";
    $("#np-keep").hidden = !keepable;
    paintLike();
    refreshKeepButtons();
    updateModes();
    paintUpNext();
  }

  function updatePlayState() {
    const playing = !audio.paused && !audio.ended;
    document.body.classList.toggle("is-playing", playing);
    for (const id of ["#mini-play", "#np-play", "#lyv-play"]) {
      const b = $(id);
      b.querySelector("use").setAttribute("href", playing ? "#i-pause" : "#i-play");
      b.setAttribute("aria-label", playing ? "Pause" : "Play");
    }
    if ("mediaSession" in navigator) navigator.mediaSession.playbackState = playing ? "playing" : "paused";
  }

  function updateModes() {
    const sh = $("#np-shuffle");
    sh.classList.toggle("on", state.shuffle);
    sh.setAttribute("aria-pressed", String(state.shuffle));
    const r = $("#np-repeat");
    r.classList.toggle("on", state.repeat !== "off");
    r.classList.toggle("one", state.repeat === "one");
    r.setAttribute("aria-pressed", String(state.repeat !== "off"));
    r.setAttribute("aria-label", { off: "Repeat", all: "Repeat all", one: "Repeat one" }[state.repeat] || "Repeat");
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

  let seeking = false; // the seek bar is being dragged: timeupdate leaves it alone
  function duration() {
    return isFinite(audio.duration) && audio.duration > 0 ? audio.duration : (cur()?.duration || 0);
  }
  const RING_C = 2 * Math.PI * 24.75; // the mini player's progress ring (r = 24.75 in a 52 px box, §3.1)
  const ringEl = $("#mini-ring"), seekEl = $("#np-seek"), curEl = $("#np-cur"), remEl = $("#np-rem");
  function setProgress(t, d) {
    const f = d ? Math.min(1, Math.max(0, t / d)) : 0;
    ringEl.style.strokeDashoffset = PC.ringOffset(f, RING_C);
    if (!seeking) paintSeek(f, t, d);
  }
  function paintSeek(f, t, d) {
    seekEl.style.setProperty("--p", f);
    curEl.textContent = fmt(t);
    remEl.textContent = "-" + fmt(Math.max(0, d - t));
    seekEl.setAttribute("aria-valuemax", String(Math.round(d)));
    seekEl.setAttribute("aria-valuenow", String(Math.round(t)));
    seekEl.setAttribute("aria-valuetext", `${fmt(t)} of ${fmt(d)}`);
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
      lis = { track: slim(t), startedAt: Date.now() / 1000, played: 0, lastT: null, lastWall: null, via: "", sent: null, beat: 0 };
      personalPing("play", { id: t.id });
    }
    const now = Date.now();
    lis.played += L.playedDelta(lis.lastT, lis.lastWall, audio.currentTime, now, audio.playbackRate);
    lis.lastT = audio.currentTime;
    lis.lastWall = now;
    if (viaLibrary()) lis.via = "nd";
    // Personalisation heartbeat: every 10 s of actual playing (the server throttles too).
    if (lis.played - lis.beat >= 10) { lis.beat = lis.played; personalPing("progress", { id: lis.track.id, t: Math.floor(audio.currentTime) }); }
  }
  // Optional anonymous YouTube Music personalisation (Settings, off by default): play start + progress go to Reef,
  // which reports them to YouTube Music in the background. Nothing is sent while the setting is off, nor for
  // tracks that aren't YouTube's (the server checks both again).
  function personalPing(kind, body) {
    if (state.settings.yt_personalize !== true || srcKey(body.id) !== "yt" || M.isPlaceholder(cur())) return;
    try {
      fetch(`/api/personal/${kind}`, { method: "POST", keepalive: true, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }).catch(() => {});
    } catch { /* best effort */ }
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
      queue: state.queue.map(qslim), index: state.index, from: state.from, fromRoute: state.fromRoute,
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
    state.fromRoute = typeof s.fromRoute === "string" && /^#[^\s]{1,300}$/.test(s.fromRoute) ? s.fromRoute : "";
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

  // ---------- sleep timer (§8.5) ----------
  // A wall-clock deadline checked every few seconds and on timeupdate/visibilitychange: Safari throttles
  // (or freezes) timers in the background, so one long setTimeout could fire far too late.
  // The sheet: 15/30/45/60 min chips, End of this song, Custom (hour / minute steppers); while a timer runs it shows the
  // time left as HH:MM:SS boxes with +5 min and Stop. The Now Playing toggle shows the minutes left and a ring of the
  // share that's left (SH.sleepFrac).
  const sleep = { mode: "off", minutes: 0, total: 0, deadline: 0, timer: 0, fade: 0, fadeFrom: 1, choosing: false, tick: 0,
    custom: SH.stepCustom(store.get("sleepCustom", { h: 0, m: 20 }), "h", 0) };
  const sleepLeft = () => Math.max(1, Math.ceil((sleep.deadline - Date.now()) / 60000));
  const SLEEP_RING_C = 2 * Math.PI * 18.5;

  function setSleep(choice, { quiet = false } = {}) {
    clearInterval(sleep.timer);
    sleep.timer = 0;
    sleep.choosing = false;
    stopFade();
    if (choice === "end") {
      Object.assign(sleep, { mode: "end", minutes: 0, total: 0, deadline: 0 });
      if (!quiet) toast("Pausing after this song");
    } else if (Number(choice) > 0) {
      const m = Math.min(SH.SLEEP_MAX, Math.round(Number(choice)));
      Object.assign(sleep, { mode: "time", minutes: m, total: m, deadline: Date.now() + m * 60000 });
      sleep.timer = setInterval(checkSleep, 5000);
      if (!quiet) toast(`Sleep timer: ${SH.durLabel(m)}`);
    } else {
      Object.assign(sleep, { mode: "off", minutes: 0, total: 0, deadline: 0 });
      if (!quiet) toast("Sleep timer off");
    }
    updateModes();
    renderSleepBadge();
  }
  function extendSleep(add = 5) {
    if (sleep.mode !== "time") return;
    const now = Date.now(), r = SH.extend(sleep.deadline, sleep.total, now, add);
    sleep.deadline = r.deadline;
    sleep.total = r.total;
    toast(`Sleep timer: ${SH.durLabel(Math.ceil((r.deadline - now) / 60000))} left`);
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
    b.setAttribute("aria-pressed", String(sleep.mode !== "off"));
    const txt = sleep.mode === "time" ? `${sleepLeft()}m` : ""; // the remaining minutes replace the moon (§4.6)
    if (badge.textContent !== txt) badge.textContent = txt;
    badge.hidden = !txt;
    b.classList.toggle("has-num", !!txt);
    // the ring: the share of the timer that's left
    const ring = $(".tg-ring circle", b), f = sleep.mode === "time" ? SH.sleepFrac(sleep.deadline - Date.now(), sleep.total) : 0;
    ring.style.strokeDasharray = String(SLEEP_RING_C);
    ring.style.strokeDashoffset = String(PC.ringOffset(f, SLEEP_RING_C));
    b.setAttribute("aria-label", sleep.mode === "time" ? `Sleep timer: ${sleepLeft()} min left`
      : sleep.mode === "end" ? "Sleep timer: end of this song" : "Sleep timer");
    if (!$("#sleep-sheet").hidden) tickSleep();
  }

  // ms left: the timer, or what's left of the song for "End of this song".
  const sleepMsLeft = () => (sleep.mode === "time" ? sleep.deadline - Date.now() : Math.max(0, (duration() - playPos()) * 1000));
  const clockAt = (ts) => new Date(ts).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  function renderSleep() {
    const el = $("#sleep-body");
    if (sleep.mode !== "off" && !sleep.choosing) {
      const c = SH.clockParts(sleepMsLeft());
      el.innerHTML = `<p class="sl-cap" id="sl-cap">${sleep.mode === "time" ? `Pausing at ${esc(clockAt(sleep.deadline))}` : "Pausing at the end of this song"}</p>
        <div class="sl-clock num" role="timer" aria-live="off" aria-label="Time left">
          <span><b id="sl-h">${c.h}</b><small>hours</small></span><i>:</i><span><b id="sl-m">${c.m}</b><small>min</small></span><i>:</i><span><b id="sl-s">${c.s}</b><small>sec</small></span></div>
        ${sleep.mode === "time" ? `<div class="sl-bar" aria-hidden="true"><i id="sl-bar"></i></div>` : ""}
        <div class="sl-acts">${sleep.mode === "time" ? `<button class="pill-btn sl-plus" data-sleep-act="plus5">${icon("plus")}5 min</button>` : ""}
          <button class="pill-btn sl-stop" data-sleep="off">Stop</button></div>
        <button class="link-btn sl-change" data-sleep-act="change">Change the timer</button>`;
      tickSleep();
      return;
    }
    const cu = sleep.custom, mins = SH.customMinutes(cu);
    el.innerHTML = `<div class="sl-chips">${SH.SLEEP_PRESETS.map((m) => {
        const on = sleep.mode === "time" && sleep.total === m;
        return `<button class="sl-chip${on ? " on" : ""}" data-sleep="${m}" aria-pressed="${on}"><b>${m}</b><small>min</small></button>`;
      }).join("")}</div>
      <button class="sl-row${sleep.mode === "end" ? " on" : ""}" data-sleep="end" aria-pressed="${sleep.mode === "end"}">${icon("note")}<span>End of this song</span>${sleep.mode === "end" ? icon("check") : ""}</button>
      <div class="sl-custom"><span class="sl-l">Custom</span>
        <div class="sl-step" role="group" aria-label="Hours"><button data-sleep-step="h:-1" aria-label="One hour less">${icon("minus")}</button>
          <span class="num"><b>${cu.h}</b><small>h</small></span><button data-sleep-step="h:1" aria-label="One hour more">${icon("plus")}</button></div>
        <div class="sl-step" role="group" aria-label="Minutes"><button data-sleep-step="m:-1" aria-label="Five minutes less">${icon("minus")}</button>
          <span class="num"><b>${String(cu.m).padStart(2, "0")}</b><small>min</small></span><button data-sleep-step="m:1" aria-label="Five minutes more">${icon("plus")}</button></div>
      </div>
      <button class="btn-wide" data-sleep-act="start"${mins ? "" : " disabled"}>${mins ? `Start · ${SH.durLabel(mins)}` : "Pick a time"}</button>
      ${sleep.mode !== "off" ? `<button class="btn-wide alt" data-sleep="off">Turn off</button>` : ""}`;
  }
  // Once a second while the sheet is open: only the numbers change (re-rendering would eat a tap mid-press).
  function tickSleep() {
    const h = $("#sl-h");
    if (!h) return;
    const c = SH.clockParts(sleepMsLeft());
    if (h.textContent !== c.h) h.textContent = c.h;
    $("#sl-m").textContent = c.m;
    $("#sl-s").textContent = c.s;
    const bar = $("#sl-bar");
    if (bar) bar.style.width = `${(SH.sleepFrac(sleep.deadline - Date.now(), sleep.total) * 100).toFixed(2)}%`;
    if (sleep.mode === "time") $("#sl-cap").textContent = `Pausing at ${clockAt(sleep.deadline)}`;
  }
  function openSleep() {
    sleep.choosing = false;
    renderSleep();
    openSheet("#sleep-sheet");
    clearInterval(sleep.tick);
    sleep.tick = setInterval(() => {
      if ($("#sleep-sheet").hidden) { clearInterval(sleep.tick); sleep.tick = 0; return; }
      if (sleep.mode === "off" && $("#sl-h")) { renderSleep(); return; } // it ran out while open
      tickSleep();
    }, 1000);
  }
  $("#sleep-body").addEventListener("click", (e) => {
    const step = e.target.closest("[data-sleep-step]"), act = e.target.closest("[data-sleep-act]");
    if (step) {
      const [part, d] = step.dataset.sleepStep.split(":");
      sleep.custom = SH.stepCustom(sleep.custom, part, Number(d));
      store.set("sleepCustom", sleep.custom);
      renderSleep();
      return;
    }
    if (!act || act.disabled) return;
    const a = act.dataset.sleepAct;
    if (a === "start") { setSleep(String(SH.customMinutes(sleep.custom))); closeSheet("#sleep-sheet"); }
    else if (a === "plus5") extendSleep(5);
    else if (a === "change") { sleep.choosing = true; renderSleep(); }
  });

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
      if (lyr.times.length && lyrVisible()) { syncLyrics(); kickLyrics(); }
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
  audio.addEventListener("play", () => {
    rec.want = true;
    updatePlayState();
    kickLyrics();
    if (miniGone) { miniGone = false; updatePlayer(); } // pulled away earlier: playing brings it back
  });
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
    if (document.visibilityState === "visible" && lyrVisible() && lyr.times.length) { syncLyrics(true); kickLyrics(); }
    if (document.visibilityState === "visible" && state.view === "home" && homeDue()) loadHome();
    if (document.visibilityState === "hidden" && cur()) saveSession();
    // iOS may kill a hidden Home Screen app without a pagehide: report the play so far (updated later if it goes on).
    if (document.visibilityState === "hidden" && lis) reportListen(false);
  });
  window.addEventListener("pagehide", () => {
    if (cur()) saveSession();
    if (lis) reportListen(false);
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
  const npEl = $("#np");
  function openNP() {
    if (!cur()) return;
    npEl.classList.add("open");
    npEl.setAttribute("aria-hidden", "false");
    syncLock();
    paintUpNext(); // the neighbours' art loads only while the player is open
    fitMarquee($("#np-t"));
    if (lyr.on) showLyrics();
  }
  function closeNP() {
    closeQ(true);
    npEl.classList.remove("open", "dragging");
    npEl.style.transform = "";
    npEl.setAttribute("aria-hidden", "true");
    syncLock();
  }
  // Page scroll stays locked while the player or any sheet is up.
  const syncLock = () => document.documentElement.classList.toggle("lock", npOpen() || $$(".sheet-wrap.open").length > 0);

  // ---------- gestures (§0: pointer events, touch-action none on the surface, the 24 px left edge left to Safari) ----------
  const reducedMotion = () => !!window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
  // A drag swallows the click the browser may still send after it (on `el`, or on an ancestor when the pointer was
  // released elsewhere; without `el`, any click in the next 400 ms).
  function swallowClick(el = null) {
    const stop = (e) => {
      if (el && !(el.contains(e.target) || e.target.contains?.(el))) return;
      e.stopPropagation();
      e.preventDefault();
    };
    window.addEventListener("click", stop, { capture: true, once: true });
    setTimeout(() => window.removeEventListener("click", stop, { capture: true }), 400);
  }
  // Pointer drags on `el`. opts: x / y (false = that axis isn't this surface's), ignore(e) (pointerdowns to leave alone),
  // onStart(g) once the axis is known, onMove(g), onEnd(g, cancelled), onTap(e, g) for a press that never moved.
  // g = {x0, y0, dx, dy, vx, vy, axis: "x" | "y", target}.
  function drag(el, opts) {
    let g = null;
    const samples = [];
    // Once pressed, the pointer is followed on the window: it may leave `el` (implicit capture holds for touch on iOS,
    // but not for every mouse or pen), and the surface may move under it.
    const move = (e) => {
      if (!g || e.pointerId !== g.id) return;
      g.dx = e.clientX - g.x0;
      g.dy = e.clientY - g.y0;
      samples.push({ t: e.timeStamp, x: e.clientX, y: e.clientY });
      if (samples.length > 24) samples.shift();
      if (g.axis == null) {
        const axis = PC.axisOf(g.dx, g.dy, { x0: g.x0, allowX: opts.x !== false, allowY: opts.y !== false });
        if (!axis) return;
        g.axis = axis;
        if (axis === "none") return;
        try { el.setPointerCapture(e.pointerId); } catch { /* followed on the window anyway */ }
        opts.onStart?.(g);
      }
      if (g.axis !== "none") opts.onMove?.(g);
    };
    const end = (e) => {
      if (!g || e.pointerId !== g.id) return;
      const done = g, cancelled = e.type === "pointercancel";
      g = null;
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", end);
      window.removeEventListener("pointercancel", end);
      // the release point counts: a finger that stopped before lifting has no velocity left
      if (!cancelled) samples.push({ t: e.timeStamp, x: e.clientX, y: e.clientY });
      Object.assign(done, PC.velocity(samples));
      if (done.axis && done.axis !== "none") { swallowClick(el); opts.onEnd?.(done, cancelled); }
      else if (!done.axis && !cancelled) opts.onTap?.(e, done);
    };
    el.addEventListener("pointerdown", (e) => {
      if (g || (e.pointerType === "mouse" && e.button !== 0) || opts.ignore?.(e)) return;
      g = { id: e.pointerId, x0: e.clientX, y0: e.clientY, dx: 0, dy: 0, vx: 0, vy: 0, axis: null, target: e.target };
      samples.length = 0;
      samples.push({ t: e.timeStamp, x: e.clientX, y: e.clientY });
      window.addEventListener("pointermove", move);
      window.addEventListener("pointerup", end);
      window.addEventListener("pointercancel", end);
    });
  }
  // Long press (550 ms) -> fn, run inside the pointerup so it still counts as the user's gesture (clipboard).
  function longPress(el, fn, ms = 550) {
    let timer = 0, fired = false, x0 = 0, y0 = 0;
    const clear = () => { clearTimeout(timer); el.classList.remove("held"); };
    el.addEventListener("pointerdown", (e) => {
      fired = false; x0 = e.clientX; y0 = e.clientY;
      clearTimeout(timer);
      timer = setTimeout(() => { fired = true; el.classList.add("held"); }, ms);
    });
    el.addEventListener("pointermove", (e) => { if (Math.hypot(e.clientX - x0, e.clientY - y0) > 10) clearTimeout(timer); });
    el.addEventListener("pointerup", (e) => { clear(); if (fired) { fired = false; swallowClick(); fn(e); } });
    el.addEventListener("pointercancel", () => { clear(); fired = false; });
    el.addEventListener("contextmenu", (e) => e.preventDefault());
  }
  // Moves that fire after a short animation. A timer set inside a tap still counts as that tap on WebKit (<= 1 s).
  const SKIP_DELAY = 180; // §4.5: the squish shows before the track changes
  // What's playing now, for PC.sameTrack: a move asked for at a tap / swipe runs later only if this hasn't changed.
  const playSnap = () => ({ token: rec.token, id: cur()?.id ?? null });
  // Run `fn` after `ms` (now with reduced motion or ms = 0), unless the track changed meanwhile.
  function later(ms, fn) {
    const snap = playSnap();
    const run = () => { if (PC.sameTrack(snap, playSnap())) fn(); };
    if (ms > 0) setTimeout(run, ms); else run();
  }
  // Go to the neighbour a swipe picked: next() for the next one, else the previous track by its id (indexes go stale).
  function goNeighbour(dir, targetId) {
    if (dir < 0) { next(); return true; }
    const i = PC.indexOfId(state.queue, targetId);
    if (i < 0 || i === state.index) return false;
    state.index = i;
    load();
    return true;
  }

  // Now Playing pulled down by its top bar (or the art, vertically): it follows the finger, then closes or settles.
  const npPull = {
    move(dy) { npEl.classList.add("dragging"); npEl.style.transform = `translate3d(0, ${Math.max(0, dy)}px, 0)`; },
    end(g, cancelled) {
      npEl.classList.remove("dragging");
      if (!cancelled && PC.npClose(g.dy, g.vy)) closeNP(); else npEl.style.transform = "";
    },
  };
  drag($("#np-grab"), { x: false, onMove: (g) => npPull.move(g.dy), onEnd: npPull.end });

  // ---------- Now Playing art (§4.3): swipe to skip, double-tap seek ----------
  // The art sits between its neighbours (±width); a swipe past 30% (or a flick) slides to one and plays it, else it
  // springs back. With no neighbour that way it moves at 30% of the finger. Immersive moves its full-bleed art instead.
  (function artGestures() {
    const wrap = $("#np-art-wrap"), track = $("#np-art-track"), bubble = $("#np-tapseek");
    let tap = null, busy = false, lyricsTimer = 0;
    const setX = (x, anim = "none") => {
      track.style.transition = anim;
      track.style.transform = x ? `translate3d(${x}px, 0, 0)` : "";
      npEl.style.setProperty("--imm-t", anim);
      npEl.style.setProperty("--imm-x", `${x}px`);
    };
    drag(wrap, {
      onStart() { clearTimeout(lyricsTimer); }, // a tap then a swipe: the swipe wins
      onMove(g) {
        if (g.axis === "y") { npPull.move(g.dy); return; }
        if (busy) return;
        const nb = PC.neighbours(state.queue.length, state.index, state.repeat);
        setX((g.dx < 0 ? nb.next : nb.prev) >= 0 ? g.dx : PC.resist(g.dx));
      },
      onEnd(g, cancelled) {
        if (g.axis === "y") { npPull.end(g, cancelled); return; }
        if (busy) return;
        const w = wrap.clientWidth;
        const nb = PC.neighbours(state.queue.length, state.index, state.repeat);
        const dir = cancelled ? 0 : PC.swipeCommit(g.dx, g.vx, w, { frac: 0.3, vmin: 800 });
        const target = dir < 0 ? nb.next : dir > 0 ? nb.prev : -1;
        if (target < 0) { setX(0, reducedMotion() ? "none" : "transform 340ms var(--ease-spring)"); return; }
        busy = true;
        const out = reducedMotion() ? 0 : 200;
        const snap = playSnap(), targetId = state.queue[target]?.id;
        setX(dir * w, out ? `transform ${out}ms var(--ease-out)` : "none");
        setTimeout(() => {
          // The track changed during the slide (it ended, another skip, recovery): drop the swipe, spring back.
          if (!PC.sameTrack(snap, playSnap()) || !goNeighbour(dir, targetId)) {
            setX(0, reducedMotion() ? "none" : "transform 340ms var(--ease-spring)");
            busy = false;
            return;
          }
          // The new art is the neighbour that just slid in (already loaded): drop the offset once it can paint.
          let done = false;
          const reset = () => { if (done) return; done = true; setX(0); busy = false; };
          const img = $("#np-art");
          if (img.decode) img.decode().then(reset, reset);
          setTimeout(reset, 250);
        }, out);
      },
      onTap(e) {
        if (busy || !cur()) return;
        const r = wrap.getBoundingClientRect();
        const side = e.clientX < r.left + r.width / 2 ? -1 : 1;
        const was = tap;
        tap = PC.tapSeek(tap, performance.now(), side);
        clearTimeout(lyricsTimer);
        // A lone tap opens the lyrics (§5), once it's clear no second tap (a double-tap seek) follows.
        if (!tap.seek && !(was && was.total > 0 && tap.at - was.at <= 1000)) {
          lyricsTimer = setTimeout(() => { if (npOpen() && !qs.open && cur()) setLyricsOn(true); }, 380);
        }
        if (!tap.seek) return;
        const d = duration();
        seekTo(PC.clamp(playPos() + tap.seek, 0, d ? Math.max(0, d - 0.5) : Infinity));
        bubble.textContent = `${side > 0 ? "+" : "−"}${tap.total}s`;
        bubble.dataset.side = side > 0 ? "r" : "l";
        bubble.classList.remove("show");
        void bubble.offsetWidth;
        bubble.classList.add("show");
      },
    });
    // Non-square YouTube art: fitted whole over a blurred copy of itself (§4.2 blur-fill).
    $("#np-art").addEventListener("load", (e) => {
      const img = e.target, w = img.naturalWidth, h = img.naturalHeight;
      $("#np-art-box").classList.toggle("wide", h > 0 && Math.abs(w / h - 1) > 0.05);
    });
  })();

  // ---------- neighbours + Up next peek ----------
  // "Up next · <title>" on the peek strip, the art either side of the current one (only while the player is open:
  // a closed player fetches nothing), and the mini player's peek layer when it's swiped.
  function paintUpNext() {
    const nb = PC.neighbours(state.queue.length, state.index, state.repeat);
    const nx = cur() ? state.queue[nb.next] : null;
    $("#qs-peek-t").innerHTML = nx ? `Up next · <b>${esc(nx.title)}</b>`
      : autoplay.inflight && autoplay.on ? "Up next · finding similar songs…" : "Nothing up next";
    $("#qs-peek").setAttribute("aria-label", nx ? `Up next: ${nx.title}. Show the queue` : "Show the queue");
    if (!npOpen()) return;
    for (const [slot, i] of [["prev", nb.prev], ["next", nb.next]]) {
      const img = $(`#np-art-track img[data-slot="${slot}"]`);
      const t = cur() ? state.queue[i] : null;
      const src = t ? artUrl(t.id, artSize(t.id, "big")) : "";
      if (img.dataset.src !== src) {
        img.dataset.src = src;
        if (t) { img.dataset.fallback = artFallback(t.id); img.src = src; } else img.removeAttribute("src");
      }
      img.closest(".np-slot").classList.toggle("empty", !t);
    }
  }

  // ---------- title marquee (§3.1, §4.2) ----------
  // A title wider than its box scrolls as a seamless loop after a pause; the text stays put with reduced motion.
  const MQ_MINI = { delay: 2500 }, MQ_NP = { delay: 3000 };
  function setMarquee(el, text, opts) {
    el.dataset.text = text;
    el._mqOpts = opts;
    el.setAttribute("aria-label", text);
    el.innerHTML = `<span class="mq-in"><span class="mq-a">${esc(text)}</span></span>`;
    el._mq?.cancel();
    el._mq = null;
    requestAnimationFrame(() => fitMarquee(el));
  }
  function fitMarquee(el) {
    const inner = el.firstElementChild, a = inner?.firstElementChild;
    if (!a) return;
    el._mq?.cancel();
    el._mq = null;
    el.classList.remove("mq-on");
    inner.querySelector(".mq-b")?.remove();
    const m = reducedMotion() || !inner.animate ? null : PC.marquee(a.offsetWidth, el.clientWidth, el._mqOpts || {});
    if (!m) return;
    el.classList.add("mq-on");
    inner.insertAdjacentHTML("beforeend", `<span class="mq-a mq-b" aria-hidden="true">${esc(el.dataset.text || "")}</span>`);
    el._mq = inner.animate([
      { transform: "translateX(0)", offset: 0 },
      { transform: "translateX(0)", offset: m.delay / m.total },
      { transform: `translateX(${-m.dist}px)`, offset: 1 },
    ], { duration: m.total, iterations: Infinity, easing: "linear" });
  }
  let mqTimer = 0;
  window.addEventListener("resize", () => {
    clearTimeout(mqTimer);
    mqTimer = setTimeout(() => { fitMarquee($("#mini-t")); fitMarquee($("#np-t")); }, 200);
  });

  // ---------- background styles (§4.1) ----------
  // Ambient (default) / Immersive / Tint / Pure black, a UI pref ("reef.npbg"). Two layers hold the art (a static blur of
  // the 64 px copy + the big art); a new track loads into the hidden one, which then cross-fades in (§0: no animated blur).
  // The "Pure black" player colours (theme) show every style as black.
  const BG_LABEL = { ambient: "Ambient", immersive: "Immersive", tint: "Tint", black: "Pure black" };
  const npBg = { style: PC.bgStyle(store.get("npbg", "ambient")), front: 0, id: null, timer: 0 };
  function applyBg() {
    const eff = pal.theme === "black" ? "black" : npBg.style;
    npEl.dataset.bg = eff;
    $("#npbg").dataset.bg = eff;
  }
  function setNpBg(t) {
    if (!t || npBg.id === t.id) return;
    npBg.id = t.id;
    const layers = $$("#npbg .npbg-layer");
    const back = layers[1 - npBg.front];
    const blur = $(".npbg-blur", back), big = $(".npbg-big", back), id = t.id;
    let done = false;
    const swap = () => {
      if (done || npBg.id !== id) return;
      done = true;
      clearTimeout(npBg.timer);
      layers[npBg.front].classList.remove("on");
      back.classList.add("on");
      npBg.front = 1 - npBg.front;
    };
    blur.onload = swap;
    blur.onerror = swap;
    big.dataset.fallback = artFallback(id);
    blur.src = artUrl(id, artSize(id, "bg"));
    big.src = artUrl(id, artSize(id, "big")); // the same URL as the art: one fetch
    clearTimeout(npBg.timer);
    npBg.timer = setTimeout(swap, 1200);
    if (blur.complete && blur.naturalWidth) swap();
  }
  function setBgStyle(v) {
    npBg.style = PC.bgStyle(v);
    store.set("npbg", npBg.style);
    applyBg();
    renderBgPicker();
    if (!$("#status-sheet").hidden) renderStatus();
  }
  // The picker previews each style with the song that's playing (the player behind the sheet changes live too).
  function renderBgPicker() {
    const t = cur();
    const small = t ? artUrl(t.id, artSize(t.id, "bg")) : "", big = t ? artUrl(t.id, artSize(t.id, "big")) : "";
    $("#bg-grid").innerHTML = PC.BGS.map((k) => `<button class="bgp${k === npBg.style ? " on" : ""}" role="radio" aria-checked="${k === npBg.style}" data-npbg="${k}">
        <span class="bgp-view"><span class="npbg" data-bg="${k}"><span class="npbg-layer on">${t
          ? `<img class="npbg-blur" src="${small}" alt=""><img class="npbg-big" src="${big}" alt="">` : ""}</span></span>
          <span class="bgp-ui">${k === "immersive" ? "" : `<span class="bgp-art">${t ? `<img src="${big}" alt="">` : ""}</span>`}<i></i><i></i></span></span>
        <span class="bgp-l">${BG_LABEL[k]}</span></button>`).join("");
    $("#bg-note").textContent = pal.theme === "black"
      ? "Player colours are set to Pure black (Server sheet), which shows every style as black."
      : "Shown with the song that's playing. Saved on this device.";
  }
  function openBgPicker() { renderBgPicker(); openSheet("#bg-sheet"); }

  // ---------- like + playlists ----------
  function paintLike() {
    const t = cur(), b = $("#np-like");
    const on = !!t && likedIds.has(t.id);
    b.classList.toggle("on", on);
    b.setAttribute("aria-pressed", String(on));
    b.setAttribute("aria-label", on ? "Liked" : "Like");
    b.hidden = !t || M.isPlaceholder(t);
    $(".np-cap").classList.toggle("solo", b.hidden || $("#np-keep").hidden);
  }
  async function loadLikes() {
    try {
      const list = await api("/api/likes");
      if (!Array.isArray(list)) return;
      likedIds.clear();
      for (const t of list) if (t && t.id) likedIds.add(t.id);
      paintLike();
    } catch { /* the button stays as it was */ }
  }
  async function toggleLike(t = cur(), { undo = false } = {}) {
    if (!t || M.isPlaceholder(t)) return;
    const on = !likedIds.has(t.id);
    if (on) likedIds.add(t.id); else likedIds.delete(t.id);
    paintLike();
    try {
      if (on) {
        await api(`/api/likes/${encodeURIComponent(t.id)}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(slim(t)) });
      } else {
        await api(`/api/likes/${encodeURIComponent(t.id)}`, { method: "DELETE" });
      }
      toast(on ? "Added to Liked songs" : "Removed from Liked songs", undo ? { action: "Undo", onAction: () => toggleLike(t) } : {});
    } catch (e) {
      if (!on && e.status === 404) return; // it wasn't liked on the server either
      if (on) likedIds.delete(t.id); else likedIds.add(t.id);
      paintLike();
      toast(`Couldn't save that: ${e.message}`);
    }
  }
  async function addToPlaylist(t, pid) {
    const p = (state.playlists || []).find((x) => x.id === pid);
    try {
      const r = await api(`/api/playlists/${encodeURIComponent(pid)}/tracks`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ tracks: [slim(t)], dedupe: true }),
      });
      toast(r.added ? `Added to “${r.name || p?.name || "playlist"}”` : `Already in “${r.name || p?.name || "playlist"}”`);
      loadPlaylists();
    } catch (e) {
      toast(`Couldn't add it: ${e.message}`);
    }
  }
  // Save as playlist (Up next) asks for a name; the track menu has its own field (createPlaylist).
  async function newPlaylist(tracks, suggested = "") {
    const name = (window.prompt("Playlist name", suggested) || "").trim();
    if (name) await createPlaylist(name, tracks);
  }
  async function createPlaylist(name, tracks) {
    try {
      const p = await api("/api/playlists", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name, tracks }) });
      toast(`Saved “${p.name}” · ${songs(p.tracks?.length ?? tracks.length)}`);
      loadPlaylists();
    } catch (e) {
      toast(`Couldn't save it: ${e.message}`);
    }
  }
  function saveQueueAsPlaylist() {
    const tracks = state.queue.filter((t) => !M.isPlaceholder(t)).map(slim);
    if (!tracks.length) { toast("Nothing to save yet"); return; }
    newPlaylist(tracks, state.from && state.from !== "From search" ? state.from : "My queue");
  }
  // Long-press the title or artist: copy "Artist – Title" (§4.2).
  async function copyTrackName() {
    const t = cur();
    if (!t) return;
    const text = `${t.channel ? `${t.channel} – ` : ""}${t.title}`;
    let ok = false;
    if (navigator.clipboard && window.isSecureContext) { try { await navigator.clipboard.writeText(text); ok = true; } catch { ok = false; } }
    if (!ok) ok = copyFallback(text);
    toast(ok ? `Copied “${text}”` : "Couldn't copy");
  }

  // ---------- mini player gestures (§3.2) ----------
  // Sideways: the first 100 px move at most 30 (then 1:1); past a third of the width or a 1000 px/s flick the card leaves
  // and comes back with the next / previous song, which the layer behind it shows meanwhile ("Nothing up next" when
  // there's none, and it springs back). Up (or a tap) opens Now Playing; pulled down > 32 px (or a flick) it pauses
  // and goes away until something plays again.
  (function miniGestures() {
    const wrap = $("#mini"), card = $("#mini-card"), peek = $("#mini-peek");
    let dirShown = 0;
    function paintPeek(dir) {
      dirShown = dir;
      const nb = PC.neighbours(state.queue.length, state.index, state.repeat);
      const t = state.queue[dir > 0 ? nb.next : nb.prev];
      peek.dataset.dir = dir > 0 ? "next" : "prev";
      peek.innerHTML = t ? `<span class="mp-art"><img src="${artUrl(t.id, artSize(t.id, "mini"))}" alt=""></span>
        <span class="mp-text"><small>${dir > 0 ? "Up next" : "Previous"}</small><b>${esc(t.title)}</b></span>`
        : `<span class="mp-text"><b>${dir > 0 ? "Nothing up next" : "Nothing before this"}</b></span>`;
    }
    const reset = (anim = true) => {
      card.style.transition = anim && !reducedMotion() ? "" : "none";
      card.style.transform = "";
      card.style.opacity = "";
      wrap.classList.remove("peeking");
      dirShown = 0;
    };
    drag(card, {
      onStart(g) { card.classList.add("dragging"); if (g.axis === "x") wrap.classList.add("peeking"); },
      onMove(g) {
        if (g.axis === "x") {
          const dir = g.dx < 0 ? 1 : -1; // dragged left: the next song
          if (dir !== dirShown) paintPeek(dir);
          card.style.transform = `translate3d(${PC.rubber(g.dx)}px, 0, 0)`;
        } else {
          card.style.transform = `translate3d(0, ${g.dy < 0 ? PC.rubber(g.dy, 60, 20) : g.dy}px, 0)`;
          card.style.opacity = g.dy > 0 ? String(1 - Math.min(0.6, g.dy / 160)) : "";
        }
      },
      onEnd(g, cancelled) {
        card.classList.remove("dragging");
        if (g.axis === "y") {
          const v = cancelled ? null : PC.miniVertical(g.dy, g.vy);
          if (v === "dismiss") { dismissMini(); return; }
          reset();
          if (v === "open") openNP();
          return;
        }
        const w = card.offsetWidth;
        const dir = cancelled ? 0 : PC.swipeCommit(g.dx, g.vx, w, { frac: 1 / 3, vmin: 1000 });
        const nb = PC.neighbours(state.queue.length, state.index, state.repeat);
        const target = dir < 0 ? nb.next : dir > 0 ? nb.prev : -1;
        if (target < 0) { reset(); return; }
        const out = reducedMotion() ? 0 : 200;
        const snap = playSnap(), targetId = state.queue[target]?.id;
        card.style.transition = out ? `transform ${out}ms var(--ease-out), opacity ${out}ms` : "none";
        card.style.transform = `translate3d(${dir * (w + 24)}px, 0, 0)`;
        card.style.opacity = "0";
        setTimeout(() => {
          if (PC.sameTrack(snap, playSnap())) goNeighbour(dir, targetId); // else: the track changed meanwhile, just come back
          // back in with the new song: from a little to the other side, fading in
          card.style.transition = "none";
          card.style.transform = reducedMotion() ? "" : `translate3d(${-dir * 36}px, 0, 0)`;
          void card.offsetWidth;
          reset();
        }, out);
      },
    });
    function dismissMini() {
      const out = reducedMotion() ? 0 : 220;
      card.style.transition = out ? `transform ${out}ms var(--ease-out), opacity ${out}ms` : "none";
      card.style.transform = "translate3d(0, 90px, 0)";
      card.style.opacity = "0";
      audio.pause();
      setTimeout(() => {
        miniGone = true;
        updatePlayer();
        reset(false);
        requestAnimationFrame(() => { card.style.transition = ""; });
      }, out);
    }
  })();

  // ---------- transport (§4.5) ----------
  // Pressing a pill stretches it and squeezes the others (CSS, on data-press); prev/next act 180 ms after the tap.
  (function transport() {
    const row = $("#np-transport");
    let t0 = 0, timer = 0;
    row.addEventListener("pointerdown", (e) => {
      const b = e.target.closest(".tp");
      if (!b || reducedMotion()) return;
      clearTimeout(timer);
      t0 = performance.now();
      row.dataset.press = b.id.slice(3); // play | prev | next
    });
    const up = () => {
      if (!row.dataset.press) return;
      clearTimeout(timer);
      timer = setTimeout(() => { delete row.dataset.press; }, Math.max(0, SKIP_DELAY - (performance.now() - t0)) + 60);
    };
    for (const ev of ["pointerup", "pointercancel", "pointerleave"]) row.addEventListener(ev, up);
  })();

  // AirPlay (§0, §4.6): only where Safari offers the picker, and only while a target is around.
  (function airplay() {
    const b = $("#np-airplay");
    if (typeof audio.webkitShowPlaybackTargetPicker !== "function") return;
    b.hidden = false;
    audio.addEventListener("webkitplaybacktargetavailabilitychanged", (e) => { b.hidden = e.availability !== "available"; });
    audio.addEventListener("webkitcurrentplaybacktargetiswirelesschanged", () => b.classList.toggle("on", !!audio.webkitCurrentPlaybackTargetIsWireless));
    b.addEventListener("click", () => { try { audio.webkitShowPlaybackTargetPicker(); } catch { /* no picker right now */ } });
  })();

  // ---------- seek bar (§4.4) ----------
  // A div slider: press anywhere on it and drag; the times follow (elapsed in bold) and the seek happens on release.
  (function seekBar() {
    let id = null, f = 0;
    const at = (e) => { const r = seekEl.getBoundingClientRect(); return PC.seekFrac(e.clientX, r.left, r.width); };
    const show = () => { const d = duration(); paintSeek(f, f * d, d); };
    const move = (e) => { if (e.pointerId === id) { f = at(e); show(); } };
    const end = (e) => {
      if (e.pointerId !== id) return;
      id = null;
      seeking = false;
      seekEl.classList.remove("drag");
      for (const [t, fn] of [["pointermove", move], ["pointerup", end], ["pointercancel", end]]) window.removeEventListener(t, fn);
      const d = duration();
      if (e.type === "pointerup" && d) seekTo(Math.min(f * d, Math.max(0, d - 0.25))); else setProgress(playPos(), d);
    };
    seekEl.addEventListener("pointerdown", (e) => {
      if (id != null || !cur() || !duration() || (e.pointerType === "mouse" && e.button !== 0)) return;
      id = e.pointerId;
      seeking = true;
      seekEl.classList.add("drag");
      try { seekEl.setPointerCapture(id); } catch { /* followed on the window anyway */ }
      for (const [t, fn] of [["pointermove", move], ["pointerup", end], ["pointercancel", end]]) window.addEventListener(t, fn);
      f = at(e);
      show();
    });
    seekEl.addEventListener("keydown", (e) => {
      const d = duration();
      if (!d || !cur()) return;
      const step = { ArrowLeft: -5, ArrowDown: -5, ArrowRight: 5, ArrowUp: 5 }[e.key];
      const to = step ? playPos() + step : e.key === "Home" ? 0 : e.key === "End" ? d - 1 : null;
      if (to == null) return;
      e.preventDefault();
      seekTo(PC.clamp(to, 0, Math.max(0, d - 0.5)));
    });
  })();

  // ---------- lyrics (§5): a full-screen view that rises over the player ----------
  // Rows (lines + interlude gaps, LV.rows) get a depth class by distance from the active one (static opacity / scale
  // / blur, toggled only when the active row changes). The active row sits at 35% of the view; when it changes, the
  // rows glide there in a short wave (FLIP on `translate`). A manual scroll pauses that for LV.HOLD_MS and shows a
  // Resync pill. Word-timed lines fill word by word (`--wf` on the current word), interlude dots fill in turn
  // (`--gp` on the gap row): both on one rAF loop that runs only while the view is visible, playing, and on such a
  // row (the active one, not the track); everything else follows `timeupdate`.
  const LYRIC_LEAD = 0.2; // a line shows a beat early: it reads as on time (and timeupdate is only ~4/s on Safari)
  const WORD_LEAD = 0.05; // words run on rAF: about a frame and a bit
  const lyr = {
    on: store.get("lyricsOn", false), // the view is open (remembered: reopening the player comes back to it)
    only: store.get("lyricsOnly", false), // "Lyrics only": the chrome hides
    cache: new Map(), // track id -> Promise of the /api/lyrics body | null (a miss) | {error} (a failure, not kept)
    seq: 0, id: null, data: null, lines: [], rows: [], times: [], els: [], wordEls: [], base: [], cls: [],
    active: -1, word: -1, wf: -1, gapP: -1, gapOut: false, wordy: false,
    held: false, holdTimer: 0, offset: 0, offsetTimer: 0, ourTop: 0, raf: 0,
  };
  const lyricsPanel = $("#np-lyrics");
  const lyv = $("#lyv");
  const lyrPop = $("#lyr-pop");
  const npOpen = () => $("#np").classList.contains("open");
  // The view is on screen (and the page too): only then does the DOM follow the song.
  const lyrVisible = () => lyr.on && npOpen() && document.visibilityState !== "hidden";

  function fetchLyrics(t) {
    let p = lyr.cache.get(t.id);
    if (!p) {
      p = api(`/api/lyrics/${encodeURIComponent(t.id)}?${lyricsHints(t)}`).catch((e) => {
        if (e.status === 404) return null; // a real miss sticks
        lyr.cache.delete(t.id); // a network blip or a server error doesn't
        return { error: e };
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

  function clearLyr() {
    stopLyrFrames();
    releaseLyrics(false);
    Object.assign(lyr, { data: null, lines: [], rows: [], times: [], els: [], wordEls: [], base: [], cls: [],
      active: -1, word: -1, wf: -1, gapP: -1, gapOut: false, wordy: false });
  }

  // One row of synced lyrics. Line-timed lines hold one span per word (they pop in turn when the line starts);
  // word-timed ones one per timed word, syllables of a word grouped so they never wrap apart.
  function lyrRowHtml(r, j) {
    if (r.kind === "gap") {
      return `<button class="${lyr.cls[j]}" data-r="${j}" aria-label="Instrumental break"><span class="dots" aria-hidden="true"><i></i><i></i><i></i></span></button>`;
    }
    const fz = LV.sizeFactor(r.text.length);
    let body;
    if (r.words) {
      body = LV.groups(r.words).map((g) => `<span class="wg">${r.words.slice(g.from, g.to + 1).map((w, k) =>
        `<span class="w${LV.glows(w, r.words[g.from + k + 1]) ? " long" : ""}">${esc(w.text.trim())}</span>`).join("")}</span>${g.space ? " " : ""}`).join("");
    } else {
      body = r.text.split(/\s+/).filter(Boolean).map((w, k) => `<span class="w" style="--k:${Math.min(k, 12)}">${esc(w)}</span>`).join(" ");
    }
    return `<button class="${lyr.cls[j]}" data-r="${j}"${fz < 1 ? ` style="--fz:${fz}"` : ""}>${body}</button>`;
  }

  function renderLyrics(data) {
    clearLyr();
    lyr.data = data && !data.error ? data : null;
    lyr.offset = Number(lyr.data?.offset_ms) || 0;
    const d = lyr.data;
    const lines = d?.synced ? LY.attachWords(LY.parseLRC(d.synced), d.words) : [];
    const rows = LV.rows(lines);
    let kind;
    if (rows.some((r) => r.kind === "line")) {
      kind = "synced";
      lyr.lines = lines;
      lyr.rows = rows;
      lyr.times = rows.map((r) => r.t);
      lyr.wordy = rows.some((r) => r.words);
      lyr.base = rows.map((r) => (r.kind === "gap" ? "lyr-row gap" : `lyr-row line${r.words ? " wordy" : ""}`));
      lyr.cls = rows.map((r, j) => `${lyr.base[j]} ${LV.depth(j, -1)}`);
      lyricsPanel.innerHTML = `<div class="lyr synced">${rows.map(lyrRowHtml).join("")}</div>`;
      lyr.els = $$(".lyr-row", lyricsPanel);
      lyr.wordEls = lyr.els.map((el, j) => (rows[j].words ? $$(".w", el) : null));
    } else if (d?.plain || d?.synced) {
      kind = "plain";
      const text = d.plain || d.synced.replace(/^(\s*\[[^\]]*\])+/gm, ""); // unparseable synced: show its words
      lyricsPanel.innerHTML = `<div class="lyr plain"><p class="lyr-note">Not synced to the song</p>${text.split(/\r?\n/).map((l) => (l.trim()
        ? `<p>${esc(l.trim())}</p>` : `<p class="gap"></p>`)).join("")}</div>`;
    } else if (data?.error) {
      kind = "error";
      lyricsPanel.innerHTML = errorState(data.error, { title: "Couldn't load the lyrics", retry: "data-lyr-retry" });
    } else if (d?.instrumental) {
      kind = "instrumental";
      lyricsPanel.innerHTML = emptyState("note", "Instrumental", "No words in this one: just listen.");
    } else {
      kind = "none";
      lyricsPanel.innerHTML = emptyState("lyrics", "No lyrics for this song", "Know they exist? Search LRCLIB and pick the right ones.",
        `<button class="pill-btn" data-lyr-search>${icon("search")}Find lyrics</button>`);
    }
    lyv.dataset.kind = kind;
    lyricsPanel.scrollTop = 0;
    lyr.ourTop = 0;
    renderLyrTools();
    if (lyrVisible()) syncLyrics(true);
    kickLyrics();
  }

  // The pill at the top: round art + title (only repainted when the track changes).
  function paintLyricsHead(t) {
    const art = $("#lyv-art");
    if (art.dataset.id === t.id) return;
    art.dataset.id = t.id;
    art.src = artUrl(t.id, artSize(t.id, "mini"));
    $("#lyv-blur").src = artUrl(t.id, artSize(t.id, "bg"));
    $("#lyv-t").textContent = t.title || "";
    $("#lyv-s").textContent = t.channel || "";
  }

  // Show the current track's lyrics (no-op past a re-sync when they're already there).
  async function showLyrics() {
    const t = cur();
    if (!lyr.on || !t) return;
    paintLyricsHead(t);
    if (lyr.id === t.id) { if (lyr.times.length && lyrVisible()) syncLyrics(true); kickLyrics(); return; }
    lyr.id = t.id;
    const seq = ++lyr.seq;
    clearLyr();
    lyv.dataset.kind = "loading";
    lyricsPanel.innerHTML = `<div class="lyr-sk" role="status" aria-label="Loading lyrics…">${"<i class=\"sk\"></i>".repeat(5)}</div>`;
    renderLyrTools();
    const data = await fetchLyrics(t);
    if (seq !== lyr.seq || cur()?.id !== t.id) return; // the track changed while this was loading
    renderLyrics(data);
  }

  // New track: drop what the view shows, then load if it's visible.
  function resetLyrics() {
    lyr.id = null; lyr.seq++;
    clearLyr();
    lyr.offset = 0;
    if (lyr.on && npOpen()) showLyrics();
  }

  // Follow the playhead (in lyrics time: the track's offset applied). The DOM changes only when the active row, the
  // word being sung, its fill (by >= 1%) or the interlude progress does.
  function syncLyrics(force = false, smooth = !force) {
    if (!lyr.times.length) return;
    const now = LY.lyricTime(audio.currentTime, lyr.offset);
    const i = LV.indexAt(lyr.times, now + LYRIC_LEAD);
    if (i !== lyr.active || force) setLyrActive(i, smooth);
    const r = lyr.rows[i];
    if (r?.words) syncWords(r, now + WORD_LEAD);
    else if (r?.kind === "gap") syncGap(r, now);
  }
  // Undo the word / interlude state the active row carries.
  function clearRowState(j) {
    const r = lyr.rows[j];
    if (r?.words) {
      const ws = lyr.wordEls[j] || [];
      for (let k = 0; k <= Math.min(lyr.word, ws.length - 1); k++) ws[k].classList.remove("sung", "now");
      ws[lyr.word]?.style.removeProperty("--wf");
    } else if (r?.kind === "gap") {
      lyr.els[j].style.removeProperty("--gp");
      lyr.els[j].classList.remove("out");
    }
    Object.assign(lyr, { word: -1, wf: -1, gapP: -1, gapOut: false });
  }
  function setLyrActive(i, smooth) {
    const old = lyr.active;
    clearRowState(old);
    const range = LV.depthRange(old, i, lyr.rows.length);
    const pop = !reducedMotion();
    if (range) {
      for (let j = range[0]; j <= range[1]; j++) {
        let c = `${lyr.base[j]} ${LV.depth(j, i)}`;
        if (j === i && pop && lyr.rows[j].kind === "line" && !lyr.rows[j].words) c += " pop"; // line bounce
        if (c !== lyr.cls[j]) { lyr.els[j].className = c; lyr.cls[j] = c; }
      }
    }
    lyr.active = i;
    if (!lyr.held) scrollToActive(smooth);
  }
  // Word-timed line: words before the current one are sung (full), the current one fills.
  function syncWords(r, now) {
    const ws = lyr.wordEls[lyr.active];
    if (!ws) return;
    const k = LV.wordAt(r.words, now);
    if (k !== lyr.word) {
      for (let j = Math.max(0, Math.min(k, lyr.word)); j <= Math.max(k, lyr.word); j++) {
        ws[j].classList.toggle("sung", j < k);
        ws[j].classList.toggle("now", j === k);
        if (j !== k) ws[j].style.removeProperty("--wf");
      }
      lyr.word = k;
      lyr.wf = -1;
    }
    if (k < 0) return;
    const f = LV.wordFill(r.words, k, now, lyr.rows[lyr.active + 1]?.t);
    if (Math.abs(f - lyr.wf) >= 0.01 || (f === 1 && lyr.wf !== 1)) {
      ws[k].style.setProperty("--wf", f.toFixed(3));
      lyr.wf = f;
    }
  }
  // Interlude: three dots fill in turn, then fade just before the next line.
  function syncGap(r, now) {
    const el = lyr.els[lyr.active], g = LV.gapProgress(r, now);
    if (Math.abs(g.p - lyr.gapP) >= 0.005 || (g.p === 1 && lyr.gapP !== 1)) { el.style.setProperty("--gp", g.p.toFixed(3)); lyr.gapP = g.p; }
    if (g.out !== lyr.gapOut) { el.classList.toggle("out", g.out); lyr.gapOut = g.out; }
  }

  // Put the active row at 35% of the view. With `smooth`, the rows near the screen keep their place for a frame
  // (translate by the scroll distance), then glide to it in a 20 ms-per-row wave; big jumps (a seek) just land.
  function scrollToActive(smooth) {
    if (!lyrVisible()) return;
    const el = lyr.els[lyr.active];
    const viewH = lyricsPanel.clientHeight;
    const from = lyricsPanel.scrollTop;
    const target = el ? LV.anchorTop(el.offsetTop, viewH) : 0;
    if (Math.abs(target - from) < 1) { lyr.ourTop = from; return; }
    lyricsPanel.scrollTop = target;
    lyr.ourTop = lyricsPanel.scrollTop;
    const delta = lyr.ourTop - from;
    if (!smooth || reducedMotion() || !LV.glides(delta, viewH)) return;
    const a = Math.max(0, lyr.active);
    const lo = Math.max(0, a - 10), hi = Math.min(lyr.els.length - 1, a + 16);
    for (let j = lo; j <= hi; j++) {
      const s = lyr.els[j].style;
      s.setProperty("--tt", "0s");
      s.translate = `0 ${delta}px`;
    }
    void lyricsPanel.offsetHeight; // the rows start where they were
    for (let j = lo; j <= hi; j++) {
      const s = lyr.els[j].style;
      s.removeProperty("--tt");
      s.setProperty("--dl", `${LV.stagger(j, a)}ms`);
      s.translate = "";
    }
  }

  // The rAF loop: only while the view is visible, playing, and the ACTIVE row is a word-timed line or an interlude
  // (timeupdate starts it again when such a row becomes active).
  function lyrNeedsFrames() {
    if (audio.paused || !lyrVisible() || !lyr.times.length) return false;
    return LV.needsFrames(lyr.rows[lyr.active]);
  }
  function lyrFrame() {
    lyr.raf = 0;
    if (!lyrNeedsFrames()) return;
    syncLyrics();
    lyr.raf = requestAnimationFrame(lyrFrame);
  }
  function kickLyrics() { if (!lyr.raf && lyrNeedsFrames()) lyr.raf = requestAnimationFrame(lyrFrame); }
  function stopLyrFrames() { if (lyr.raf) cancelAnimationFrame(lyr.raf); lyr.raf = 0; }

  function setLyricsOn(on) {
    lyr.on = on;
    store.set("lyricsOn", on);
    $("#np").classList.toggle("show-lyrics", on);
    lyv.setAttribute("aria-hidden", String(!on));
    lyv.inert = !on;
    $("#np-lyr").setAttribute("aria-pressed", String(on));
    $("#np-lyr").classList.toggle("on", on);
    if (on) { showLyrics(); return; }
    closeSheet("#lyr-opts-sheet");
    releaseLyrics(false);
    stopLyrFrames();
  }
  function setLyricsOnly(on) {
    lyr.only = on;
    store.set("lyricsOnly", on);
    lyv.classList.toggle("only", on);
  }

  // Reading ahead or back: a manual scroll pauses auto-follow for LV.HOLD_MS after it stops; Resync ends it at once.
  function holdLyrics() {
    if (!lyr.held) {
      lyr.held = true;
      lyv.classList.add("held");
      $("#lyv-resync").tabIndex = 0;
    }
    clearTimeout(lyr.holdTimer);
    lyr.holdTimer = setTimeout(() => releaseLyrics(true), LV.HOLD_MS);
  }
  function releaseLyrics(scroll) {
    clearTimeout(lyr.holdTimer);
    if (!lyr.held) return;
    lyr.held = false;
    lyv.classList.remove("held");
    $("#lyv-resync").tabIndex = -1;
    if (scroll) scrollToActive(true);
  }
  // Any scroll that isn't ours (touch, momentum, wheel, keys) is the user reading.
  lyricsPanel.addEventListener("scroll", () => {
    if (lyr.times.length && Math.abs(lyricsPanel.scrollTop - lyr.ourTop) >= 2) holdLyrics();
  }, { passive: true });
  lyricsPanel.addEventListener("click", (e) => {
    if (e.target.closest("[data-lyr-search]")) { openLyricsSearch(); return; }
    if (e.target.closest("[data-lyr-retry]")) { if (lyr.id) lyr.cache.delete(lyr.id); lyr.id = null; showLyrics(); return; }
    const row = e.target.closest(".lyr-row");
    const r = row && lyr.rows[row.dataset.r];
    if (!r) return;
    releaseLyrics(false);
    seekTo(LY.seekTime(r.t, lyr.offset)); // tap a line: play from there
    if (audio.paused) playAudio();
    syncLyrics(true, true);
  });
  $("#lyv-resync").addEventListener("click", () => releaseLyrics(true));
  for (const id of ["#lyv-back", "#lyv-pill"]) $(id).addEventListener("click", () => setLyricsOn(false));
  $("#lyv-play").addEventListener("click", () => togglePlay());
  // Pull the view down by its top to go back to the player.
  drag($("#lyv-grab"), {
    x: false,
    onMove(g) { lyv.classList.add("dragging"); lyv.style.transform = `translate3d(0, ${Math.max(0, g.dy)}px, 0)`; },
    onEnd(g, cancelled) {
      lyv.classList.remove("dragging");
      lyv.style.transform = "";
      if (!cancelled && PC.npClose(g.dy, g.vy, { dist: 90 })) setLyricsOn(false);
    },
  });

  // ---------- lyrics options: timing nudge, "Lyrics only", source, "Wrong lyrics?" (a light sheet from ⋯) ----------
  function renderLyrTools() {
    const synced = lyr.times.length > 0, d = lyr.data;
    const src = d?.provider ? `From ${esc(LY.sourceLabel(d.provider))}${lyr.wordy ? " · word timing" : ""}` : "";
    lyrPop.innerHTML = `${src ? `<p class="lyr-src">${src}</p>` : ""}
      <div class="lyr-group">
        <div class="lyr-opt lyr-timing">
          <span class="lyr-opt-l"><span>Timing</span><small>${synced ? "Lyrics ahead of the song? Tap +0.5." : "Only synced lyrics can be nudged."}</small></span>
          <div class="lyr-nudges">
            <button class="lyr-nudge" data-lyr-nudge="${-LY.NUDGE}" aria-label="Show lyrics half a second sooner" ${synced ? "" : "disabled"}>−0.5</button>
            <button class="lyr-nudge lyr-off" data-lyr-nudge="reset" aria-label="Reset lyrics timing" ${synced && lyr.offset ? "" : "disabled"}>${esc(LY.fmtOffset(lyr.offset))}</button>
            <button class="lyr-nudge" data-lyr-nudge="${LY.NUDGE}" aria-label="Show lyrics half a second later" ${synced ? "" : "disabled"}>+0.5</button>
          </div>
        </div>
        <label class="lyr-opt"><span class="lyr-opt-l"><span>Lyrics only</span><small>Hide the song and the controls</small></span>
          <input type="checkbox" class="sw" data-lyr-only${lyr.only ? " checked" : ""} aria-label="Lyrics only"></label>
        <button class="lyr-opt" data-lyr-search><span class="lyr-opt-l"><span>Wrong lyrics?</span><small>Search LRCLIB and pick the right ones</small></span>${icon("search")}</button>
      </div>`;
  }
  function setLyricsOffset(ms) {
    const id = lyr.id;
    lyr.offset = ms;
    if (lyr.data) lyr.data.offset_ms = ms; // the cached promise holds this object: reopening keeps the nudge
    const off = $(".lyr-off", lyrPop);
    if (off) { off.textContent = LY.fmtOffset(ms); off.disabled = !ms; }
    syncLyrics(true, true);
    clearTimeout(lyr.offsetTimer);
    lyr.offsetTimer = setTimeout(() => {
      api(`/api/lyrics/${encodeURIComponent(id)}/offset`, {
        method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ms }),
      }).catch(() => toast("Couldn't save the lyrics timing"));
    }, 600); // several taps, one save
  }
  $("#lyr-more").addEventListener("click", () => { renderLyrTools(); openSheet("#lyr-opts-sheet"); });
  lyrPop.addEventListener("click", (e) => {
    const b = e.target.closest("[data-lyr-nudge],[data-lyr-search]");
    if (!b || b.disabled) return;
    if ("lyrSearch" in b.dataset) { closeSheet("#lyr-opts-sheet"); openLyricsSearch(); return; }
    const v = b.dataset.lyrNudge;
    setLyricsOffset(v === "reset" ? 0 : LY.nudge(lyr.offset, Number(v)));
  });
  lyrPop.addEventListener("change", (e) => { if ("lyrOnly" in e.target.dataset) setLyricsOnly(e.target.checked); });

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
    if (s === "pending") { libFocus = "lib-dl"; location.hash = "#library"; return; }
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
    $("#status-list").innerHTML = themePicker() + swipePicker()
      + `<li class="st-link"><button data-open-bg><span class="k">Player background</span><span class="v">${esc(BG_LABEL[npBg.style])}${icon("chev-right")}</span></button></li>`
      + rows.map(([k, v, c]) => `<li><span class="k">${esc(k)}</span><span class="v ${c}">${esc(v)}</span></li>`).join("")
      + stLink("sources", "Sources", sourcesSummary())
      + stLink("updates", "Updates", updatesSummary(), updateAvailable())
      + stLink("scrobble", "Scrobbling", scrobbleSummary())
      + stLink("backup", "Backup", backupSummary())
      + stLink("import", "Import playlist", "CSV")
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
  const SUBS = { sources: renderSourcesSheet, updates: renderUpdatesSheet, scrobble: renderScrobbleSheet, backup: renderBackupSheet, import: renderImportSheet };
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
  const skel = (n) => skRows(n, { art: false });

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

  // Import: a CSV (Exportify / Spotify-style, TuneMyMusic, Google Takeout) -> POST /api/import -> a Reef playlist.
  function renderImportSheet() {
    const f = imp.file, r = imp.result;
    $("#import-body").innerHTML = `<p class="st-note st-lead">A playlist exported as CSV (Exportify, TuneMyMusic, Google Takeout's
        YouTube Music playlists) becomes a Reef playlist. Songs are found on YouTube Music in the background; you can
        play it right away.</p>
      <label class="st-file">${icon("note")}<span>${esc(f ? f.name : "Choose a CSV file")}</span>
        <input type="file" id="import-file" accept=".csv,text/csv,text/comma-separated-values,text/plain"></label>
      <input class="st-input imp-name" id="import-name" type="text" maxlength="100" placeholder="Playlist name (optional)" aria-label="Playlist name" value="${esc(imp.name)}">
      <button class="btn-wide" data-st="import-go"${ON(!f || imp.busy)}>${imp.busy ? "Importing…" : "Import"}</button>
      ${r ? `<p class="st-note ok">Imported “${esc(r.playlist.name)}”: ${esc(songs(r.rows))}${r.to_match ? `, ${r.to_match} to match` : ""}${r.skipped ? ` (${r.skipped} rows skipped)` : ""}.</p>
        <a class="btn-wide alt" href="#local/${encodeURIComponent(r.playlist.id)}">${icon("note")}Open playlist</a>` : ""}`;
  }
  async function runImport() {
    const f = imp.file;
    if (!f || imp.busy) return;
    imp.busy = true;
    const name = imp.name.trim();
    renderImportSheet();
    const fd = new FormData();
    fd.append("file", f);
    if (name) fd.append("name", name);
    try {
      imp.result = await api("/api/import", { method: "POST", body: fd });
      imp.file = null;
      imp.name = "";
      if (imp.result.status) imp.status.set(imp.result.playlist.id, imp.result.status);
      toast(`Imported “${imp.result.playlist.name}”`);
      loadPlaylists();
    } catch (e) {
      toast(`Couldn't import: ${e.message}`);
    }
    imp.busy = false;
    renderImportSheet();
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
    if (a === "import-go") runImport();
  }
  $("#import-body").addEventListener("input", (e) => { if (e.target.id === "import-name") imp.name = e.target.value; });
  $("#import-body").addEventListener("change", (e) => {
    if (e.target.id !== "import-file") return;
    imp.file = e.target.files?.[0] || null;
    imp.result = null;
    renderImportSheet();
  });
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

  // ---------- sheets (§8): one stack, drag to dismiss, focus, Escape ----------
  // Open sheets stack in the order they opened (each above the last, wherever it sits in the page); Escape and the
  // scrim close the top one. A sheet's top (grabber + heading or header card) drags it down: past min(140 px, 35%) or
  // on a flick it closes, else it settles back (SH.sheetDismiss). Focus moves into a sheet as it opens, Tab stays in
  // the top one, and focus goes back to what had it once the sheet closes.
  const sheetStack = [];
  const topSheet = () => sheetStack[sheetStack.length - 1] || null;
  const FOCUSABLE = 'button:not([disabled]), [href], input:not([disabled]), select, textarea, [tabindex]:not([tabindex="-1"])';
  // Safari doesn't focus a tapped / clicked button, so the opener is also taken from the last press.
  let pressed = null, pressedAt = 0;
  document.addEventListener("pointerdown", (e) => { pressed = e.target.closest?.("button, a[href], [tabindex]") || null; pressedAt = Date.now(); }, true);
  const opener = () => (document.activeElement && document.activeElement !== document.body ? document.activeElement
    : pressed && Date.now() - pressedAt < 2000 && !pressed.closest(".sheet-wrap") ? pressed : null);
  function openSheet(sel, { ret = null } = {}) {
    const w = typeof sel === "string" ? $(sel) : sel;
    if (!w) return;
    const at = sheetStack.indexOf(w);
    if (at >= 0) sheetStack.splice(at, 1);
    else w._ret = ret || opener();
    sheetStack.push(w);
    w.style.zIndex = String(60 + sheetStack.length);
    clearTimeout(w._hide);
    w.hidden = false;
    const sheet = $(".sheet", w);
    sheet.style.transform = "";
    sheet.style.transition = "";
    const seq = (w._seq = (w._seq || 0) + 1);
    requestAnimationFrame(() => requestAnimationFrame(() => {
      if (w._seq !== seq || !sheetStack.includes(w)) return; // closed again meanwhile
      w.classList.add("open");
      syncLock();
      if (!sheet.contains(document.activeElement)) { try { sheet.focus({ preventScroll: true }); } catch { /* not focusable */ } }
    }));
  }
  function closeSheet(w) {
    if (typeof w === "string") w = $(w);
    if (!w || w.hidden) return;
    const at = sheetStack.indexOf(w);
    if (at >= 0) sheetStack.splice(at, 1);
    w._seq = (w._seq || 0) + 1;
    w.classList.remove("open");
    $(".sheet", w).style.transform = "";
    syncLock();
    let ret = w._ret;
    w._ret = null;
    // the ⋯ that opened it may have been re-rendered (a list repaint): its stand-in for the same song
    if (ret && !ret.isConnected && ret.dataset?.more) ret = $(`[data-more="${CSS.escape(ret.dataset.more)}"][data-list="${CSS.escape(ret.dataset.list || "")}"]`);
    if (w.contains(document.activeElement)) {
      const top = topSheet();
      const to = top ? $(".sheet", top) : ret && ret.isConnected && !ret.closest("[hidden], [inert], [aria-hidden=true]") ? ret : null;
      try { if (to) to.focus({ preventScroll: true }); else document.activeElement.blur(); } catch { /* gone */ }
    }
    clearTimeout(w._hide);
    w._hide = setTimeout(() => { if (!w.classList.contains("open")) w.hidden = true; }, 380);
  }
  // Tab and Shift+Tab wrap inside the top sheet.
  document.addEventListener("keydown", (e) => {
    const w = e.key === "Tab" && topSheet();
    if (!w) return;
    const items = $$(FOCUSABLE, $(".sheet", w)).filter((x) => x.offsetParent !== null && !x.closest("[inert]"));
    if (!items.length) { e.preventDefault(); return; }
    const first = items[0], last = items[items.length - 1], inside = w.contains(document.activeElement);
    if (e.shiftKey && (!inside || document.activeElement === first || document.activeElement === $(".sheet", w))) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && (!inside || document.activeElement === last)) { e.preventDefault(); first.focus(); }
  });
  for (const w of $$(".sheet-wrap")) {
    const sheet = $(".sheet", w);
    let h = 0;
    drag($(".sheet-top", w), {
      x: false,
      onStart() { h = sheet.offsetHeight; sheet.style.transition = "none"; },
      onMove(g) { sheet.style.transform = `translate3d(0, ${SH.sheetPull(g.dy)}px, 0)`; },
      onEnd(g, cancelled) {
        sheet.style.transition = "";
        if (!cancelled && SH.sheetDismiss(g.dy, g.vy, h)) closeSheet(w); else sheet.style.transform = "";
      },
    });
    // iOS scrolls (or rubber-bands) the page behind a fixed layer: only a sheet's own scrolling parts may move.
    w.addEventListener("touchmove", (e) => { if (!e.target.closest("[data-scroll], .sheet-body")) e.preventDefault(); }, { passive: false });
  }

  async function copyText(text, done = "Copied") {
    let ok = false;
    if (navigator.clipboard && window.isSecureContext) { try { await navigator.clipboard.writeText(text); ok = true; } catch { ok = false; } }
    if (!ok) ok = copyFallback(text); // plain http (the tailnet address): no clipboard API, only this, inside the tap
    toast(ok ? done : "Couldn't copy");
    return ok;
  }
  const ytLink = (t) => `https://www.youtube.com/watch?v=${encodeURIComponent(t.id)}`;

  // ---------- track menu (§8.1) ----------
  // A floating card tinted with the song's cover: the song (art, title, artist, source; Share, Info, ✕), a grid of quick
  // actions, then joined groups Go to · Recommendations · More. SH.menuModel decides what applies to which song (a
  // placeholder, a library track, the playing one, a queue row). The artist and playlist pickers slide in as sub-pages.
  // Opens from ⋯ on a song, a long press on any song row / card / queue row, and Now Playing's ⋯.
  let actTarget = null; // {t, list, page: "main" | "artists" | "playlists", form (the new playlist field is open)}
  const actSheet = $("#act-sheet"), actPages = $("#act-pages"), actMain = $("#act-menu"), actSub = $("#act-sub");
  const MI = {
    next: ["Play next", "play-next"], queue: ["Add to queue", "queue-add"], remove: ["Remove", "x"], radio: ["Start radio", "radio"],
    keep: ["Keep", "plus"], playlist: ["Add to playlist", "note"], like: ["Like", "heart"],
  };
  function findTrack(id, listName, i) {
    const list = lists[listName]?.() || [];
    return (list[i]?.id === id ? list[i] : list.find((x) => x && x.id === id)) || state.queue.find((x) => x.id === id) || null;
  }
  function openActions(id, listName, i, { page = "main" } = {}) {
    const t = findTrack(id, listName, i);
    if (!t) return;
    actTarget = { t: slim(t), list: listName, page: "main", form: false };
    renderActions();
    showActPage(page, true);
    tintMenu(actTarget.t);
    openSheet(actSheet);
    if (page === "playlists") loadPickerPlaylists();
  }
  function menuCtx(a) {
    const t = a.t, ph = M.isPlaceholder(t);
    return { placeholder: ph, src: srcKey(t.id), playing: cur()?.id === t.id,
      context: a.list === "player" ? "player" : a.list === "queue" ? "queue" : "list",
      keep: keepState(t.id), canKeep: canKeep(t.id), artists: linkable(t).length,
      album: ALBUM_ID.test(t.album_id || "") && !(state.view === "detail" && detail.kind === "album" && detail.id === t.album_id),
      hide: !!hideTarget(t), matched: !!(SH.fixKey(t) || t.replaced_from) };
  }
  // "YouTube", "Monochrome · FLAC", "Library", "Not matched yet", "Replacement".
  function sourceTag(t) {
    if (M.isPlaceholder(t)) return t.miss ? "Not found" : "Not matched yet";
    const k = srcKey(t.id), c = codecLabel(t);
    return [srcInfo(k).name, k === "mc" && c ? c : "", t.replaced_from ? "Replacement" : ""].filter(Boolean).join(" · ");
  }
  const mrow = (act, label, sub, ic, { arg = "", nav = false } = {}) => `<button class="mrow" data-act="${act}"${arg ? ` data-arg="${esc(arg)}"` : ""}>
      <span class="mic">${icon(ic)}</span><span class="ml"><span>${label}</span>${sub ? `<small>${sub}</small>` : ""}</span>${nav ? icon("chev-right", "mchev") : ""}</button>`;
  function renderActions() {
    const a = actTarget;
    if (!a) return;
    const t = a.t, m = SH.menuModel(menuCtx(a)), ks = keepState(t.id), liked = likedIds.has(t.id);
    $("#act-head").innerHTML = `<span class="mh-art">${art(t.id)}</span>
      <span class="mh-text"><span class="mh-t" id="act-title">${esc(t.title)}</span><span class="mh-s">${xBadge(t)}${esc(t.channel || t.artist || "")}</span>
        <span class="mh-badge">${esc(sourceTag(t))}</span></span>
      <span class="mh-btns">${m.head.includes("share") ? `<button class="mh-btn" data-act="share" aria-label="Share">${icon("share")}</button>` : ""}
        <button class="mh-btn" data-act="info" aria-label="Song info">${icon("info")}</button></span>`;
    const tile = (k) => {
      let [label, ic] = MI[k], cls = "", extra = "";
      if (k === "keep" && ks === "kept") { label = "In library"; ic = "check"; cls = " on"; extra = ` aria-disabled="true"`; }
      else if (k === "keep" && ks === "pending") { label = "Keeping…"; ic = "download"; cls = " busy"; }
      if (k === "like") { if (liked) { label = "Liked"; cls = " on"; } extra = ` aria-pressed="${liked}"`; }
      return `<button class="mqt${cls}" data-act="${k}"${extra}>${icon(ic, k === "like" && liked ? "solid" : "")}<span>${label}</span></button>`;
    };
    const as = linkable(t), ha = hideTarget(t);
    const rows = {
      artist: () => mrow("artist", "Go to artist", esc(as[0]?.name || ""), "person", { arg: as[0]?.id, nav: true }),
      artists: () => mrow("artists", "Go to artist", esc(as.map((x) => x.name).join(", ")), "person", { nav: true }),
      album: () => mrow("album", "Go to album", esc(t.album || ""), "disc", { arg: t.album_id, nav: true }),
      dislike: () => mrow("dislike", "Not interested", "Show this song less", "thumb-down"),
      hide: () => mrow("hide", `Hide ${esc(ha?.name || "artist")}`, "Out of search, radio and Home", "hide"),
      info: () => mrow("info", "Song info", "Source, quality, file and ids", "info", { nav: true }),
      fix: () => mrow("fix", "Fix wrong match", "Paste the right link or pick another", "swap", { nav: true }),
      copy: () => mrow("copy", "Copy link", "", "link"),
      youtube: () => mrow("youtube", "Open on YouTube", "", "external"),
      npbg: () => mrow("npbg", "Player background", esc(BG_LABEL[npBg.style]), "image", { nav: true }),
    };
    const group = (title, keys) => (keys.length ? `<section class="mgroup"><h4>${title}</h4><div class="mlist">${keys.map((k) => rows[k]()).join("")}</div></section>` : "");
    actMain.innerHTML = (m.quick.length ? `<div class="mquick c${SH.gridCols(m.quick.length)}">${m.quick.map(tile).join("")}</div>` : "")
      + group("Go to", m.goto) + group("Recommendations", m.recs) + group("More", m.more);
  }
  // Sub-pages: the artist picker and Add to playlist (with an inline "New playlist" field).
  function renderSub() {
    const a = actTarget;
    if (!a || a.page === "main") return;
    const back = (title) => `<div class="mback-row"><button class="mback" data-act="back" aria-label="Back">${icon("chev-left")}</button><h4>${title}</h4></div>`;
    if (a.page === "artists") {
      actSub.innerHTML = back("Go to artist") + `<div class="mlist">${linkable(a.t).map((x) => mrow("artist", esc(x.name), "Artist", "person", { arg: x.id, nav: true })).join("")}</div>`;
      return;
    }
    const pls = state.playlists;
    const list = pls === null
      ? (state.playlistsErr ? `<p class="st-note bad">Couldn't load your playlists. <button class="link-btn" data-act="pl-retry">Retry</button></p>` : skRows(3))
      : pls.length ? `<div class="mlist">${pls.map((p) => `<button class="mrow" data-act="pl-add" data-arg="${esc(p.id)}">
          <span class="mic art">${p.cover_id ? art(p.cover_id) : icon("note")}</span>
          <span class="ml"><span>${esc(p.name)}</span><small>${esc(songs(p.count))}</small></span></button>`).join("")}</div>`
      : `<p class="st-note">No playlists yet: make the first one.</p>`;
    const form = a.form ? `<form class="mform" id="pl-form" autocomplete="off">
        <input class="st-input" id="pl-name" maxlength="100" enterkeyhint="done" placeholder="Playlist name" aria-label="New playlist name">
        <button class="chip" type="submit">Create</button></form>`
      : `<div class="mlist">${mrow("pl-new", "New playlist", "With this song in it", "plus")}</div>`;
    actSub.innerHTML = back("Add to playlist") + form + `<h4 class="sh-label">Your playlists</h4>` + list;
  }
  function showActPage(page, instant = false) {
    if (!actTarget) return;
    actTarget.page = page;
    actTarget.form = false;
    renderSub();
    const on = page !== "main";
    if (instant) actPages.classList.add("instant");
    actPages.classList.toggle("on-sub", on);
    actMain.inert = on; actSub.inert = !on;
    actMain.setAttribute("aria-hidden", String(on));
    actSub.setAttribute("aria-hidden", String(!on));
    (on ? actSub : actMain).scrollTop = 0;
    actPages.scrollLeft = 0; // an engine without overflow: clip may have scrolled it to a focused sub-page button
    $(".sheet", actSheet).scrollLeft = 0;
    if (instant) { void actPages.offsetWidth; actPages.classList.remove("instant"); return; }
    try { $(FOCUSABLE, on ? actSub : actMain)?.focus({ preventScroll: true }); } catch { /* old engine */ }
  }
  async function loadPickerPlaylists(force = false) {
    if (state.playlists !== null && !force) loadPlaylists().then(() => { if (actTarget?.page === "playlists" && !actTarget.form) renderSub(); });
    else { await loadPlaylists(); if (actTarget?.page === "playlists" && !actTarget.form) renderSub(); }
  }
  // The card takes the song's cover colours (Pure black / Accent only themes: the plain ones).
  let menuTint = 0;
  function tintMenu(t) {
    const sheet = $(".sheet", actSheet), seq = ++menuTint;
    const set = (p) => { for (const [k, v] of Object.entries(P.vars(p))) sheet.style.setProperty(k, v); };
    if (pal.theme !== "cover" || M.isPlaceholder(t)) { set(pal.theme === "black" ? P.BLACK : P.DEFAULT); return; }
    set(pal.mem.get(t.id) || P.DEFAULT);
    paletteFor(t.id).then((p) => { if (seq === menuTint) set(p || P.DEFAULT); });
  }
  // Long-press Like (or the menu's Add to playlist): the playlist picker.
  function openPlaylistPicker(t) {
    if (!t || M.isPlaceholder(t)) return;
    openActions(t.id, "player", state.index, { page: "playlists" });
  }
  function runAction(act, arg) {
    const a = actTarget;
    if (!a) return;
    const t = a.t;
    // these stay in the sheet
    if (act === "artists") { showActPage("artists"); return; }
    if (act === "playlist") { showActPage("playlists"); loadPickerPlaylists(); return; }
    if (act === "back") { showActPage("main"); return; }
    if (act === "pl-new") { a.form = true; renderSub(); $("#pl-name")?.focus(); return; }
    if (act === "pl-retry") { state.playlistsErr = null; renderSub(); loadPickerPlaylists(true); return; }
    if (act === "keep" && keepState(t.id) === "kept") { toast("Already in your library"); return; }
    const ret = actSheet._ret;
    closeSheet(actSheet);
    if (act === "next") { if (a.list === "queue") playNextFrom(t.id); else queueWithUndo(t, a.list, "next"); }
    else if (act === "queue") queueWithUndo(t, a.list, "queue");
    else if (act === "remove") removeWithUndo(t.id);
    else if (act === "radio") startRadio(t);
    else if (act === "keep") keep(t.id, a.list);
    else if (act === "like") toggleLike(t);
    else if (act === "pl-add" && arg) addToPlaylist(t, arg);
    else if (act === "artist" && arg) openRoute(`#artist/${encodeURIComponent(arg)}`);
    else if (act === "album" && arg) openRoute(`#album/${encodeURIComponent(arg)}`);
    else if (act === "share") share(t);
    else if (act === "copy") copyText(ytLink(t), "Link copied");
    else if (act === "youtube") window.open(ytLink(t), "_blank", "noopener");
    else if (act === "dislike") notInterested(t);
    else if (act === "hide") hideArtist(hideTarget(t), { undo: true });
    else if (act === "info") openSongInfo(t, { ret });
    else if (act === "fix") openFix(t, { ret });
    else if (act === "npbg") openBgPicker();
  }
  actSub.addEventListener("submit", async (e) => {
    if (e.target.id !== "pl-form") return;
    e.preventDefault();
    const name = $("#pl-name").value.trim(), t = actTarget?.t;
    if (!name) { $("#pl-name").focus(); return; }
    if (!t) return;
    closeSheet(actSheet);
    await createPlaylist(name, [slim(t)]);
  });

  // Play next / Add to queue with Undo: the song leaves the queue again (or goes back where it was, if it was queued).
  function queueWithUndo(t, listName, mode) {
    if (!t) return;
    if (!cur() || cur().id === t.id) { addToQueue(t, listName, mode); return; } // starts playing / already playing
    const upAt = (id) => state.queue.findIndex((x, k) => k > state.index && x.id === id);
    const before = upAt(t.id);
    const rel = before >= 0 ? before - state.index : 0, prevT = before >= 0 ? state.queue[before] : null;
    addToQueue(t, listName, mode, { quiet: true });
    const id = t.id;
    toast(mode === "next" ? `Playing next: “${t.title}”` : `Added to queue: “${t.title}”`, { action: "Undo", onAction: () => {
      const i = upAt(id);
      if (i < 0) return;
      let r = Q.removeAt(state.queue, state.index, i);
      if (prevT && rel > 0) r = PC.reinsert(r.queue, r.index, prevT, rel);
      state.queue = r.queue; state.index = r.index;
      if (state.original && !prevT && !state.queue.some((x) => x.id === id)) state.original = state.original.filter((x) => x.id !== id);
      queueChanged();
    } });
  }

  // ---------- long press: the track menu from any song (§8.1) ----------
  // 500 ms without moving (a scroll or a swipe cancels it). The menu opens at once; the click the release would send
  // (it would land on the menu's scrim) is swallowed. Right-click opens it too.
  (function longPressMenu() {
    const TARGET = ".row.trk, .card[data-track], .poster[data-track], .top-card[data-track], .qrow";
    let p = null, firedAt = 0;
    const clear = () => { if (p) { clearTimeout(p.timer); p.el.classList.remove("held"); } p = null; };
    function menuFor(el) {
      if (el.classList.contains("qrow")) { openActions(el.dataset.track, "queue", Number(el.dataset.qi)); return; }
      const b = el.matches("[data-play][data-list]") ? el : $("[data-play][data-list]", el);
      if (b) openActions(el.dataset.track, b.dataset.list, Number(b.dataset.play));
    }
    function fire(el) {
      if (Date.now() - firedAt < 700) return;
      firedAt = Date.now();
      menuFor(el);
    }
    document.addEventListener("pointerdown", (e) => {
      clear();
      if (e.pointerType === "mouse" && e.button !== 0) return;
      const el = e.target.closest(TARGET);
      if (!el || !el.dataset.track || e.target.closest(".keep, .more-btn, [data-q-grip], input")) return;
      const q = { el, x: e.clientX, y: e.clientY, id: e.pointerId, fired: false, timer: 0 };
      q.timer = setTimeout(() => { q.fired = true; el.classList.add("held"); fire(el); }, 500);
      p = q;
    }, true);
    document.addEventListener("pointermove", (e) => {
      if (p && e.pointerId === p.id && !p.fired && Math.hypot(e.clientX - p.x, e.clientY - p.y) > 10) clear();
    }, true);
    for (const type of ["pointerup", "pointercancel"]) {
      document.addEventListener(type, (e) => {
        if (!p || e.pointerId !== p.id) return;
        if (p.fired && type === "pointerup") swallowClick();
        clear();
      }, true);
    }
    // a scroll that moves the pressed song (the page, its list) is the finger scrolling, not holding
    window.addEventListener("scroll", (e) => {
      if (p && !p.fired && (e.target === document || e.target === window || e.target.contains?.(p.el))) clear();
    }, { passive: true, capture: true });
    // with a mouse, a drag on a row's artist link or art would start a native drag and end the swipe / press
    document.addEventListener("dragstart", (e) => { if (e.target.closest?.(TARGET)) e.preventDefault(); });
    document.addEventListener("contextmenu", (e) => {
      const el = e.target.closest(TARGET);
      if (!el || !el.dataset.track) return;
      e.preventDefault();
      fire(el);
    });
  })();

  // ---------- row swipes (§8.2) ----------
  // On song rows in lists (not the Quick picks grid, which scrolls sideways): right and left each run an action
  // (default Play next / Add to queue; Server sheet → Row swipes). The row follows the finger 1:1 up to 115 px (then a
  // third), a coloured band with the action's icon opens under it, and past that point (or a flick) the action runs as
  // the row springs back; queue actions toast with Undo. A drag from the left 24 px is Safari's back swipe (drag()).
  const swipe = { prefs: SH.swipePrefs(store.get("swipe", null)) };
  const SWIPE_UI = { next: ["Play next", "play-next"], queue: ["Add to queue", "queue-add"], like: ["Like", "heart"], keep: ["Keep", "plus"] };
  const SWIPE_LABEL = { next: "Next", queue: "Queue", like: "Like", keep: "Keep", off: "Off" };
  function swipeBand(side, act, usable, t) {
    if (act === "off" || !SWIPE_UI[act]) return "";
    let [label, ic] = SWIPE_UI[act];
    if (act === "like" && likedIds.has(t.id)) label = "Unlike";
    return `<span class="sb sb-${side} sa-${act}${usable ? "" : " no"}"><span class="sb-in">${icon(ic)}<span>${label}</span></span></span>`;
  }
  drag($(".app"), {
    y: false,
    ignore: (e) => {
      const r = e.target.closest(".rows > .row.trk");
      return !r || !$(".row-hit[data-play]", r) || !!e.target.closest("input");
    },
    onStart(g) {
      const row = g.target.closest(".row.trk"), b = $(".row-hit[data-play]", row);
      const t = findTrack(row.dataset.track, b.dataset.list, Number(b.dataset.play));
      if (!t) return;
      const c = { placeholder: M.isPlaceholder(t), playing: cur()?.id === t.id, keep: keepState(t.id), canKeep: canKeep(t.id) };
      Object.assign(g, { row, t, list: b.dataset.list, w: row.offsetWidth,
        acts: { r: SH.swipeAction(swipe.prefs, 1), l: SH.swipeAction(swipe.prefs, -1) } });
      g.ok = { r: SH.swipeUsable(g.acts.r, c), l: SH.swipeUsable(g.acts.l, c) };
      $(".row-under", row)?.remove();
      row.insertAdjacentHTML("afterbegin", `<span class="row-under" aria-hidden="true">${swipeBand("r", g.acts.r, g.ok.r, t)}${swipeBand("l", g.acts.l, g.ok.l, t)}</span>`);
      row.classList.remove("settling");
      row.classList.add("swiping");
    },
    onMove(g) {
      if (!g.row) return;
      const side = g.dx > 0 ? "r" : "l";
      const x = g.ok[side] ? SH.rowOffset(g.dx, g.w) : PC.resist(g.dx, 0.2);
      g.row.style.setProperty("--sx", `${x}px`);
      g.row.dataset.side = side;
      g.row.classList.toggle("armed", g.ok[side] && SH.rowArmed(g.dx, g.w));
    },
    onEnd(g, cancelled) {
      const row = g.row;
      if (!row) return;
      const dir = cancelled ? 0 : SH.rowCommit(g.dx, g.vx, g.w), side = dir > 0 ? "r" : "l";
      const act = dir && g.ok[side] ? g.acts[side] : null;
      row.classList.remove("swiping");
      const done = () => {
        if (row.classList.contains("swiping")) return; // a new swipe on the same row took over
        row.classList.remove("armed", "settling");
        row.style.removeProperty("--sx");
        delete row.dataset.side;
        $(".row-under", row)?.remove();
      };
      if (reducedMotion()) done();
      else { row.classList.add("settling"); row.style.setProperty("--sx", "0px"); setTimeout(done, 340); }
      if (!act) return;
      if (act === "next" || act === "queue") queueWithUndo(slim(g.t), g.list, act);
      else if (act === "like") toggleLike(slim(g.t), { undo: true });
      else if (act === "keep") keep(g.t.id, g.list);
    },
  });
  function setSwipePref(dir, act) {
    if (!["right", "left"].includes(dir) || !SH.SWIPE_ACTIONS.includes(act)) return;
    swipe.prefs = SH.swipePrefs({ ...swipe.prefs, [dir]: act });
    store.set("swipe", swipe.prefs);
    if (!$("#status-sheet").hidden) renderStatus();
  }
  const swipePicker = () => `<li class="st-theme st-swipe"><span class="k">Row swipes</span>
      ${["right", "left"].map((d) => `<span class="hint">Swipe ${d}</span><div class="seg" role="radiogroup" aria-label="Swipe ${d}">${SH.SWIPE_ACTIONS.map((k) =>
        `<button role="radio" aria-checked="${swipe.prefs[d] === k}" class="${swipe.prefs[d] === k ? "on" : ""}" data-set-swipe="${d}:${k}">${SWIPE_LABEL[k]}</button>`).join("")}</div>`).join("")}
      <span class="hint">On song rows. A long press on any song opens its menu.</span></li>`;

  // ---------- Song info (§8.3) ----------
  // Details, Technical (source, the stream's codec / bitrate / sample rate once it has been resolved, the kept file and
  // its format, ReplayGain, where the lyrics came from) and ids with copy buttons. GET /api/stream/<id>/info never
  // resolves a stream: before a song has played its stream is "known once it plays".
  const sinfo = { t: null, data: null, lyrics: null, seq: 0 };
  function openSongInfo(t, { ret = null } = {}) {
    sinfo.t = M.isPlaceholder(t) ? t : slim(t);
    sinfo.data = null;
    sinfo.lyrics = null;
    const seq = ++sinfo.seq;
    renderSongInfo();
    $("#info-body").scrollTop = 0;
    openSheet("#info-sheet", { ret });
    if (M.isPlaceholder(t)) return;
    api(`/api/stream/${encodeURIComponent(t.id)}/info`)
      .then((d) => { if (seq === sinfo.seq) { sinfo.data = d; renderSongInfo(); } })
      .catch((e) => { if (seq === sinfo.seq) { sinfo.data = { error: e }; renderSongInfo(); } });
    const lp = lyr.cache.get(t.id); // only what's already loaded: no lyrics lookup from here
    if (lp) lp.then((d) => {
      if (seq !== sinfo.seq) return;
      sinfo.lyrics = d?.error ? "Couldn't load" : d?.provider ? LY.sourceLabel(d.provider) + (d.words ? " · word timing" : d.synced ? " · synced" : "")
        : d?.instrumental ? "Instrumental" : "None found";
      renderSongInfo();
    });
  }
  function renderSongInfo() {
    const t = sinfo.t;
    if (!t) return;
    const d = sinfo.data, s = d?.stream, k = d?.kept, ph = M.isPlaceholder(t), src = srcKey(t.id), loading = !ph && !d;
    $("#info-head").innerHTML = `<span class="mh-art">${art(t.id)}</span>
      <span class="mh-text"><span class="mh-t" id="info-title">${esc(t.title)}</span><span class="mh-s">${xBadge(t)}${esc(t.channel || t.artist || "")}</span>
        <span class="mh-badge">${esc(sourceTag(t))}</span></span>`;
    const kv = (label, value, { copy = "", mono = false } = {}) => (value ? `<div class="kv"><span class="k">${label}</span>
        <span class="v${mono ? " mono" : ""}">${value}</span>${copy ? `<button class="kv-copy" data-copy="${esc(copy)}" aria-label="Copy ${label.toLowerCase()}">${icon("copy")}</button>` : ""}</div>` : "");
    const card = (title, rows) => (rows.join("") ? `<h4 class="sh-label">${title}</h4><div class="kvs">${rows.join("")}</div>` : "");
    const names = (Array.isArray(t.artists) ? t.artists : []).filter((a) => a?.name);
    const ks = keepState(t.id), playingNow = cur()?.id === t.id;
    const via = playingNow && viaLibrary() ? "Your library (Navidrome)" : "";
    const lyrText = sinfo.lyrics || (playingNow && lyr.id === t.id && lyr.data?.provider ? LY.sourceLabel(lyr.data.provider) : ph ? "" : "Not loaded yet");
    const details = [
      kv("Title", esc(t.title)), kv("Artist", esc(names.length ? names.map((a) => a.name).join(", ") : t.channel || t.artist || "")),
      kv("Album", esc(t.album || "")), kv("Length", t.duration ? fmt(t.duration) : ""), kv("Explicit", t.explicit ? "Yes" : ""),
      kv("Liked", likedIds.has(t.id) ? "Yes" : ""),
      kv("In library", ks === "kept" ? (k?.kept_at ? `Kept ${esc(timeAgo(k.kept_at))}` : "Yes") : ks === "pending" ? "Keeping…" : ""),
    ];
    const tech = [
      kv("Source", esc(sourceTag(t))), kv("Playing from", esc(via)),
      kv("Stream", loading ? "…" : d?.error ? "Couldn't ask Reef" : s ? esc(SH.techLine(s) || (s.ext || "").toUpperCase() || "Unknown")
        : ph ? "" : d?.test_audio ? "Test audio (a local file)" : "Known once it plays"),
      kv("Stream size", s ? SH.fmtSize(s.size) : ""), kv("Player client", s?.client && s.client !== "-" ? esc(s.client) : ""),
      kv("Kept file", k?.file ? esc(k.file) + (k.exists === false ? " (missing)" : "") : "", { mono: true }),
      kv("File format", k?.file ? esc(SH.techLine(k) || (k.format || "").toUpperCase()) : ""),
      kv("File size", k ? SH.fmtSize(k.size) : ""), kv("ReplayGain", k?.replaygain ? esc(k.replaygain) : ""),
      kv("Lyrics", esc(lyrText)),
    ];
    const ids = [
      kv("Track id", esc(t.id), { copy: t.id, mono: true }), kv("Album id", esc(t.album_id || ""), { copy: t.album_id || "", mono: true }),
      ...linkable(t).map((a) => kv(`Artist id · ${esc(a.name)}`, esc(a.id), { copy: a.id, mono: true })),
      kv("Navidrome id", esc(ndIdOf(t) || k?.nd_id || ""), { copy: ndIdOf(t) || k?.nd_id || "", mono: true }),
      kv("Replacement for", esc(t.replaced_from || ""), { copy: t.replaced_from || "", mono: true }),
      kv("Upgraded from", esc(k?.upgraded_from && k.upgraded_from !== t.id ? k.upgraded_from : ""), { mono: true }),
      kv("Match key", esc(SH.fixKey(t) || ""), { copy: SH.fixKey(t) || "", mono: true }),
    ];
    const yt = src === "yt" && !ph;
    $("#info-body").innerHTML = card("Details", details) + card("Technical", tech) + card("Ids", ids)
      + (yt ? `<div class="actions joined h info-acts"><button class="pill-btn" data-copy="${esc(ytLink(t))}" data-done="Link copied">${icon("link")}Copy link</button>
          <a class="pill-btn" href="${esc(ytLink(t))}" target="_blank" rel="noopener">${icon("external")}YouTube</a></div>` : "");
  }
  $("#info-body").addEventListener("click", (e) => {
    const b = e.target.closest("[data-copy]");
    if (b) copyText(b.dataset.copy, b.dataset.done || "Copied");
  });

  // ---------- Fix wrong match (§8.4) ----------
  // For a song Reef picked (an import's placeholder or match, a dead-track replacement) and the song in the player or
  // queue: paste the right YouTube / YouTube Music link, or pick from YouTube Music's top 5 (scored like /api/match).
  // The choice is pinned under the song's match key (PUT /api/match/<key>: imports and later lookups use it, the
  // automatic matcher never overwrites it) and swapped in where the song sits in the queue (playing on if it's current).
  const fix = { t: null, key: null, pinned: null, seq: 0, busy: false, pending: null };
  function openFix(t, { ret = null } = {}) {
    fix.t = M.isPlaceholder(t) ? t : slim(t);
    fix.key = SH.fixKey(t);
    fix.pinned = null;
    $("#fix-link").value = "";
    $("#fix-q").value = `${t.title || ""} ${t.artist || t.channel || ""}`.trim();
    renderFixTrack();
    openSheet("#fix-sheet", { ret });
    runFixSearch("");
  }
  function renderFixTrack() {
    const t = fix.t;
    if (!t) return;
    const picked = M.isPlaceholder(t) || SH.fixKey(t) || t.replaced_from;
    const note = M.isPlaceholder(t) ? `Reef hasn't found “${esc(t.title)}” yet. The song you choose plays for it from now on, imports included.`
      : picked ? `Reef picked this for “${esc(t.title)}”. The song you choose replaces it here and for good.`
      : `The song you choose plays here instead, and whenever Reef looks up “${esc(t.title)}”.`;
    $("#fix-track").innerHTML = `<div class="fix-card"><span class="row-art">${art(t.id)}</span><span class="row-text">
        <span class="row-t">${esc(t.title)}</span><span class="row-s">${esc([t.channel || t.artist, sourceTag(t)].filter(Boolean).join(" · "))}</span></span></div>
      <p class="st-note">${note}</p>${fix.pinned ? `<p class="st-note ok">Pinned now: “${esc(fix.pinned.title)}”${fix.pinned.channel ? ` · ${esc(fix.pinned.channel)}` : ""}</p>` : ""}`;
  }
  const fixQuery = (t) => ({ title: t.title || "", artist: t.artist || t.channel || "", duration: t.duration || null, ...(t.album ? { album: t.album } : {}) });
  function runFixSearch(q) {
    const t = fix.t;
    if (!t) return null;
    const seq = ++fix.seq, box = $("#fix-results");
    box.innerHTML = skRows(3);
    const p = api("/api/match/candidates", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...fixQuery(t), ...(fix.key ? { key: fix.key } : {}), ...(q ? { q } : {}) }) })
      .then((r) => {
        if (seq !== fix.seq) return;
        fix.key = r.key || fix.key;
        fix.pinned = r.pinned || null;
        renderFixTrack();
        const list = r.candidates || [];
        box.innerHTML = list.length ? `<div class="fix-list">${list.map(({ track: c, score, ranked }) => `<button class="fix-res" data-fix-pick="${esc(c.id)}"
            data-title="${esc(c.title)}" data-artist="${esc(c.channel || "")}"><span class="row-art">${art(c.id)}</span>
            <span class="row-text"><span class="row-t">${esc(c.title)}</span><span class="row-s">${esc([c.channel, c.album, c.duration ? fmt(c.duration) : ""].filter(Boolean).join(" · "))}</span></span>
            <span class="fix-tags">${c.id === t.id ? `<span class="lyr-badge">This one</span>` : ""}${fix.pinned?.id === c.id ? `<span class="lyr-badge on">Pinned</span>` : ""}<span class="fix-score num">${SH.scorePct(ranked ?? score)}</span></span></button>`).join("")}</div>`
          : `<p class="st-note${r.error ? " bad" : ""}">${esc(r.error ? `Couldn't search: ${r.error}` : "Nothing on YouTube Music for that. Try other words, or paste a link above.")}</p>`;
      })
      .catch((e) => { if (seq === fix.seq) box.innerHTML = `<p class="st-note bad">${esc(e.message || "Couldn't search")}</p>`; });
    fix.pending = p;
    return p;
  }
  async function pinFix(raw, hint = null) {
    const t = fix.t;
    if (!t || fix.busy || !raw) return;
    if (!fix.key && fix.pending) await fix.pending;
    if (!fix.key) { toast("Couldn't reach Reef"); return; }
    fix.busy = true;
    $("#fix-sheet").classList.add("busy");
    let r = null;
    try {
      r = await api(`/api/match/${encodeURIComponent(fix.key)}`, { method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id: raw, title: hint?.title || t.title || "", artist: hint?.artist || t.artist || t.channel || "" }) });
    } catch (e) {
      toast(e.message || "Couldn't use that");
    }
    fix.busy = false;
    $("#fix-sheet").classList.remove("busy");
    if (r?.track?.id) applyFix(t, r.track, fix.key);
  }
  function applyFix(old, track, key) {
    noteKept([track]);
    const nt = { ...slim(track), mkey: key };
    closeSheet("#fix-sheet");
    if (nt.id === old.id) { toast("Pinned: this is the song from now on"); return; }
    if (state.queue.some((x) => x.id === old.id)) {
      const wasCur = cur()?.id === old.id;
      swapEverywhere(old.id, nt);
      if (wasCur) load(); else queueChanged();
    }
    if (state.view === "detail" && detail.kind === "local") loadPage(detail.key, { force: true }); // the import's row
    toast(`Fixed: “${nt.title}” plays for this song now`);
  }
  $("#fix-link-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const v = $("#fix-link").value.trim();
    if (!v) { $("#fix-link").focus(); return; }
    $("#fix-link").blur();
    pinFix(v);
  });
  $("#fix-search-form").addEventListener("submit", (e) => {
    e.preventDefault();
    $("#fix-q").blur();
    runFixSearch($("#fix-q").value.trim());
  });
  $("#fix-results").addEventListener("click", (e) => {
    const b = e.target.closest("[data-fix-pick]");
    if (b) pinFix(b.dataset.fixPick, { title: b.dataset.title, artist: b.dataset.artist });
  });

  // ---------- Up next (§4.7, §7) ----------
  // The sheet rests as the 60 px peek strip at the bottom of Now Playing and pulls up over it: f = 0 (peek) .. 1 (open)
  // drives its position, the player's fade (1 - f/.4), the art's scale (1 - .1f) and the sheet's top radius (28 -> 0).
  // A drag on its top (or on the list, once scrolled to the top) moves it; a flick or the threshold decides (PC.pullSettle).
  const qsheet = $("#qsheet"), qBody = $("#q-body");
  const qs = { f: 0, open: false, anim: 0, busy: false, pending: false };
  const qUp = () => npEl.classList.contains("q-up");
  const qRange = () => Math.max(1, npEl.clientHeight - $(".np-peek-space").offsetHeight);
  function setQF(f) {
    qs.f = PC.clamp(f, 0, 1);
    const look = PC.pullLook(qs.f);
    qsheet.style.transform = qs.f > 0 ? `translate3d(0, ${(1 - qs.f) * qRange()}px, 0)` : "";
    qsheet.style.setProperty("--q-radius", `${look.radius}px`);
    qsheet.style.setProperty("--q-f", String(qs.f));
    npEl.style.setProperty("--q-fade", String(look.fade));
    npEl.style.setProperty("--q-scale", String(look.scale));
  }
  function finishQ(open) {
    qs.open = open;
    npEl.classList.toggle("q-open", open);
    npEl.classList.toggle("q-up", open);
    $("#np-queue").classList.toggle("on", open);
    qsheet.setAttribute("aria-modal", String(open));
    if (!open) setQF(0);
    if (open && qs.pending) renderQueue();
  }
  function animQ(open) {
    cancelAnimationFrame(qs.anim);
    const from = qs.f, to = open ? 1 : 0;
    if (reducedMotion() || from === to) {
      setQF(to);
      finishQ(open);
      if (open && reducedMotion() && from === 0 && qsheet.animate) qsheet.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 200 });
      return;
    }
    const t0 = performance.now();
    const step = (now) => {
      const k = PC.settle(now - t0, 380);
      setQF(from + (to - from) * k);
      if (k < 1) qs.anim = requestAnimationFrame(step); else finishQ(open);
    };
    qs.anim = requestAnimationFrame(step);
  }
  // Paint the full sheet before it shows (the first pull, the Queue button).
  function prepQ() {
    if (qUp()) return;
    npEl.classList.add("q-up");
    renderQueue(true);
    scrollQueueToCurrent();
  }
  function openQ() { if (!cur()) return; prepQ(); animQ(true); }
  function closeQ(instant) {
    if (!qUp() && !qs.f) return;
    if (instant) { cancelAnimationFrame(qs.anim); finishQ(false); return; }
    animQ(false);
  }
  // Opening puts the current song 1.5 rows from the top, so a bit of what played before shows above it.
  function scrollQueueToCurrent() {
    const row = $(".qrow.cur", qBody);
    qBody.scrollTop = row ? Math.max(0, row.offsetTop - 1.5 * row.offsetHeight) : 0;
  }
  const openQueue = openQ;

  for (const zone of [$("#qs-peek"), $("#qs-grab")]) {
    let f0 = 0;
    drag(zone, {
      x: false,
      onStart() { cancelAnimationFrame(qs.anim); f0 = qs.f; prepQ(); },
      onMove(g) { setQF(f0 - g.dy / qRange()); },
      onEnd(g, cancelled) { animQ(cancelled ? f0 >= 1 : PC.pullSettle(f0 >= 1, g.dy, g.vy, npEl.clientHeight)); },
    });
  }
  $("#qs-peek").addEventListener("click", openQ);
  $("#qs-close").addEventListener("click", () => closeQ());

  // The list, scrolled to its top, pulled further down collapses the sheet (touch events: the list scrolls natively).
  (function listPull() {
    let p = null;
    qBody.addEventListener("touchstart", (e) => {
      p = qs.open && e.touches.length === 1 && qBody.scrollTop <= 0 ? { y: e.touches[0].clientY, pull: false, s: [] } : null;
    }, { passive: true });
    qBody.addEventListener("touchmove", (e) => {
      if (!p || qs.busy) return;
      const y = e.touches[0].clientY, dy = y - p.y;
      p.s.push({ t: e.timeStamp, x: 0, y });
      if (p.s.length > 24) p.s.shift();
      if (!p.pull) {
        if (dy < 0 || qBody.scrollTop > 0) { p = null; return; } // scrolling the list
        e.preventDefault(); // at the top, going down: ours (before Safari starts a scroll it won't give back)
        if (dy < 8) return;
        p.pull = true;
        p.y = y;
        cancelAnimationFrame(qs.anim);
      }
      e.preventDefault();
      setQF(1 - Math.max(0, y - p.y) / qRange());
    }, { passive: false });
    const end = (cancelled) => {
      if (p?.pull) {
        const v = PC.velocity(p.s);
        animQ(cancelled || PC.pullSettle(true, (1 - qs.f) * qRange(), v.vy, npEl.clientHeight));
      }
      p = null;
    };
    qBody.addEventListener("touchend", () => end(false));
    qBody.addEventListener("touchcancel", () => end(true));
  })();

  function renderQueueModes() {
    const el = $("#q-modes");
    if (!el || !qUp()) return;
    const any = state.queue.length - state.index - 1 > 0;
    el.innerHTML = `<button class="chip sm${state.shuffle ? " on" : ""}" data-q-mode="shuffle" aria-pressed="${state.shuffle}">${icon("shuffle")}Shuffle</button>
      <button class="chip sm" data-q-clear${any ? "" : " disabled"}>${icon("x")}Clear</button>
      <button class="chip sm" data-q-save>${icon("queue-add")}Save as playlist</button>
      <label class="qs-auto"><span class="qs-auto-l">${icon("infinity")}<span>Autoplay<small>Endless radio when the queue ends</small></span></span>
        <input type="checkbox" class="sw" data-q-auto${autoplay.on ? " checked" : ""} aria-label="Autoplay"></label>`;
  }
  $("#q-modes").addEventListener("change", (e) => { if (e.target.matches("[data-q-auto]")) setAutoplay(e.target.checked); });

  // Rows (§7): the current one rounder on the cover's tonal colour with the equaliser, the rest on black .55; what played
  // before sits above it, dimmed. Swipe left = Play next, right = Remove (with Undo); the handle drags to reorder.
  function qRow(t, i, kind) {
    const title = esc(t.title);
    return `<li class="qrow ${kind}" data-qi="${i}" data-track="${esc(t.id)}">
      ${kind === "cur" ? "" : `<span class="qrow-under" aria-hidden="true"><span class="qu-next">${icon("play-next")}Play next</span><span class="qu-rm">Remove${icon("x")}</span></span>`}
      <span class="qrow-fg"><span class="row-art">${art(t.id)}${kind === "cur" ? `<span class="eq"><i></i><i></i><i></i></span>` : ""}</span>
        <span class="row-text">${kind === "cur" ? `<span class="row-t">${title}</span>` : `<button class="row-hit" data-q-jump="${i}" aria-label="Play ${title}"><span class="row-t">${title}</span></button>`}<span class="row-s">${trackSub(t)}</span></span>
        ${kind === "up" ? `<button class="qrow-grip" data-q-grip aria-label="Drag to reorder ${title}">${icon("grip")}</button>` : ""}</span></li>`;
  }
  function renderQueue(force) {
    paintUpNext();
    if (!force && !qUp()) return;
    if (qs.busy) { qs.pending = true; return; } // a row is being swiped or dragged: paint once it lets go
    qs.pending = false;
    const t = cur();
    const stats = PC.queueStats(state.queue);
    $("#qs-from").textContent = playingFrom();
    $("#qs-stats").textContent = t ? [songs(stats.count), fmtLen(stats.seconds)].filter(Boolean).join(" · ") : "";
    renderQueueModes();
    if (!t) { qBody.innerHTML = emptyState("note", "Nothing playing", "Play a song to start a queue."); return; }
    const { manual, auto, at } = Q.upcomingParts(state.queue, state.index);
    const hist = state.queue.slice(0, state.index);
    const empty = state.repeat === "all" && state.queue.length > 1 ? "Repeat is on: the queue starts over after this."
      : autoplay.on && state.repeat === "off" && autoplay.inflight ? "Autoplay is finding similar songs…"
      : "Nothing queued. Use ⋯ on a song to add it.";
    const top = qBody.scrollTop;
    qBody.innerHTML = (hist.length ? `<ul class="qlist hist">${hist.map((x, k) => qRow(x, k, "hist")).join("")}</ul>` : "")
      + `<h4 class="q-label">Now playing</h4><ul class="qlist">${qRow(t, state.index, "cur")}</ul>`
      + (manual.length || !auto.length ? `<h4 class="q-label">${state.from ? `Next from ${esc(state.from.replace(/^From /, ""))}` : "Next in queue"}</h4>` : "")
      + (manual.length ? `<ul class="qlist" data-block="manual">${manual.map((x, k) => qRow(x, state.index + 1 + k, "up")).join("")}</ul>`
        : auto.length ? "" : `<p class="q-empty">${empty}</p>`)
      + (auto.length ? `<h4 class="q-label q-auto">${icon("infinity")}Autoplay suggestions</h4>
        <ul class="qlist" data-block="auto">${auto.map((x, k) => qRow(x, at + k, "up")).join("")}</ul>` : "");
    qBody.scrollTop = top;
  }

  // Swipe a row: the first 60 px move at most 20 (then 1:1); past 40% (or a flick) left = Play next, right = Remove.
  drag(qBody, {
    y: false,
    ignore: (e) => !e.target.closest(".qrow:not(.cur)") || !!e.target.closest("[data-q-grip]"),
    onStart(g) { qs.busy = true; g.row = g.target.closest(".qrow"); g.row.classList.add("swiping"); },
    onMove(g) {
      g.row.dataset.dir = g.dx < 0 ? "next" : "rm";
      $(".qrow-fg", g.row).style.transform = `translate3d(${PC.rubber(g.dx, 60, 20)}px, 0, 0)`;
    },
    onEnd(g, cancelled) {
      const row = g.row, fg = $(".qrow-fg", row), id = row.dataset.track;
      row.classList.remove("swiping");
      const dir = cancelled ? 0 : PC.swipeCommit(g.dx, g.vx, row.offsetWidth, { frac: 0.4, vmin: 1200 });
      const settle = () => { qs.busy = false; if (qs.pending) renderQueue(); };
      if (dir > 0 && !reducedMotion()) {
        fg.style.transform = `translate3d(${row.offsetWidth}px, 0, 0)`;
        setTimeout(() => { qs.busy = false; removeWithUndo(id); }, 180);
        return;
      }
      fg.style.transform = "";
      if (dir > 0) { qs.busy = false; removeWithUndo(id); return; }
      setTimeout(() => { delete row.dataset.dir; settle(); if (dir < 0) playNextFrom(id); }, dir < 0 ? 200 : 300);
    },
  });

  // Drag a row by its handle to reorder it within its block (what you queued, or the autoplay block). The lifted row
  // follows the finger with a shadow, the others make room; near the list's edges it scrolls.
  drag(qBody, {
    ignore: (e) => !e.target.closest("[data-q-grip]"),
    onStart(g) {
      const row = g.target.closest(".qrow"), list = row.parentElement, rows = [...list.children];
      qs.busy = true;
      Object.assign(g, {
        row, list, rows, from: Number(row.dataset.qi), to: Number(row.dataset.qi),
        lo: Number(rows[0].dataset.qi), hi: Number(rows[rows.length - 1].dataset.qi),
        rowH: rows.length > 1 ? rows[1].offsetTop - rows[0].offsetTop : row.offsetHeight + 6, scroll0: qBody.scrollTop, raf: 0,
      });
      row.classList.add("lifted");
      list.classList.add("sorting");
      const loop = () => { // edge auto-scroll while the finger rests near the top or bottom
        const r = qBody.getBoundingClientRect(), y = g.y0 + g.dy;
        const v = y < r.top + 56 ? -8 : y > r.bottom - 56 ? 8 : 0;
        if (v) { qBody.scrollTop += v; place(g); }
        g.raf = requestAnimationFrame(loop);
      };
      g.raf = requestAnimationFrame(loop);
    },
    onMove(g) { place(g); },
    onEnd(g, cancelled) {
      cancelAnimationFrame(g.raf);
      for (const r of g.rows) r.style.transform = "";
      g.row.classList.remove("lifted");
      g.list.classList.remove("sorting");
      qs.busy = false;
      if (!cancelled && g.to !== g.from && state.queue[g.from]?.id === g.row.dataset.track) {
        const r = PC.moveTrack(state.queue, state.index, g.from, g.to);
        state.queue = r.queue; state.index = r.index;
        queueChanged();
      } else if (qs.pending) renderQueue();
    },
  });
  function place(g) {
    const dy = g.dy + (qBody.scrollTop - g.scroll0);
    g.to = PC.dropIndex(g.from, dy, g.rowH, g.lo, g.hi);
    g.row.style.transform = `translate3d(0, ${dy}px, 0)`;
    for (const r of g.rows) {
      if (r === g.row) continue;
      const k = Number(r.dataset.qi);
      const shift = g.from < k && k <= g.to ? -g.rowH : g.to <= k && k < g.from ? g.rowH : 0;
      r.style.transform = shift ? `translate3d(0, ${shift}px, 0)` : "";
    }
  }

  // Remove with Undo: the song goes back where it was, relative to the current one (PC.reinsert).
  // `at`: the track's id (a row acting after its animation: the queue may have shifted) or its queue index.
  const qIndex = (at) => (typeof at === "string" ? state.queue.findIndex((x) => x.id === at) : at);
  function removeWithUndo(at) {
    const i = qIndex(at), t = state.queue[i];
    if (!t || i === state.index) return;
    const rel = i - state.index;
    const r = Q.removeAt(state.queue, state.index, i);
    state.queue = r.queue; state.index = r.index;
    queueChanged();
    toast(`Removed “${t.title}”`, { action: "Undo", onAction: () => {
      const u = PC.reinsert(state.queue, state.index, t, rel);
      if (u.queue.length === state.queue.length) return;
      state.queue = u.queue; state.index = u.index;
      queueChanged();
    } });
  }
  // Play next from the queue itself (also a song that already played: it moves, ids stay unique). An autoplay song
  // moved up becomes one you queued.
  function playNextFrom(at) {
    const i = qIndex(at), t = state.queue[i];
    if (!t || i === state.index) return;
    const plain = { ...t };
    delete plain.auto;
    let q = state.queue, idx = state.index;
    if (i < idx) { const r = Q.removeAt(q, idx, i); q = r.queue; idx = r.index; } // an upcoming copy is moved by playNext
    const r = Q.playNext(q, idx, plain, state.shuffle ? state.original : null);
    state.queue = r.queue; state.index = idx;
    if (state.shuffle) state.original = r.original;
    queueChanged();
    toast(`Playing next: “${t.title}”`);
  }

  // ---------- toast ----------
  // toast(msg) or toast(msg, {action: "Undo", onAction}) with a button (5 s).
  let toastTimer, toastAct = null;
  function toast(msg, { action = "", onAction = null } = {}) {
    const el = $("#toast");
    toastAct = action && onAction ? onAction : null;
    el.textContent = msg;
    if (toastAct) el.insertAdjacentHTML("beforeend", `<button class="toast-act" type="button">${esc(action)}</button>`);
    el.classList.toggle("has-act", !!toastAct);
    el.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { el.classList.remove("show"); toastAct = null; },
      toastAct ? 5000 : Math.max(2600, String(msg).length * 55)); // long hints stay longer
  }
  $("#toast").addEventListener("click", (e) => {
    if (!e.target.closest(".toast-act") || !toastAct) return;
    const f = toastAct;
    toastAct = null;
    $("#toast").classList.remove("show");
    f();
  });

  // ---------- events ----------
  document.addEventListener("click", (e) => {
    const t = e.target.closest("[data-keep],[data-play],[data-search],[data-source],[data-retry-search],[data-retry-home],[data-retry-history],[data-retry-playlists],[data-retry-library],[data-set-theme],[data-set-swipe],[data-filter],[data-dismiss],[data-play-all],[data-shuffle-all],[data-close],[data-clear-finished],[data-clear-searches],[data-more],[data-act],[data-q-jump],[data-q-remove],[data-q-clear],[data-q-mode],[data-q-save],[data-npbg],[data-open-bg],[data-sleep],[data-fill],[data-keep-all],[data-retry],[data-page-radio],[data-artist-hide],[data-unhide],[data-unhide-page],[data-open-hidden],[data-yt-reset],[data-copy-diag],[data-about],[data-mix],[data-st],[data-import-resolve]");
    if (!t) return;
    const d = t.dataset;
    if ("keep" in d) { e.preventDefault(); if (d.keep) keep(d.keep, d.list); return; }
    if ("more" in d) { openActions(d.more, d.list, Number(d.i)); return; }
    if ("act" in d) { if (!t.disabled) runAction(d.act, d.arg); return; }
    if ("keepAll" in d) { keepAll(); return; }
    if ("retry" in d) { detail.errors.delete(detail.key); renderDetail(); loadPage(detail.key, { force: true }); return; }
    if ("retrySearch" in d) { if (state.query) runSearch(state.query, state.link ? { link: true } : {}); return; }
    if ("retryHome" in d) { state.homeErr = null; renderHome(); loadHome(); return; }
    if ("retryHistory" in d) { state.historyErr = null; renderKept(); loadHistory(); return; }
    if ("retryPlaylists" in d) { state.playlistsErr = null; renderLibPlaylists(); loadPlaylists(); return; }
    if ("retryLibrary" in d) { state.library = null; renderLibNavidrome(); loadHealth().then(loadLibrary); return; }
    if ("setTheme" in d) { setTheme(d.setTheme); return; }
    if ("setSwipe" in d) { const [dir, act] = d.setSwipe.split(":"); setSwipePref(dir, act); return; }
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
    if ("importResolve" in d) { t.disabled = true; resolveImport(d.importResolve, "retryMissing" in d); return; }
    if ("about" in d) {
      detail.about = !detail.about;
      $("#about")?.classList.toggle("clamp", !detail.about);
      t.textContent = detail.about ? "Less" : "More";
      t.setAttribute("aria-expanded", String(detail.about));
      return;
    }
    if ("qJump" in d) { jumpTo(Number(d.qJump)); return; }
    if ("qRemove" in d) { removeWithUndo(Number(d.qRemove)); return; }
    if ("qClear" in d) { clearUpcoming(); return; }
    if ("qSave" in d) { saveQueueAsPlaylist(); return; }
    if ("qMode" in d) {
      if (d.qMode === "shuffle") toggleShuffle(); else if (d.qMode === "autoplay") setAutoplay(!autoplay.on); else cycleRepeat();
      return;
    }
    if ("npbg" in d) { setBgStyle(d.npbg); return; }
    if ("openBg" in d) { openBgPicker(); return; }
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
  // Skips wait for the squish (§4.5), unless motion is reduced.
  $("#np-next").addEventListener("click", () => later(reducedMotion() ? 0 : SKIP_DELAY, next));
  $("#np-prev").addEventListener("click", () => later(reducedMotion() ? 0 : SKIP_DELAY, prev));
  $("#np-queue").addEventListener("click", () => (qs.open ? closeQ() : openQueue()));
  $("#np-sleep").addEventListener("click", openSleep);
  $("#np-lyr").addEventListener("click", () => setLyricsOn(!lyr.on));
  $("#np-shuffle").addEventListener("click", toggleShuffle);
  $("#np-repeat").addEventListener("click", cycleRepeat);
  $("#np-like").addEventListener("click", () => toggleLike());
  longPress($("#np-like"), () => openPlaylistPicker(cur()));
  for (const id of ["#np-more", "#np-more2"]) $(id).addEventListener("click", () => { if (cur()) openActions(cur().id, "player", state.index); });
  $("#np-from-btn").addEventListener("click", () => { if (state.fromRoute) openRoute(state.fromRoute); });
  longPress($("#np-t"), copyTrackName);
  longPress($("#np-s"), copyTrackName);
  $("#status-btn").addEventListener("click", openStatus);
  $("#top-back").addEventListener("click", goBack);
  $("#np-s").addEventListener("click", () => {
    const a = linkable(cur())[0];
    if (a) openRoute(`#artist/${encodeURIComponent(a.id)}`);
  });
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    if (topSheet()) closeSheet(topSheet());
    else if (lyr.on && npOpen()) setLyricsOn(false);
    else if (qs.open) closeQ();
    else if ($("#np").classList.contains("open")) closeNP();
  });

  const top = $("#top");
  window.addEventListener("scroll", () => {
    top.classList.toggle("scrolled", window.scrollY > 4);
    scrollAt.set(navDepth, window.scrollY);
    if (state.view === "detail") syncTopTitle();
  }, { passive: true });
  window.addEventListener("hashchange", route);

  // iOS Safari only applies :active (the press feedback) when a touch listener exists somewhere on the page.
  document.addEventListener("touchstart", () => {}, { passive: true });

  // ---------- boot ----------
  state.view = "";
  applyPalette(null);
  applyBg();
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
  setLyricsOnly(lyr.only);
  loadSources();
  loadSettings();
  loadHealth();
  loadHistory();
  loadLikes();
  loadTiles();
  pollJobs();
})();
