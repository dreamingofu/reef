"""Piped API: fallback search/stream for the `yt` source, used only when yt-dlp fails.

Enabled by REEF_PIPED_INSTANCES (registered in sources/__init__.py); instances must allow API access.
"""
import re
from urllib.parse import quote

import settings

from . import failover

MAX_INSTANCES = 3   # tried per call, so a dead list can't stall a stream request
BUDGET = 12         # seconds for one call across all its instances

_VIDEO_ID = re.compile(r"[?&]v=([A-Za-z0-9_-]{11})(?:&|$)")


def _instances():
    instances = settings.get("piped_instances")  # REEF_PIPED_INSTANCES unless set in Settings
    if not instances:
        raise RuntimeError("no Piped instances configured")
    return instances[:MAX_INSTANCES]


def search(q, limit):
    data = failover.get_json(_instances(), f"/search?q={quote(q)}&filter=music_songs", budget=BUDGET)
    results = []
    for item in data.get("items") or []:
        m = _VIDEO_ID.search(item.get("url") or "")
        if not m:
            continue
        vid = m.group(1)
        dur = item.get("duration")
        results.append({
            "id": vid,
            "title": item.get("title") or "",
            "channel": item.get("uploaderName") or "",
            "duration": dur if isinstance(dur, (int, float)) and dur > 0 else None,
            "thumb": f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg",
        })
        if len(results) >= limit:
            break
    return results


def resolve(video_id):
    data = failover.get_json(_instances(), f"/streams/{video_id}", budget=BUDGET)
    streams = [s for s in data.get("audioStreams") or [] if s.get("url")]
    if not streams:
        raise RuntimeError(f"Piped has no audio streams for {video_id}")
    mp4 = [s for s in streams if (s.get("mimeType") or "").startswith("audio/mp4")]
    best = max(mp4 or streams, key=lambda s: s.get("bitrate") or 0)
    mime = (best.get("mimeType") or "").split(";")[0].strip() or None
    return {
        "url": best["url"],
        "headers": {},
        "mime": mime,
        "ext": "m4a" if mime == "audio/mp4" else "webm",
    }


search.backend = resolve.backend = "piped"
