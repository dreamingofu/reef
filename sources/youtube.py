"""YouTube via yt-dlp: search (search.py) and stream URL resolution.

D3 hook: append callables to `search_fallbacks` (`f(q, limit) -> [track]`) and `resolve_fallbacks`
(`f(video_id) -> {"url", "headers", "mime", "ext"}`). They are tried in order, only after yt-dlp
raised; the first that returns without raising wins, otherwise yt-dlp's error is re-raised.
Set `last_backend` to say which one answered (reported by /api/health).
Piped (sources/piped.py) and Invidious (sources/invidious.py) are registered by sources/__init__.py
when REEF_PIPED_INSTANCES / REEF_INVIDIOUS_INSTANCES are set.
"""
import threading

import yt_dlp

import config
import search as _yt_search

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
        results = _with_fallbacks("search", _yt_search.search, search_fallbacks, q, limit)
        for r in results:
            r["source"] = self.key
        return results

    def resolve(self, native_id, turn=None):
        return _with_fallbacks("resolve", lambda vid: _extract(vid, turn), resolve_fallbacks, native_id)

    def art(self, native_id, size):
        # mq 320x180, hq 480x360 (always exists), maxres 1280x720 (may be missing; the client falls back)
        name = "mqdefault" if size <= 200 else "maxresdefault" if size >= 600 else "hqdefault"
        return ("redirect", f"https://i.ytimg.com/vi/{native_id}/{name}.jpg")


SOURCE = YouTube()
