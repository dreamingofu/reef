"""Request a path from a list of interchangeable API instances, rotating past dead ones."""
import random
import time

import requests
from requests.adapters import HTTPAdapter

from .base import BROWSER_UA

TIMEOUT = 10
RETRY_STATUS = 429

_session = requests.Session()
_session.mount("https://", HTTPAdapter(pool_connections=4, pool_maxsize=8))
_session.mount("http://", HTTPAdapter(pool_connections=4, pool_maxsize=8))
_session.headers["User-Agent"] = BROWSER_UA


def _retryable(status):
    return status == RETRY_STATUS or status >= 500


def request(instances, path, method="GET", budget=None, **kwargs):
    """The first non-429/5xx response for `path`, trying at most 2 x len(instances) times from a random start.

    `budget` (seconds) caps the whole rotation: each attempt's timeout shrinks to the time left, and no
    attempt starts once it is spent. The response carries `.base`, the instance that answered. Raises the
    last error when every attempt failed (a connection error, or an HTTPError for the last 429/5xx).
    """
    if not instances:
        raise RuntimeError("no instances configured")
    limit = kwargs.pop("timeout", TIMEOUT)
    timeout = limit
    deadline = None if budget is None else time.monotonic() + budget
    start = random.randrange(len(instances))
    last = None
    for attempt in range(2 * len(instances)):
        base = instances[(start + attempt) % len(instances)]
        if deadline is not None:
            left = deadline - time.monotonic()
            if left < 0.5:
                last = last or TimeoutError(f"failover budget of {budget}s spent")
                break
            timeout = min(limit, left) if isinstance(limit, (int, float)) else left
        try:
            r = _session.request(method, base + path, timeout=timeout, **kwargs)
        except requests.RequestException as e:
            last = e
            continue
        if _retryable(r.status_code):
            last = requests.HTTPError(f"{r.status_code} from {base}", response=r)
            r.close()
            continue
        r.base = base
        return r
    raise last


def get_json(instances, path, **kwargs):
    r = request(instances, path, **kwargs)
    r.raise_for_status()
    return r.json()
