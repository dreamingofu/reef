"""Home from cache (`GET /api/home`, production plan P1.14).

The shelves (recommend.py) are built in the background and kept in `home.json`, so Home opens from disk at
once even when YouTube Music is slow or refusing. A build is due COMPLETE_TTL after a complete one, FALLBACK_TTL
after one that lost radio lanes, RATE_LIMIT_TTL (or the server's Retry-After) after a 429, and as soon as
LISTENS_TRIGGER new counted listens arrive, so Quick picks follow what's being played. One build runs at a time.
The very first request (no file yet) waits up to BUDGET for the build, else answers with the shelves that need
no network and `stale: true`.

What's stored is filtered again on every request (hidden artists, "Not interested" tracks), and Liked songs,
the Charts shelf (charts.py's cache, warmed by the refresher thread) and the mood tiles are read fresh (all cheap),
so none of these waits for a rebuild.

With personalisation on (personal.py), a build also stores YouTube Music's own home shelves for the kept visitor
id ("Quick picks", "Mixed for you", …); they're served after Reef's shelves (ids `ytm:<slug>`), minus anything a
shelf before them already shows. `GET /api/home?ytm=0` leaves them out.
"""
import logging
import threading
import time

import re

import blocklist
import charts
import config
import history
import likes
import listens
import personal
import recommend
import store
import tiles
import ytmusic

logger = logging.getLogger(__name__)

COMPLETE_TTL = 6 * 3600
FALLBACK_TTL = 30 * 60
RATE_LIMIT_TTL = 60
RETRY_AFTER_MAX = 6 * 3600
LISTENS_TRIGGER = 5
LONG_PLAY = 4 * 3600  # longest play new_listens looks back for
BUDGET = 8.0          # seconds the first request waits for the first build
BOOT_DELAY = 20       # after start-up: tiles and the first requests go first
TICK = 60
# id -> (title, kind), in the order the client shows them
SHELVES = {
    "quick": ("Quick picks", "tracks"),
    "on_repeat": ("On repeat", "tracks"),
    "mixed": ("Mixed for you", "seeds"),
    "jump_back": ("Jump back in", "tracks"),
    "liked": ("Liked songs", "tracks"),
    "top_artists": ("Your top artists", "artists"),
    # YouTube Music's own shelves (personalisation on) come here, in YouTube's order.
    "charts": ("Charts", "tracks"),
    "moods": ("Moods & genres", "moods"),
}
YTM_PREFIX = "ytm:"

_file = store.JsonFile(lambda: config.HOME_FILE, dict)
_lock = threading.Lock()   # held while a build runs
_guard = threading.Lock()  # "start a build and publish its event" as one step
_running = None            # threading.Event of the build in flight (set when it ends)
_retry_at = 0.0            # a build crashed: not before this (memory only)


def rate_limited(e):
    return getattr(getattr(e, "response", None), "status_code", None) == 429 or "HTTP 429" in str(e)


def retry_after(e):
    """Seconds to wait after a 429: the exception's Retry-After header, else the one ytmusic saw, else None."""
    headers = getattr(getattr(e, "response", None), "headers", None) or {}
    value = str(headers.get("Retry-After") or "").strip()
    return int(value) if value.isdigit() else ytmusic.retry_after()


def build(now=None, remote=True):
    """A home document: {generated_at, source: fresh|fallback, reason, next_at, ytm, seeds, errors, shelves}.
    `remote=False` skips the radio calls (the cold-start answer while the real build runs)."""
    now = time.time() if now is None else now
    items = listens.entries()
    kept = history.list_recent(None)
    blocked = blocklist.matcher()
    disliked = recommend.disliked_ids(now)
    seeds = recommend.seeds(items, kept, blocked, disliked)
    ytm = bool(ytmusic.enabled())
    picks, lanes, errors = [], [{"seed": s, "artists": []} for s in seeds], []
    wanted = bool(seeds and ytm)
    if wanted and remote:
        exclude = recommend.heard_since(items, now - recommend.FRESH_DAYS * recommend.DAY)
        exclude |= {h.get("id") for h in kept} | disliked
        picks, lanes, errors = recommend.quick_picks(seeds, ytmusic.radio, exclude, blocked,
                                                     recommend.affinity(items, now), stop=rate_limited)
    limited = next((e for e in errors if rate_limited(e)), None)
    if limited is not None:
        reason, wait = "rate_limited", min(retry_after(limited) or RATE_LIMIT_TTL, RETRY_AFTER_MAX)
    elif errors:
        reason, wait = "radio_failed", FALLBACK_TTL
    elif wanted and not remote:
        reason, wait = "local_only", 0
    else:
        reason, wait = None, COMPLETE_TTL
    shelves = {
        "quick": picks,
        "on_repeat": recommend.on_repeat(items, now),
        "mixed": recommend.mixes(lanes),
        "jump_back": recommend.jump_back_in(items, now),
        "top_artists": recommend.top_artists(items, now),
    }
    personalised = ytm and personal.active()
    doc = {"generated_at": now, "source": "fallback" if reason else "fresh", "reason": reason,
           "next_at": now + wait, "ytm": ytm, "personal": personalised, "seeds": len(seeds),
           "errors": [str(e)[:200] for e in errors[:3]],
           "shelves": [{"id": k, "kind": SHELVES[k][1], "items": v} for k, v in shelves.items() if v]}
    if personalised and remote and limited is None:
        try:
            doc["shelves"] += _ytm_shelves(ytmusic.home())
        except Exception as e:  # Reef's own shelves stand on their own
            doc["ytm_error"] = str(e)[:200]
            logger.warning(f"home: YouTube Music home failed: {str(e)[:200]}")
    return doc


def _ytm_shelves(rows):
    """YouTube Music home rows as stored shelves {id: "ytm:<slug>", title, kind, items}."""
    out, seen = [], set()
    for row in rows:
        slug = re.sub(r"[^a-z0-9]+", "-", row["title"].casefold()).strip("-")[:40] or str(len(out))
        sid = YTM_PREFIX + slug
        while sid in seen:
            sid += "-2"
        seen.add(sid)
        out.append({"id": sid, "title": row["title"], "kind": row["kind"], "items": row["items"]})
    return out


def load():
    """The stored document (shared: read only), or None before the first build."""
    with _file.lock:
        doc = _file.load()
        return doc if doc.get("generated_at") else None


def refresh(now=None):
    """Build and store now (callers normally go through refresh_async, which holds the one-build lock).
    A build that lost every radio lane keeps the previous Quick picks rather than showing none."""
    global _retry_at
    start = time.monotonic()
    try:
        doc = build(now)
    except Exception as e:
        _retry_at = time.time() + FALLBACK_TTL
        logger.warning(f"home: build failed: {str(e)[:200]}")
        return None
    prev = load()
    if doc["reason"] and prev and not any(s["id"] == "quick" for s in doc["shelves"]):
        quick = next((s for s in prev.get("shelves") or [] if s.get("id") == "quick"), None)
        if quick:
            doc["shelves"].insert(0, quick)
            doc["carried"] = ["quick"]
    _file.save(doc)
    logger.info(f"home refreshed source={doc['source']} reason={doc['reason']} seeds={doc['seeds']} "
                f"shelves={','.join(s['id'] for s in doc['shelves']) or '-'} "
                f"next_s={int(doc['next_at'] - doc['generated_at'])} ms={int((time.monotonic() - start) * 1000)}")
    return doc


def _run(ev):
    try:
        refresh()
    finally:
        _lock.release()
        ev.set()


def refresh_async():
    """Start a background build unless one is running. Returns the Event of the build in flight."""
    global _running
    with _guard:
        if not _lock.acquire(blocking=False):
            return _running
        ev = _running = threading.Event()
    threading.Thread(target=_run, args=(ev,), daemon=True, name="reef-home").start()
    return ev


def refreshing():
    return _lock.locked()


def new_listens(since):
    """Counted listens reported (ended) after `since`. A play is reported when it ends, so one that started
    before a build can still be new; the log is in start order, so the scan stops LONG_PLAY before `since`."""
    n = 0
    for e in reversed(listens.entries()):
        if e.get("started_at", 0) <= since - LONG_PLAY:
            break
        n += bool(e.get("counted") and (e.get("ended_at") or e.get("started_at", 0)) > since)
    return n


def due(doc, now=None):
    """Whether a rebuild is due. A 429's wait is honoured even if listens pile up meanwhile."""
    now = time.time() if now is None else now
    if now < _retry_at:
        return False
    if doc is None or now >= doc.get("next_at", 0):
        return True
    if doc.get("reason") == "rate_limited":
        return False
    ytm = bool(ytmusic.enabled())
    if doc.get("ytm") != ytm or bool(doc.get("personal")) != (ytm and personal.active()):
        return True
    n = new_listens(doc.get("generated_at", 0))
    return n >= LISTENS_TRIGGER or (n > 0 and not doc.get("seeds"))  # first listens: Quick picks can start


def tick(now=None):
    """Start a build when one is due (the refresher thread, new listens)."""
    if due(load(), now):
        return refresh_async()
    return None


def listened():
    """A counted listen came in: rebuild when enough have. Before the first build this is the refresher's
    (or the first GET's) job, which also keeps a bare test client from starting builds."""
    if load() is not None:
        tick()


def _filtered(s, blocked, disliked, shown):
    """A stored shelf's items minus hidden artists, "Not interested" tracks and (YouTube Music's shelves) what
    an earlier shelf already shows. Copies: the stored document is shared."""
    kind, items = s.get("kind"), s.get("items") or []
    dedupe = str(s.get("id", "")).startswith(YTM_PREFIX)
    if kind == "tracks":
        items = [dict(t) for t in items if t.get("id") not in disliked and not blocked.track(t)
                 and not (dedupe and t.get("id") in shown)]
    elif kind == "artists":
        items = [dict(a) for a in items if not blocked.artist(a.get("id"), a.get("name"))]
    elif kind == "seeds":
        items = [dict(m) for m in items if m["seed"].get("id") not in disliked and not blocked.track(m["seed"])]
    elif kind == "cards":
        items = [dict(c) for c in items if c.get("id") not in shown and c.get("id") not in disliked
                 and not (c.get("type") == "track" and blocked.track(c.get("track") or {}))
                 and not (c.get("type") == "artist" and blocked.artist(c.get("id"), c.get("title")))]
    for x in items:
        shown.add(x["seed"].get("id") if kind == "seeds" else x.get("id"))
    return items


def _present(doc, source, stale, now, ytm=True):
    blocked = blocklist.matcher()
    disliked = recommend.disliked_ids(now)
    stored = {s.get("id"): s for s in doc.get("shelves") or []}
    stored["liked"] = {"id": "liked", "kind": "tracks",
                       "items": [t for t in (store.clean_track(e) for e in likes.entries()[:recommend.LIKED]) if t]}
    chart = charts.home_shelf() if ytmusic.enabled() else None
    if chart:
        stored["charts"] = {"id": "charts", "kind": "tracks", "items": chart["items"],
                            "extra": {"country": chart["country"], "name": chart["name"], "section": chart["section"],
                                      "playlist_id": chart.get("playlist_id")}}
    ytm_shelves = [s for s in doc.get("shelves") or [] if str(s.get("id", "")).startswith(YTM_PREFIX)]
    shelves, shown = [], set()
    for sid in SHELVES:
        if sid == "charts" and ytm and ytmusic.enabled() and personal.active():
            for s in ytm_shelves:
                items = _filtered(s, blocked, disliked, shown)
                if items:
                    shelves.append({"id": s["id"], "title": s.get("title") or "", "kind": s["kind"],
                                    "source": "ytmusic", "items": items})
        s = stored.get(sid)
        if not s:
            continue
        items = _filtered(s, blocked, disliked, shown)
        if items:
            shelves.append({"id": sid, "title": SHELVES[sid][0], "kind": s.get("kind"), "items": items,
                            **(s.get("extra") or {})})
    moods = tiles.load()
    if moods:
        shelves.append({"id": "moods", "title": SHELVES["moods"][0], "kind": "moods", "items": moods})
    body = {"generated_at": int(doc["generated_at"]), "stale": stale, "source": source, "shelves": shelves}
    if doc.get("reason"):
        body["reason"] = doc["reason"]
    return body


def serve(now=None, budget=BUDGET, ytm=True):
    """The /api/home body. From the file when there is one (kicking a rebuild when due); the first time, the
    build if it finishes within `budget`, else the local shelves now and the rest on a later request.
    `ytm=False` leaves YouTube Music's own shelves out."""
    now = time.time() if now is None else now
    doc = load()
    if doc is None:
        ev = refresh_async()
        if ev is not None:
            ev.wait(budget)
        doc = load()
        if doc is None:
            return _present(build(now, remote=False), "fallback", True, now, ytm)
        return _present(doc, doc["source"], refreshing(), now, ytm)
    stale = due(doc, now)
    if stale:
        refresh_async()
    return _present(doc, "cache" if doc.get("source") == "fresh" else "fallback", stale or refreshing(), now, ytm)


def status():
    """For /api/diagnostics."""
    doc = load()
    if not doc:
        return {"built": False, "refreshing": refreshing()}
    return {"built": True, "generated_at": int(doc["generated_at"]), "source": doc.get("source"),
            "reason": doc.get("reason"), "next_at": int(doc.get("next_at", 0)), "seeds": doc.get("seeds"),
            "errors": doc.get("errors"), "refreshing": refreshing(), "personal": bool(doc.get("personal")),
            "ytm_shelves": sum(str(s.get("id", "")).startswith(YTM_PREFIX) for s in doc.get("shelves") or []),
            "ytm_error": doc.get("ytm_error"), "charts_cached": charts.fresh(charts.country())}


def _loop():
    time.sleep(BOOT_DELAY)
    while True:
        try:
            tick()
        except Exception as e:
            logger.warning(f"home: tick failed: {str(e)[:200]}")
        charts.warm()  # the Charts shelf reads the cache only; never raises
        time.sleep(TICK)


def start_refresher():
    threading.Thread(target=_loop, daemon=True, name="reef-home-tick").start()
