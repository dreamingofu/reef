import yt_dlp

from config import SEARCH_LIMIT

_OPTS = {
    "quiet": True,
    "no_warnings": True,
    "extract_flat": True,
    "skip_download": True,
}


def search(query, limit=SEARCH_LIMIT):
    with yt_dlp.YoutubeDL(_OPTS) as ydl:
        info = ydl.extract_info(f"ytsearch{limit}:{query}", download=False)
    results = []
    for entry in info.get("entries") or []:
        if not entry or not entry.get("id"):
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
