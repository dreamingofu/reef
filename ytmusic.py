"""YouTube Music (anonymous InnerTube via ytmusicapi): song search with artist/album, suggestions, radio,
and metadata for pasted links. yt-dlp still resolves the stream; this only finds and describes tracks.

Every call raises on failure; callers decide the fallback (sources/youtube.py falls back to yt-dlp search).
"""
import re
import threading
import time
from collections import OrderedDict
from functools import partial

import requests

import config

try:  # optional at runtime: a phone that pulled new code but hasn't re-run pip keeps yt-dlp search
    import ytmusicapi
    from ytmusicapi import YTMusic
except ImportError:  # pragma: no cover - exercised only without the package
    ytmusicapi = YTMusic = None

VERSION = getattr(ytmusicapi, "__version__", None)
TIMEOUT = (5, 10)  # connect, read; the library default is 30 s, too long for a search box
THUMB_SIZE = 544
SUGGEST_TTL, SUGGEST_MAX = 600, 8
RADIO_TTL = 1800
CACHE_MAX = 256       # entries per response cache
ART_MAX = 5000        # remembered square thumbs

_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
# googleusercontent / ggpht art is square and sized by its "=w120-h120-l90-rj" or "=s192" suffix.
_SIZED = re.compile(r"=(?:w\d+-h\d+|s\d+)(?:-[\w-]*)?$")
_ART_HOSTS = (".googleusercontent.com", ".ggpht.com")

# YTMusic holds one requests.Session; share one instance and serialise calls on it.
_yt = None
_lock = threading.Lock()


def enabled():
    return config.YTMUSIC and YTMusic is not None


def _client():
    global _yt
    if _yt is None:
        session = requests.Session()
        session.request = partial(session.request, timeout=TIMEOUT)
        _yt = YTMusic(requests_session=session)
    return _yt


def _call(method, *args, **kwargs):
    if not enabled():
        raise RuntimeError("YouTube Music is disabled")
    with _lock:
        return getattr(_client(), method)(*args, **kwargs)


class _TTLCache:
    """Small bounded TTL cache; values are stored and returned as-is (callers copy)."""

    def __init__(self, ttl, size=CACHE_MAX):
        self.ttl, self.size, self.data, self.lock = ttl, size, OrderedDict(), threading.Lock()

    def get(self, key):
        with self.lock:
            hit = self.data.get(key)
            if hit is None or time.monotonic() - hit[0] > self.ttl:
                return None
            return hit[1]

    def put(self, key, value):
        with self.lock:
            self.data[key] = (time.monotonic(), value)
            self.data.move_to_end(key)
            while len(self.data) > self.size:
                self.data.popitem(last=False)

    def clear(self):
        with self.lock:
            self.data.clear()


_suggest_cache = _TTLCache(SUGGEST_TTL)
_radio_cache = _TTLCache(RADIO_TTL)
_art = OrderedDict()  # video id -> square art URL without its size suffix
_art_lock = threading.Lock()


def _remember_art(video_id, base):
    with _art_lock:
        _art[video_id] = base
        _art.move_to_end(video_id)
        while len(_art) > ART_MAX:
            _art.popitem(last=False)


def square_art(video_id, size):
    """Square art URL at `size` px for a track seen in a YT Music response, else None."""
    with _art_lock:
        base = _art.get(video_id)
        if base is not None:
            _art.move_to_end(video_id)
    return f"{base}=w{size}-h{size}-l90-rj" if base else None


def _square_base(url):
    """The URL minus its size suffix when it's resizable square art, else None."""
    if not isinstance(url, str) or not url.startswith("https://"):
        return None
    host = url[8:].split("/", 1)[0]
    if not host.endswith(_ART_HOSTS) or not _SIZED.search(url):
        return None
    return _SIZED.sub("", url)


def _largest(thumbs):
    if not isinstance(thumbs, list):
        return None
    thumbs = [t for t in thumbs if isinstance(t, dict) and t.get("url")]
    return max(thumbs, key=lambda t: t.get("width") or 0)["url"] if thumbs else None


def parse_duration(value):
    """Seconds from an int or "m:ss" / "h:mm:ss", else None."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return int(value) if value > 0 else None
    if not isinstance(value, str) or not re.fullmatch(r"\d{1,3}(?::\d{1,2}){1,2}", value.strip()):
        return None
    secs = 0
    for part in value.strip().split(":"):
        secs = secs * 60 + int(part)
    return secs or None


def normalize(item, album=None, thumb=None):
    """A Reef track dict from a ytmusicapi song/track dict, or None without a valid videoId.

    `album` ({name, id}) and `thumb` fill in what album-page tracks leave out (they carry neither art
    nor the album id)."""
    if not isinstance(item, dict):
        return None
    vid = item.get("videoId")
    if not isinstance(vid, str) or not _VIDEO_ID.match(vid):
        return None
    artists = [{"name": a.get("name") or "", "id": a.get("id")}
               for a in item.get("artists") or [] if isinstance(a, dict) and a.get("name")]
    alb = item.get("album")
    if isinstance(alb, str):
        alb = {"name": alb, "id": (album or {}).get("id")}
    elif not isinstance(alb, dict):
        alb = album or {}
    duration = item.get("duration_seconds")
    if not isinstance(duration, int) or isinstance(duration, bool) or duration <= 0:
        duration = parse_duration(item.get("length") or item.get("duration"))
    url = _largest(item.get("thumbnails") or item.get("thumbnail")) or thumb
    base = _square_base(url)
    if base:
        _remember_art(vid, base)
        url = f"{base}=w{THUMB_SIZE}-h{THUMB_SIZE}-l90-rj"
    else:
        url = f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg"  # 16:9 video art, as yt-dlp search gives
    return {
        "id": vid,
        "title": item.get("title") or "",
        "channel": ", ".join(a["name"] for a in artists),
        "artists": artists,
        "album": alb.get("name") or None,
        "album_id": alb.get("id") or None,
        "duration": duration,
        "thumb": url,
        "explicit": bool(item.get("isExplicit")),
        "source": "yt",
    }


def _tracks(items, limit, **fill):
    out, seen = [], set()
    for item in items or []:
        t = normalize(item, **fill)
        if t and t["id"] not in seen:
            seen.add(t["id"])
            out.append(t)
            if len(out) >= limit:
                break
    return out


def search(q, limit=config.SEARCH_LIMIT):
    """Songs (not videos): official audio with artist/album and square art."""
    return _tracks(_call("search", q, filter="songs", limit=limit), limit)


def suggestions(q):
    key = q.strip().lower()
    if not key:
        return []
    hit = _suggest_cache.get(key)
    if hit is None:
        raw = _call("get_search_suggestions", key)
        hit = [s for s in raw or [] if isinstance(s, str)][:SUGGEST_MAX]
        _suggest_cache.put(key, hit)
    return list(hit)


def radio(video_id, limit=50):
    """The song's radio mix. The first track is usually the seed itself; order is kept, the client dedupes."""
    key = (video_id, limit)
    hit = _radio_cache.get(key)
    if hit is None:
        hit = _tracks(_call("get_watch_playlist", videoId=video_id, radio=True, limit=limit).get("tracks"), limit)
        _radio_cache.put(key, hit)
    return [dict(t) for t in hit]


def song(video_id):
    """{id, title, channel, duration, thumb, ...} for one video id, else None."""
    try:
        details = (_call("get_song", video_id) or {}).get("videoDetails") or {}
    except Exception as e:
        if not enabled():
            raise
        print(f"ytmusic get_song {video_id} failed: {str(e)[:160]!r}", flush=True)
        details = {}
    if details.get("videoId") == video_id and details.get("title"):
        t = normalize({
            "videoId": video_id, "title": details["title"],
            "artists": [{"name": details.get("author") or "", "id": details.get("channelId")}],
            "duration_seconds": int(details["lengthSeconds"]) if str(details.get("lengthSeconds", "")).isdigit() else None,
            "thumbnails": (details.get("thumbnail") or {}).get("thumbnails"),
        })
        if t:
            return t
    # get_song can come back without videoDetails (e.g. playability errors); the watch "next" call
    # still describes the track, with artists and album.
    tracks = (_call("get_watch_playlist", videoId=video_id, limit=1) or {}).get("tracks") or []
    for t in _tracks(tracks, 1):
        if t["id"] == video_id:
            return t
    return None


def album(browse_id):
    """(title, tracks) for an album browse id (MPRE...)."""
    a = _call("get_album", browse_id) or {}
    alb = {"name": a.get("title"), "id": browse_id}
    return a.get("title"), _tracks(a.get("tracks"), 500, album=alb, thumb=_largest(a.get("thumbnails")))


def playlist(list_id, limit=100):
    """(title, tracks) for a playlist id. RD... mixes only exist as watch playlists."""
    if list_id.startswith("RD"):
        data = _call("get_watch_playlist", playlistId=list_id, limit=limit) or {}
        return None, _tracks(data.get("tracks"), limit)
    data = _call("get_playlist", list_id, limit=limit) or {}
    return data.get("title"), _tracks(data.get("tracks"), limit)
