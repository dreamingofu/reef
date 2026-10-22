"""Source health panel (production plan P1.13): `GET /api/health/sources` checks every service Reef talks to,
in parallel, each within BUDGET seconds, and caches the answer for a minute.

Statuses: ONLINE = it did the real thing (resolved a stream, returned search JSON, accepted the token);
REACHABLE = the host answered but not usefully (an error status, HTML instead of JSON, a bot wall, a rejected
token); OFFLINE = no answer (DNS, refused, timeout); DISABLED = not configured or turned off.
Checks never use the failover module's session, so a health check doesn't put an instance on cooldown.
"""
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import wait as wait_futures
from urllib.parse import urlsplit

import requests

import config
import errors
import log
import navidrome
import settings
import streamer
import ytmusic

logger = logging.getLogger(__name__)

BUDGET = 6            # seconds per check (the whole panel takes about this long: they run in parallel)
CACHE_S = 60
RECENT_PLAY_S = 600   # a YouTube stream that worked this recently counts, without resolving again
KNOWN_VIDEO = "dQw4w9WgXcQ"
ONLINE, REACHABLE, OFFLINE, DISABLED = "ONLINE", "REACHABLE", "OFFLINE", "DISABLED"

_session = requests.Session()
_session.headers["User-Agent"] = "Reef (https://github.com/dreamingofu/reef)"
_lock = threading.Lock()
_cache = {"at": 0.0, "rows": None}
_clock = time.monotonic  # tests patch this


def _row(name, kind, status, detail="", latency_ms=None):
    # Masked before the cut, like every detail below: a cut URL loses the "@" that marks its user:pass.
    return {"name": name, "kind": kind, "status": status, "latency_ms": latency_ms,
            "detail": errors.message(detail, 200)}


def _down(e):
    """OFFLINE for no answer at all; REACHABLE for anything that means the host did answer."""
    if isinstance(e, (requests.ConnectionError, requests.Timeout, TimeoutError, ConnectionError)):
        return OFFLINE
    return REACHABLE


def _http(url, want_json=True, method="GET", **kw):
    """(status, detail) of one request: ONLINE for a 2xx (with JSON when `want_json`), else REACHABLE/OFFLINE."""
    try:
        r = _session.request(method, url, timeout=BUDGET, **kw)
    except requests.RequestException as e:
        return _down(e), log.redact(str(e))[:120]
    with r:
        if not r.ok:
            return REACHABLE, f"HTTP {r.status_code}"
        if want_json:
            try:
                r.json()
            except ValueError:
                return REACHABLE, "answered, but not with JSON"
        return ONLINE, f"HTTP {r.status_code}"


def _timed(fn):
    t0 = time.monotonic()
    status, detail = fn()
    return status, detail, int((time.monotonic() - t0) * 1000)


# ---------- the checks: each returns (status, detail) ----------

def check_youtube():
    t = streamer.last_timing or {}
    if (t.get("src") == "yt" and t.get("status") in (200, 206) and t.get("at")
            and time.time() - t["at"] < RECENT_PLAY_S):
        return ONLINE, f"played {int((time.time() - t['at']) // 60)} min ago (resolve {t.get('resolve_ms')} ms)"
    try:
        entry = streamer.resolve(KNOWN_VIDEO)  # shares the stream cache: a recent resolve is a hit
    except Exception as e:
        block = errors.classify_exc(e)
        return (OFFLINE if block == "NETWORK" else REACHABLE), f"{block}: {log.redact(str(e))[:100]}"
    return ONLINE, f"resolved via {entry.get('client') or 'yt-dlp'}"


def check_ytmusic():
    if not ytmusic.enabled():
        return DISABLED, "YouTube Music is off"
    try:
        out = ytmusic._call("get_search_suggestions", "reef")  # uncached, unlike ytmusic.suggestions
    except Exception as e:
        return _down(e), log.redact(str(e))[:120]
    return ONLINE, f"{len(out or [])} suggestions"


def check_monochrome(base):
    return _http(f"{base}/search/tracks?q=test&limit=1")


def check_piped(base):
    return _http(f"{base}/search?q=test&filter=music_songs")


def check_invidious(base):
    return _http(f"{base}/api/v1/search?q=test&type=video")


def check_navidrome():
    if not navidrome.configured():
        return DISABLED, "REEF_NAVIDROME_USER/PASS not set"
    try:
        body = navidrome._call("ping")
    except Exception as e:
        return _down(e), log.redact(str(e))[:120]
    return ONLINE, f"Navidrome {body.get('serverVersion', '')}".strip()


def check_lrclib():
    return _http("https://lrclib.net/api/get?artist_name=Rick%20Astley&track_name=Never%20Gonna%20Give%20You%20Up")


def check_listenbrainz():
    token = settings.get("listenbrainz_token")
    if not token:
        return DISABLED, "no token"
    try:
        r = _session.get("https://api.listenbrainz.org/1/validate-token", timeout=BUDGET,
                         headers={"Authorization": f"Token {token}"})
        body = r.json() if r.ok else {}
    except requests.RequestException as e:
        return _down(e), log.redact(str(e))[:120]
    except ValueError:
        return REACHABLE, "answered, but not with JSON"
    if body.get("valid"):
        return ONLINE, f"signed in as {body.get('user_name') or '?'}"
    return REACHABLE, "the token isn't valid" if r.ok else f"HTTP {r.status_code}"


def check_github():
    return _http(f"https://github.com/{config.UPDATE_REPO}", want_json=False, method="HEAD", allow_redirects=True)


def check_pypi():
    return _http("https://pypi.org/simple/yt-dlp/", want_json=False, method="HEAD", allow_redirects=True)


def _instances(key):
    return list(settings.get(key) or [])


def plan():
    """[(name, kind, check | None)]; None = DISABLED with nothing to run."""
    out = [("YouTube", "youtube", check_youtube), ("YouTube Music", "ytmusic", check_ytmusic)]
    mono = _instances("tracks_api")
    out += [(f"Monochrome {_host(b)}", "monochrome", lambda b=b: check_monochrome(b)) for b in mono] \
        or [("Monochrome", "monochrome", None)]
    for key, label, kind, fn in (("piped_instances", "Piped", "piped", check_piped),
                                 ("invidious_instances", "Invidious", "invidious", check_invidious)):
        bases = _instances(key)
        out += [(f"{label} {_host(b)}", kind, lambda b=b, fn=fn: fn(b)) for b in bases] or [(label, kind, None)]
    out += [("Navidrome", "navidrome", check_navidrome), ("LRCLIB lyrics", "lyrics", check_lrclib),
            ("ListenBrainz", "scrobble", check_listenbrainz), ("GitHub", "updates", check_github),
            ("PyPI", "updates", check_pypi)]
    return out


def _host(url):
    try:
        return urlsplit(url).hostname or url
    except ValueError:
        return url


def run(checks=None, budget=BUDGET):
    """Run the checks in parallel; one that hasn't answered within budget (+1 s slack) is OFFLINE."""
    checks = plan() if checks is None else checks
    rows = [None] * len(checks)
    pool = ThreadPoolExecutor(max_workers=max(1, min(16, len(checks))), thread_name_prefix="reef-health")
    futures = {}
    try:
        for i, (name, kind, fn) in enumerate(checks):
            if fn is None:
                rows[i] = _row(name, kind, DISABLED, "not configured")
            else:
                futures[pool.submit(_timed, fn)] = i
        done, _ = wait_futures(futures, timeout=budget + 1)
        for fut, i in futures.items():
            name, kind, _ = checks[i]
            if fut not in done:
                rows[i] = _row(name, kind, OFFLINE, f"no answer within {budget} s", budget * 1000)
                continue
            try:
                status, detail, ms = fut.result()
            except Exception as e:
                status, detail, ms = REACHABLE, f"check failed: {errors.message(e, 100)}", None
            rows[i] = _row(name, kind, status, detail, None if status == DISABLED else ms)
    finally:
        pool.shutdown(wait=False, cancel_futures=True)  # a stuck check finishes on its own; nobody waits for it
    return rows


def sources_health(force=False):
    """[row + {checked_at}] from the cache (CACHE_S), or a fresh run. Concurrent callers share one run."""
    with _lock:
        if force or _cache["rows"] is None or _clock() - _cache["at"] >= CACHE_S:
            now = time.time()
            rows = [{**r, "checked_at": now} for r in run()]
            _cache.update(at=_clock(), rows=rows)
            bad = [r["name"] for r in rows if r["status"] in (OFFLINE, REACHABLE)]
            logger.info(f"health sources n={len(rows)} trouble={','.join(bad) or '-'}")
        return [dict(r) for r in _cache["rows"]]

