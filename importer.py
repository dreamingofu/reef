"""Playlist import from CSV (production plan P2 → IL 4.4): Exportify / Spotify-style exports, TuneMyMusic-style lists,
any CSV with a title and an artist column, and Google Takeout's YouTube Music CSVs.

- Columns are found by name, case- and punctuation-insensitively: title (`Track Name`, `title`, `song`, `track`,
  `name`, …), artist (`Artist Name(s)`, `artist`, `artists`, `creator`, Takeout's `Artist Name 1..n`), album,
  duration (`Duration (ms)`, `length`, `3:45`, seconds), ISRC, a playlist name column, a YouTube video id.
- Takeout is told apart by its rows, not its file name: a `Video ID` column (playlist CSVs have `Video ID` +
  `Playlist Video Creation Timestamp`, after a few lines of playlist metadata in older exports), or rows that are
  just an 11-character id and a timestamp. Those rows are YouTube ids already: real tracks (their titles are
  filled in by the background pass when the CSV has none). The playlist name comes from the file name minus
  `-videos.csv` / `.csv`.
- Every other row becomes a placeholder `{id: "pq:…", q: true, title, artist, album?, duration?, isrc?, key}`, which
  the player resolves lazily through /api/match (match-core in app.js), or `resolve()` does for the whole playlist
  in the background: one YouTube Music request per REQUEST_GAP at most, a 429 pauses the run (Retry-After, else
  RATE_WAIT) and it resumes by itself (MAX_429 times, then it stops until asked again); what nothing matched stays
  a placeholder marked `miss` and is listed by `status()`.

Matching is app.py's `_find_song` (matching.best: the same thresholds and title floor as /api/match) plus
`loose_best`: accent-folded word-subset titles ("Song (From the film X)" vs "Song") with the same artist and length.
Results go into matches.json under the placeholder's key, so the player and a later import reuse them.
"""
import csv
import io
import logging
import re
import threading
import time
import uuid

import matching
import playlists
import store

logger = logging.getLogger(__name__)

MAX_BYTES = 5 * 1024 * 1024  # the CSV itself (UTF-8), however it is sent
# The request around it: JSON escaping can grow the text (every `"` of a fully quoted Exportify row doubles, `\n` is
# two bytes, non-ASCII sent as \uXXXX is 6), so a JSON body of a 5 MB CSV may well be past 5 MB. Up to 4x is taken;
# the 5 MB limit is then checked on the decoded text. Multipart uploads send the file as is (+ form overhead).
REQUEST_MAX = 4 * MAX_BYTES
ROWS_MAX = playlists.TRACKS_MAX
REQUEST_GAP = 0.3     # seconds between two YouTube Music requests of the background pass
RATE_WAIT = 60        # a 429 without Retry-After pauses this long
RATE_WAIT_MAX = 900
MAX_429 = 3           # 429s in a row before the run stops (resume with POST /resolve)
MAX_ERRORS = 5        # other failures in a row before the run stops
LOOSE_EXTRA_WORDS = 3  # word-subset titles of unknown length may differ by at most this many words
PLACEHOLDER_ID = re.compile(r"^pq:[0-9a-f]{6,32}$")

# Normalised header -> field. Earlier names win when a file has several (Exportify has "Artist Name(s)" and
# "Album Artist Name(s)").
TITLE_COLS = ("trackname", "songtitle", "tracktitle", "songname", "title", "song", "track", "name")
ARTIST_COLS = ("artistnames", "artistname", "artists", "artist", "creator", "artistsname", "performer",
               "albumartistnames", "albumartistname", "albumartist", "channel")
ALBUM_COLS = ("albumname", "albumtitle", "album", "release")
DURATION_COLS = ("trackdurationms", "durationms", "lengthms", "ms", "duration", "length", "time", "tracklength")
ISRC_COLS = ("isrc",)
VIDEO_COLS = ("videoid", "youtubevideoid", "youtubeid", "videoidyoutube")
PLAYLIST_COLS = ("playlistname", "playlist", "playlisttitle")
TAKEOUT_MARKERS = ("playlistvideocreationtimestamp", "timeadded", "videoid")
_ARTIST_N = re.compile(r"^artistname\d+$")
_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}")


class ImportFailed(ValueError):
    """The file can't be imported (not a CSV, no usable columns, no rows)."""


def _norm(header):
    return re.sub(r"[^a-z0-9]", "", str(header or "").lower())


def _cell(row, i):
    return row[i].strip() if i is not None and i < len(row) and isinstance(row[i], str) else ""


def duration(value, in_ms=False):
    """Seconds from "3:45", "1:02:03", "225", "225.4" or milliseconds ("225000", or any column named …ms)."""
    v = str(value or "").strip()
    if not v:
        return None
    if ":" in v:
        try:
            parts = [float(x) for x in v.split(":")]
        except ValueError:
            return None
        secs = 0.0
        for p in parts:
            secs = secs * 60 + p
    else:
        try:
            secs = float(v.replace(",", "."))
        except ValueError:
            return None
        if in_ms or secs >= 20000:  # nobody's song is 5.5 hours long: that's milliseconds
            secs /= 1000
    return round(secs) if 0.5 <= secs < 86400 else None


def name_from_filename(filename):
    """Takeout's "My Mix-videos.csv" -> "My Mix"; "Liked songs.csv" -> "Liked songs"."""
    base = re.split(r"[\\/]", str(filename or ""))[-1]
    base = re.sub(r"(?i)(-videos)?\.csv$", "", base).strip()
    return base[:playlists.NAME_MAX]


def _decode(raw):
    if isinstance(raw, str):
        return raw.lstrip("﻿")
    for enc in ("utf-8-sig", "utf-16"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1")


def _rows(text):
    sample = text[:8192]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    return [r for r in csv.reader(io.StringIO(text), dialect)]


def _find(cols, names):
    for name in names:
        if name in cols:
            return cols[name]
    return None


def _header_at(rows):
    """(index of the header row, its normalised names) — the first row naming a title or a video id within the
    first 20 rows (older Takeout playlist CSVs start with playlist metadata), else (None, None)."""
    for i, row in enumerate(rows[:20]):
        names = [_norm(c) for c in row]
        if any(n in TITLE_COLS or n in VIDEO_COLS for n in names):
            return i, names
    return None, None


def _bare_takeout(rows):
    """Rows that are only [video id, timestamp] (a Takeout playlist without a header): the ids, else None."""
    ids = []
    for row in rows:
        cells = [c.strip() for c in row if c.strip()]
        if not cells:
            continue
        if store.VIDEO_ID.match(cells[0]) and (len(cells) == 1 or _TIMESTAMP.match(cells[1])):
            ids.append(cells[0])
        elif ids:
            return None
    return ids or None


def parse(raw, filename=""):
    """{name, kind: "takeout" | "csv", rows: [{video_id?, title, artist, album, duration, isrc}], skipped} from
    the CSV's bytes or text. Raises ImportFailed when nothing in it can be imported."""
    text = _decode(raw)
    if not text.strip():
        raise ImportFailed("the file is empty")
    try:
        rows = _rows(text)
    except csv.Error as e:
        raise ImportFailed(f"that isn't a CSV file ({e})") from None
    at, names = _header_at(rows)
    name = name_from_filename(filename)
    if at is None:
        ids = _bare_takeout(rows)
        if not ids:
            raise ImportFailed("no title or video id column found (looked for Track Name, Title, Song, Video ID, …)")
        out = [{"video_id": v, "title": "", "artist": "", "album": "", "duration": None, "isrc": ""} for v in ids]
        return {"name": name, "kind": "takeout", "rows": out[:ROWS_MAX], "skipped": max(0, len(out) - ROWS_MAX)}
    cols = {}
    for i, n in enumerate(names):
        cols.setdefault(n, i)
    title_i, video_i = _find(cols, TITLE_COLS), _find(cols, VIDEO_COLS)
    artist_i, album_i = _find(cols, ARTIST_COLS), _find(cols, ALBUM_COLS)
    artist_n = [i for i, n in enumerate(names) if _ARTIST_N.match(n)]  # Takeout: "Artist Name 1", "Artist Name 2"
    dur_name = next((n for n in DURATION_COLS if n in cols), None)
    dur_i = cols.get(dur_name) if dur_name else None
    isrc_i, list_i = _find(cols, ISRC_COLS), _find(cols, PLAYLIST_COLS)
    takeout = video_i is not None and any(m in cols for m in TAKEOUT_MARKERS)
    out, skipped = [], 0
    for row in rows[at + 1:]:
        if not any(c.strip() for c in row):
            continue
        vid = _cell(row, video_i)
        title = _cell(row, title_i)
        if artist_i is not None:
            artist = _cell(row, artist_i)
        else:
            artist = ", ".join(a for a in (_cell(row, i) for i in artist_n) if a)
        artist = ", ".join(a.strip() for a in re.split(r"\s*;\s*", artist) if a.strip())  # older Exportify: "A;B"
        if not (vid and store.VIDEO_ID.match(vid)):
            vid = ""
        if not vid and not title:
            skipped += 1
            continue
        if list_i is not None and not name:
            name = _cell(row, list_i)[:playlists.NAME_MAX]
        out.append({"video_id": vid, "title": store.text(title), "artist": store.text(artist),
                    "album": store.text(_cell(row, album_i)),
                    "duration": duration(_cell(row, dur_i), in_ms=bool(dur_name and dur_name.endswith("ms"))),
                    "isrc": re.sub(r"[^A-Z0-9]", "", _cell(row, isrc_i).upper())[:12]})
    if not out:
        raise ImportFailed("no rows with a title or a video id")
    return {"name": name, "kind": "takeout" if takeout else "csv", "rows": out[:ROWS_MAX],
            "skipped": skipped + max(0, len(out) - ROWS_MAX)}


# ---------- playlist entries ----------

def is_placeholder(t):
    return isinstance(t, dict) and t.get("q") is True and isinstance(t.get("id"), str) \
        and bool(PLACEHOLDER_ID.match(t["id"]))


def placeholder(row):
    """A stored placeholder for a CSV row (see playlists.clean_entry for the shape)."""
    ph = {"id": "pq:" + uuid.uuid4().hex[:12], "q": True, "title": row["title"], "artist": row["artist"],
          "channel": row["artist"], "duration": row.get("duration"),
          "key": matching.key_for(row["title"], row["artist"])}
    if row.get("album"):
        ph["album"] = row["album"]
    if row.get("isrc"):
        ph["isrc"] = row["isrc"]
    return ph


def entries(parsed):
    """Playlist entries for parsed rows: real tracks for YouTube ids, placeholders for the rest."""
    out = []
    for r in parsed["rows"]:
        if r.get("video_id"):
            out.append({"id": r["video_id"], "title": r["title"], "channel": r["artist"], "duration": r["duration"],
                        "source": "yt", **({"album": r["album"]} if r.get("album") else {})})
        else:
            out.append(placeholder(r))
    return out


def create(parsed, name=None):
    """Create the playlist for parsed rows. Returns it (playlists.get shape)."""
    tracks = entries(parsed)
    total = sum(1 for t in tracks if is_placeholder(t) or not t.get("title"))
    meta = {"kind": parsed["kind"], "rows": len(tracks), "skipped": parsed.get("skipped", 0), "total": total,
            "at": time.time()}
    title = (name or "").strip() or parsed.get("name") or "Imported playlist"
    p = playlists.create(title, tracks, extra={"import": meta})
    logger.info(f"import playlist={p['id']} kind={parsed['kind']} rows={len(tracks)} to_match={total} "
                f"skipped={meta['skipped']}")
    return p


# ---------- word-subset matching ----------

def _words(title):
    return set(matching.norm_title(title).split())


def loose_best(query, candidates, exclude=()):
    """(candidate, raw score) of the first candidate (search order) whose normalised, accent-folded title words are
    a subset of the query's or the other way round, by an alike artist (Dice >= matching.ARTIST_SAME), within 10 s
    when both lengths are known (else at most LOOSE_EXTRA_WORDS words apart), with the same version words and a
    raw score >= matching.ACCEPT. None otherwise. For titles the strict title floor rejects only for extra words:
    "Song (From the film X)" / "Song", "Title Track - Single Version" / "Title Track"."""
    q = matching.fields(query)
    qw = _words(q["title"])
    qa = matching.norm_artist(q["artist"])
    if not qw or not qa:
        return None
    skip = set(exclude or ())
    for c in candidates or []:
        if not isinstance(c, dict) or c.get("id") in skip:
            continue
        f = matching.fields(c)
        cw, ca = _words(f["title"]), matching.norm_artist(f["artist"])
        if not cw or not ca or not (qw <= cw or cw <= qw):
            continue
        if matching.dice(qa, ca) < matching.ARTIST_SAME or matching.versions(q["title"]) != matching.versions(f["title"]):
            continue
        known = q["duration"] and f["duration"]
        if known and matching.duration_score(q["duration"], f["duration"]) < 0.5:
            continue
        if not known and len(qw ^ cw) > LOOSE_EXTRA_WORDS:
            continue
        raw, _ = matching.score(query, c)
        if raw >= matching.ACCEPT:
            return c, raw
    return None


# ---------- background resolve ----------

class Throttle:
    """At most one call per `gap` seconds across threads."""

    def __init__(self, gap=REQUEST_GAP):
        self.gap, self._next, self._lock = gap, 0.0, threading.Lock()

    def wait(self):
        with self._lock:
            now = time.monotonic()
            delay = self._next - now
            self._next = max(now, self._next) + self.gap
        if delay > 0:
            _sleep(delay)


throttle = Throttle()
_sleep = time.sleep  # tests patch this
_jobs = {}            # playlist id -> run state
_jobs_lock = threading.Lock()


def _query(t):
    return {"title": t.get("title") or "", "artist": t.get("artist") or t.get("channel") or "",
            "album": t.get("album") or "", "duration": t.get("duration")}


def _todo(p, attempted):
    """Entries the pass still has to do: placeholders not marked missing, YouTube rows without a title."""
    return [t for t in p["tracks"]
            if t["id"] not in attempted and ((is_placeholder(t) and not t.get("miss")) or (not is_placeholder(t) and not t.get("title")))]


def _swap(pid, entry_id, new):
    playlists.replace_entries(pid, {entry_id: new})


def materialize(pid):
    """Swap placeholders that already have a match in matches.json (the player resolved them, or a manual fix) for
    their tracks. Cheap: no network. Returns how many changed."""
    p = playlists.get(pid)
    if not p:
        return 0
    changes = {}
    for t in p["tracks"]:
        if is_placeholder(t) and t.get("key"):
            hit = matching.cached(t["key"])
            clean = store.clean_track(hit.get("track")) if hit else None
            if clean:
                changes[t["id"]] = clean
    if changes:
        playlists.replace_entries(pid, changes)
    return len(changes)


def _wait(job, seconds):
    """Sleep `seconds` unless the run is cancelled meanwhile (True = carry on)."""
    return not job["stop"].wait(seconds)


def _run(pid, job, find, lookup, rate_limited, retry_after):
    attempted, in_429, errors = set(), 0, 0
    try:
        while not job["stop"].is_set():
            p = playlists.get(pid)
            if p is None:
                job["error"] = "the playlist was deleted"
                return
            todo = _todo(p, attempted)
            if not todo:
                return
            t = todo[0]
            try:
                if is_placeholder(t):
                    hit = matching.cached(t["key"]) if t.get("key") else None
                    found = (hit["track"], hit.get("score")) if hit and hit.get("track") else find(_query(t))
                    if found:
                        track, score = found
                        if not hit and t.get("key"):
                            matching.remember(t["key"], track, score)
                        clean = store.clean_track(track)
                        _swap(pid, t["id"], clean if clean else {**t, "miss": True, "miss_at": time.time()})
                    else:
                        _swap(pid, t["id"], {**t, "miss": True, "miss_at": time.time()})
                else:  # a Takeout video id without a title
                    info = lookup(t["id"])
                    new = store.clean_track({**t, **(info or {}), "id": t["id"]}) if info else None
                    _swap(pid, t["id"], new if new and new.get("title") else {**t, "title": t["id"]})
                attempted.add(t["id"])
                in_429 = errors = 0
            except Exception as e:
                if rate_limited(e):
                    in_429 += 1
                    wait = min(RATE_WAIT_MAX, max(RATE_WAIT, retry_after(e) or 0))
                    job.update(rate_limited=True, retry_at=time.time() + wait)
                    logger.info(f"import playlist={pid} 429 #{in_429}: waiting {wait} s")
                    if in_429 >= MAX_429:
                        job["error"] = "YouTube Music is rate limiting: try again later"
                        return
                    if not _wait(job, wait):
                        return
                    job.update(rate_limited=False, retry_at=None)
                    continue
                errors += 1
                attempted.add(t["id"])  # this run moves on; the next one asks again
                job["failed"] = job.get("failed", 0) + 1
                logger.warning(f"import playlist={pid} entry={t['id']} lookup failed: {str(e)[:160]!r}")
                if errors >= MAX_ERRORS:
                    job["error"] = f"lookups keep failing: {str(e)[:160]}"
                    return
    except Exception as e:  # never let the thread die silently (a store write failed, ...)
        logger.exception(f"import playlist={pid} resolve crashed")
        job["error"] = str(e)[:200]
    finally:
        job.update(running=False, finished_at=time.time(), rate_limited=False)
        logger.info(f"import playlist={pid} resolve ended error={job.get('error')!r}")


def resolve(pid, find, lookup, rate_limited=lambda e: False, retry_after=lambda e: None, retry_missing=False):
    """Start the background pass for playlist `pid` (no-op while one runs). `find(query)` -> (track, score) | None
    and `lookup(video_id)` -> track | None make the (throttled) requests. Returns status(), None when there is
    no such playlist."""
    if playlists.get(pid) is None:
        return None
    if retry_missing:
        p = playlists.get(pid)
        again = {t["id"]: {k: v for k, v in t.items() if k not in ("miss", "miss_at")}
                 for t in p["tracks"] if is_placeholder(t) and t.get("miss")}
        if again:
            playlists.replace_entries(pid, again)
    with _jobs_lock:
        job = _jobs.get(pid)
        if not (job and job["running"]):
            job = _jobs[pid] = {"running": True, "started_at": time.time(), "finished_at": None, "error": None,
                                "rate_limited": False, "retry_at": None, "stop": threading.Event()}
            threading.Thread(target=_run, args=(pid, job, find, lookup, rate_limited, retry_after), daemon=True,
                             name=f"reef-import-{pid}").start()
    return status(pid)


def cancel(pid):
    with _jobs_lock:
        job = _jobs.get(pid)
    if job:
        job["stop"].set()


def status(pid):
    """{total, resolved, pending, missing: [{id, title, artist, album?}], running, rate_limited, retry_at, error,
    tracks, started_at, finished_at} for playlist `pid`, None when there is none. `total` counts the rows the import
    left to match (placeholders + untitled YouTube rows); a playlist made otherwise counts what it holds now."""
    p = playlists.get(pid)
    if p is None:
        return None
    phs = [t for t in p["tracks"] if is_placeholder(t)]
    missing = [t for t in phs if t.get("miss")]
    pending = len(phs) - len(missing) + sum(1 for t in p["tracks"] if not is_placeholder(t) and not t.get("title"))
    total = (p.get("import") or {}).get("total")
    if not isinstance(total, int) or total < pending + len(missing):
        total = pending + len(missing)
    with _jobs_lock:
        job = dict(_jobs.get(pid) or {})
    return {"total": total, "resolved": total - pending - len(missing), "pending": pending,
            "missing": [{"id": t["id"], "title": t.get("title") or "", "artist": t.get("artist") or "",
                         **({"album": t["album"]} if t.get("album") else {})} for t in missing],
            "running": bool(job.get("running")), "rate_limited": bool(job.get("rate_limited")),
            "retry_at": job.get("retry_at"), "error": job.get("error"), "failed": job.get("failed", 0),
            "tracks": len(p["tracks"]), "started_at": job.get("started_at"), "finished_at": job.get("finished_at")}
