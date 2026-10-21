import re
import threading

import yt_dlp

from config import SEARCH_LIMIT, yt_extractor_args

_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")  # flat ytsearch also yields channels/playlists

_OPTS = {
    "quiet": True,
    "no_warnings": True,
    "extract_flat": True,
    "skip_download": True,
    "extractor_args": yt_extractor_args(),
}

# YoutubeDL is not thread-safe; share one lazily built instance behind a lock.
_ydl = None
_ydl_lock = threading.Lock()


def search(query, limit=SEARCH_LIMIT):
    global _ydl
    with _ydl_lock:
        if _ydl is None:
            _ydl = yt_dlp.YoutubeDL(_OPTS)
        info = _ydl.extract_info(f"ytsearch{limit}:{query}", download=False)
    results = []
    for entry in info.get("entries") or []:
        if not entry or not _VIDEO_ID.match(entry.get("id") or ""):
            continue
        vid = entry["id"]
        results.append({
            "id": vid,
            "title": entry.get("title") or "",
            "channel": entry.get("channel") or entry.get("uploader") or "",
            "duration": entry.get("duration"),
            "thumb": f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg",
        })
    return results
