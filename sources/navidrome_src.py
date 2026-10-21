"""The Navidrome library itself, over Subsonic: search3 + stream?format=raw. Already kept by definition."""
import threading
from urllib.parse import urlencode

import config
import navidrome

from .base import Source

_MAX_REMEMBERED = 2000


class NavidromeLibrary(Source):
    key = "nd"
    name = "Library"
    can_keep = False
    codec_label = ""

    def __init__(self):
        self._songs = {}  # song id -> {cover, suffix}, filled by search
        self._lock = threading.Lock()

    def enabled(self):
        return navidrome.configured()

    def _remember(self, song_id, **fields):
        with self._lock:
            if len(self._songs) >= _MAX_REMEMBERED:
                self._songs.clear()
            self._songs[song_id] = fields

    def search(self, q, limit=config.SEARCH_LIMIT):
        body = navidrome._call("search3", query=q, songCount=limit, albumCount=0, artistCount=0)
        out = []
        for s in (body.get("searchResult3") or {}).get("song") or []:
            sid = s.get("id")
            if not sid:
                continue
            suffix = (s.get("suffix") or "").lower()
            cover = s.get("coverArt") or f"mf-{sid}"
            self._remember(sid, cover=cover, suffix=suffix)
            out.append({
                "id": self.track_id(sid),
                "title": s.get("title") or "",
                "channel": s.get("artist") or "",
                "album": s.get("album") or "",
                "duration": s.get("duration"),
                "source": self.key,
                "codec": suffix,
                "cover": cover,
                "kept": True,
            })
        return out

    def resolve(self, native_id, turn=None):
        params = {**navidrome._auth_params(), "id": native_id, "format": "raw"}
        params.pop("f", None)
        return {
            "url": f"{config.NAVIDROME_URL}/rest/stream?{urlencode(params)}",
            "headers": {},
            "mime": None,  # upstream Content-Type matches the file
            "ext": self._songs.get(native_id, {}).get("suffix") or None,
        }

    def art(self, native_id, size):
        cover = self._songs.get(native_id, {}).get("cover") or f"mf-{native_id}"
        try:
            data, mime = navidrome.cover_art(cover, size)
        except Exception:
            return None
        return ("bytes", data, mime) if mime.startswith("image/") else None


SOURCE = NavidromeLibrary()
