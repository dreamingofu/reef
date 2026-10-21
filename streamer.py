import threading
import time
from concurrent.futures import ThreadPoolExecutor

import requests
from flask import Response, request
from requests.adapters import HTTPAdapter

import sources
from sources.youtube import ydl_options  # noqa: F401  (re-exported for scripts/bench_resolve.py)

_TTL = 3600
_CHUNK = 64 * 1024
_YIELD_S = 30  # longest a prefetch waits for interactive resolves before it extracts anyway


# One keep-alive pool for the upstream media hosts, so Safari's Range requests skip the TLS handshake.
_session = requests.Session()
_adapter = HTTPAdapter(pool_connections=4, pool_maxsize=8, max_retries=1)
_session.mount("https://", _adapter)
_session.mount("http://", _adapter)
session = _session  # also used by downloader's generic (non-yt-dlp) keep path

_cache = {}        # track id -> {url, headers, mime, ext, expires}
_inflight = {}     # id -> Event, set when the extraction running for that id ends
_queued = set()    # ids handed to the prefetch pool that have not started yet
_fg = {}           # id -> number of /api/stream requests waiting for it to resolve
_lock = threading.Lock()
_cond = threading.Condition(_lock)
_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="reef-prefetch")

last_timing = {}


def _ms(t0):
    return int((time.monotonic() - t0) * 1000)


def _fresh(track_id):
    hit = _cache.get(track_id)
    return hit if hit and hit["expires"] > time.time() else None


def _may_extract(track_id):
    # A prefetch steps aside while a player is waiting on some other id.
    return not _fg or track_id in _fg


class _Turn:
    """Handed to a source's resolve for background prefetches: step aside while a player waits on another id."""

    def __init__(self, track_id):
        self.track_id = track_id
        self.deadline = time.monotonic() + _YIELD_S

    def wait(self):
        with _cond:
            _cond.wait_for(lambda: _may_extract(self.track_id), timeout=max(0.0, self.deadline - time.monotonic()))

    def should_yield(self):
        if time.monotonic() >= self.deadline:
            return False
        with _lock:
            return not _may_extract(self.track_id)


def _extract(track_id, foreground):
    key, native = sources.parse(track_id)
    src = sources.get(key)
    if src is None:
        raise ValueError(f"unknown source {key!r}")
    entry = src.resolve(native, turn=None if foreground else _Turn(track_id))
    return {
        "url": entry["url"],
        "headers": dict(entry.get("headers") or {}),
        "mime": entry.get("mime"),
        "ext": entry.get("ext"),
        "expires": time.time() + _TTL,
    }


def _resolve(track_id, foreground=True):
    """Return (entry, how), how in hit|wait|miss. Concurrent calls for one id share one extraction."""
    how = "hit"
    registered = False
    try:
        while True:
            with _lock:
                hit = _fresh(track_id)
                if hit:
                    return hit, how
                if foreground and not registered:
                    _fg[track_id] = _fg.get(track_id, 0) + 1
                    registered = True
                ev = _inflight.get(track_id)
                leader = ev is None
                if leader:
                    ev = _inflight[track_id] = threading.Event()
            if not leader:
                how = "wait"
                ev.wait(60)
                continue  # cached now, or the leader failed and this call takes over
            how = "miss"
            try:
                entry = _extract(track_id, foreground)
                with _lock:
                    now = time.time()
                    for k in [k for k, v in _cache.items() if v["expires"] <= now]:
                        del _cache[k]
                    _cache[track_id] = entry
                return entry, how
            finally:
                with _lock:
                    _inflight.pop(track_id, None)
                ev.set()
    finally:
        if registered:
            with _cond:
                n = _fg.get(track_id, 0) - 1
                if n > 0:
                    _fg[track_id] = n
                else:
                    _fg.pop(track_id, None)
                _cond.notify_all()


def _warm(track_id):
    with _lock:
        _queued.discard(track_id)
    t0 = time.monotonic()
    try:
        _, how = _resolve(track_id, foreground=False)
        if how != "hit":
            print(f"prefetch id={track_id} cache={how} resolve_ms={_ms(t0)}", flush=True)
    except Exception as e:
        print(f"prefetch id={track_id} error={str(e)[:160]!r}", flush=True)


def prefetch(ids):
    """Warm the resolve cache in the background. Returns how many ids were queued."""
    todo = []
    with _lock:
        for tid in ids:
            if tid in todo or tid in _queued or tid in _inflight or _fresh(tid):
                continue
            _queued.add(tid)
            todo.append(tid)
    for tid in todo:
        _pool.submit(_warm, tid)
    return len(todo)


def _open(entry):
    headers = dict(entry["headers"])
    if request.headers.get("Range"):
        headers["Range"] = request.headers["Range"]
    t0 = time.monotonic()
    upstream = _session.get(entry["url"], headers=headers, stream=True, timeout=(10, 60))
    return upstream, _ms(t0)


def _log(timing):
    global last_timing
    last_timing = timing
    print("stream " + " ".join(f"{k}={v}" for k, v in timing.items() if k != "at"), flush=True)


def resolve(track_id):
    """The cached stream entry for a track id (shares the proxy's cache and in-flight coalescing)."""
    return _resolve(track_id)[0]


def proxy(track_id):
    key, native = sources.parse(track_id)
    timing = {"id": track_id, "src": key, "cache": "-", "resolve_ms": 0, "ttfb_ms": 0, "status": 0,
              "range": request.headers.get("Range", "-").replace(" ", ""), "retry": 0, "at": int(time.time())}
    t0 = time.monotonic()
    try:
        entry, timing["cache"] = _resolve(track_id)
        timing["resolve_ms"] = _ms(t0)
        upstream, timing["ttfb_ms"] = _open(entry)
        if upstream.status_code >= 400:
            # Resolved URL expired or was bound to a different client; refresh once.
            upstream.close()
            with _lock:
                if _cache.get(track_id) is entry:
                    del _cache[track_id]
            src = sources.get(key)
            if src is not None:
                src.forget(native)
            timing["retry"] = 1
            t0 = time.monotonic()
            entry, timing["cache"] = _resolve(track_id)
            timing["resolve_ms"] += _ms(t0)
            upstream, ttfb = _open(entry)
            timing["ttfb_ms"] += ttfb
    except Exception as e:
        timing["resolve_ms"] = timing["resolve_ms"] or _ms(t0)
        timing["error"] = repr(str(e)[:160])
        _log(timing)
        raise
    timing["status"] = upstream.status_code
    _log(timing)
    if upstream.status_code >= 400:
        upstream.close()
        return Response("upstream error", status=502)

    out_headers = {
        "Content-Type": entry["mime"] or upstream.headers.get("Content-Type") or "application/octet-stream",
        "Accept-Ranges": "bytes",
        "Cache-Control": "no-store",
    }
    for name in ("Content-Length", "Content-Range"):
        if name in upstream.headers:
            out_headers[name] = upstream.headers[name]

    def generate():
        try:
            for chunk in upstream.iter_content(_CHUNK):
                if chunk:
                    yield chunk
        finally:
            upstream.close()  # fully read -> connection goes back to the pool

    return Response(generate(), status=upstream.status_code, headers=out_headers, direct_passthrough=True)
