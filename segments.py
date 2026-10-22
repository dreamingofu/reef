"""SponsorBlock at play time (production plan P1.9): the non-music parts of a YouTube video (talking intros, skits,
outros) that the player jumps over. Keeps use yt-dlp's own SponsorBlock cut instead (downloader.py).

Lookups go by the first 4 hex chars of sha256(video id), so SponsorBlock only learns a bucket shared with many other
videos, never which one is playing; the answer lists every video in the bucket and we pick ours.
Cached in memory only (a restart costs one request per track): a result, or "none" (404 or not in the bucket),
for 7 days; an error for 5 minutes, so a SponsorBlock outage isn't asked about on every track change.
"""
import hashlib
import json
import logging
import threading
import time
from collections import OrderedDict

import requests

logger = logging.getLogger(__name__)

API = "https://sponsor.ajay.app/api/skipSegments/"
CATEGORIES = ["music_offtopic"]
TIMEOUT = (4, 6)
TTL = 7 * 86400
ERROR_TTL = 300
CACHE_MAX = 2000
MIN_LEN = 1.0  # shorter segments aren't worth a seek (and a seek costs a rebuffer on a slow link)

_HEADERS = {"User-Agent": "Reef (https://github.com/dreamingofu/reef)"}
_session = requests.Session()
_cache = OrderedDict()  # video id -> (monotonic expiry, segments list | None for an error)
_lock = threading.Lock()


class Unavailable(Exception):
    """SponsorBlock couldn't be asked (network, 5xx, 429, junk); not cached as "none"."""


def prefix(video_id):
    return hashlib.sha256(video_id.encode("utf-8")).hexdigest()[:4]


def _parse(body, video_id):
    """[{start, end, category}] for `video_id` from a hash-prefix answer, sorted, junk dropped."""
    if not isinstance(body, list):
        raise Unavailable("unexpected answer")
    out = []
    for entry in body:
        if not isinstance(entry, dict) or entry.get("videoID") != video_id:
            continue
        for s in entry.get("segments") or []:
            if not isinstance(s, dict) or s.get("category") not in CATEGORIES or s.get("actionType", "skip") != "skip":
                continue
            seg = s.get("segment")
            try:
                start, end = float(seg[0]), float(seg[1])
            except (TypeError, ValueError, IndexError):
                continue
            if start >= 0 and end - start >= MIN_LEN:
                out.append({"start": round(start, 3), "end": round(end, 3), "category": s["category"]})
    return sorted(out, key=lambda s: s["start"])


def _fetch(video_id):
    params = {"categories": json.dumps(CATEGORIES), "actionTypes": json.dumps(["skip"]), "service": "YouTube"}
    try:
        r = _session.get(API + prefix(video_id), params=params, headers=_HEADERS, timeout=TIMEOUT)
    except requests.RequestException as e:
        raise Unavailable(str(e)[:200]) from None
    if r.status_code == 404:  # nothing in the whole bucket
        return []
    if r.status_code != 200:
        raise Unavailable(f"SponsorBlock {r.status_code}")
    try:
        return _parse(r.json(), video_id)
    except ValueError:
        raise Unavailable("SponsorBlock sent something that isn't JSON") from None


def _put(video_id, value, ttl):
    with _lock:
        _cache[video_id] = (time.monotonic() + ttl, value)
        _cache.move_to_end(video_id)
        while len(_cache) > CACHE_MAX:
            _cache.popitem(last=False)


def for_video(video_id):
    """The video's skippable segments ([] for none). Raises Unavailable when SponsorBlock can't say (now, or in
    the last 5 minutes)."""
    with _lock:
        hit = _cache.get(video_id)
        if hit and hit[0] > time.monotonic():
            _cache.move_to_end(video_id)
            if hit[1] is None:
                raise Unavailable("SponsorBlock was unreachable moments ago")
            return [dict(s) for s in hit[1]]
    try:
        segs = _fetch(video_id)
    except Unavailable as e:
        logger.warning(f"segments id={video_id} error={e}")
        _put(video_id, None, ERROR_TTL)
        raise
    _put(video_id, segs, TTL)
    return [dict(s) for s in segs]


def clear():
    with _lock:
        _cache.clear()
