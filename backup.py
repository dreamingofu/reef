"""Backup and restore (production plan P1.12): one JSON file with everything Reef has learned about you.

    {"format": "reef-backup", "schemaVersion": 1, "createdAt": ISO-8601 UTC, "app": {"reef": short sha},
     "sections": {"history", "lyrics", "listens", "likes", "playlists", "dislikes", "blocklist", "settings"}}

Settings are an allow-list of the values changed from the UI: never the ListenBrainz token, instance URLs (they
may carry API keys) or anything device-specific. A file is written to `<name>.tmp`, read back and validated, and
only then renamed (snapshots) or served (downloads).

Restore refuses another format or a newer schemaVersion, validates every section (a section of the wrong type
refuses the whole file; bad items inside one are skipped and counted), saves the current state to
`backups/pre-restore-<ts>.json`, then rewrites each store through its own atomic write (`.tmp` + os.replace).
"replace" swaps each section present in the file; "merge" unions by id and the newer copy wins, so merging the
same file twice changes nothing the second time. Sections missing from the file are left alone. Kept-track
`file` paths survive only when the file exists on this device.

A daily snapshot (`backups/auto-<ts>.json`, newest 7 kept) runs from the app's chores thread.
"""
import copy
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import blocklist
import config
import history
import likes
import listens
import lyrics
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
                 "replace_dead_tracks")
_NAME = re.compile(r"^(auto|pre-restore)-(\d{8}-\d{6})(?:-(\d+))?\.json$")


class BackupError(ValueError):
    pass


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


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


STORES = {
    "history": _JsonStore(lambda: history._file),
    "lyrics": _LyricsStore(),
    "listens": _JsonStore(lambda: listens._file),
    "likes": _JsonStore(lambda: likes._file),
    "playlists": _JsonStore(lambda: playlists._file),
    "dislikes": _JsonStore(lambda: recommend._dislikes),
    "blocklist": _BlocklistStore(),
    "settings": _SettingsStore(),
}
SECTION_TYPES = {"lyrics": dict, "settings": dict}  # the rest are lists


# ---------- item validation: a clean copy, or None to skip ----------

def _tid(e):
    return store.track_id(e.get("id")) if isinstance(e, dict) else None


def _history_item(e):
    tid = _tid(e)
    if not tid:
        return None
    e = {**e, "id": tid}
    f = e.get("file")
    if f is not None and not (isinstance(f, str) and os.path.isfile(f)):
        e.pop("file")  # another device's path (or gone): the entry stays, without a file to point at
    return e


def _listen_item(e):
    tid = _tid(e)
    if not tid or not _num(e.get("started_at")) or not _num(e.get("played_s")):
        return None
    return {**e, "id": tid}


def _like_item(e):
    tid = _tid(e)
    return {**e, "id": tid} if tid else None


def _playlist_item(p):
    if not isinstance(p, dict) or not playlists.valid_id(p.get("id")) or not isinstance(p.get("name"), str):
        return None
    raw = p.get("tracks") if isinstance(p.get("tracks"), list) else []
    tracks = [t for t in (store.clean_track(t) for t in raw) if t]
    return {**p, "tracks": tracks[:playlists.TRACKS_MAX]}


def _dislike_item(e):
    tid = _tid(e)
    return {**e, "id": tid} if tid and _num(e.get("at")) else None


def _block_item(e):
    if not isinstance(e, dict) or not isinstance(e.get("name"), str) or not e["name"].strip():
        return None
    aid = e.get("id")
    return {"id": aid if isinstance(aid, str) and aid else None, "name": e["name"].strip()[:blocklist.NAME_MAX],
            "at": e.get("at") if _num(e.get("at")) else time.time()}


def _lyrics_items(d):
    out, bad = {}, 0
    for k, v in d.items():
        tid = store.track_id(k)
        if not tid or not isinstance(v, dict):
            bad += 1
            continue
        if v.get("miss"):
            continue  # misses are dropped quietly: they're retried after a day anyway
        if not any(isinstance(v.get(f), str) and v.get(f) for f in ("synced", "plain")) and not v.get("instrumental"):
            bad += 1
            continue
        out[tid] = {"synced": v.get("synced"), "plain": v.get("plain"), "instrumental": bool(v.get("instrumental")),
                    "at": v.get("at") if _num(v.get("at")) else 0}
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


ITEM = {"history": _history_item, "listens": _listen_item, "likes": _like_item, "playlists": _playlist_item,
        "dislikes": _dislike_item, "blocklist": _block_item}


def _clean_section(name, raw):
    """(clean value, skipped count). Raises BackupError when the section isn't the right type at all."""
    want = SECTION_TYPES.get(name, list)
    if not isinstance(raw, want):
        raise BackupError(f"section {name} must be a {'JSON object' if want is dict else 'list'}")
    if name == "lyrics":
        return _lyrics_items(raw)
    if name == "settings":
        return _settings_items(raw)
    out = [c for c in (ITEM[name](e) for e in raw) if c is not None]
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
            data, _ = _lyrics_items(data)  # hits only: misses are retried after a day anyway
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
        if have is None or (e.get("played_s") or 0) > (have.get("played_s") or 0):
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
            if k not in out or out[k].get("miss") or _newer(out[k], v, "at"):
                out[k] = v
        return out
    if name == "listens":
        rows = list(_merge_listens([] if mode == "replace" else cur, new).values())
        rows.sort(key=lambda e: e.get("started_at") or 0)
        return rows[-listens.MAX:]
    if mode == "replace":
        rows = list(new)
    elif name == "history":
        rows = _merge_by(cur, new, lambda e: e.get("id"), "kept_at", order=lambda e: -(e.get("kept_at") or 0))
    elif name == "likes":
        rows = _merge_by(cur, new, lambda e: e.get("id"), "liked_at", order=lambda e: -(e.get("liked_at") or 0))
    elif name == "dislikes":
        rows = _merge_by(cur, new, lambda e: e.get("id"), "at", order=lambda e: -(e.get("at") or 0))
    elif name == "playlists":
        rows = _merge_by(cur, new, lambda e: e.get("id"), "updated_at")
    elif name == "blocklist":
        rows = _merge_by(cur, new, lambda e: e.get("id") or "n:" + blocklist.key(e.get("name")), "at")
    else:
        raise BackupError(f"unknown section {name}")
    if name == "history" and mode == "replace":
        rows.sort(key=lambda e: -(e.get("kept_at") or 0))
    return rows


def restore(doc, mode="merge", now=None):
    """Restore a parsed backup. Returns {mode, snapshot, sections: {name: {before, in_backup, skipped, after}}}."""
    if mode not in ("replace", "merge"):
        raise BackupError("mode must be replace or merge")
    clean = validate(doc)
    if not clean:
        raise BackupError("the backup has none of Reef's sections")
    snap = snapshot("pre-restore", now)
    counts = {}
    for name, (new, skipped) in clean.items():
        before = {}

        def change(cur, name=name, new=new, before=before):
            before["n"] = len(cur)
            return _apply(name, mode, cur, new)

        after = STORES[name].transact(change)
        counts[name] = {"before": before["n"], "in_backup": len(new), "skipped": skipped, "after": len(after)}
    logger.info(f"backup restore mode={mode} snapshot={snap} " + " ".join(f"{k}={v['after']}" for k, v in counts.items()))
    return {"mode": mode, "snapshot": snap, "sections": counts}
