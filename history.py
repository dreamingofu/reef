"""Kept tracks (`history.json`), newest first: {id, title, channel, duration, file, kept_at, nd_id?, nd_cover?, ...}.

These entries are the user's library: kept state, the Navidrome id (P1.5) and covers are looked up here, so an
entry with a `file` is never dropped. Only file-less entries (none are written today) are capped at _MAX.
Reads go through store.JsonFile: one parsed copy in memory, re-read only when the file changes, so marking every
search result as kept costs a stat(), not a parse.
"""
import time

import store
from config import HISTORY_FILE

_MAX = 500  # entries without a file
_file = store.JsonFile(lambda: HISTORY_FILE, list)  # module global read per call: tests patch HISTORY_FILE


def _cap(items):
    """Every entry with a file, plus the newest _MAX without one."""
    out, loose = [], 0
    for i in items:
        if not i.get("file"):
            loose += 1
            if loose > _MAX:
                continue
        out.append(i)
    return out


def _entries():
    return [i for i in _file.load() if isinstance(i, dict)]


def add(entry):
    with _file.lock:
        items = [i for i in _entries() if i.get("id") != entry["id"]]
        items.insert(0, {**entry, "kept_at": time.time()})
        _file.save(_cap(items))


def get(track_id):
    with _file.lock:
        return next((dict(i) for i in _entries() if i.get("id") == track_id), None)


def update(track_id, **fields):
    """Merge `fields` into the entry for `track_id`; returns False when it isn't in history."""
    with _file.lock:
        items = _entries()
        for i in items:
            if i.get("id") == track_id:
                i.update(fields)
                _file.save(items)
                return True
        return False


def list_recent(limit=50):
    """The newest `limit` entries (copies); limit=None returns every entry."""
    with _file.lock:
        items = _entries()
        return [dict(i) for i in (items if limit is None else items[:limit])]


def ids():
    with _file.lock:
        return {i.get("id") for i in _entries()}


def nd_ids():
    """{track id: Navidrome song id or None} for every kept track (one pass, for marking lists)."""
    with _file.lock:
        return {i.get("id"): i.get("nd_id") for i in _entries()}
