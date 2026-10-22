"""Blocked artists: `blocklist.json` entries {id: YouTube channel id|null, name, at}.

Lists are filtered where Reef hands tracks to the client (search, radio, playlists, album pages, tiles).
A track is blocked when one of its artist ids matches, or one of its artist names does after casefolding and
dropping punctuation/spacing, so "Daft Punk", "Daft Punk - Topic" and "DaftPunkVEVO" (yt-dlp results only
know the channel) all match. Names can collide across artists; ids can't, but yt-dlp results don't have them.
"""
import json
import os
import re
import threading
import time

import config
from tagging import clean_artist

NAME_MAX = 200
_lock = threading.Lock()


def key(name):
    """Comparable form of an artist name ('' for none)."""
    folded = clean_artist(name).casefold()
    return re.sub(r"[\W_]+", "", folded) or folded.strip()  # "!!!" is a band; keep it matchable


def _read():
    try:
        data = json.loads(config.BLOCKLIST_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [e for e in data if isinstance(e, dict) and isinstance(e.get("name"), str)] if isinstance(data, list) else []


def _write(items):
    path = config.BLOCKLIST_FILE
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def entries():
    with _lock:
        return _read()


def _same(entry, artist_id, name_key):
    return (artist_id and entry.get("id") == artist_id) or key(entry["name"]) == name_key


def add(artist_id, name):
    """(entry, created). An existing entry for the same id or name is returned (and gains the id if it lacked one)."""
    name = name.strip()[:NAME_MAX]
    with _lock:
        items = _read()
        for e in items:
            if _same(e, artist_id, key(name)):
                if artist_id and not e.get("id"):
                    e["id"] = artist_id
                    _write(items)
                return e, False
        entry = {"id": artist_id or None, "name": name, "at": time.time()}
        items.append(entry)
        _write(items)
        return entry, True


def remove(artist_id=None, name=None):
    """Drop entries matching the id or the name; False when nothing matched."""
    name_key = key(name) if name else None
    with _lock:
        items = _read()
        keep = [e for e in items if not ((artist_id and e.get("id") == artist_id) or (name_key and key(e["name"]) == name_key))]
        if len(keep) == len(items):
            return False
        _write(keep)
        return True


class Matcher:
    """A snapshot of the list for one request (the file is read once, not per track)."""

    def __init__(self, items):
        self.ids = {e["id"] for e in items if e.get("id")}
        self.names = {k for k in (key(e["name"]) for e in items) if k}

    def __bool__(self):
        return bool(self.ids or self.names)

    def artist(self, artist_id=None, name=None):
        return bool((artist_id and artist_id in self.ids) or (name and key(name) in self.names))

    def any_artist(self, artists):
        return any(self.artist(a.get("id"), a.get("name")) for a in artists or [] if isinstance(a, dict))

    def track(self, t):
        artists = t.get("artists")
        if artists:
            return self.any_artist(artists)
        # yt-dlp / Piped results only carry the uploading channel ("Daft Punk - Topic", "RickAstleyVEVO").
        return self.artist(name=t.get("channel"))

    def tracks(self, tracks):
        return [t for t in tracks if not self.track(t)] if self else list(tracks)


def matcher():
    return Matcher(entries())


def filter_tracks(tracks):
    return matcher().tracks(tracks)
