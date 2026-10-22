"""Shared plumbing for Reef's small JSON stores (listens, likes, playlists, settings).

`JsonFile` keeps one parsed copy in memory (the listen log alone can be a few MB) and re-reads it only when the
file changed on disk (mtime/size), e.g. after a restore. Writes go to `<name>.tmp` and are swapped in with
os.replace, so a crash mid-write leaves the old file intact. Callers hold `.lock` around read-modify-write.

`clean_track` is the one place a track dict sent by the client is trimmed to known fields and sizes before
it's stored, so a store never keeps whatever else the UI had on the object.
"""
import json
import math
import os
import re
import threading

KEYS = ("yt", "mc", "nd")  # source keys a stored track id may carry (also ones that are off right now)
VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
TRACK_ID = re.compile(r"^(?:([a-z]{2}):)?([\w.-]{1,128})$")
TEXT_MAX = 200
ARTISTS_MAX = 10


class JsonFile:
    def __init__(self, path_fn, default):
        self._path_fn = path_fn  # read per call: tests point config.*_FILE elsewhere
        self._default = default
        self._cache = None
        self._sig = None
        self.lock = threading.RLock()

    @property
    def path(self):
        return self._path_fn()

    def _signature(self, path):
        try:
            st = path.stat()
        except OSError:
            return (str(path), None)
        return (str(path), st.st_mtime_ns, st.st_size)

    def load(self):
        """The parsed data (shared, mutate only under `.lock` and then `save`). Missing or broken = default."""
        with self.lock:
            path = self.path
            sig = self._signature(path)
            if self._cache is None or sig != self._sig:
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    data = None
                self._cache = data if isinstance(data, type(self._default())) else self._default()
                self._sig = sig
            return self._cache

    def save(self, data):
        with self.lock:
            path = self.path
            tmp = path.with_name(path.name + ".tmp")
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
                os.replace(tmp, path)
            except Exception:
                self._cache = None  # the in-memory copy may be ahead of the file now: re-read next time
                raise
            self._cache = data
            self._sig = self._signature(path)


def track_id(raw):
    """The canonical id (bare for YouTube, "mc:…"/"nd:…" otherwise) of a well-formed track id, else None.
    Sources that are off still count: a liked Monochrome track stays liked while Monochrome is down."""
    if not isinstance(raw, str):
        return None
    m = TRACK_ID.match(raw)
    if not m:
        return None
    key, native = m.group(1) or "yt", m.group(2)
    if key not in KEYS or (key == "yt" and not VIDEO_ID.match(native)):
        return None
    return native if key == "yt" else f"{key}:{native}"


def source_of(tid):
    return tid[:2] if tid[2:3] == ":" else "yt"


def text(value, limit=TEXT_MAX):
    return value.strip()[:limit] if isinstance(value, str) else ""


def seconds(value, limit=86400):
    """A finite number of seconds in [0, limit), else None (bools and nan/inf too)."""
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        v = float(value)
    except ValueError:
        return None
    return v if math.isfinite(v) and 0 <= v < limit else None


def clean_track(raw):
    """A stored track: {id, title, channel, duration, source, album?, album_id?, artists?, explicit?}, or None
    when the id isn't valid."""
    if not isinstance(raw, dict):
        return None
    tid = track_id(raw.get("id"))
    if not tid:
        return None
    d = seconds(raw.get("duration"))
    t = {"id": tid, "title": text(raw.get("title")), "channel": text(raw.get("channel")),
         "duration": round(d) if d else None, "source": source_of(tid)}
    if text(raw.get("album")):
        t["album"] = text(raw.get("album"))
    album_id = raw.get("album_id")
    if isinstance(album_id, str) and re.fullmatch(r"[\w-]{1,80}", album_id):
        t["album_id"] = album_id
    artists = []
    for a in raw["artists"] if isinstance(raw.get("artists"), list) else []:
        if isinstance(a, dict) and text(a.get("name")):
            aid = a.get("id")
            artists.append({"name": text(a.get("name")),
                            "id": aid if isinstance(aid, str) and re.fullmatch(r"[\w-]{1,64}", aid) else None})
    if artists:
        t["artists"] = artists[:ARTISTS_MAX]
    if raw.get("explicit") is True:
        t["explicit"] = True
    return t
