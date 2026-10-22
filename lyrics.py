"""Lyrics from LRCLIB (https://lrclib.net/docs): looked up once per track, cached, embedded into kept files.

Navidrome reads the lyrics tag and, when the text carries LRC timestamps, serves it to Subsonic clients
as synced lyrics, so the LRC text is written verbatim.
"""
import json
import os
import re
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

API = "https://lrclib.net/api"
_HEADERS = {"User-Agent": "Reef (https://github.com/dreamingofu/reef)"}  # LRCLIB asks clients to name themselves
_TIMEOUT = 8
_SLACK_S = 5          # a search candidate's duration may differ by this much and still be the same recording
_MISS_TTL = 24 * 3600  # LRCLIB grows daily, so a miss is retried after a day; hits are kept for good
_MAX = 3000
_DOWN_S = 60          # after a network error, skip LRCLIB this long instead of making every caller wait 8 s

# Own keep-alive pool: streamer.session is tuned for the media hosts and must not carry our User-Agent.
_session = requests.Session()
_lock = threading.Lock()  # guards _cache and the file
_stripes = [threading.Lock() for _ in range(16)]  # one lookup per track at a time: the UI and a keep share it
_cache = None         # track id -> entry, loaded lazily from config.LYRICS_FILE
_cache_path = None
_down_until = 0.0

_BRACKETS = re.compile(r"\s*(\([^()]*\)|\[[^\[\]]*\])")
_FEAT = re.compile(r"\s+(feat\.?|ft\.?|featuring)\s.*$", re.I)
_STAMP = re.compile(r"^(\s*\[[^\]]*\])+")  # leading [mm:ss.xx] (and [ar:...]-style) tags of an LRC line
_MIN_LINES = 3


def _get(path, params):
    """Parsed JSON, or None for a real "not found". Network trouble raises, so callers don't cache it as a miss."""
    global _down_until
    if time.monotonic() < _down_until:
        raise requests.ConnectionError("LRCLIB was unreachable moments ago")
    try:
        r = _session.get(API + path, params=params, headers=_HEADERS, timeout=_TIMEOUT)
        if r.status_code == 429 or r.status_code >= 500:
            raise requests.HTTPError(f"LRCLIB {r.status_code}", response=r)
    except requests.RequestException:
        _down_until = time.monotonic() + _DOWN_S
        raise
    if r.status_code >= 400:  # 404 TrackNotFound (or a 4xx about our query): a genuine miss
        return None
    return r.json()


def _text(value):
    """The lyrics text, or None when it is too thin to be a song: LRCLIB is crowd-edited and some popular
    records hold junk like "[00:00.00]probe"."""
    if not isinstance(value, str):
        return None
    lines = [ln for ln in (_STAMP.sub("", ln).strip() for ln in value.splitlines()) if ln]
    return value if len(lines) >= _MIN_LINES else None


def _found(rec):
    if not isinstance(rec, dict):
        return None
    out = {"synced": _text(rec.get("syncedLyrics")), "plain": _text(rec.get("plainLyrics")),
           "instrumental": bool(rec.get("instrumental"))}
    return out if out["synced"] or out["plain"] or out["instrumental"] else None


def _bare(title):
    """Search title: no "(Remastered 2011)", "[Live]" or "feat. X", which LRCLIB's index rarely has."""
    return _FEAT.sub("", _BRACKETS.sub("", title)).strip() or title


def _pick(candidates, duration):
    best = None
    for i, c in enumerate(candidates if isinstance(candidates, list) else []):
        found = _found(c)
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
        found = _found(_get("/get", params))
        if found:
            return found
    params = {"track_name": _bare(title)}
    if artist:
        params["artist_name"] = artist
    found = _pick(_get("/search", params), duration)
    if not found and ", " in artist:
        # Keeps join several artists with ", "; LRCLIB usually files the song under the first one.
        found = _pick(_get("/search", {**params, "artist_name": artist.split(", ")[0]}), duration)
    return found


def lookup(title, artist, album=None, duration=None):
    """{"synced": str|None, "plain": str|None, "instrumental": bool}, or None when nothing was found
    (or LRCLIB couldn't be reached). Uncached; use for_track when there is a track id."""
    try:
        return _lookup(title, artist, album, duration)
    except (requests.RequestException, ValueError):
        return None


# ---------- cache: track id -> {"synced", "plain", "instrumental", "at"} or {"miss": True, "at"} ----------

def _entries():
    global _cache, _cache_path
    if _cache is None or _cache_path != config.LYRICS_FILE:
        try:
            data = json.loads(Path(config.LYRICS_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        _cache, _cache_path = (data if isinstance(data, dict) else {}), config.LYRICS_FILE
    return _cache


def _cached(track_id):
    """The cached result ({} for a fresh miss), or None when there is nothing usable."""
    e = _entries().get(track_id)
    if not isinstance(e, dict):
        return None
    if e.get("miss"):
        return {} if time.time() - (e.get("at") or 0) < _MISS_TTL else None
    return {k: e.get(k) for k in ("synced", "plain", "instrumental")}


def _store(track_id, found):
    entries = _entries()
    entries[track_id] = {**(found or {"miss": True}), "at": time.time()}
    if len(entries) > _MAX:
        for k in sorted(entries, key=lambda k: entries[k].get("at") or 0)[:len(entries) - _MAX]:
            del entries[k]
    path = Path(_cache_path)
    tmp = path.with_name(path.name + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(entries, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)  # a crash mid-write leaves the old file, never half a JSON
    except OSError as e:
        print(f"reef: couldn't save the lyrics cache: {e}")


def for_track(track_id, title, artist, album=None, duration=None):
    """lookup() cached by track id, so the lyrics view and a keep of the same track share one fetch."""
    with _stripes[hash(track_id) % len(_stripes)]:
        with _lock:
            hit = _cached(track_id)
        if hit is not None:
            return hit or None
        try:
            found = _lookup(title, artist, album, duration)
        except (requests.RequestException, ValueError) as e:
            print(f"reef: lyrics lookup failed for {track_id}: {str(e)[:200]}")
            return None  # not cached: next time LRCLIB may be back
        with _lock:
            _store(track_id, found)
        return found


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
        print(f"reef: couldn't embed lyrics in {os.path.basename(str(path))}: {e}")
        return False
    if config.LRC_SIDECAR and found.get("synced"):
        try:
            Path(path).with_suffix(".lrc").write_text(found["synced"], encoding="utf-8")
        except OSError as e:
            print(f"reef: couldn't write the .lrc sidecar: {e}")
    return True
