"""Local playlists (`playlists.json`): [{id, name, description, tracks: [track], pinned, created_at, updated_at,
cover_id, exported?, import?}]. Tracks are store.clean_track dicts in play order; a playlist may hold any source's
tracks, and placeholders of an import (importer.py) that aren't matched to a song yet: {id: "pq:<hex>", q: true,
title, artist, channel, duration, album?, isrc?, key?, miss?, miss_at?} (`clean_entry`). The player looks those up
by name (/api/match) before they play; nothing can be kept or exported until they are.

Navidrome imports `.m3u` files it finds in the music folder, so `export` writes one into
`<music>/Reef/Playlists/` with the kept tracks' files (paths relative to the playlist file, which is how
Navidrome resolves them) and the server asks Navidrome to rescan.
"""
import os
import re
import secrets
import threading
import time
import uuid
from pathlib import Path

import config
import history
import store

NAME_MAX = 100
DESC_MAX = 500
TRACKS_MAX = 5000
PLAYLISTS_MAX = 500
ID_LEN = 8
PLACEHOLDER_ID = re.compile(r"^pq:[0-9a-f]{6,32}$")

_file = store.JsonFile(lambda: config.PLAYLISTS_FILE, list)
_export_lock = threading.Lock()  # an export picks its .m3u name, writes it and records it before the next one looks
EXPORT_TRIES = 50


def valid_id(pid):
    return isinstance(pid, str) and len(pid) == ID_LEN and all(c in "0123456789abcdef" for c in pid)


def summary(p):
    """A list row: everything but the tracks, plus their count, total length (s), `unmatched` placeholders still to
    look up and `not_found` ones nothing matched (each only when > 0)."""
    tracks = p.get("tracks") or []
    unmatched = sum(1 for t in tracks if t.get("q") and not t.get("miss"))
    not_found = sum(1 for t in tracks if t.get("q") and t.get("miss"))
    return {**{k: v for k, v in p.items() if k != "tracks"}, "count": len(tracks),
            "duration": int(sum(t.get("duration") or 0 for t in tracks)), **({"unmatched": unmatched} if unmatched else {}),
            **({"not_found": not_found} if not_found else {})}


def _copy(p):
    return {**p, "tracks": [dict(t) for t in p.get("tracks") or []]}


def _touch(p, now=None):
    p["updated_at"] = time.time() if now is None else now
    p["cover_id"] = next((t["id"] for t in p["tracks"] if not t.get("q")), None)  # a placeholder has no art


def all_playlists():
    """Summaries, pinned first, then most recently changed."""
    with _file.lock:
        rows = [summary(p) for p in _file.load()]
    rows.sort(key=lambda p: (not p.get("pinned"), -(p.get("updated_at") or 0)))
    return rows


def get(pid):
    with _file.lock:
        p = next((p for p in _file.load() if p.get("id") == pid), None)
        return _copy(p) if p else None


def _edit(pid, change):
    """Run change(playlist) under the lock and save; returns the playlist copy, None if it doesn't exist.
    `change` may raise ValueError to refuse (nothing is saved)."""
    with _file.lock:
        items = _file.load()
        p = next((p for p in items if p.get("id") == pid), None)
        if p is None:
            return None
        work = _copy(p)
        change(work)
        _touch(work)
        items[items.index(p)] = work
        _file.save(items)
        return _copy(work)


def _name(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("name is required")
    return value.strip()[:NAME_MAX]


def clean_placeholder(raw):
    """A stored placeholder (see the module doc), or None when `raw` isn't one."""
    if not isinstance(raw, dict) or raw.get("q") is not True or not isinstance(raw.get("id"), str) \
            or not PLACEHOLDER_ID.match(raw["id"]) or not store.text(raw.get("title")):
        return None
    artist = store.text(raw.get("artist") or raw.get("channel"))
    d = store.seconds(raw.get("duration"))
    t = {"id": raw["id"], "q": True, "title": store.text(raw.get("title")), "artist": artist, "channel": artist,
         "duration": round(d) if d else None}
    if store.text(raw.get("album")):
        t["album"] = store.text(raw.get("album"))
    isrc = raw.get("isrc")
    if isinstance(isrc, str) and re.fullmatch(r"[A-Z0-9]{12}", isrc):
        t["isrc"] = isrc
    key = raw.get("key")
    if isinstance(key, str) and 0 < len(key) <= 200 and key.isprintable() and "/" not in key:
        t["key"] = key
    if raw.get("miss") is True:
        t["miss"] = True
        if store.seconds(raw.get("miss_at"), 1e11) is not None:
            t["miss_at"] = raw["miss_at"]
    return t


def clean_entry(raw):
    """A playlist entry: a clean track, or a clean placeholder; None for anything else."""
    return clean_placeholder(raw) if isinstance(raw, dict) and raw.get("q") is True else store.clean_track(raw)


def _tracks(raw):
    if not isinstance(raw, list):
        raise ValueError("tracks must be a list")
    return [t for t in map(clean_entry, raw) if t]


def create(name, tracks=None, description="", extra=None):
    """A new playlist (raises ValueError for a bad name or too many playlists/tracks). `extra`: more fields to
    store on it (an import's {"import": {...}})."""
    now = time.time()
    p = {**(extra or {}), "id": uuid.uuid4().hex[:ID_LEN], "name": _name(name),
         "description": store.text(description, DESC_MAX), "tracks": _tracks([] if tracks is None else tracks)[:TRACKS_MAX],
         "pinned": False, "created_at": now}
    _touch(p, now)
    with _file.lock:
        items = _file.load()
        if len(items) >= PLAYLISTS_MAX:
            raise ValueError(f"at most {PLAYLISTS_MAX} playlists")
        while any(x.get("id") == p["id"] for x in items):
            p["id"] = uuid.uuid4().hex[:ID_LEN]
        items.append(p)
        _file.save(items)
    return _copy(p)


def update(pid, fields):
    """PATCH: name, description, pinned (others are refused before anything changes)."""
    unknown = set(fields) - {"name", "description", "pinned"}
    if unknown:
        raise ValueError(f"unknown field: {sorted(unknown)[0]}")
    clean = {}
    if "name" in fields:
        clean["name"] = _name(fields["name"])
    if "description" in fields:
        if fields["description"] is not None and not isinstance(fields["description"], str):
            raise ValueError("description must be a string")
        clean["description"] = store.text(fields["description"], DESC_MAX)
    if "pinned" in fields:
        if not isinstance(fields["pinned"], bool):
            raise ValueError("pinned must be true or false")
        clean["pinned"] = fields["pinned"]
    return _edit(pid, lambda p: p.update(clean))


def delete(pid):
    with _file.lock:
        items = _file.load()
        keep = [p for p in items if p.get("id") != pid]
        if len(keep) == len(items):
            return False
        _file.save(keep)
        return True


def add_tracks(pid, raw, position=None, dedupe=False):
    """Insert tracks at `position` (None/out of range = the end). dedupe: skip ids already in the playlist
    (and repeats within the batch). Returns (playlist, added, skipped) or None."""
    tracks = _tracks(raw)
    if position is not None and (isinstance(position, bool) or not isinstance(position, int)):
        raise ValueError("position must be an integer")
    result = {}

    def change(p):
        have = {t["id"] for t in p["tracks"]}
        new = []
        for t in tracks:
            if dedupe and t["id"] in have:
                continue
            have.add(t["id"])
            new.append(t)
        if len(p["tracks"]) + len(new) > TRACKS_MAX:
            raise ValueError(f"at most {TRACKS_MAX} tracks per playlist")
        at = len(p["tracks"]) if position is None or not 0 <= position <= len(p["tracks"]) else position
        p["tracks"][at:at] = new
        result.update(added=len(new), skipped=len(tracks) - len(new) + (len(raw) - len(tracks)))

    p = _edit(pid, change)
    return (p, result["added"], result["skipped"]) if p else None


def remove_track(pid, index, expect_id=None):
    """Drop the track at `index`. expect_id: refuse (ValueError) when another track is there now, e.g. the
    list changed on another device since the UI loaded it."""
    def change(p):
        if not 0 <= index < len(p["tracks"]):
            raise IndexError(index)
        if expect_id and p["tracks"][index]["id"] != expect_id:
            raise ValueError("the playlist changed; reload it")
        del p["tracks"][index]

    return _edit(pid, change)


def move(pid, src, dst):
    """Move the track at `src` so it ends up at `dst` (both indexes into the current list)."""
    def change(p):
        n = len(p["tracks"])
        if not (0 <= src < n and 0 <= dst < n):
            raise IndexError(src)
        p["tracks"].insert(dst, p["tracks"].pop(src))

    return _edit(pid, change)


def replace_entries(pid, changes):
    """Swap entries by id ({entry id: new entry}), wherever they are now: an import's background pass replaces
    placeholders with the songs they matched while the user may be moving or removing rows. Entries that aren't
    there any more are skipped. Returns the playlist, None if it doesn't exist."""
    clean = {k: clean_entry(v) for k, v in changes.items()}

    def change(p):
        p["tracks"] = [clean.get(t["id"]) or t for t in p["tracks"]]

    return _edit(pid, change)


def set_exported(pid, path):
    """Record the playlist's export; False when the playlist is gone."""
    with _file.lock:
        items = _file.load()
        for p in items:
            if p.get("id") == pid:
                p["exported"] = {"path": path, "at": time.time()}
                _file.save(items)
                return True
        return False


def _exports_of_others(pid):
    """The export paths (relative to the music folder) other playlists have recorded."""
    with _file.lock:
        return {p["exported"]["path"] for p in _file.load()
                if p.get("id") != pid and isinstance(p.get("exported"), dict) and isinstance(p["exported"].get("path"), str)}


# ---------- .m3u ----------

def _kept_file(track_id):
    entry = history.get(track_id)
    f = entry.get("file") if entry else None
    return f if f and os.path.exists(f) else None


def _extinf(t):
    artist = t["artists"][0]["name"] if t.get("artists") else t.get("channel") or ""
    label = f"{artist} - {t.get('title') or ''}" if artist else t.get("title") or ""
    return f"#EXTINF:{int(t['duration']) if t.get('duration') else -1},{' '.join(label.split())}"


def _rel(path, base):
    """`path` relative to `base` (forward slashes, as Navidrome reads them) when it's inside the music folder,
    else absolute (a file Navidrome can't see anyway)."""
    p, music = Path(path).resolve(), Path(config.MUSIC_DIR).resolve()
    if music not in p.parents:
        return str(path)
    return os.path.relpath(p, Path(base).resolve()).replace(os.sep, "/")


def m3u(p, base=None, kept_only=False):
    """(text, kept, skipped). Kept tracks point at their file, relative to `base` (default: the music folder);
    other YouTube tracks at their watch URL (players that stream URLs can use them; Navidrome skips them),
    others are left as a comment. kept_only drops everything that isn't a kept file."""
    base = base or config.MUSIC_DIR
    lines, kept, skipped = ["#EXTM3U", f"#PLAYLIST:{' '.join(p['name'].split())}"], 0, 0
    for t in p.get("tracks") or []:
        f = _kept_file(t["id"])
        if f:
            lines += [_extinf(t), _rel(f, base)]
            kept += 1
            continue
        skipped += 1
        if kept_only:
            continue
        if t.get("source") == "yt":
            lines += [_extinf(t), f"https://www.youtube.com/watch?v={t['id']}"]
        else:
            lines.append(f"# not kept: {t['id']} {' '.join((t.get('title') or '').split())}")
    return "\n".join(lines) + "\n", kept, skipped


def export_dir():
    return Path(config.DOWNLOAD_DIR) / "Playlists"


def export_name(name):
    """A safe file name for a playlist name (same rules as kept tracks' file names)."""
    from downloader import _filename  # lazy: the downloader pulls in yt-dlp
    name = _filename(name).strip(". ")  # no "..", no hidden files
    return name or "Playlist"


def _write_new(path, text):
    """Write `text` to `path` through a temporary file created exclusively (an existing file or link named like it is
    never opened, so its target is never written), then swapped in: os.replace replaces a link or hard link at `path`
    itself, never what it points at. The temporary file is removed when anything fails."""
    path = Path(path)
    for _ in range(100):
        tmp = path.with_name(f".{path.name[:150]}.{secrets.token_hex(4)}.tmp")
        try:
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
        except FileExistsError:
            continue
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
            os.replace(tmp, path)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        return
    raise FileExistsError(f"no free temporary name next to {path.name}")


def _music_rel(path):
    return os.path.relpath(path, config.MUSIC_DIR).replace(os.sep, "/")


def export(pid):
    """Write `<music>/Reef/Playlists/<name>.m3u` with the kept tracks. Returns {path (relative to the music
    folder), tracks, skipped}, None when the playlist doesn't exist; ValueError when nothing in it is kept.
    A name another playlist's export uses, or a file Reef didn't write, is left alone: this one is then saved as
    `<name> [<id>].m3u`. A previous export under another name (the playlist was renamed) is removed, unless another
    playlist's export points at it."""
    with _export_lock:
        p = get(pid)
        if p is None:
            return None
        folder = export_dir()
        text, kept, skipped = m3u(p, base=folder, kept_only=True)
        if not kept:
            raise ValueError("none of these tracks are kept yet; keep some first")
        from downloader import _is_same, _may_be_same  # lazy: the downloader pulls in yt-dlp
        music = Path(config.MUSIC_DIR)
        others = [music / rel for rel in _exports_of_others(pid)]

        def taken(path):  # another playlist's export is, or could be, this file (a link, another case)
            return any(_may_be_same(path, o) for o in others)

        old = (p.get("exported") or {}).get("path")
        old_path = music / old if isinstance(old, str) else None
        mine = old_path is not None and not taken(old_path)
        name = export_name(p["name"])
        tags = ["", f" [{pid}]", *(f" [{pid}] ({n})" for n in range(2, EXPORT_TRIES + 1))]
        dest = next((d for d in (folder / f"{name}{tag}.m3u" for tag in tags)
                     if not taken(d) and ((mine and _is_same(d, old_path)) or not os.path.lexists(d))), None)
        if dest is None:
            raise ValueError(f"no free file name for this playlist in {folder.name}/")
        folder.mkdir(parents=True, exist_ok=True)
        _write_new(dest, text)
        rel = _music_rel(dest)
        if not set_exported(pid, rel):  # deleted while this ran: don't leave its file behind
            dest.unlink(missing_ok=True)
            return None
        if mine and not _is_same(old_path, dest):
            if old_path.parent == folder and old_path.suffix == ".m3u":  # never delete outside our folder
                try:
                    old_path.unlink()
                except OSError:
                    pass
        return {"path": rel, "tracks": kept, "skipped": skipped}
