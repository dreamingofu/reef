"""Artist aliases (`aliases.json`, production plan P2): the name to show and tag for an artist, e.g. "Beyoncé" for
"BeyonceVEVO", or a transliteration.

Entries are {id: YouTube channel id (UC…) | null, match: the name as sources give it | null, name, at}; an id beats a
name. Names compare like the blocklist's (`blocklist.key`: no " - Topic" / "VEVO", casefolded, punctuation dropped),
so one entry for "BeyonceVEVO" also covers "Beyonce" and "Beyonce - Topic".

Applied where Reef hands lists to the client (app.py: every track list, album / artist page headers, Home) and to the
artist / album artist tags of new keeps (downloader.py). What's stored (history, likes, listens) and what lyrics
lookups and matching use stay the source's names, and blocking still matches the original names.
"""
import re
import time

import config
import store
from blocklist import key

NAME_MAX = 200
CHANNEL_ID = re.compile(r"^UC[A-Za-z0-9_-]{22}$")

_file = store.JsonFile(lambda: config.ALIASES_FILE, list)


def _valid(e):
    return isinstance(e, dict) and isinstance(e.get("name"), str) and e["name"].strip() \
        and (CHANNEL_ID.match(str(e.get("id") or "")) or (isinstance(e.get("match"), str) and key(e["match"])))


def entries():
    """Every alias, oldest first (copies)."""
    with _file.lock:
        return [dict(e) for e in _file.load() if _valid(e)]


def _same(e, artist_id, match_key):
    return bool((artist_id and e.get("id") == artist_id) or (match_key and e.get("match") and key(e["match"]) == match_key))


def put(name, artist_id=None, match=None):
    """Add or change an alias: (entry, created). An entry for the same id, or the same name when there's no id,
    gets the new display name (and the id / name it lacked). Raises ValueError for a bad request."""
    name = name.strip() if isinstance(name, str) else ""
    match = match.strip() if isinstance(match, str) else ""
    artist_id = artist_id.strip() if isinstance(artist_id, str) else ""
    if not name or len(name) > NAME_MAX or len(match) > NAME_MAX:
        raise ValueError(f"name must be 1 to {NAME_MAX} characters")
    if artist_id and not CHANNEL_ID.match(artist_id):
        raise ValueError("id must be a YouTube channel id (UC…)")
    if not artist_id and not key(match):
        raise ValueError("send the artist's channel id or the name to replace")
    match_key = key(match) if match else ""
    with _file.lock:
        items = [e for e in _file.load() if _valid(e)]
        found = next((e for e in items if _same(e, artist_id, None)), None) if artist_id else None
        found = found or next((e for e in items if _same(e, None, match_key) and (not artist_id or not e.get("id"))),
                              None)
        if found:
            found.update(name=name, at=time.time())
            if artist_id:
                found["id"] = artist_id
            if match:
                found["match"] = match
            _file.save(items)
            return dict(found), False
        entry = {"id": artist_id or None, "match": match or None, "name": name, "at": time.time()}
        items.append(entry)
        _file.save(items)
        return dict(entry), True


def remove(artist_id=None, match=None):
    """Drop aliases for this channel id or name; False when none matched."""
    match_key = key(match) if match else ""
    with _file.lock:
        items = [e for e in _file.load() if _valid(e)]
        left = [e for e in items if not _same(e, artist_id, match_key)]
        if len(left) == len(items):
            return False
        _file.save(left)
        return True


class Resolver:
    """A snapshot of the aliases for one request: dict lookups per name, nothing when there are none."""

    def __init__(self, items):
        self.ids = {e["id"]: e["name"] for e in items if e.get("id")}
        self.names = {key(e["match"]): e["name"] for e in items if e.get("match") and key(e["match"])}

    def __bool__(self):
        return bool(self.ids or self.names)

    def name(self, name, artist_id=None):
        """The display name for an artist: by channel id first, then by name; the name itself otherwise."""
        if not self:
            return name
        if artist_id and artist_id in self.ids:
            return self.ids[artist_id]
        return self.names.get(key(name), name) if name else name

    def track(self, t):
        """Rename a track dict's artists in place; `channel` follows them (or is renamed itself when the track
        only knows its uploading channel, as yt-dlp results do)."""
        artists = t.get("artists")
        if isinstance(artists, list) and artists:
            renamed = [{**a, "name": self.name(a["name"], a.get("id"))}
                       if isinstance(a, dict) and isinstance(a.get("name"), str) and a["name"] else a for a in artists]
            if renamed != artists:  # a new list: the old one may be shared with a response cache
                t["artists"] = renamed
                t["channel"] = ", ".join(a["name"] for a in renamed if isinstance(a, dict) and a.get("name"))
        elif isinstance(t.get("channel"), str) and t["channel"]:
            t["channel"] = self.name(t["channel"])
        return t

    def tracks(self, tracks):
        if self:
            for t in tracks:
                if isinstance(t, dict):
                    self.track(t)
        return tracks


def resolver():
    return Resolver(entries())
