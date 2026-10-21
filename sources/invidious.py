"""Invidious API: fallback search/stream for the `yt` source, used only when yt-dlp fails.

Enabled by REEF_INVIDIOUS_INSTANCES (registered in sources/__init__.py); instances must allow API access.
Streams use `latest_version ... local=true`, so the instance proxies the media (no Google IP binding).
"""
from urllib.parse import quote

import config

from . import failover

MAX_INSTANCES = 3   # tried per call, so a dead list can't stall a stream request
BUDGET = 12         # seconds for one call across all its instances


def _instances():
    if not config.INVIDIOUS_INSTANCES:
        raise RuntimeError("no Invidious instances configured")
    return config.INVIDIOUS_INSTANCES[:MAX_INSTANCES]


def search(q, limit):
    data = failover.get_json(_instances(), f"/api/v1/search?q={quote(q)}&type=video", budget=BUDGET)
    results = []
    for item in data if isinstance(data, list) else []:
        vid = item.get("videoId") or ""
        if len(vid) != 11 or item.get("type", "video") != "video":
            continue
        dur = item.get("lengthSeconds")
        results.append({
            "id": vid,
            "title": item.get("title") or "",
            "channel": item.get("author") or "",
            "duration": dur if isinstance(dur, (int, float)) and dur > 0 else None,
            "thumb": f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg",
        })
        if len(results) >= limit:
            break
    return results


def resolve(video_id):
    # Picks a live instance and confirms the video exists there; the media comes from that instance.
    r = failover.request(_instances(), f"/api/v1/videos/{video_id}?fields=videoId", budget=BUDGET)
    r.close()
    if r.status_code != 200:
        raise RuntimeError(f"Invidious {r.base} answered {r.status_code} for {video_id}")
    return {
        "url": f"{r.base}/latest_version?id={video_id}&itag=140&local=true",
        "headers": {},
        "mime": "audio/mp4",
        "ext": "m4a",
    }


search.backend = resolve.backend = "invidious"
