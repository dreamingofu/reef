"""Request a path from a list of interchangeable API instances, rotating past dead ones."""
import logging
import random
import threading
import time

import requests
from requests.adapters import HTTPAdapter

from .base import BROWSER_UA

logger = logging.getLogger(__name__)

TIMEOUT = 10
RETRY_STATUS = 429
COOL_S = 180         # a host that is down, slow, failing (5xx) or serving HTML instead of JSON sits out this long
COOL_BLOCKED_S = 300  # ... and this long when it refuses us (403 / captcha)
_clock = time.monotonic  # tests patch this

_session = requests.Session()
_session.mount("https://", HTTPAdapter(pool_connections=4, pool_maxsize=8))
_session.mount("http://", HTTPAdapter(pool_connections=4, pool_maxsize=8))
_session.headers["User-Agent"] = BROWSER_UA

_cooling = {}  # instance base -> monotonic time its cooldown ends (Meld's QobuzAudioProvider host cooldowns)
_cool_lock = threading.Lock()


def _retryable(status):
    return status == RETRY_STATUS or status >= 500


def cool(base, seconds, why=""):
    with _cool_lock:
        _cooling[base] = max(_cooling.get(base, 0), _clock() + seconds)
    logger.warning(f"failover cool host={base} s={seconds} why={why[:80]!r}")


def _until(base):
    with _cool_lock:
        until = _cooling.get(base)
        if until is not None and until <= _clock():
            del _cooling[base]
            return None
        return until


def cooling():
    """{instance: seconds of cooldown left} for /api/health."""
    now = _clock()
    with _cool_lock:
        return {b: int(u - now) for b, u in _cooling.items() if u > now}


def _blocked_body(r):
    """403, or a captcha page: the instance refuses us, not just a hiccup."""
    if r.status_code == 403:
        return True
    ctype = r.headers.get("Content-Type", "")
    return "html" in ctype and "captcha" in r.text[:4096].lower()


def _html_instead_of_json(r):
    try:
        return r.text.lstrip()[:1] == "<"
    except Exception:
        return False


def request(instances, path, method="GET", budget=None, expect_json=False, **kwargs):
    """The first good response for `path`, trying at most 2 x len(instances) times from a random start.

    Hosts that failed recently (DNS, refused, timeout, 5xx, HTML where JSON was expected: 3 min; 403 or a
    captcha: 5 min) are skipped; when every host is cooling, the one whose cooldown ends first gets one try.
    `budget` (seconds) caps the whole rotation: each attempt's timeout shrinks to the time left, and no
    attempt starts once it is spent. The response carries `.base`, the instance that answered. Raises the
    last error when every attempt failed (a connection error, or an HTTPError for the last bad response).
    """
    if not instances:
        raise RuntimeError("no instances configured")
    limit = kwargs.pop("timeout", TIMEOUT)
    timeout = limit
    deadline = None if budget is None else time.monotonic() + budget
    start = random.randrange(len(instances))
    last = None
    tried_cooling = False
    for attempt in range(2 * len(instances)):
        base = instances[(start + attempt) % len(instances)]
        if _until(base) is not None:
            warm = [b for b in instances if _until(b) is None]
            if warm:
                continue
            if tried_cooling:
                break
            tried_cooling = True
            base = min(instances, key=lambda b: _until(b) or 0)
        if deadline is not None:
            left = deadline - time.monotonic()
            if left < 0.5:
                last = last or TimeoutError(f"failover budget of {budget}s spent")
                break
            timeout = min(limit, left) if isinstance(limit, (int, float)) else left
        try:
            r = _session.request(method, base + path, timeout=timeout, **kwargs)
        except requests.RequestException as e:
            if isinstance(e, (requests.ConnectionError, requests.Timeout)):
                cool(base, COOL_S, str(e))
            last = e
            continue
        bad = None
        if r.status_code == 403 or (r.status_code >= 400 and _blocked_body(r)):
            cool(base, COOL_BLOCKED_S, f"{r.status_code}")
            bad = f"{r.status_code} from {base}"
        elif _retryable(r.status_code):
            if r.status_code >= 500:
                cool(base, COOL_S, f"{r.status_code}")
            bad = f"{r.status_code} from {base}"
        elif expect_json and r.ok and _html_instead_of_json(r):
            blocked = "captcha" in r.text[:4096].lower()
            cool(base, COOL_BLOCKED_S if blocked else COOL_S, "captcha page" if blocked else "HTML instead of JSON")
            bad = f"HTML instead of JSON from {base}"
        if bad:
            last = requests.HTTPError(bad, response=r)
            r.close()
            continue
        r.base = base
        return r
    raise last or RuntimeError("every instance is cooling down")


def get_json(instances, path, **kwargs):
    r = request(instances, path, expect_json=True, **kwargs)
    r.raise_for_status()
    return r.json()
