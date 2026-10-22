import logging
import re
import struct
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qs, urlsplit

import requests
from flask import Response, jsonify, request
from requests.adapters import HTTPAdapter
from urllib3.exceptions import ProtocolError, ReadTimeoutError

import config
import errors
import sources
import testaudio
from sources.youtube import client_of
from sources.youtube import ydl_options  # noqa: F401  (re-exported for scripts/bench_resolve.py)

logger = logging.getLogger(__name__)

_DEFAULT_TTL = 300   # seconds, for URLs that don't say when they expire
_EXPIRY_MARGIN = 60  # stop handing out a signed URL this long before its `expire`
_EXPIRING_S = 30     # an upstream 403 this close to `expire` is plain expiry, not a denial
_CHUNK = 64 * 1024
# Upstream pieces: googlevideo throttles an open-ended `Range: bytes=N-` to ~2x realtime (~32 KB/s for itag 140)
# but serves a bounded `bytes=N-M` of a few MB at full speed, so every client range is fetched as bounded pieces.
_FIRST_SPAN = 2 * 1024 * 1024  # the first piece of a request: small, so the first bytes come fast
_SPAN = 4 * 1024 * 1024        # every later one
_YIELD_S = 30  # longest a prefetch waits for interactive resolves before it extracts anyway
_HEAD_CAP = 512 * 1024  # most bytes held back while waiting for a whole moov
_MP4 = {"audio/mp4", "video/mp4"}
_RESUMES = 5         # reopens of one dropped upstream body
_RESUME_GAP_S = 0.5
_GATING = ("denied", "token", "missing")  # verdicts that change the client set, so a second refresh can help
_REMEMBER = 500      # ids kept in the small per-id maps below
# What a dropped upstream body looks like from iter_content (requests wraps most of urllib3's, not all).
_DROPS = (requests.exceptions.ChunkedEncodingError, requests.ConnectionError, requests.Timeout,
          ProtocolError, ReadTimeoutError)
_RANGE = re.compile(r"\s*bytes\s*=\s*(\d*)\s*-\s*(\d*)\s*$", re.I)
_now = time.time     # tests patch these
_sleep = time.sleep


# One keep-alive pool for the upstream media hosts, so Safari's Range requests skip the TLS handshake.
_session = requests.Session()
_adapter = HTTPAdapter(pool_connections=4, pool_maxsize=8, max_retries=1)
_session.mount("https://", _adapter)
_session.mount("http://", _adapter)
session = _session  # also used by downloader's generic (non-yt-dlp) keep path

# track id -> {url, headers, mime, ext, duration, client, has_pot, itag, lmt, clen, expire, expires, gen}
_cache = {}
_inflight = {}     # id -> Event, set when the extraction running for that id ends
_queued = set()    # ids handed to the prefetch pool that have not started yet
_fg = {}           # id -> number of /api/stream requests waiting for it to resolve
# id -> generation: bumped by every stored URL and every invalidation, so an extraction that started before
# an invalidation (slow prefetch, 403 refresh, ?fresh=) can't store its now-stale URL over a newer one.
_gen = {}
_served = {}       # id -> (itag, lmt, clen) of the file last proxied; a mid-song Range must not switch files
_fresh_tokens = {}  # id -> the ?fresh= tokens already acted on (newest last, at most _FRESH_KEEP)
_FRESH_KEEP = 8
_stats = {"hits": 0, "waits": 0, "misses": 0}
_lock = threading.Lock()
_cond = threading.Condition(_lock)
_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="reef-prefetch")

last_timing = {}


def _ms(t0):
    return int((time.monotonic() - t0) * 1000)


def _num(v, kind=int):
    try:
        return kind(v)
    except (TypeError, ValueError):
        return None


def _url_meta(url):
    """What a stream URL says about itself: googlevideo's expire/c/pot/itag/lmt/clen/dur, or a CDN's Expires."""
    q = parse_qs(urlsplit(url or "").query)

    def one(*keys):
        return next((q[k][0] for k in keys if q.get(k)), None)

    expire = _num(one("expire", "Expires", "etsp"))
    return {"client": client_of(url), "has_pot": "pot" in q, "itag": one("itag"), "lmt": one("lmt"),
            "clen": _num(one("clen")), "expire": expire if expire and expire > 1e9 else None,
            "dur": _num(one("dur"), float)}


def _remember(d, key, value):
    d.pop(key, None)
    d[key] = value
    if len(d) > _REMEMBER:
        del d[next(iter(d))]


def _usable(track_id, hit, now):
    """A cached entry is reused only while it has not expired, a signed URL will outlive the song (+60 s), and
    its source still accepts it (a gated YouTube client's URL is not)."""
    if hit.get("dead") or hit["expires"] <= now:
        return False
    if hit["expire"] and hit["duration"] and hit["expire"] - now < hit["duration"] + _EXPIRY_MARGIN:
        return False
    key, native = sources.parse(track_id)
    src = sources.get(key)
    return src is None or src.usable(native, hit)


def _fresh(track_id):
    hit = _cache.get(track_id)
    return hit if hit and _usable(track_id, hit, _now()) else None


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
    meta = _url_meta(entry["url"])
    now = _now()
    return {
        "url": entry["url"],
        "headers": dict(entry.get("headers") or {}),
        "mime": entry.get("mime"),
        "ext": entry.get("ext"),
        "duration": _num(entry.get("duration"), float) or meta["dur"],
        "codec": entry.get("codec") if isinstance(entry.get("codec"), str) else None,
        "bitrate": _num(entry.get("bitrate"), float),
        "sample_rate": _num(entry.get("sample_rate")),
        "client": meta["client"],
        "has_pot": meta["has_pot"],
        "itag": meta["itag"],
        "lmt": meta["lmt"],
        "clen": meta["clen"],
        "expire": meta["expire"],
        "expires": meta["expire"] - _EXPIRY_MARGIN if meta["expire"] else now + _DEFAULT_TTL,
        "gen": 0,
    }


def _resolve(track_id, foreground=True):
    """Return (entry, how), how in hit|wait|miss. Concurrent calls for one id share one extraction."""
    how = "hit"
    registered = False
    stale = 0
    try:
        while True:
            with _lock:
                hit = _fresh(track_id)
                if hit:
                    _stats[how + "s"] += 1
                    return hit, how
                if foreground and not registered:
                    _fg[track_id] = _fg.get(track_id, 0) + 1
                    registered = True
                ev = _inflight.get(track_id)
                leader = ev is None
                if leader:
                    ev = _inflight[track_id] = threading.Event()
                    gen = _gen.get(track_id, 0)
            if not leader:
                how = "wait"
                ev.wait(60)
                continue  # cached now, or the leader failed and this call takes over
            how = "miss"
            try:
                entry = _extract(track_id, foreground)
                with _lock:
                    if _gen.get(track_id, 0) == gen:
                        entry["gen"] = _gen[track_id] = gen + 1
                        now = _now()
                        for k in [k for k, v in _cache.items() if v["expires"] <= now]:
                            del _cache[k]
                            _fresh_tokens.pop(k, None)
                        _cache[track_id] = entry
                        _stats["misses"] += 1
                        return entry, how
                stale += 1
                logger.warning(f"stream id={track_id} dropped a stale resolve (gen {gen})")
                if stale >= 2 or not foreground:
                    entry["gen"] = gen
                    return entry, how
            finally:
                with _lock:
                    if _inflight.get(track_id) is ev:
                        del _inflight[track_id]
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


def _invalidate(track_id, entry=None):
    """Drop the id's cached URL and any in-flight result, and bump its generation. With `entry`, only if
    nobody dropped that entry already (concurrent 403s on one URL refresh it once). True when it acted."""
    with _lock:
        if entry is not None:
            if entry.get("dead"):
                return False
            entry["dead"] = True
            if _cache.get(track_id) is not entry:
                return True  # already replaced (expiry) or being replaced: leave the newer URL alone
        current = _cache.pop(track_id, None)
        if current is not None:
            current["dead"] = True
        _inflight.pop(track_id, None)
        _gen[track_id] = _gen.get(track_id, 0) + 1
        return True


def _verdict(entry, status):
    """Why an upstream refused a resolved URL, from the URL's own params (Flow's StreamDenialClassifier)."""
    if status in (404, 410, 416):
        return "missing"
    if status != 403:
        return "other"
    if not entry.get("expire") or entry["expire"] - _now() <= _EXPIRING_S:
        return "expired"  # no `expire` (not googlevideo): treat as expiry, re-resolve the same way
    return "token" if entry.get("has_pot") else "denied"


def _refresh(track_id, entry, verdict):
    """After `entry` failed upstream: tell its source (YouTube gates the client), drop it, resolve again."""
    key, native = sources.parse(track_id)
    src = sources.get(key)
    if _invalidate(track_id, entry) and src is not None:
        src.failed(native, entry, verdict)
        src.forget(native)
    return _resolve(track_id)


def _ident(entry):
    """(itag, lmt, clen): two URLs with the same triple serve byte-identical files. None when unknown."""
    ident = (entry.get("itag"), entry.get("lmt"), entry.get("clen"))
    return ident if any(ident) else None


def _same_file(a, b):
    ia, ib = _ident(a), _ident(b)
    # Sources without format ids (Monochrome, Navidrome) serve one file per track.
    return ia == ib


def _changed_under_player(track_id, entry):
    with _lock:
        last = _served.get(track_id)
    ident = _ident(entry)
    return last is not None and ident is not None and last != ident


def _take_fresh(track_id, token):
    """True the first time `?fresh=<token>` is seen for an id. A reloaded <audio> repeats its src (and so the
    token) on every Range request, and an older element or a plain probe may interleave with it: a token evicts
    at most once. The last _FRESH_KEEP tokens per id are kept until the id's cache entry expires."""
    if not token:
        return False
    with _lock:
        seen = _fresh_tokens.get(track_id, ())
        if token in seen:
            return False
        _remember(_fresh_tokens, track_id, (*seen, token)[-_FRESH_KEEP:])
        return True


def _warm(track_id):
    with _lock:
        _queued.discard(track_id)
    t0 = time.monotonic()
    try:
        _, how = _resolve(track_id, foreground=False)
        if how != "hit":
            logger.info(f"prefetch id={track_id} cache={how} resolve_ms={_ms(t0)}")
    except Exception as e:
        logger.warning(f"prefetch id={track_id} error={str(e)[:160]!r}")


def prefetch(ids):
    """Warm the resolve cache in the background. Returns how many ids were queued."""
    ids = [tid for tid in ids if not testaudio.covers(sources.parse(tid)[0])]  # test audio: YouTube is never resolved
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


def _open(entry, rng=None):
    headers = dict(entry["headers"])
    if rng:
        headers["Range"] = rng
    t0 = time.monotonic()
    upstream = _session.get(entry["url"], headers=headers, stream=True, timeout=(10, 60))
    return upstream, _ms(t0)


def _log(timing):
    global last_timing
    last_timing = timing
    logger.info("stream " + " ".join(f"{k}={v}" for k, v in timing.items() if k != "at"))


def resolve(track_id):
    """The cached stream entry for a track id (shares the proxy's cache and in-flight coalescing)."""
    return _resolve(track_id)[0]


def _clear_cache():
    with _lock:
        n = len(_cache)
        for tid, e in _cache.items():
            e["dead"] = True
            _gen[tid] = _gen.get(tid, 0) + 1
        _cache.clear()
    return {"resolve_cache": n}


sources.youtube.reset_hooks.append(_clear_cache)


def reset_youtube():
    """POST /api/yt/reset: yt-dlp cache dir, YoutubeDL instances, client gates and the resolve cache."""
    return sources.youtube.reset()


def stats():
    """Resolve-cache numbers for /api/health, with the newest entries' client and time left."""
    now = _now()
    with _lock:
        recent = [{"id": tid, "client": e["client"], "itag": e["itag"], "gen": e["gen"], "pot": e["has_pot"],
                   "ttl_s": int(e["expires"] - now), "duration": e["duration"]}
                  for tid, e in list(_cache.items())[-10:]]
        return {"entries": len(_cache), **_stats, "recent": recent}


# YouTube audio itags -> (codec, kbps, Hz), for entries whose source didn't say (fallback resolvers).
_ITAGS = {"139": ("aac", 48, 22050), "140": ("aac", 128, 44100), "141": ("aac", 256, 44100),
          "249": ("opus", 50, 48000), "250": ("opus", 70, 48000), "251": ("opus", 160, 48000),
          "18": ("aac", 96, 44100)}
_EXT_CODEC = {"flac": "flac", "opus": "opus", "webm": "opus", "mp3": "mp3", "m4a": "aac", "ogg": "vorbis"}


def info(track_id):
    """What the resolve cache knows about `track_id`'s stream (Song info, §8.3), or None when it isn't cached.
    Never resolves: no network, no yt-dlp."""
    now = _now()
    with _lock:
        e = _cache.get(track_id)
        if not e or e.get("dead"):
            return None
        e = dict(e)
    guess = _ITAGS.get(str(e.get("itag") or ""), (None, None, None))
    bitrate = e.get("bitrate") or guess[1]
    return {"client": e.get("client"), "itag": e.get("itag"), "mime": e.get("mime"), "ext": e.get("ext"),
            "codec": e.get("codec") or guess[0] or _EXT_CODEC.get(e.get("ext") or ""),
            "bitrate": round(bitrate) if bitrate else None, "sample_rate": e.get("sample_rate") or guess[2],
            "duration": e.get("duration"), "size": e.get("clen"), "ttl_s": max(0, int(e["expires"] - now))}


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


def _range_start(rng):
    """First byte a Range header asks for: None without one, -1 for a suffix range (bytes=-N)."""
    m = _RANGE.match(rng or "")
    if not m:
        return None
    return int(m.group(1)) if m.group(1) else -1


def _span(upstream):
    """(first byte, last byte or None, body length or None) of an upstream response; first None = unknown."""
    length = _num(upstream.headers.get("Content-Length"))
    if upstream.status_code == 200:
        return 0, (length - 1 if length else None), length
    m = re.match(r"\s*bytes\s+(\d+)-(\d+)", upstream.headers.get("Content-Range", ""))
    if upstream.status_code == 206 and m:
        return int(m.group(1)), int(m.group(2)), length
    return None, None, length


def _wanted(rng):
    """(first, last or None) of the bytes a client asks for; (0, None) without a Range. None when the range can't
    be fetched in pieces (a suffix `bytes=-N`, a multi-range, garbage, last < first): it goes upstream as is."""
    if not rng:
        return 0, None
    m = _RANGE.match(rng)
    if not m or not m.group(1):
        return None
    first, last = int(m.group(1)), int(m.group(2)) if m.group(2) else None
    return None if last is not None and last < first else (first, last)


def _piece(entry, first, last, span):
    """The upstream Range for a piece starting at `first`: at most `span` bytes, never past the client's `last`
    or the end of the file when the URL says its length (clen)."""
    hi = first + span - 1
    if last is not None:
        hi = min(hi, last)
    clen = entry.get("clen")
    if clen and first < clen:
        hi = min(hi, clen - 1)
    return f"bytes={first}-{hi}"


def _total(upstream):
    """The file length from a 206's `Content-Range: bytes a-b/total`; None when it says `*` or nothing."""
    m = re.match(r"\s*bytes\s+\d+-\d+\s*/\s*(\d+)", upstream.headers.get("Content-Range", ""))
    return int(m.group(1)) if upstream.status_code == 206 and m else None


def _walled(entry, start):
    """REEF_FAKE_WALL: a track's first URL "dies" ~60 s in (clen x 60 / duration), like a walled client."""
    if not config.FAKE_WALL or entry.get("gen") != 1 or not start or start < 0:
        return False
    clen, dur = entry.get("clen"), entry.get("duration")
    return bool(clen and dur) and start > clen * 60 / dur


def _fail(status, block, message):
    resp = jsonify({"error": message, "block": block, "hint": errors.hint(block)})
    resp.status_code = status
    resp.headers["Cache-Control"] = "no-store"
    return resp


class _Body:
    """The response body, fetched upstream piece by piece and surviving drops.

    With `end` (the last byte the client asked for), the bytes after the first upstream response come as bounded
    Range requests of at most _SPAN bytes (see _FIRST_SPAN), opened one after another on the keep-alive session.
    Without it, the first upstream body is the whole body. Either way, a mid-body error or a short piece reopens
    at the next byte (Gyawun's stream_client), up to _RESUMES times per piece, so Safari never sees the cut."""

    def __init__(self, track_id, entry, upstream, end=None):
        self.track_id, self.entry, self.upstream, self.end = track_id, entry, upstream, end
        self.pos, self.last, _ = _span(upstream)  # next byte to send / last byte of the current piece (None: unknown)
        self.sent = 0
        self.pieces = 1
        self.resumes = 0  # reopens of the current piece
        self.reopens = 0  # of all pieces, for the log

    def chunks(self):
        while True:
            try:
                for c in self.upstream.iter_content(_CHUNK):
                    if not c:
                        continue
                    over = self.end is not None and self.pos + len(c) > self.end + 1
                    if over:  # the upstream sent past the asked-for end: never more than Content-Length
                        c = c[:self.end + 1 - self.pos]
                    self.sent += len(c)
                    if self.pos is not None:
                        self.pos += len(c)
                    if c:
                        yield c
                    if over:
                        return
                if self.last is None or self.pos > self.last:
                    if self.end is None or self.pos > self.end:
                        return
                    if not self._next():
                        return
                    continue
                err = f"short body (ends before byte {self.pos}, piece runs to {self.last})"
            except _DROPS as e:
                err = f"{type(e).__name__}: {str(e)[:120]}"
            if not self._open_rest(err):
                return

    def _next(self):
        self.last = min(self.pos + _SPAN - 1, self.end)
        self.pieces += 1
        self.resumes = 0
        return self._open_rest(None)

    def _open_rest(self, err):
        """Open the current piece from self.pos on. err None: the planned first open of a new piece; otherwise
        (a drop, a short body, a failed open) each attempt logs, waits _RESUME_GAP_S and counts toward _RESUMES."""
        if self.pos is None:
            logger.warning(f"stream-resume id={self.track_id} can't resume (unknown offset) err={err!r}")
            return False
        at = self.pos
        rng = f"bytes={at}-{'' if self.last is None else self.last}"
        while True:
            if err is not None:
                if self.resumes >= _RESUMES:
                    break
                self.resumes += 1
                self.reopens += 1
                logger.info(f"stream-resume id={self.track_id} at={at} try={self.resumes}/{_RESUMES} err={err!r}")
                _sleep(_RESUME_GAP_S)
            self.upstream.close()  # fully read -> its connection goes back to the pool
            try:
                self.upstream = self._reopen(at, rng)
            except errors.StreamError as e:
                err = str(e)
                break  # the refreshed URL is another file (or the fake wall): carrying on would splice two files
            except Exception as e:
                err = f"reopen {type(e).__name__}: {str(e)[:120]}"
                continue
            got = _span(self.upstream)[0]
            if self.upstream.status_code == 206 and got == at:
                return True
            err = f"reopen answered {self.upstream.status_code} at {got}"
        logger.warning(f"stream-resume id={self.track_id} gave up after {self.sent} bytes err={err!r}")
        return False

    def _reopen(self, at, rng):
        if _walled(self.entry, at):
            # REEF_FAKE_WALL: end the body here, so the player's next Range request (past the wall) meets the 403.
            raise errors.StreamError(errors.EXPIRED, f"fake wall at byte {at} (REEF_FAKE_WALL)")
        up, _ = _open(self.entry, rng)
        if up.status_code in (403, 410):
            # The URL died mid-body: a refreshed one may carry on, but only if it serves the same file.
            verdict = _verdict(self.entry, up.status_code)
            up.close()
            new, _ = _refresh(self.track_id, self.entry, verdict)
            if not _same_file(self.entry, new):
                raise errors.StreamError(errors.EXPIRED, "refreshed URL serves a different file")
            self.entry = new
            up, _ = _open(new, rng)
        return up

    def close(self):
        self.upstream.close()  # fully read -> connection goes back to the pool


def proxy(track_id, fresh=None):
    """Proxy one (Range) request for a track. `fresh` (the ?fresh= token) evicts the cached URL first.

    The client's range is fetched upstream as bounded pieces (_FIRST_SPAN, then _SPAN bytes; _Body) and answered
    as one 206 (200 without a Range) for exactly that range. An upstream that ignores Range (200) or doesn't say
    the file length passes through as is.
    Upstream 4xx: classify, gate the client, re-resolve. A mid-song Range (start > 0) only moves to the new URL
    when it serves the same file (itag+lmt+clen); otherwise 502 {block: EXPIRED} so the player reloads.
    """
    key, native = sources.parse(track_id)
    rng = request.headers.get("Range")
    start = _range_start(rng)
    mid = bool(start)  # past byte 0 (or a suffix range): the element is already playing some file
    want = _wanted(rng)

    def first_piece(entry):
        return rng if want is None else _piece(entry, want[0], want[1], _FIRST_SPAN)

    timing = {"id": track_id, "src": key, "cache": "-", "resolve_ms": 0, "ttfb_ms": 0, "status": 0,
              "range": (rng or "-").replace(" ", ""), "up": "-", "retry": 0, "at": int(time.time())}
    t0 = time.monotonic()
    try:
        fresh = _take_fresh(track_id, fresh)
        if fresh:
            timing["fresh"] = 1
            _invalidate(track_id)
            src = sources.get(key)
            if src is not None:
                src.forget(native)
        entry, timing["cache"] = _resolve(track_id)
        timing["resolve_ms"] = _ms(t0)
        timing.update(gen=entry.get("gen"), client=entry.get("client") or "-")
        if _walled(entry, start):
            timing.update(status=403, fake_wall=1)
            _log(timing)
            return _fail(403, errors.EXPIRED, f"fake wall at byte {start} (REEF_FAKE_WALL)")
        if mid and not fresh and _changed_under_player(track_id, entry):
            timing.update(status=502, swap="changed")
            _log(timing)
            return _fail(502, errors.EXPIRED, "the stream now serves a different file; reload it")
        timing["up"] = (first_piece(entry) or "-").replace(" ", "")
        upstream, timing["ttfb_ms"] = _open(entry, first_piece(entry))
        limit = 1
        while upstream.status_code >= 400 and timing["retry"] < limit:
            verdict = _verdict(entry, upstream.status_code)
            if verdict in _GATING:
                limit = 2  # the client set changes, so a second refresh can land on another client
            upstream.close()
            timing["retry"] += 1
            timing["verdict"] = verdict
            t1 = time.monotonic()
            new, timing["cache"] = _refresh(track_id, entry, verdict)
            timing["resolve_ms"] += _ms(t1)
            if mid and not _same_file(entry, new):
                timing.update(status=502, swap="refused", gen=new.get("gen"), client=new.get("client") or "-")
                _log(timing)
                return _fail(502, errors.EXPIRED, "the stream URL expired mid-song and its replacement is a "
                                                  "different file; reload it")
            if mid:
                timing["swap"] = "same-file"
            entry = new
            timing.update(gen=entry.get("gen"), client=entry.get("client") or "-")
            timing["up"] = (first_piece(entry) or "-").replace(" ", "")
            upstream, ttfb = _open(entry, first_piece(entry))
            timing["ttfb_ms"] += ttfb
        total = _total(upstream)
        pieces = want is not None and total is not None and _span(upstream)[0] == want[0] < total
        if want is not None and upstream.status_code == 206 and not pieces:
            # No file length (or an odd answer) to fetch the rest in pieces by: ask for the client's range as is.
            upstream.close()
            timing["up"] = (rng or "-").replace(" ", "")
            upstream, ttfb = _open(entry, rng)
            timing["ttfb_ms"] += ttfb
    except Exception as e:
        timing["resolve_ms"] = timing["resolve_ms"] or _ms(t0)
        timing["error"] = repr(str(e)[:160])
        _log(timing)
        raise
    timing["status"] = upstream.status_code
    _log(timing)
    if upstream.status_code >= 400:
        status, verdict = upstream.status_code, _verdict(entry, upstream.status_code)
        upstream.close()
        block = "RATE_LIMITED" if status == 429 else "BOT_WALL" if verdict in ("denied", "token") else "UNKNOWN"
        return _fail(502, block, f"upstream answered {status} ({verdict})")
    with _lock:
        if _ident(entry):
            _remember(_served, track_id, _ident(entry))

    out_headers = {
        "Content-Type": entry["mime"] or upstream.headers.get("Content-Type") or "application/octet-stream",
        "Accept-Ranges": "bytes",
        "Cache-Control": "no-store",
    }
    end = None
    if pieces:
        # The answer describes the client's range, whatever pieces it is fetched in.
        first, end = want[0], total - 1 if want[1] is None else min(want[1], total - 1)
        status = 206 if rng else 200
        out_headers["Content-Length"] = str(end - first + 1)
        if rng:
            out_headers["Content-Range"] = f"bytes {first}-{end}/{total}"
    else:
        status = upstream.status_code
        for name in ("Content-Length", "Content-Range"):
            if name in upstream.headers:
                out_headers[name] = upstream.headers[name]

    # Only a body that starts at byte 0 carries moov; every other range passes through untouched.
    is_mp4 = any((m or "").split(";")[0].strip().lower() in _MP4
                 for m in (entry["mime"], upstream.headers.get("Content-Type")))
    fix_head = is_mp4 and _from_start(upstream)
    body = _Body(track_id, entry, upstream, end)

    def generate():
        t1 = time.monotonic()
        try:
            # The head patch sits above the pieces and resumes: they come after it and pass through untouched.
            yield from _patched_head(body.chunks()) if fix_head else body.chunks()
        finally:
            body.close()
            timing.update(bytes=body.sent, pieces=body.pieces, resumes=body.reopens, body_ms=_ms(t1))
            logger.info(f"stream-body id={track_id} range={timing['range']} bytes={body.sent} pieces={body.pieces} "
                        f"resumes={body.reopens} body_ms={timing['body_ms']}")

    return Response(generate(), status=status, headers=out_headers, direct_passthrough=True)
