"""Backup and restore (production plan P1.12): one JSON file with everything Reef has learned about you.

    {"format": "reef-backup", "schemaVersion": 1, "createdAt": ISO-8601 UTC, "app": {"reef": short sha},
     "sections": {"history", "lyrics", "listens", "likes", "playlists", "dislikes", "blocklist", "settings",
                  "aliases", "matches"}}

Settings are an allow-list of the values changed from the UI: never the ListenBrainz token, instance URLs (they
may carry API keys), the YouTube Music visitor id or anything device-specific. Lyrics carry the hits, the timing
offsets (also offset-only entries: library tracks, songs whose lookup found nothing) and pinned records; misses
are dropped. Matches carry the manual "Wrong song?" fixes only (automatic matches are a cache). Playlists keep
an import's placeholders. A Reef before aliases / matches ignores those sections (same schemaVersion). A file is written to `<name>.tmp`, read back and validated, and
only then renamed (snapshots) or served (downloads).

Restore refuses another format or a newer schemaVersion, validates every section (a section of the wrong type
refuses the whole file; bad items inside one are skipped and counted; a bad field inside an item is dropped:
timestamps must be finite epoch seconds, lengths finite seconds, ids / urls / paths their usual shapes, and
fields Reef doesn't know only travel as plain scalars), merges every section into a copy of what's here (one that
can't be applied refuses the restore before anything is written), saves the current state to
`backups/pre-restore-<ts>.json`, then rewrites each store through its own atomic write (`.tmp` + os.replace),
merging again against that store as it is then. The stores are separate files: a write that fails midway raises
RestoreIncomplete naming the sections already restored and the snapshot holding the state before.
"replace" swaps each section present in the file; "merge" unions by id and the newer copy wins, so merging the
same file twice changes nothing the second time. Sections missing from the file are left alone. Kept-track
`file` paths survive only when the file exists on this device.

A daily snapshot (`backups/auto-<ts>.json`, newest 7 kept) runs from the app's chores thread.
"""
import copy
import json
import logging
import math
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import blocklist
import aliases
import config
import history
import likes
import listens
import lyrics
import matching
import playlists
import recommend
import settings
import store

logger = logging.getLogger(__name__)

FORMAT = "reef-backup"
SCHEMA_VERSION = 1
MAX_BYTES = 20 * 1024 * 1024
KEEP_AUTO = 7
KEEP_PRE_RESTORE = 10
AUTO_EVERY = 24 * 3600
# Settings that travel with a backup: preferences, not secrets, URLs or per-device state.
SETTINGS_KEYS = ("default_source", "ytmusic", "prefer_library", "autoplay_radio", "sponsorblock_keep", "replaygain",
                 "lrc_sidecar", "lyrics_providers", "scrobble_enabled", "keep_lossless", "sponsorblock_play",
                 "replace_dead_tracks", "yt_personalize", "charts_country", "itunes_suggestions")
_NAME = re.compile(r"^(auto|pre-restore)-(\d{8}-\d{6})(?:-(\d+))?\.json$")


class BackupError(ValueError):
    pass


class RestoreIncomplete(BackupError):
    """Writing a section failed after others were written. `snapshot` holds everything as it was before."""

    def __init__(self, message, snapshot):
        super().__init__(message)
        self.snapshot = snapshot


TS_MAX = 1e11  # epoch seconds (beyond year 5000): a later "timestamp" is junk, not a date
TEXT_MAX = 1000  # an unknown text field travels when it's at most this long


def _num(v):
    """A finite int/float (not a bool). JSON gives inf for 1e400, nan for NaN, and ints too big for a float."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return False
    try:
        return math.isfinite(v)
    except OverflowError:
        return False


def _ts(v):
    return _num(v) and 0 <= v < TS_MAX


def _sort_num(v):
    """A sort key that never raises: the number, or 0 for anything else (old data a restore merges with)."""
    return v if _num(v) else 0


# ---------- stores: read a copy / read-modify-write under the store's own lock ----------

class _JsonStore:
    def __init__(self, get):
        self._get = get  # the module's JsonFile, looked up per call (tests swap paths, not objects)

    def read(self):
        f = self._get()
        with f.lock:
            return copy.deepcopy(f.load())

    def transact(self, change):
        f = self._get()
        with f.lock:
            new = change(copy.deepcopy(f.load()))
            f.save(new)
            return new


class _LyricsStore:
    """lyrics.json has its own cache and lock (no JsonFile): write the file, then drop the in-memory copy."""

    def read(self):
        with lyrics._lock:
            return copy.deepcopy(lyrics._entries())

    def transact(self, change):
        with lyrics._lock:
            new = change(copy.deepcopy(lyrics._entries()))
            _atomic_json(Path(config.LYRICS_FILE), new)
            lyrics._cache = None  # re-read on the next lookup
            return new


class _BlocklistStore:
    def read(self):
        with blocklist._lock:
            return blocklist._read()

    def transact(self, change):
        with blocklist._lock:
            new = change(blocklist._read())
            blocklist._write(new)
            return new


class _SettingsStore:
    """Only the allow-listed keys are read or written; the token, URLs and skipped version stay as they are."""

    def read(self):
        return {k: v for k, v in settings._overrides().items() if k in SETTINGS_KEYS}

    def transact(self, change):
        f = settings._file
        with f.lock:
            data = dict(f.load())
            mine = {k: v for k, v in data.items() if k in SETTINGS_KEYS}
            new = change(mine)
            data = {k: v for k, v in data.items() if k not in SETTINGS_KEYS}
            data.update(new)
            f.save(data)
            return new


class _MatchesStore:
    """matches.json's manual fixes only: automatic matches are a cache, they stay as they are (an automatic entry
    under a key a restored fix uses gives way to the fix)."""

    def read(self):
        f = matching._file
        with f.lock:
            return {k: copy.deepcopy(v) for k, v in f.load().items() if isinstance(v, dict) and v.get("manual")}

    def transact(self, change):
        f = matching._file
        with f.lock:
            data = dict(f.load())
            mine = {k: copy.deepcopy(v) for k, v in data.items() if isinstance(v, dict) and v.get("manual")}
            new = change(mine)
            data = {k: v for k, v in data.items() if not (isinstance(v, dict) and v.get("manual")) and k not in new}
            data.update(new)
            f.save(data)
            return new


STORES = {
    "history": _JsonStore(lambda: history._file),
    "lyrics": _LyricsStore(),
    "listens": _JsonStore(lambda: listens._file),
    "likes": _JsonStore(lambda: likes._file),
    "playlists": _JsonStore(lambda: playlists._file),
    "dislikes": _JsonStore(lambda: recommend._dislikes),
    "blocklist": _BlocklistStore(),
    "settings": _SettingsStore(),
    "aliases": _JsonStore(lambda: aliases._file),
    "matches": _MatchesStore(),
}
SECTION_TYPES = {"lyrics": dict, "settings": dict, "matches": dict}  # the rest are lists


# ---------- item validation: a clean copy, or None to skip ----------

# Field checks: each returns the value to keep, or _DROP (the field goes; the item stays unless it was required).
_DROP = object()
_STORE_ID = re.compile(r"^[\w.-]{1,128}$")  # a Navidrome song / cover id (app.COVER_ID)
_CHANNEL = re.compile(r"^UC[A-Za-z0-9_-]{22}$")
_URL = re.compile(r"^https?://\S{1,2040}$")


def _scalar(v):
    """An unknown field's value when it's plain: null, a bool, a finite number or short text (nothing nested)."""
    if v is None or isinstance(v, bool) or (isinstance(v, str) and len(v) <= TEXT_MAX) or (_num(v) and abs(v) < 2 ** 53):
        return v
    return _DROP


def _nullable(check):
    return lambda v: None if v is None else check(v)


def _c_ts(v):
    return v if _ts(v) else _DROP


def _c_bool(v):
    return v if isinstance(v, bool) else _DROP


def _c_text(limit):
    return lambda v: v[:limit] if isinstance(v, str) else _DROP


def _c_match(rx):
    return lambda v: v if isinstance(v, str) and rx.match(v) else _DROP


def _c_range(lo, hi):
    return lambda v: v if _num(v) and lo <= v <= hi else _DROP


def _c_choice(values):
    return lambda v: v if v in values else _DROP


def _c_url(v):
    return v if isinstance(v, str) and (v == "" or _URL.match(v)) else _DROP


def _c_tid(v):
    return store.track_id(v) or _DROP


def _c_file(v):
    return v if isinstance(v, str) and os.path.isfile(v) else _DROP  # another device's path (or gone): no file


def _c_seconds(v):
    return v if _num(v) and 0 <= v < 86400 else _DROP


def _c_blocked(v):
    """The artist block a "Not interested" mark caused: {id: UC… | null, name}."""
    if not isinstance(v, dict) or not isinstance(v.get("name"), str) or not v["name"].strip():
        return _DROP
    aid = v.get("id")
    return {"id": aid if isinstance(aid, str) and _CHANNEL.match(aid) else None, "name": v["name"].strip()[:blocklist.NAME_MAX]}


def _c_exported(v):
    """A playlist's last .m3u export {path relative to the music folder, at}; playlists.export deletes that file when
    the name changes, so only a plain relative .m3u path travels."""
    path = v.get("path") if isinstance(v, dict) else None
    if not isinstance(path, str) or not path.endswith(".m3u") or len(path) > 500 or path.startswith(("/", "\\")) \
            or ".." in path.replace("\\", "/").split("/") or not path.isprintable():
        return _DROP
    return {"path": path, **({"at": v["at"]} if _ts(v.get("at")) else {})}


def _c_import(v):
    """An import's {kind, rows, skipped, total, at} (importer.status reads `total`); bad parts are left out."""
    if not isinstance(v, dict):
        return _DROP
    out = {}
    if v.get("kind") in ("csv", "takeout"):
        out["kind"] = v["kind"]
    for k in ("rows", "skipped", "total"):
        n = v.get(k)
        if isinstance(n, int) and not isinstance(n, bool) and 0 <= n <= 10 ** 7:
            out[k] = n
    if _ts(v.get("at")):
        out["at"] = v["at"]
    return out


TRACK_KEYS = ("id", "title", "channel", "duration", "source", "album", "album_id", "artists", "explicit")
HISTORY_FIELDS = {"file": _c_file, "kept_at": _c_ts, "nd_id": _nullable(_c_match(_STORE_ID)),
                  "nd_cover": _nullable(_c_match(_STORE_ID)), "thumb": _c_url, "art": _c_url, "upgraded_from": _c_tid,
                  "lyrics": _c_choice(("synced", "plain", "instrumental", None)),
                  "gain_db": _nullable(_c_range(-100, 100))}
LISTEN_FIELDS = {"played_s": _c_seconds, "started_at": _c_ts, "ended_at": _c_ts, "counted": _c_bool,
                 "skipped": _c_bool, "scrobbled": _c_bool, "via": _c_choice(("nd",))}
LIKE_FIELDS = {"liked_at": _c_ts}
DISLIKE_FIELDS = {"at": _c_ts, "until": _c_ts, "artist_key": _c_text(blocklist.NAME_MAX),
                  "artist_id": _nullable(_c_match(_CHANNEL)), "blocked": _c_blocked}
PLAYLIST_FIELDS = {"description": _c_text(playlists.DESC_MAX), "pinned": _c_bool, "created_at": _c_ts,
                   "updated_at": _c_ts, "exported": _c_exported, "import": _c_import}


def _fields(e, spec, skip=()):
    """`e`'s fields through their checks in `spec` (a failed check drops the field); fields the spec doesn't know
    travel only as plain scalars, so a newer Reef's extras survive but nothing nested or non-finite does."""
    out = {}
    for k, v in e.items():
        if k in skip:
            continue
        v = spec.get(k, _scalar)(v)
        if v is not _DROP:
            out[k] = v
    return out


def _track_item(e, spec, required=()):
    """A stored track entry: store.clean_track's fields (id, title, artists, duration, ...), then the section's own
    (`spec`). None when the id isn't valid or a `required` field is missing or bad."""
    core = store.clean_track(e)
    if not core:
        return None
    if e.get("album") == "":
        core["album"] = ""  # likes store "" for no album
    out = {**_fields(e, spec, TRACK_KEYS), **core}
    return out if all(k in out for k in required) else None


def _history_item(e):
    return _track_item(e, HISTORY_FIELDS)


def _listen_item(e):
    return _track_item(e, LISTEN_FIELDS, required=("started_at", "played_s"))


def _like_item(e):
    return _track_item(e, LIKE_FIELDS)


def _dislike_item(e):
    return _track_item(e, DISLIKE_FIELDS, required=("at",))


def _playlist_item(p):
    if not isinstance(p, dict) or not playlists.valid_id(p.get("id")) or not isinstance(p.get("name"), str) \
            or not p["name"].strip():
        return None
    raw = p.get("tracks") if isinstance(p.get("tracks"), list) else []
    tracks = [t for t in (playlists.clean_entry(t) for t in raw) if t][:playlists.TRACKS_MAX]  # placeholders too
    out = _fields(p, PLAYLIST_FIELDS, ("id", "name", "tracks", "cover_id"))
    out.setdefault("pinned", False)
    return {**out, "id": p["id"], "name": p["name"].strip()[:playlists.NAME_MAX], "tracks": tracks,
            "cover_id": next((t["id"] for t in tracks if not t.get("q")), None)}  # as playlists._touch sets it


def _block_item(e):
    if not isinstance(e, dict) or not isinstance(e.get("name"), str) or not e["name"].strip():
        return None
    aid = e.get("id")
    return {"id": aid if isinstance(aid, str) and aid else None, "name": e["name"].strip()[:blocklist.NAME_MAX],
            "at": e.get("at") if _ts(e.get("at")) else time.time()}


def _offset(v):
    o = v.get("offset_ms")
    return int(o) if _num(o) and o and abs(o) <= lyrics.OFFSET_MAX else None


def _words(rows):
    """Packed word timing (lyrics._pack: [[[t ms, d ms | null, text], ...] per line]) when it's all well formed."""
    def ms(n):
        return isinstance(n, int) and not isinstance(n, bool) and 0 <= n < 10 ** 8

    if not isinstance(rows, list) or not all(
            isinstance(line, list) and all(isinstance(w, list) and len(w) == 3 and ms(w[0])
                                           and (w[1] is None or ms(w[1])) and isinstance(w[2], str) for w in line)
            for line in rows):
        return None
    return rows


def _lyrics_item(v):
    """A clean lyrics entry: a hit (with its source, word timing, pin, offset), an offset-only entry, or None (a
    miss without an offset, or junk). Reef 1 hits (no "v") stay Reef 1 entries."""
    at = v.get("at") if _ts(v.get("at")) else 0
    off = _offset(v)
    has_text = any(isinstance(v.get(f), str) and v.get(f) for f in ("synced", "plain")) or v.get("instrumental") is True
    if v.get("miss") or not has_text:
        return {"v": lyrics.VERSION, "offset_ms": off, "at": at} if off else None
    e = {"synced": v.get("synced") if isinstance(v.get("synced"), str) else None,
         "plain": v.get("plain") if isinstance(v.get("plain"), str) else None,
         "instrumental": v.get("instrumental") is True, "at": at}
    if v.get("v") == lyrics.VERSION:
        e["v"] = lyrics.VERSION
        if isinstance(v.get("provider"), str) and len(v["provider"]) <= 40:
            e["provider"] = v["provider"]
        if _words(v.get("words")) is not None:
            e["words"] = v["words"]
        lrc = v.get("lrclib")
        if isinstance(lrc, dict):
            e["lrclib"] = {"synced": lrc.get("synced") if isinstance(lrc.get("synced"), str) else None,
                           "plain": lrc.get("plain") if isinstance(lrc.get("plain"), str) else None,
                           "instrumental": lrc.get("instrumental") is True}
        if v.get("retry") is True:
            e["retry"] = True
        lid = v.get("lrclib_id")
        if v.get("pinned") is True and isinstance(lid, int) and not isinstance(lid, bool) and 0 < lid < 2 ** 53:
            e.update(pinned=True, lrclib_id=v["lrclib_id"], provider=lyrics.PINNED)
    if off:
        e["offset_ms"] = off
    return e


def _lyrics_items(d):
    out, bad = {}, 0
    for k, v in d.items():
        tid = store.track_id(k)
        if not tid or not isinstance(v, dict):
            bad += 1
            continue
        e = _lyrics_item(v)
        if e is None:
            if not v.get("miss"):  # misses are dropped quietly: they're retried after a day anyway
                bad += 1
            continue
        out[tid] = e
    return out, bad


def _lyrics_merge(have, new):
    """Merge one lyrics entry: a pin beats a lookup, else the newer entry wins; an offset-only entry only brings
    its offset; an offset the winner lacks is carried over unless the winner is a pin (a pin starts timing over)."""
    if have is None or have.get("miss") and not new.get("offset_ms"):
        return new
    text = ("synced", "plain", "instrumental")
    new_text, have_text = any(new.get(k) for k in text), any(have.get(k) for k in text) and not have.get("miss")
    if not new_text:  # offset only
        if new.get("offset_ms") and (not have.get("offset_ms") or _newer(have, new, "at")) and not have.get("pinned"):
            return {**have, "offset_ms": new["offset_ms"]}
        return have
    if not have_text:
        out = dict(new)
        if have.get("offset_ms") and not out.get("offset_ms") and not out.get("pinned"):
            out["offset_ms"] = have["offset_ms"]
        return out
    if bool(new.get("pinned")) != bool(have.get("pinned")):
        win, lose = (new, have) if new.get("pinned") else (have, new)
    else:
        win, lose = (new, have) if _newer(have, new, "at") else (have, new)
    if lose.get("offset_ms") and not win.get("offset_ms") and not win.get("pinned"):
        return {**win, "offset_ms": lose["offset_ms"]}
    return win


def _alias_item(e):
    if not aliases._valid(e):
        return None
    aid = e.get("id") if isinstance(e.get("id"), str) and aliases.CHANNEL_ID.match(e["id"]) else None
    match = e.get("match").strip()[:aliases.NAME_MAX] if isinstance(e.get("match"), str) and e["match"].strip() else None
    if not aid and not match:
        return None
    return {"id": aid, "match": match, "name": e["name"].strip()[:aliases.NAME_MAX],
            "at": e.get("at") if _ts(e.get("at")) else 0}


def _alias_key(e):
    return e.get("id") or "n:" + blocklist.key(e.get("match"))


def _matches_items(d):
    """Manual fixes only; anything else (automatic matches, junk) is skipped and counted."""
    out, bad = {}, 0
    for k, v in d.items():
        clean = store.clean_track(v.get("track")) if isinstance(v, dict) else None
        if not matching.valid_key(k) or not clean or v.get("manual") is not True or store.track_id(v.get("id")) != clean["id"]:
            bad += 1
            continue
        out[k] = {"id": clean["id"], "score": v.get("score") if _num(v.get("score")) else None, "manual": True,
                  "at": v.get("at") if _ts(v.get("at")) else 0, "track": clean}
    return out, bad


def _settings_items(d):
    out, bad = {}, 0
    for k, v in d.items():
        if k not in SETTINGS_KEYS:
            bad += 1
            continue
        try:
            out[k] = settings._valid(k, v)
        except ValueError:
            bad += 1
    return out, bad


UNIQUE_IDS = ("history", "likes", "dislikes", "playlists")
ITEM = {"history": _history_item, "listens": _listen_item, "likes": _like_item, "playlists": _playlist_item,
        "dislikes": _dislike_item, "blocklist": _block_item, "aliases": _alias_item}


def _clean_section(name, raw):
    """(clean value, skipped count). Raises BackupError when the section isn't the right type at all."""
    want = SECTION_TYPES.get(name, list)
    if not isinstance(raw, want):
        raise BackupError(f"section {name} must be a {'JSON object' if want is dict else 'list'}")
    if name == "lyrics":
        return _lyrics_items(raw)
    if name == "settings":
        return _settings_items(raw)
    if name == "matches":
        return _matches_items(raw)
    out = [c for c in (ITEM[name](e) for e in raw) if c is not None]
    if name in UNIQUE_IDS:  # one entry per id (the first), as the stores keep them
        seen = set()
        out = [c for c in out if not (c["id"] in seen or seen.add(c["id"]))]
    if name == "playlists":
        out = out[:playlists.PLAYLISTS_MAX]
    return out, len(raw) - len(out)


def validate(doc):
    """{section: (clean value, skipped)} for a parsed backup; raises BackupError for one Reef can't take."""
    if not isinstance(doc, dict) or doc.get("format") != FORMAT:
        raise BackupError("not a Reef backup (format isn't reef-backup)")
    ver = doc.get("schemaVersion")
    if not isinstance(ver, int) or isinstance(ver, bool) or ver < 1:
        raise BackupError("the backup has no valid schemaVersion")
    if ver > SCHEMA_VERSION:
        raise BackupError(f"the backup is from a newer Reef (schema {ver}; this one reads {SCHEMA_VERSION}): update Reef first")
    sections = doc.get("sections")
    if not isinstance(sections, dict):
        raise BackupError("the backup has no sections")
    return {name: _clean_section(name, sections[name]) for name in STORES if name in sections}


# ---------- build / write ----------

def _iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _reef_sha():
    try:
        import updater  # lazy: only for the "app" stamp
        return updater.local()["short"]
    except Exception:
        return None


def build(now=None):
    now = time.time() if now is None else now
    sections = {}
    for name, st in STORES.items():
        data = st.read()
        if name == "lyrics":
            data, _ = _lyrics_items(data)  # hits and offsets: misses are retried after a day anyway
        sections[name] = data
    return {"format": FORMAT, "schemaVersion": SCHEMA_VERSION, "createdAt": _iso(now),
            "app": {"reef": _reef_sha()}, "sections": sections}


def _atomic_json(path, data):
    tmp = path.with_name(path.name + ".tmp")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, path)


def write(path, doc=None, now=None):
    """Write a backup to `path` via `<path>.tmp`, read it back and validate it before the rename. Returns the bytes."""
    doc = build(now) if doc is None else doc
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        tmp.write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        raw = tmp.read_bytes()
        validate(json.loads(raw))
        os.replace(tmp, path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    return raw


def download(now=None):
    """(filename, bytes) for GET /api/backup: built, written to a temp file, read back and validated."""
    now = time.time() if now is None else now
    path = config.BACKUP_DIR / f".download-{os.getpid()}-{int(now * 1000)}.json"
    try:
        raw = write(path, now=now)
    finally:
        path.unlink(missing_ok=True)
    return f"reef-backup-{datetime.fromtimestamp(now).strftime('%Y-%m-%d')}.json", raw


# ---------- snapshots ----------

def snapshots():
    """[{name, kind, at, size}] newest first."""
    out = []
    try:
        names = os.listdir(config.BACKUP_DIR)
    except OSError:
        return []
    for n in names:
        m = _NAME.match(n)
        if not m:
            continue
        try:
            st = (config.BACKUP_DIR / n).stat()
            at = datetime.strptime(m.group(2), "%Y%m%d-%H%M%S").replace(tzinfo=timezone.utc).timestamp()
        except (OSError, ValueError):
            continue
        out.append({"name": n, "kind": m.group(1), "at": at, "size": st.st_size, "_n": int(m.group(3) or 0)})
    out.sort(key=lambda s: (-s["at"], -s.pop("_n")))
    return out


def snapshot(kind, now=None):
    """Write `backups/<kind>-<UTC ts>.json` and prune that kind to its limit. Returns the file name."""
    now = time.time() if now is None else now
    stamp = f"{kind}-{datetime.fromtimestamp(now, timezone.utc).strftime('%Y%m%d-%H%M%S')}"
    name, n = f"{stamp}.json", 1
    while (config.BACKUP_DIR / name).exists():  # two restores within a second: never overwrite a snapshot
        n += 1
        name = f"{stamp}-{n}.json"
    write(config.BACKUP_DIR / name, now=now)
    keep = KEEP_AUTO if kind == "auto" else KEEP_PRE_RESTORE
    for old in [s for s in snapshots() if s["kind"] == kind][keep:]:
        try:
            (config.BACKUP_DIR / old["name"]).unlink()
        except OSError:
            pass
    logger.info(f"backup snapshot name={name}")
    return name


def tick(now=None):
    """The daily automatic snapshot (the app's chores thread calls this every minute)."""
    now = time.time() if now is None else now
    last = next((s for s in snapshots() if s["kind"] == "auto"), None)
    if last is None or now - last["at"] >= AUTO_EVERY:
        return snapshot("auto", now)
    return None


def status():
    snaps = snapshots()
    auto = next((s for s in snaps if s["kind"] == "auto"), None)
    return {"last_auto_at": auto["at"] if auto else None, "snapshots": snaps, "max_bytes": MAX_BYTES,
            "sections": list(STORES)}


# ---------- restore ----------

def _newer(a, b, field):
    """True when `b` should replace `a` (strictly newer: ties keep what's here, so a second merge is a no-op)."""
    av, bv = a.get(field), b.get(field)
    return _num(bv) and (not _num(av) or bv > av)


def _merge_by(cur, incoming, keyf, field, order=None):
    out = {}
    for e in cur:
        out.setdefault(keyf(e), e)
    for e in incoming:
        k = keyf(e)
        if k not in out or _newer(out[k], e, field):
            out[k] = e
    rows = list(out.values())
    return sorted(rows, key=order) if order else rows


def _merge_listens(cur, incoming):
    out = {}
    for e in cur:
        out.setdefault((e.get("id"), e.get("started_at")), e)
    for e in incoming:
        k = (e.get("id"), e.get("started_at"))
        have = out.get(k)
        if have is None or _sort_num(e.get("played_s")) > _sort_num(have.get("played_s")):
            if have is not None and have.get("scrobbled"):
                e = {**e, "scrobbled": True}
            out[k] = e
    return out


def _apply(name, mode, cur, new):
    if name == "settings":
        return dict(new) if mode == "replace" else {**new, **cur}  # merge: what's set here stays
    if name == "lyrics":
        if mode == "replace":
            return dict(new)
        out = dict(cur)
        for k, v in new.items():
            out[k] = _lyrics_merge(out.get(k) if isinstance(out.get(k), dict) else None, v)
        return out
    if name == "matches":
        if mode == "replace":
            return dict(new)
        out = dict(cur)
        for k, v in new.items():
            if k not in out or _newer(out[k], v, "at"):
                out[k] = v
        return out
    if name == "listens":
        rows = list(_merge_listens([] if mode == "replace" else cur, new).values())
        rows.sort(key=lambda e: _sort_num(e.get("started_at")))
        return rows[-listens.MAX:]
    if mode == "replace":
        rows = list(new)
    elif name == "history":
        rows = _merge_by(cur, new, lambda e: e.get("id"), "kept_at", order=lambda e: -_sort_num(e.get("kept_at")))
    elif name == "likes":
        rows = _merge_by(cur, new, lambda e: e.get("id"), "liked_at", order=lambda e: -_sort_num(e.get("liked_at")))
    elif name == "dislikes":
        rows = _merge_by(cur, new, lambda e: e.get("id"), "at", order=lambda e: -_sort_num(e.get("at")))
    elif name == "playlists":
        rows = _merge_by(cur, new, lambda e: e.get("id"), "updated_at")
    elif name == "blocklist":
        rows = _merge_by(cur, new, lambda e: e.get("id") or "n:" + blocklist.key(e.get("name")), "at")
    elif name == "aliases":
        rows = _merge_by(cur, new, _alias_key, "at")
    else:
        raise BackupError(f"unknown section {name}")
    if name == "history" and mode == "replace":
        rows.sort(key=lambda e: -_sort_num(e.get("kept_at")))
    return rows


def restore(doc, mode="merge", now=None):
    """Restore a parsed backup. Returns {mode, snapshot, sections: {name: {before, in_backup, skipped, after}}}."""
    if mode not in ("replace", "merge"):
        raise BackupError("mode must be replace or merge")
    clean = validate(doc)
    if not clean:
        raise BackupError("the backup has none of Reef's sections")
    # Every section is merged into a copy of what's here first: one that can't be applied refuses the whole restore
    # before anything is written. The writes below merge again, each against its store as it is then (under its lock),
    # so a change made meanwhile isn't lost.
    for name, (new, _) in clean.items():
        try:
            _apply(name, mode, STORES[name].read(), new)
        except Exception as e:
            logger.warning(f"backup restore refused: section {name} can't be applied: {type(e).__name__}: {str(e)[:160]}")
            raise BackupError(f"section {name} can't be restored ({type(e).__name__}); nothing was changed") from None
    snap = snapshot("pre-restore", now)
    counts = {}
    for name, (new, skipped) in clean.items():
        before = {}

        def change(cur, name=name, new=new, before=before):
            before["n"] = len(cur)
            return _apply(name, mode, cur, new)

        try:
            after = STORES[name].transact(change)
        except Exception as e:
            # Per-store files are atomic, the restore as a whole isn't: say what was written and where the old state is.
            why = (e.strerror if isinstance(e, OSError) else None) or type(e).__name__
            done = ", ".join(counts) or "nothing"
            logger.warning(f"backup restore stopped at {name}: {type(e).__name__}: {str(e)[:160]} written={done} "
                           f"snapshot={snap}")
            raise RestoreIncomplete(f"the restore stopped at {name} ({why}) after restoring {done}; everything as it "
                                    f"was before is in backups/{snap}", snap) from None
        counts[name] = {"before": before["n"], "in_backup": len(new), "skipped": skipped, "after": len(after)}
    logger.info(f"backup restore mode={mode} snapshot={snap} " + " ".join(f"{k}={v['after']}" for k, v in counts.items()))
    return {"mode": mode, "snapshot": snap, "sections": counts}
