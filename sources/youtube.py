"""YouTube: search via YouTube Music (ytmusic.py) then yt-dlp (search.py); stream URLs via yt-dlp.

D3 hook: append callables to `search_fallbacks` (`f(q, limit) -> [track]`) and `resolve_fallbacks`
(`f(video_id) -> {"url", "headers", "mime", "ext"}`). They are tried in order, only after yt-dlp
raised; the first that returns without raising wins, otherwise yt-dlp's error is re-raised.
Search tries YouTube Music first (when REEF_YTMUSIC is on) and drops to that chain on an error or no results.
Set `last_backend` to say which one answered (reported by /api/health).
Piped (sources/piped.py) and Invidious (sources/invidious.py) are registered by sources/__init__.py
when REEF_PIPED_INSTANCES / REEF_INVIDIOUS_INSTANCES are set.
"""
import threading

import yt_dlp

import config
import search as _yt_search
import ytmusic

from .base import Source

_MIME = {"m4a": "audio/mp4", "webm": "audio/webm", "opus": "audio/ogg", "mp3": "audio/mpeg"}

search_fallbacks = []
resolve_fallbacks = []
last_backend = {"search": None, "resolve": None}


def fallback_names():
    return [getattr(fb, "backend", getattr(fb, "__name__", "fallback")) for fb in resolve_fallbacks]


def ydl_options(clients=None):
    # iOS Safari plays AAC/m4a natively; webm/opus support is spotty, so prefer m4a.
    return {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "format": "bestaudio[ext=m4a]/bestaudio/best",
        "extractor_args": config.yt_extractor_args(clients),
    }


# YoutubeDL is not thread-safe; one lazily built instance keeps its player JS / nsig caches warm.
_ydl = None
_ydl_lock = threading.Lock()


def _extract(video_id, turn=None):
    global _ydl
    while True:
        if turn is not None:
            turn.wait()
        with _ydl_lock:
            if turn is not None and turn.should_yield():
                continue
            if _ydl is None:
                _ydl = yt_dlp.YoutubeDL(ydl_options())
            info = _ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
            break
    return {
        "url": info["url"],
        "headers": dict(info.get("http_headers") or {}),
        "mime": _MIME.get(info.get("ext"), "application/octet-stream"),
        "ext": info.get("ext"),
    }


def _with_fallbacks(kind, primary, fallbacks, *args):
    try:
        out = primary(*args)
        last_backend[kind] = "yt-dlp"
        return out
    except Exception as err:
        for fb in list(fallbacks):
            try:
                out = fb(*args)
            except Exception as e:
                print(f"yt {kind} fallback {getattr(fb, '__name__', fb)} failed: {str(e)[:160]!r}", flush=True)
                continue
            last_backend[kind] = getattr(fb, "backend", getattr(fb, "__name__", "fallback"))
            print(f"yt {kind} via {last_backend[kind]} (yt-dlp failed: {str(err)[:120]!r})", flush=True)
            return out
        raise err


class YouTube(Source):
    key = "yt"
    name = "YouTube"
    can_keep = True
    codec_label = "AAC"

    def track_id(self, native_id):
        return native_id  # bare ids, for history.json / localStorage / tiles.json back-compat

    def search(self, q, limit=config.SEARCH_LIMIT):
        results = None
        if ytmusic.enabled():
            try:
                results = ytmusic.search(q, limit)
            except Exception as e:
                print(f"yt search via ytmusic failed: {str(e)[:160]!r}", flush=True)
        if results:
            last_backend["search"] = "ytmusic"
        else:
            results = _with_fallbacks("search", _yt_search.search, search_fallbacks, q, limit)
        for r in results:
            r["source"] = self.key
        return results

    def resolve(self, native_id, turn=None):
        return _with_fallbacks("resolve", lambda vid: _extract(vid, turn), resolve_fallbacks, native_id)

    def art(self, native_id, size):
        square = ytmusic.square_art(native_id, size)  # known once a YT Music response listed the track
        if square:
            return ("redirect", square)
        # mq 320x180, hq 480x360 (always exists), maxres 1280x720 (may be missing; the client falls back)
        name = "mqdefault" if size <= 200 else "maxresdefault" if size >= 600 else "hqdefault"
        return ("redirect", f"https://i.ytimg.com/vi/{native_id}/{name}.jpg")


SOURCE = YouTube()


def lookup(video_id):
    """Basic metadata for one video via yt-dlp (pasted links when YouTube Music is off or failed).
    Uses the resolver's YoutubeDL, so it costs one full extraction."""
    global _ydl
    with _ydl_lock:
        if _ydl is None:
            _ydl = yt_dlp.YoutubeDL(ydl_options())
        info = _ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False, process=False)
    return {
        "id": video_id,
        "title": info.get("track") or info.get("title") or "",
        "channel": info.get("artist") or info.get("channel") or info.get("uploader") or "",
        "album": info.get("album"),
        "duration": info.get("duration"),
        "thumb": f"https://i.ytimg.com/vi/{video_id}/mqdefault.jpg",
        "source": "yt",
    }


_flat = None
_flat_lock = threading.Lock()


def playlist_flat(list_id, limit=100):
    """(title, tracks) for a playlist via yt-dlp flat extraction: ids, titles, channels, durations."""
    global _flat
    with _flat_lock:
        if _flat is None:
            _flat = yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "extract_flat": "in_playlist",
                                      "skip_download": True, "extractor_args": config.yt_extractor_args()})
        _flat.params["playlistend"] = limit
        info = _flat.extract_info(f"https://www.youtube.com/playlist?list={list_id}", download=False)
    tracks = []
    for e in info.get("entries") or []:
        vid = (e or {}).get("id")
        if isinstance(vid, str) and len(vid) == 11 and all(c.isalnum() or c in "-_" for c in vid):
            tracks.append({"id": vid, "title": e.get("title") or "", "duration": e.get("duration"),
                           "channel": e.get("channel") or e.get("uploader") or "",
                           "thumb": f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg", "source": "yt"})
    return info.get("title"), tracks[:limit]
