"""Monochrome's tracks API (https://tracks.monochrome.st): lossless FLAC search + stream."""
import threading
import time
from urllib.parse import quote, urlencode

import config
import history
import settings

from . import failover
from .base import BROWSER_UA, Source

_HEALTHY_TTL = 3600
_MAX_REMEMBERED = 2000


class Monochrome(Source):
    key = "mc"
    name = "Monochrome"
    can_keep = True
    codec_label = "FLAC"

    def __init__(self, instances, setting=None):
        self._instances = list(instances)
        self._setting = setting  # settings key whose override (Settings / status sheet) replaces the list
        self._tracks = {}  # native id -> {title, artists, artwork, releaseId, duration}
        self._albums = {}  # releaseId -> album title ("" when the lookup failed)
        self._healthy = (None, 0.0)  # (instance base, expires)
        self._lock = threading.Lock()

    @property
    def instances(self):
        return settings.get(self._setting) if self._setting else self._instances

    def enabled(self):
        return bool(self.instances)

    def search(self, q, limit=config.SEARCH_LIMIT):
        body = failover.get_json(self.instances, "/search/tracks?" + urlencode({"q": q, "limit": limit}))
        out = []
        for t in body.get("tracks") or []:
            tid = str(t.get("id") or "")
            if not tid or t.get("playable") is False:
                continue
            artists = ", ".join(a for a in t.get("artistNames") or [] if a)
            dur = round(t["duration"] / 1000) if isinstance(t.get("duration"), (int, float)) else None
            meta = {"title": t.get("title") or "", "artists": artists, "artwork": t.get("artwork") or "",
                    "releaseId": str(t.get("releaseId") or ""), "duration": dur, "isrc": t.get("isrc") or ""}
            with self._lock:
                if len(self._tracks) >= _MAX_REMEMBERED:
                    self._tracks.clear()
                self._tracks[tid] = meta
            out.append({
                "id": self.track_id(tid),
                "title": meta["title"],
                "channel": artists,
                "duration": dur,
                "thumb": meta["artwork"],
                "art": meta["artwork"],
                "source": self.key,
                "codec": "flac",
                "releaseId": meta["releaseId"],
                "isrc": t.get("isrc") or "",
            })
        return out

    def _base_for(self, path):
        base, expires = self._healthy
        if base and expires > time.time() and base in self.instances:
            return base
        r = failover.request(self.instances, path, headers={"Range": "bytes=0-0"}, stream=True)
        r.close()
        r.raise_for_status()
        self._healthy = (r.base, time.time() + _HEALTHY_TTL)
        return r.base

    def resolve(self, native_id, turn=None):
        path = f"/track/{quote(native_id, safe='')}"
        return {"url": self._base_for(path) + path, "headers": {"User-Agent": BROWSER_UA},
                "mime": "audio/flac", "ext": "flac"}

    def forget(self, native_id):
        self._healthy = (None, 0.0)  # pick an instance afresh

    def _artwork(self, native_id):
        url = self._tracks.get(native_id, {}).get("artwork")
        if url:
            return url
        entry = history.get(self.track_id(native_id)) or {}  # after a restart, for kept tracks
        return entry.get("art") or None

    def art(self, native_id, size):
        url = self._artwork(native_id)
        return ("redirect", url) if url else None

    def _album(self, release_id):
        if not release_id:
            return ""
        if release_id not in self._albums:
            try:
                body = failover.get_json(self.instances, f"/releases/{quote(release_id, safe='')}")
                self._albums[release_id] = body.get("title") or body.get("name") or ""
            except Exception:
                self._albums[release_id] = ""
        return self._albums[release_id]

    def info(self, native_id, hint=None):
        t = self._tracks.get(native_id)
        if t is None and hint and hint.get("title"):
            # No metadata route for one track id (e.g. after a restart): search again and find it.
            try:
                self.search(" ".join(x for x in (hint.get("title"), hint.get("artist")) if x), 20)
            except Exception:
                pass
            t = self._tracks.get(native_id)
        t = t or {}
        return {"title": t.get("title"), "artist": t.get("artists"), "album": self._album(t.get("releaseId")),
                "art": self._artwork(native_id), "duration": t.get("duration"), "isrc": t.get("isrc")}


SOURCE = Monochrome(config.TRACKS_API, setting="tracks_api")
