"""Lyrics: looked up once per track through a chain of sources, cached, and (LRCLIB's only) embedded into kept files.

Chain (production plan P1.3; order and on/off = the `lyrics_providers` setting): LRCLIB (https://lrclib.net/docs)
-> SimpMusic -> YouTube Music's own lyrics -> BetterLyrics (word-timed). The other sources live in
lyrics_providers.py. Each source gets 8 s, a lookup 25 s; the first line-synced answer ends it, except that the
word-timed source (started at once, next to the others) gets up to 4 s more to beat it. Synced beats plain; plain
is only the fallback. A source that answers 401/403/429 is skipped for 10 min, one that fails or times out for 60 s.

Only LRCLIB text is written into kept files (Navidrome reads the lyrics tag and serves LRC timestamps to Subsonic
clients as synced lyrics): the others are for display, so `for_track` (keeps) returns LRCLIB's part of the entry and
`for_display` (the lyrics view) the best one.

Per track the cache (lyrics.json) also holds the user's timing offset and a manually pinned LRCLIB record, which
automatic lookups never replace.
"""
import json
import logging
import os
import re
import secrets
import threading
import time
from pathlib import Path

import requests
from mutagen import File as MutagenFile
from mutagen import MutagenError
from mutagen.flac import FLAC
from mutagen.id3 import USLT
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4
from mutagen.ogg import OggFileType

import config
import lyrics_providers as P
import settings
import store

logger = logging.getLogger(__name__)

API = "https://lrclib.net/api"
_HEADERS = {"User-Agent": "Reef (https://github.com/dreamingofu/reef)"}  # LRCLIB asks clients to name themselves
_TIMEOUT = 8
_RETRY_S = 0.4        # before the one retry of a 502/503/504
_SLACK_S = 5          # a search candidate's duration may differ by this much and still be the same recording
_MISS_TTL = 24 * 3600  # sources grow daily, so a miss is retried after a day; hits are kept for good
_MAX = 3000
PROVIDERS = ("lrclib", "simpmusic", "youtube", "betterlyrics")
WORD_SOURCES = ("betterlyrics",)  # may still add word timing after a line-synced answer
PROVIDER_TIMEOUT = 8
BUDGET = 25
WORD_WAIT = 4
BLOCKED_S = 600       # 401/403/429
DOWN_S = 60           # network error, 5xx, or no answer in PROVIDER_TIMEOUT
OFFSET_MAX = 10000    # ms either way
VERSION = 2           # cache entry format: 1 = LRCLIB only (no "v"); its misses and plain hits are asked again
PINNED = "lrclib-pinned"
SEARCH_MAX = 20

# Own keep-alive pool: streamer.session is tuned for the media hosts and must not carry our User-Agent.
_session = requests.Session()
_lock = threading.Lock()  # guards _cache and the file
_stripes = [threading.Lock() for _ in range(16)]  # one lookup per track at a time: the UI and a keep share it
_cache = None         # track id -> entry, loaded lazily from config.LYRICS_FILE
_cache_path = None
_paused = {}          # source -> time.monotonic() before which it isn't asked

_BRACKETS = re.compile(r"\s*(\([^()]*\)|\[[^\[\]]*\])")
_FEAT = re.compile(r"\s+(feat\.?|ft\.?|featuring)\s.*$", re.I)


# ---------- LRCLIB ----------

def _get(path, params):
    """Parsed JSON, or None for a real "not found". Network trouble / 5xx raise (callers don't cache it as a miss);
    401/403/429 raise P.Blocked."""
    r = _session.get(API + path, params=params, headers=_HEADERS, timeout=_TIMEOUT)
    if r.status_code in (502, 503, 504):
        # LRCLIB's 503s come and go request by request (one in three on 2026-10-22): one quick retry saves the
        # lookup a 60 s pause.
        time.sleep(_RETRY_S)
        r = _session.get(API + path, params=params, headers=_HEADERS, timeout=_TIMEOUT)
    if r.status_code in (401, 403, 429):
        raise P.Blocked("LRCLIB", r.status_code)
    if r.status_code >= 500:
        raise requests.HTTPError(f"LRCLIB {r.status_code}", response=r)
    if r.status_code >= 400:  # 404 TrackNotFound (or a 4xx about our query): a genuine miss
        return None
    return r.json()


def _found(rec, duration=None):
    """{"synced", "plain", "instrumental"} of an LRCLIB record after the checks every source gets (junk, credit
    lines, [mm:ss:xx] stamps, timing that can't be this song's), or None."""
    if not isinstance(rec, dict):
        return None
    out = P.result(rec.get("syncedLyrics"), rec.get("plainLyrics"), bool(rec.get("instrumental")), None, duration)
    return {k: out[k] for k in ("synced", "plain", "instrumental")} if out else None


def _bare(title):
    """Search title: no "(Remastered 2011)", "[Live]" or "feat. X", which LRCLIB's index rarely has."""
    return _FEAT.sub("", _BRACKETS.sub("", title)).strip() or title


def _pick(candidates, duration):
    best = None
    for i, c in enumerate(candidates if isinstance(candidates, list) else []):
        found = _found(c, duration)
        if not found:
            continue
        gap = 0.0
        if duration:
            try:
                gap = abs(float(c.get("duration") or 0) - duration)
            except (TypeError, ValueError):
                continue
            if gap > _SLACK_S:
                continue
        # Synced beats plain beats instrumental; among equals the closest duration, then LRCLIB's own order.
        rank = (not found["synced"], not found["plain"], gap, i)
        if best is None or rank < best[0]:
            best = (rank, found)
    return best[1] if best else None


def _lookup(title, artist, album=None, duration=None):
    title, artist = str(title or "").strip(), str(artist or "").strip()
    if not title:
        return None
    try:
        duration = float(duration) if duration else None
    except (TypeError, ValueError):
        duration = None
    if artist:
        # /get is the exact signature match (LRCLIB allows ±2 s); album and duration narrow it when known.
        params = {"track_name": title, "artist_name": artist}
        if album:
            params["album_name"] = album
        if duration:
            params["duration"] = round(duration)
        found = _found(_get("/get", params), duration)
        if found:
            return found
    # Then the search, within ±5 s, the closest first.
    params = {"track_name": _bare(title)}
    if artist:
        params["artist_name"] = artist
    found = _pick(_get("/search", params), duration)
    if not found and ", " in artist:
        # Keeps join several artists with ", "; LRCLIB usually files the song under the first one.
        found = _pick(_get("/search", {**params, "artist_name": artist.split(", ")[0]}), duration)
    return found


def lookup(title, artist, album=None, duration=None):
    """LRCLIB only: {"synced": str|None, "plain": str|None, "instrumental": bool}, or None when nothing was found
    (or LRCLIB couldn't be reached). Uncached; for_track / for_display go through the cache and the chain."""
    try:
        return _lookup(title, artist, album, duration)
    except (requests.RequestException, P.Blocked, ValueError):
        return None


def _lrclib(q, timeout):
    found = _lookup(q.get("title"), q.get("artist"), q.get("album"), q.get("duration"))
    return {**found, "words": None} if found else None


FETCHERS = {"lrclib": _lrclib, "simpmusic": P.simpmusic, "youtube": P.youtube, "betterlyrics": P.betterlyrics}


# ---------- the chain ----------

def order():
    """The sources to ask, in order (the setting; unknown names are ignored)."""
    return [p for p in (settings.get("lyrics_providers") or []) if p in FETCHERS]


def _ready(name):
    return time.monotonic() >= _paused.get(name, 0.0)


def _pause(name, secs, why):
    _paused[name] = time.monotonic() + secs
    logger.warning(f"reef: lyrics source {name} skipped for {secs} s: {str(why)[:160]}")


class _Job:
    """One source's fetch on its own daemon thread: a stuck source costs the lookup its timeout, never more
    (the thread finishes on its own when the HTTP timeout fires)."""

    def __init__(self, name, q):
        self.name, self.start, self.done = name, time.monotonic(), threading.Event()
        self.value = self.error = None
        threading.Thread(target=self._run, args=(q,), daemon=True, name=f"reef-lyrics-{name}").start()

    def _run(self, q):
        try:
            self.value = FETCHERS[self.name](q, PROVIDER_TIMEOUT)
        except Exception as e:
            self.error = e
        finally:
            self.done.set()

    def result(self, until):
        """The answer, waiting at most until `until` (monotonic) and the source's own timeout. TimeoutError when
        neither brought it; the source's error otherwise."""
        if not self.done.wait(max(0.0, min(until, self.start + PROVIDER_TIMEOUT) - time.monotonic())):
            raise TimeoutError
        if self.error:
            raise self.error
        return self.value


def _chain(q):
    """Ask the sources -> (best answer + "provider" or None, LRCLIB's answer or None, complete, trace). Not
    complete = a source that mattered (one before the answer, or the word-timed one waited for) didn't answer,
    so the result may change soon: a miss isn't cached and a hit is re-asked after a day."""
    names, start = order(), time.monotonic()
    deadline = start + BUDGET
    jobs, got, trace, complete = {}, {}, [], True
    # The word-timed source runs from the start next to the others: by the time a line-synced answer is in,
    # its answer usually is too.
    word_names = [n for n in names if n in WORD_SOURCES and _ready(n)]
    for n in word_names:
        jobs[n] = _Job(n, q)

    def ask(name, until):
        nonlocal complete
        job = jobs.get(name) or jobs.setdefault(name, _Job(name, q))
        try:
            res = job.result(until)
        except TimeoutError:
            complete = False
            if time.monotonic() - job.start >= PROVIDER_TIMEOUT - 0.05:
                _pause(name, DOWN_S, f"no answer in {PROVIDER_TIMEOUT} s")
                trace.append(f"{name}:timeout")
            else:
                trace.append(f"{name}:cut")  # our budget ran out, not the source's fault
            return None
        except P.Blocked as e:
            complete = False
            _pause(name, BLOCKED_S, e)
            trace.append(f"{name}:{e.status}")
            return None
        except Exception as e:  # network, 5xx, bad JSON, a ytmusicapi surprise: try it again soon
            complete = False
            _pause(name, DOWN_S, f"{type(e).__name__}: {e}")
            trace.append(f"{name}:error")
            return None
        got[name] = res
        kind = "none" if not res else "words" if res.get("words") else "synced" if res["synced"] else \
            "plain" if res["plain"] else "instrumental"
        trace.append(f"{name}:{kind}")
        return res

    synced_at = None
    for name in names:
        if time.monotonic() >= deadline:
            complete = False
            trace.append(f"{name}:budget")
            break
        if name not in jobs and not _ready(name):
            complete = False
            trace.append(f"{name}:paused")
            continue
        res = ask(name, deadline)
        if res and res["synced"]:
            synced_at = name
            break
    if synced_at and not got[synced_at].get("words"):
        for n in word_names:
            if n not in got and n in jobs:
                ask(n, min(deadline, time.monotonic() + WORD_WAIT))

    best = None
    for test in (lambda r: r.get("words"), lambda r: r["synced"], lambda r: r["plain"], lambda r: r["instrumental"]):
        best = next((n for n in names if got.get(n) and test(got[n])), None)
        if best:
            break
    found = {**got[best], "provider": best} if best else None
    return found, got.get("lrclib"), complete, trace


# ---------- cache: track id -> entry ----------
# Hit:  {"v": 2, "provider", "synced", "plain", "instrumental", "words"? (_pack), "lrclib"? (LRCLIB's answer when another
#        source won: the keep embeds it), "retry"? (not complete: asked again after a day), "pinned"?, "lrclib_id"?,
#        "offset_ms"?, "at"}
# Miss: {"v": 2, "miss": True, "sig": "lrclib,youtube", "at"} (fresh for a day, and only for the same sources)
# Offset only (no lookup yet, or a library track whose lyrics come from Navidrome): {"v": 2, "offset_ms", "at"}
# Reef 1 entries have no "v": {"synced", "plain", "instrumental", "at"} or {"miss": True, "at"}, all from LRCLIB.

def _entries():
    global _cache, _cache_path
    if _cache is None or _cache_path != config.LYRICS_FILE:
        try:
            data = json.loads(Path(config.LYRICS_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        _cache, _cache_path = (data if isinstance(data, dict) else {}), config.LYRICS_FILE
    return _cache


def _sig():
    return ",".join(order())


def _is_hit(e):
    return isinstance(e, dict) and not e.get("miss") and any(e.get(k) for k in ("synced", "plain", "instrumental"))


def _fresh(e, now):
    """Whether entry `e` answers without asking the sources."""
    if not isinstance(e, dict):
        return False
    if e.get("pinned"):
        return True
    age = now - (e.get("at") or 0)
    if e.get("v") != VERSION:
        return _is_hit(e) and bool(e.get("synced"))  # an LRCLIB synced hit stands; the rest gets the new chain
    if e.get("miss"):
        return e.get("sig") == _sig() and age < _MISS_TTL
    if not _is_hit(e):
        return False  # offset only
    return not e.get("retry") or age < _MISS_TTL


def _put(track_id, entry):
    """Store `entry` (caller holds _lock) and write the file."""
    entries = _entries()
    entries[track_id] = entry
    if len(entries) > _MAX:
        # Pinned records go last: they were chosen by hand.
        for k in sorted(entries, key=lambda k: (bool(entries[k].get("pinned")), entries[k].get("at") or 0))[:len(entries) - _MAX]:
            del entries[k]
    path = Path(_cache_path)
    tmp = path.with_name(path.name + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(entries, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)  # a crash mid-write leaves the old file, never half a JSON
    except OSError as e:
        logger.warning(f"reef: couldn't save the lyrics cache: {e}")


def _query(track_id, title, artist, album, duration):
    try:
        duration = float(duration) if duration else None
    except (TypeError, ValueError):
        duration = None
    vid = track_id if isinstance(track_id, str) and store.VIDEO_ID.match(track_id) else None
    return {"video_id": vid, "title": str(title or "").strip(), "artist": str(artist or "").strip(),
            "album": str(album or "").strip() or None, "duration": duration}


def _entry(track_id, title, artist, album=None, duration=None):
    """The cache entry for `track_id`, asking the sources when it's due; None when there's nothing to show."""
    with _stripes[hash(track_id) % len(_stripes)]:
        with _lock:
            cur = _entries().get(track_id)
            if _fresh(cur, time.time()):
                return cur if _is_hit(cur) else None
        t0 = time.monotonic()
        found, lrc, complete, trace = _chain(_query(track_id, title, artist, album, duration))
        logger.info(f"lyrics id={track_id} provider={found['provider'] if found else None} complete={int(complete)} "
                    f"ms={int((time.monotonic() - t0) * 1000)} asked={','.join(trace)}")
        with _lock:
            cur = _entries().get(track_id)
            if isinstance(cur, dict) and cur.get("pinned"):
                return cur  # pinned while we were asking
            now = time.time()
            if found:
                new = {**found, "v": VERSION, "at": now}
                if new.get("words"):
                    new["words"] = _pack(new["words"])
                else:
                    new.pop("words", None)
                if lrc and found["provider"] != "lrclib":
                    new["lrclib"] = {k: lrc[k] for k in ("synced", "plain", "instrumental")}
                if not complete:
                    new["retry"] = True
            elif not complete:
                return cur if _is_hit(cur) else None  # nothing stored: keep serving what we had
            else:
                new = {"v": VERSION, "miss": True, "sig": _sig(), "at": now}
            if isinstance(cur, dict) and cur.get("offset_ms"):
                new["offset_ms"] = cur["offset_ms"]
            _put(track_id, new)
            return new if _is_hit(new) else None


def _pack(words):
    """Word timing as stored: [[t ms, d ms | null, text]] per line, about half the JSON of the API's dicts (a
    word-timed song is ~500 words, and lyrics.json is rewritten whole on every new entry)."""
    return [[[round(w["t"] * 1000), round(w["d"] * 1000) if w.get("d") is not None else None, w["text"]]
             for w in line] for line in words]


def _unpack(rows):
    out = []
    for line in rows if isinstance(rows, list) else []:
        words = []
        for w in line if isinstance(line, list) else []:
            if isinstance(w, list) and len(w) == 3 and isinstance(w[0], int) and isinstance(w[2], str):
                words.append({"t": w[0] / 1000, "text": w[2], **({"d": w[1] / 1000} if isinstance(w[1], int) else {})})
        out.append(words)
    return out


def _display(e):
    out = {"synced": e.get("synced"), "plain": e.get("plain"), "instrumental": bool(e.get("instrumental")),
           "provider": e.get("provider") or "lrclib", "offset_ms": int(e.get("offset_ms") or 0)}
    if e.get("words") and e.get("synced"):
        out["words"] = _unpack(e["words"])
    return out


def for_track(track_id, title, artist, album=None, duration=None):
    """For a keep: LRCLIB's lyrics (a pinned LRCLIB record included) as {"synced", "plain", "instrumental"}, or
    None. Shares the cached lookup with the lyrics view, so the view and a keep of the same track fetch once."""
    e = _entry(track_id, title, artist, album, duration)
    if not e:
        return None
    if (e.get("provider") or "lrclib") in ("lrclib", PINNED):
        return {k: e.get(k) for k in ("synced", "plain", "instrumental")}
    lrc = e.get("lrclib")
    return dict(lrc) if isinstance(lrc, dict) else None


def for_display(track_id, title, artist, album=None, duration=None):
    """For the lyrics view: {synced, plain, instrumental, words?, provider, offset_ms} of the best source, or None."""
    e = _entry(track_id, title, artist, album, duration)
    return _display(e) if e else None


def pinned(track_id):
    """The pinned record's display dict, or None. Library tracks ask this before Navidrome's tags."""
    with _lock:
        e = _entries().get(track_id)
        return _display(e) if isinstance(e, dict) and e.get("pinned") else None


# ---------- timing offset, manual search and pin ----------

def offset(track_id):
    with _lock:
        e = _entries().get(track_id)
        return int(e.get("offset_ms") or 0) if isinstance(e, dict) else 0


def set_offset(track_id, ms):
    """Store the track's timing offset (ms, + = lyrics later). Returns it. ValueError when out of range."""
    if isinstance(ms, bool) or not isinstance(ms, (int, float)) or ms != ms or abs(ms) > OFFSET_MAX:
        raise ValueError(f"ms must be a number from -{OFFSET_MAX} to {OFFSET_MAX}")
    ms = int(round(ms))
    with _lock:
        cur = _entries().get(track_id)
        new = dict(cur) if isinstance(cur, dict) else {"v": VERSION, "at": time.time()}
        if ms:
            new["offset_ms"] = ms
        else:
            new.pop("offset_ms", None)
        _put(track_id, new)
    return ms


Unavailable = P.Unavailable


def _lrclib_call(path, params):
    """_get for the user's own requests (search, pin): any failure is Unavailable (a 502 for the route)."""
    try:
        return _get(path, params)
    except (requests.RequestException, P.Blocked, ValueError) as e:
        raise Unavailable(f"LRCLIB: {str(e)[:160]}") from None


def search(q):
    """LRCLIB's search for the "Wrong lyrics?" sheet: [{id, track, artist, album, duration, synced, plain,
    instrumental}]. Unavailable when LRCLIB can't answer."""
    found = _lrclib_call("/search", {"q": q})
    out = []
    for rec in (found if isinstance(found, list) else [])[:SEARCH_MAX * 2]:
        if not isinstance(rec, dict) or not isinstance(rec.get("id"), int):
            continue
        found = _found(rec)
        if not found:
            continue
        out.append({"id": rec["id"], "track": str(rec.get("trackName") or ""), "artist": str(rec.get("artistName") or ""),
                    "album": str(rec.get("albumName") or ""), "duration": rec.get("duration"),
                    "synced": bool(found["synced"]), "plain": bool(found["plain"]),
                    "instrumental": found["instrumental"]})
        if len(out) >= SEARCH_MAX:
            break
    return out


def pin(track_id, lrclib_id):
    """Pin LRCLIB record `lrclib_id` to the track: shown (and embedded on a later keep) instead of anything the
    chain finds, until unpinned. The timing offset starts over (it was for other lyrics). Returns the display dict.
    LookupError: no such record, or it holds no lyrics; Unavailable: LRCLIB can't answer."""
    found = _found(_lrclib_call(f"/get/{int(lrclib_id)}", None))
    if found is None:
        raise LookupError("no lyrics in that LRCLIB record")
    entry = {**found, "provider": PINNED, "pinned": True, "lrclib_id": int(lrclib_id), "v": VERSION, "at": time.time()}
    with _lock:
        _entries()  # _put writes to the path this loads
        _put(track_id, entry)
    return _display(entry)


def unpin(track_id):
    """Drop a pin (the next view asks the sources again). False when there was none."""
    with _lock:
        cur = _entries().get(track_id)
        if not (isinstance(cur, dict) and cur.get("pinned")):
            return False
        _put(track_id, {"v": VERSION, "at": time.time(), **({"offset_ms": cur["offset_ms"]} if cur.get("offset_ms") else {})})
        return True


# ---------- embedding ----------

def embed(path, found):
    """Write the lyrics into the file's tags (synced LRC text when there is some, else plain).
    False when there is no text, the format has no lyrics tag here, or the write failed."""
    text = (found or {}).get("synced") or (found or {}).get("plain")
    if not text:
        return False
    try:
        f = MutagenFile(path)
        if f is None:
            return False
        if f.tags is None and isinstance(f, (MP3, MP4, FLAC, OggFileType)):
            f.add_tags()
        if isinstance(f, MP3):
            f.tags.setall("USLT", [USLT(encoding=3, lang="eng", desc="", text=text)])
        elif isinstance(f, MP4):
            f.tags["\xa9lyr"] = [text]
        elif isinstance(f, (FLAC, OggFileType)):
            f["LYRICS"] = [text]  # Ogg Opus / Vorbis / FLAC vorbis comment
        else:
            return False
        f.save()
    except (MutagenError, OSError, ValueError) as e:
        logger.warning(f"reef: couldn't embed lyrics in {os.path.basename(str(path))}: {e}")
        return False
    if settings.get("lrc_sidecar") and found.get("synced"):
        try:
            _write_new(Path(path).with_suffix(".lrc"), found["synced"])  # never through a link left there
        except OSError as e:
            logger.warning(f"reef: couldn't write the .lrc sidecar: {e}")
    return True


def _write_new(path, text):
    """Write `text` to `path` through a temporary file created exclusively (an existing file or link named like it is
    never opened, so its target is never written), then swapped in: os.replace replaces a link or hard link at `path`
    itself, never what it points at. The temporary file is removed when anything fails."""
    path = Path(path)
    for _ in range(100):
        tmp = path.with_name(f".{path.name[:150]}.{secrets.token_hex(4)}.tmp")
        try:
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
        except FileExistsError:
            continue
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
            os.replace(tmp, path)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        return
    raise FileExistsError(f"no free temporary name next to {path.name}")
