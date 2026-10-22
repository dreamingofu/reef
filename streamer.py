import re
import struct
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
_HEAD_CAP = 512 * 1024  # most bytes held back while waiting for a whole moov
_MP4 = {"audio/mp4", "video/mp4"}


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


# ---------- fragmented MP4 duration fix ----------
# YouTube's m4a (format 140) is fragmented: every sample lives in moof/mdat fragments, yet moov's
# mvhd/tkhd/mdhd still carry the full track duration. Apple's media stack adds that to the fragment
# durations, so iOS Safari reports twice the length and never fires `ended` (the queue stalls).
# Zeroing those fields (as a fragmenter normally writes them) gives the true length; sizes never change.

def _boxes(buf, start, end):
    """Yield (type, body, box_end) for the boxes in buf[start:end]; stops at the first incomplete one."""
    pos = start
    while pos + 8 <= end:
        size, kind = struct.unpack_from(">I4s", buf, pos)
        hdr = 8
        if size == 1:
            if pos + 16 > end:
                return
            size, hdr = struct.unpack_from(">Q", buf, pos + 8)[0], 16
        elif size == 0:
            size = end - pos  # extends to the end of its parent
        if size < hdr or pos + size > end:
            return
        yield kind, pos + hdr, pos + size
        pos += size


# box type -> (offset of the duration field from the box body, per version 0 / 1)
_DUR_AT = {b"mvhd": (16, 24), b"tkhd": (20, 28), b"mdhd": (16, 24)}


def _duration_fields(buf, body, end):
    """(offset, width) of the moov duration fields to zero, or None if any of them is malformed."""
    fields = []

    def visit(kind, body, end):
        if kind in _DUR_AT:
            if body >= end or buf[body] > 1:
                return False
            ver = buf[body]
            at, width = body + _DUR_AT[kind][ver], 8 if ver else 4
            if at + width > end:
                return False
            fields.append((at, width))
        elif kind in (b"trak", b"mdia"):
            return all(visit(*child) for child in _boxes(buf, body, end))
        return True

    return fields if all(visit(*child) for child in _boxes(buf, body, end)) else None


def fix_fragmented_durations(buf):
    """Zero the moov durations of a fragmented MP4 (one with mvex) whose moov is wholly inside buf.

    Anything else (no moov yet, no mvex, malformed) comes back unchanged; the length never changes.
    """
    try:
        for kind, body, end in _boxes(buf, 0, len(buf)):
            if kind != b"moov":
                continue
            if not any(k == b"mvex" for k, _, _ in _boxes(buf, body, end)):
                return buf  # not fragmented: its durations are real
            fields = _duration_fields(buf, body, end)
            if not fields:
                return buf
            out = bytearray(buf)
            for at, width in fields:
                out[at:at + width] = bytes(width)
            return bytes(out)
    except (struct.error, IndexError, TypeError):
        pass
    return buf


def _head_ready(buf):
    """True once buf holds the whole moov, or the file is not one whose moov comes before the media."""
    pos = 0
    while True:
        if pos + 8 > len(buf):
            return False
        size, kind = struct.unpack_from(">I4s", buf, pos)
        hdr = 8
        if size == 1:
            if pos + 16 > len(buf):
                return False
            size, hdr = struct.unpack_from(">Q", buf, pos + 8)[0], 16
        if kind in (b"moof", b"mdat") or (size and size < hdr):
            return True  # media first or garbage: nothing worth waiting for
        if size == 0:
            return False  # runs to EOF; the cap or EOF ends the wait
        pos += size
        if kind == b"moov":
            return pos <= len(buf)


def _patched_head(chunks):
    """Pass chunks through, holding back the start until moov is whole so its durations can be fixed."""
    buf = bytearray()
    it = iter(chunks)
    for chunk in it:
        buf += chunk
        if len(buf) >= _HEAD_CAP or _head_ready(buf):
            break
    if buf:
        yield fix_fragmented_durations(bytes(buf))
    yield from it


def _from_start(upstream):
    if upstream.status_code == 200:
        return True
    m = re.match(r"\s*bytes\s+(\d+)-", upstream.headers.get("Content-Range", ""))
    return upstream.status_code == 206 and m is not None and int(m.group(1)) == 0


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

    # Only a body that starts at byte 0 carries moov; every other range passes through untouched.
    is_mp4 = any((m or "").split(";")[0].strip().lower() in _MP4
                 for m in (entry["mime"], upstream.headers.get("Content-Type")))
    fix_head = is_mp4 and _from_start(upstream)

    def generate():
        try:
            chunks = (c for c in upstream.iter_content(_CHUNK) if c)
            yield from _patched_head(chunks) if fix_head else chunks
        finally:
            upstream.close()  # fully read -> connection goes back to the pool

    return Response(generate(), status=upstream.status_code, headers=out_headers, direct_passthrough=True)
