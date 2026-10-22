"""Liked tracks (`likes.json`): [{id, title, channel, album, duration, artists?, album_id?, source, liked_at}],
newest first. A like is just a mark: it doesn't keep the file (Keep does), so any source's track can be liked.
"""
import time

import config
import store

MAX = 10000

_file = store.JsonFile(lambda: config.LIKES_FILE, list)


def entries():
    with _file.lock:
        return [dict(e) for e in _file.load()]


def ids():
    with _file.lock:
        return {e.get("id") for e in _file.load()}


def get(track_id):
    with _file.lock:
        return next((dict(e) for e in _file.load() if e.get("id") == track_id), None)


def add(track):
    """Like a cleaned track (store.clean_track): (entry, created). Liking again refreshes the metadata
    but keeps the entry's place and `liked_at`, so a repeated PUT is harmless."""
    with _file.lock:
        items = _file.load()
        for i, e in enumerate(items):
            if e.get("id") == track["id"]:
                items[i] = {**track, "album": track.get("album", e.get("album", "")), "liked_at": e.get("liked_at")}
                _file.save(items)
                return dict(items[i]), False
        entry = {**track, "album": track.get("album", ""), "liked_at": time.time()}
        items.insert(0, entry)
        del items[MAX:]
        _file.save(items)
        return dict(entry), True


def remove(track_id):
    with _file.lock:
        items = _file.load()
        keep = [e for e in items if e.get("id") != track_id]
        if len(keep) == len(items):
            return False
        _file.save(keep)
        return True
