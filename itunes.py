"""Search-box suggestions from Apple's iTunes Search API (production plan P2), for when YouTube Music's are off or
failing: `GET https://itunes.apple.com/search?term=&media=music&entity=song,musicArtist&limit=10` is official and
needs no key. Songs become "Artist - Song", artists their name; deduped (case-insensitively), at most SUGGEST_MAX,
in Apple's order. Cached SUGGEST_TTL like YouTube Music's; a failure answers [] and pauses the API for
FAIL_PAUSE (the box asks on every keystroke: no request storm while Apple is unreachable or rate limiting).
"""
import logging
import threading
import time

import requests

from ytmusic import SUGGEST_MAX, SUGGEST_TTL, _TTLCache

logger = logging.getLogger(__name__)

URL = "https://itunes.apple.com/search"
TIMEOUT = 3
FAIL_PAUSE = 60
LIMIT = 10

_session = requests.Session()
_session.headers["User-Agent"] = "Reef (self-hosted music companion)"
_cache = _TTLCache(SUGGEST_TTL)
_paused_until = 0.0
_lock = threading.Lock()


def _strings(results):
    out, seen = [], set()
    for r in results if isinstance(results, list) else []:
        if not isinstance(r, dict):
            continue
        artist = str(r.get("artistName") or "").strip()
        if r.get("wrapperType") == "track" and r.get("kind", "song") == "song":
            song = str(r.get("trackName") or "").strip()
            text = f"{artist} - {song}" if artist and song else song
        elif r.get("wrapperType") == "artist":
            text = artist
        else:
            continue
        key = " ".join(text.lower().split())
        if text and key not in seen:
            seen.add(key)
            out.append(" ".join(text.split()))
        if len(out) >= SUGGEST_MAX:
            break
    return out


def suggestions(q):
    """Up to SUGGEST_MAX suggestion strings for `q`; [] on any failure (and the API pauses FAIL_PAUSE s)."""
    global _paused_until
    key = " ".join(str(q or "").lower().split())
    if not key:
        return []
    hit = _cache.get(key)
    if hit is not None:
        return list(hit)
    with _lock:
        if time.monotonic() < _paused_until:
            return []
    try:
        r = _session.get(URL, params={"term": key, "media": "music", "entity": "song,musicArtist", "limit": LIMIT},
                         timeout=TIMEOUT)
        r.raise_for_status()
        out = _strings(r.json().get("results"))
    except Exception as e:
        with _lock:
            _paused_until = time.monotonic() + FAIL_PAUSE
        logger.warning(f"itunes suggest failed: {str(e)[:160]!r}")
        return []
    _cache.put(key, out)
    return list(out)


def clear():
    """Forget the cache and any pause (tests)."""
    global _paused_until
    _cache.clear()
    _paused_until = 0.0
