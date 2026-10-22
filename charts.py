"""YouTube Music charts (production plan P2 "Charts shelf", UI remake §10.4): `GET /api/charts` and Home's "Charts".

Everything comes from YouTube Music through ytmusicapi (no Spotify / Billboard scraping). The anonymous charts page
(`ytmusic.charts`) lists the country's chart playlists (Top 100 Music Videos, Trending 20, …) and its top artists;
the weekly "Top 100 Songs <country>" playlist isn't on it, so it's looked up by title among YouTube Charts' own
playlists (the global one is known). Sections, in order: Top songs (`tracks`), Top 100 Music Videos (`videos`),
Trending (`trending`), Top artists (`artists`); each track carries its chart `rank`.

A country's charts are cached TTL in memory (PARTIAL_TTL when a section failed). Home only reads the cache; the
Home refresher thread (home.py) warms the chosen country (`charts_country` setting) in the background.
"""
import copy
import logging
import re
import threading
import time

import settings
import ytmusic

logger = logging.getLogger(__name__)

TTL = 6 * 3600
PARTIAL_TTL = 30 * 60
RETRY_S = 600          # after a failed fetch: the old copy is served (or warm() waits) this long
LIST_TTL = 7 * 86400   # a found (or missing) "Top 100 Songs <country>" playlist id
COUNTRY = re.compile(r"^[A-Z]{2}$")
SONGS_MAX = VIDEOS_MAX = 100
TRENDING_MAX = 20
ARTISTS_MAX = 40
HOME_ITEMS = 10

_cache = {}   # country -> (stored at, ttl, doc)
_lists = {}   # country -> (looked up at, playlist id | None)
_lock = threading.Lock()
_fetching = {}  # country -> Lock: concurrent requests for one country share one fetch
_failed = {}    # country -> time of the last failed fetch


def country():
    """The chosen charts country (setting, else REEF_CHARTS_COUNTRY), ZZ = global."""
    return settings.get("charts_country")


def valid(code):
    return isinstance(code, str) and bool(COUNTRY.match(code))


def cached(code):
    """The stored charts of a country, any age (shared, read only), else None."""
    with _lock:
        hit = _cache.get(code)
    return hit[2] if hit else None


def fresh(code, now=None):
    now = time.time() if now is None else now
    with _lock:
        hit = _cache.get(code)
    return bool(hit) and now - hit[0] < hit[1]


def _songs_list(code, page):
    """The "Top 100 Songs <name>" playlist id for a country (cached LIST_TTL, misses too)."""
    if code == "ZZ":
        return ytmusic.SONGS_GLOBAL
    hit = _lists.get(code)
    if hit and time.time() - hit[0] < LIST_TTL:
        return hit[1]
    found = ytmusic.chart_playlist(f"Top 100 Songs {page['name']}") if page.get("name") else None
    _lists[code] = (time.time(), found)
    return found


def _first(playlists, prefix):
    return next((p for p in playlists if p["title"].startswith(prefix)), None)


def _fetch(code):
    """(doc, complete). Raises when the charts page itself can't be read or no section came back."""
    page = ytmusic.charts(code)
    sections, failed = [], 0

    def add(kind, list_id, title, limit):
        nonlocal failed
        try:
            tracks = ytmusic.playlist(list_id, limit=limit)["tracks"][:limit]
        except Exception as e:
            failed += 1
            logger.warning(f"charts {code}: {kind} playlist {list_id} failed: {str(e)[:160]}")
            return
        for rank, t in enumerate(tracks, 1):
            t["rank"] = rank
        if tracks:
            sections.append({"title": title, "kind": kind, "playlist_id": list_id, "items": tracks})

    try:
        songs = _songs_list(code, page)
    except Exception as e:
        songs, failed = None, failed + 1
        logger.warning(f"charts {code}: songs chart lookup failed: {str(e)[:160]}")
    if songs:
        add("tracks", songs, f"Top 100 Songs {page.get('name') or code}", SONGS_MAX)
    videos = _first(page["playlists"], "Top 100 Music Videos")
    if videos:
        add("videos", videos["id"], videos["title"], VIDEOS_MAX)
    trending = _first(page["playlists"], "Trending")
    if trending:
        add("trending", trending["id"], trending["title"], TRENDING_MAX)
    if page["artists"]:
        sections.append({"title": "Top artists", "kind": "artists", "items": page["artists"][:ARTISTS_MAX]})
    if not sections:
        raise RuntimeError(f"YouTube Music has no charts for {code}")
    doc = {"country": code, "name": page.get("name") or code, "countries": page.get("countries") or [],
           "generated_at": int(time.time()), "sections": sections}
    return doc, not failed


def get(code):
    """A deep copy of a country's charts, fetched when the cached copy is older than its TTL. Raises when it
    can't be fetched and nothing is cached; a failed refetch serves the old copy with `stale: true`."""
    if fresh(code):
        return copy.deepcopy(cached(code))
    with _lock:
        gate = _fetching.setdefault(code, threading.Lock())
    with gate:
        if fresh(code):  # another request fetched it meanwhile
            return copy.deepcopy(cached(code))
        start = time.monotonic()
        try:
            doc, complete = _fetch(code)
        except Exception as e:
            _failed[code] = time.time()
            old = cached(code)
            if old is None:
                raise
            logger.warning(f"charts {code}: refetch failed, serving the old copy: {str(e)[:160]}")
            with _lock:
                _cache[code] = (time.time() - TTL + RETRY_S, TTL, old)  # asked again in RETRY_S
            return {**copy.deepcopy(old), "stale": True}
        with _lock:
            _cache[code] = (time.time(), TTL if complete else PARTIAL_TTL, doc)
        logger.info(f"charts country={code} sections={','.join(s['kind'] for s in doc['sections'])} "
                    f"complete={int(complete)} ms={int((time.monotonic() - start) * 1000)}")
        return copy.deepcopy(doc)


def warm():
    """Fetch the chosen country's charts when they're due (the Home refresher thread). Never raises."""
    code = country()
    if not ytmusic.enabled() or not valid(code) or fresh(code) or ytmusic.retry_after() is not None \
            or time.time() - _failed.get(code, 0) < RETRY_S:
        return False
    try:
        get(code)
        return True
    except Exception as e:
        logger.warning(f"charts {code}: warm failed: {str(e)[:160]}")
        return False


def home_shelf(code=None):
    """Home's "Charts" shelf from the cache only: {country, name, section, items} (top HOME_ITEMS songs, or music
    videos when the songs chart is missing), else None."""
    code = code or country()
    doc = cached(code)
    if not doc:
        return None
    section = next((s for s in doc["sections"] if s["kind"] == "tracks"), None) \
        or next((s for s in doc["sections"] if s["kind"] == "videos"), None)
    if not section:
        return None
    return {"country": code, "name": doc["name"], "section": section["title"],
            "items": [dict(t) for t in section["items"][:HOME_ITEMS]]}
