"""The listen log: every play the player reports (`POST /api/listens`), in `listens.json`, oldest first.

A play is reported when it ends or is left (next track, page hidden), with the seconds actually played (the
player adds up playback time; seeking doesn't count). It *counts* as a listen at >= 30 s or >= half the track,
like Last.fm / ListenBrainz scrobbles. Short plays the user skipped stay too (`skipped`), for recommendations.
The player may report one play more than once (page hidden, then the next track): entries are keyed by
(id, started_at), and a later report updates the entry instead of adding one.

Stats and recaps are pure functions over a list of entries (`aggregate`, `streak`, `recap_bounds`), so they're
tested without a server. Local time (hours, weekdays, days) comes from the `tz` the caller passes; None means
the server's local zone.
"""
import bisect
import time
from datetime import datetime, timedelta

import config
import store
from blocklist import key as name_key
from tagging import clean_artist

MAX = 20000          # entries kept; the oldest go first (~6 MB of JSON at the cap)
COUNT_S = 30         # a play of at least this many seconds counts...
COUNT_SHARE = 0.5    # ...and so does one of at least half the track
SESSION_GAP = 30 * 60  # a pause longer than this between plays starts a new listening session
TOP = 10
PERIODS = {"week": 7, "month": 30, "year": 365, "all": None}  # /api/stats: rolling windows ending now
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December"]
_UPSERT_SCAN = 500   # how far back a repeated report of the same play is looked for

_file = store.JsonFile(lambda: config.LISTENS_FILE, list)


def counts(played, duration):
    """The counting rule: >= 30 s played, or >= half of a known duration."""
    return played >= COUNT_S or bool(duration and played >= duration * COUNT_SHARE)


def make_entry(body, now=None):
    """A log entry from a client report, or raises ValueError (the message is the API error)."""
    now = time.time() if now is None else now
    if not isinstance(body, dict):
        raise ValueError("body must be a JSON object")
    track = store.clean_track(body)
    if not track:
        raise ValueError("invalid track id")
    played = store.seconds(body.get("played_s"))
    if played is None:
        raise ValueError("played_s must be a number of seconds")
    started = store.seconds(body.get("started_at"), limit=now + 300)
    if started is None or started < 1e9:  # not a plausible epoch: assume the play ended now
        started = now - played
    ended = store.seconds(body.get("ended_at"), limit=now + 300)
    ended = max(started, ended if ended is not None else now)
    duration = track["duration"]
    counted = counts(played, duration)
    entry = {**track, "played_s": round(played, 1), "started_at": int(started), "ended_at": int(ended),
             "counted": counted, "skipped": body.get("skipped") is True and not counted}
    if body.get("via") == "nd":  # played from the Navidrome copy of a kept track (P1.5)
        entry["via"] = "nd"
    return entry


def record(body, now=None):
    """Store a report: (entry, created). Raises ValueError for an invalid one."""
    entry = make_entry(body, now)
    with _file.lock:
        items = _file.load()
        for i in range(len(items) - 1, max(-1, len(items) - 1 - _UPSERT_SCAN), -1):
            e = items[i]
            if e.get("id") == entry["id"] and e.get("started_at") == entry["started_at"]:
                if entry["played_s"] >= e.get("played_s", 0):
                    if e.get("scrobbled"):
                        entry["scrobbled"] = True  # ListenBrainz has it: a later report mustn't submit it again
                    items[i] = entry  # the later, longer report knows more (counted, skipped, end)
                    _file.save(items)
                return dict(items[i]), False
        bisect.insort(items, entry, key=lambda e: e.get("started_at", 0))
        if len(items) > MAX:
            del items[:len(items) - MAX]
        _file.save(items)
        return dict(entry), True


def mark_scrobbled(keys):
    """Flag the entries ListenBrainz accepted, by (id, started_at) (scrobble.py); returns how many."""
    want = {(tid, int(ts)) for tid, ts in keys}
    n = 0
    with _file.lock:
        items = _file.load()
        for e in reversed(items):
            if (e.get("id"), e.get("started_at")) in want and not e.get("scrobbled"):
                e["scrobbled"] = True
                n += 1
                if n == len(want):
                    break
        if n:
            _file.save(items)
    return n


def entries():
    """A snapshot of the log, oldest first (a new list; treat the entries as read-only)."""
    with _file.lock:
        return list(_file.load())


def recent(limit=50, before=None, counted=None):
    """Newest first; `before` (epoch s) pages back, `counted` filters to listens (True) or not (False)."""
    out = []
    for e in reversed(entries()):
        if before is not None and e.get("started_at", 0) >= before:
            continue
        if counted is not None and bool(e.get("counted")) != counted:
            continue
        out.append(e)
        if len(out) >= limit:
            break
    return out


def clear():
    with _file.lock:
        _file.save([])


# ---------- aggregates (pure) ----------

def _local(ts, tz):
    return datetime.fromtimestamp(ts, tz)


def artists_of(e):
    """[(display name, artist id|None)] credited for an entry: its artists, else the cleaned channel name."""
    named = [(a["name"], a.get("id")) for a in e.get("artists") or [] if isinstance(a, dict) and a.get("name")]
    if named:
        return named
    channel = clean_artist(e.get("channel"))
    return [(channel, None)] if channel else []


def _bump(table, k, base, entry):
    row = table.get(k)
    if row is None:
        row = table[k] = {**base, "count": 0, "minutes": 0.0}
    row["count"] += 1 if entry.get("counted") else 0
    row["minutes"] += entry.get("played_s", 0) / 60
    return row


def _top(table, limit):
    """Rows with at least one counted play, most played first (ties: most minutes); limit None = all."""
    rows = [r for r in table.values() if r["count"] > 0]
    rows.sort(key=lambda r: (-r["count"], -r["minutes"]))
    for r in rows:
        r["minutes"] = round(r["minutes"], 1)
    return rows if limit is None else rows[:limit]


def _longest_run(days):
    """The longest run of consecutive dates in a set."""
    best = 0
    for d in days:
        if d - timedelta(days=1) in days:
            continue
        n = 1
        while d + timedelta(days=n) in days:
            n += 1
        best = max(best, n)
    return best


def streak(items, now, tz=None):
    """Days in a row, up to today, with at least one counted listen. A streak still counts until a day is
    missed: with nothing yet today it runs up to yesterday."""
    days = {_local(e["started_at"], tz).date() for e in items if e.get("counted")}
    day = _local(now, tz).date()
    if day not in days:
        day -= timedelta(days=1)
    n = 0
    while day in days:
        n += 1
        day -= timedelta(days=1)
    return n


def in_window(items, start=None, end=None):
    return [e for e in items if (start is None or e.get("started_at", 0) >= start)
            and (end is None or e.get("started_at", 0) < end)]


def aggregate(items, start=None, end=None, tz=None, limit=TOP):
    """Totals over the entries started in [start, end) (None = open). `listens` and every `count` are
    counted plays; `minutes` is all time played, skips included. Hours and weekdays (Monday = 0) are local."""
    window = in_window(items, start, end)
    tracks, artists, albums = {}, {}, {}
    by_hour, by_weekday = [0] * 24, [0] * 7
    days = set()
    skips = {}
    seconds = 0.0
    first = None
    for e in window:
        seconds += e.get("played_s", 0)
        tid = e.get("id")
        _bump(tracks, tid, {k: e[k] for k in ("id", "title", "channel", "duration", "source", "album", "artists")
                            if k in e}, e)
        credited = artists_of(e)
        for name, aid in credited:
            row = _bump(artists, name_key(name) or name, {"name": name, "id": aid}, e)
            row["id"] = row["id"] or aid
        if e.get("album"):
            lead = credited[0][0] if credited else ""
            k = e.get("album_id") or f"{name_key(e['album'])}|{name_key(lead)}"
            _bump(albums, k, {"name": e["album"], "artist": lead, "album_id": e.get("album_id"), "cover_id": tid}, e)
        if e.get("skipped"):
            s = skips.setdefault(tid, {"id": tid, "title": e.get("title", ""), "channel": e.get("channel", ""),
                                       "count": 0})
            s["count"] += 1
        if e.get("counted"):
            local = _local(e["started_at"], tz)
            by_hour[local.hour] += 1
            by_weekday[local.weekday()] += 1
            days.add(local.date())
            first = e["started_at"] if first is None else min(first, e["started_at"])
    counted = [e for e in window if e.get("counted")]
    return {
        "listens": len(counted),
        "minutes": round(seconds / 60, 1),
        "skips": sum(s["count"] for s in skips.values()),
        "tracks_count": sum(1 for r in tracks.values() if r["count"]),
        "artists_count": sum(1 for r in artists.values() if r["count"]),
        "days_count": len(days),
        "top_tracks": _top(tracks, limit),
        "top_artists": _top(artists, limit),
        "top_albums": _top(albums, limit),
        "by_hour": by_hour,
        "by_weekday": by_weekday,
        "longest_streak_days": _longest_run(days),
        "first_listen_at": first,
        "most_skipped": sorted(skips.values(), key=lambda s: -s["count"])[:5],
    }


def stats(items, period="week", now=None, tz=None, limit=TOP):
    """/api/stats: aggregates over a rolling window ending now (`all` = everything), plus the current streak."""
    now = time.time() if now is None else now
    days = PERIODS[period]
    start = now - days * 86400 if days else None
    out = aggregate(items, start, None, tz, limit)
    return {"period": period, "from": int(start) if start else None, "to": int(now), **out,
            "streak_days": streak(items, now, tz)}


def recap_bounds(period, offset, now, tz=None):
    """(start, end, label) of a calendar month/year in local time. offset 0 = the last closed one (last month,
    last year), 1 = the one before, ...; -1 = the current one so far."""
    today = _local(now, tz)
    if period == "year":
        y = today.year - 1 - offset
        start, end = datetime(y, 1, 1, tzinfo=tz), datetime(y + 1, 1, 1, tzinfo=tz)
        label = str(y)
    else:
        n = today.year * 12 + (today.month - 1) - 1 - offset
        y, m = divmod(n, 12)
        start = datetime(y, m + 1, 1, tzinfo=tz)
        y2, m2 = divmod(n + 1, 12)
        end = datetime(y2, m2 + 1, 1, tzinfo=tz)
        label = f"{MONTHS[m]} {y}"
    return start.timestamp(), end.timestamp(), label


def sessions(window):
    """Listening sessions: runs of plays with no gap over SESSION_GAP; each one's length is time played."""
    out, cur, last_end = [], 0.0, None
    for e in sorted(window, key=lambda e: e.get("started_at", 0)):
        if last_end is not None and e.get("started_at", 0) - last_end > SESSION_GAP:
            out.append(cur)
            cur = 0.0
        cur += e.get("played_s", 0)
        last_end = max(last_end or 0, e.get("ended_at") or e.get("started_at", 0) + e.get("played_s", 0))
    if last_end is not None:
        out.append(cur)
    return out


def recap(items, period="month", offset=0, now=None, tz=None, limit=TOP):
    """/api/recap: `aggregate` over a calendar month/year plus what the story pages need."""
    now = time.time() if now is None else now
    start, end, label = recap_bounds(period, offset, now, tz)
    out = aggregate(items, start, end, tz, limit=None)  # untruncated: "new artists" counts every artist heard
    before = {name_key(n) or n for e in items if e.get("counted") and e.get("started_at", 0) < start
              for n, _ in artists_of(e)}
    new = [a for a in out["top_artists"] if (name_key(a["name"]) or a["name"]) not in before]
    for k in ("top_tracks", "top_artists", "top_albums"):
        out[k] = out[k][:limit]
    top = out["top_tracks"][0] if out["top_tracks"] else None
    return {"period": period, "offset": offset, "period_label": label, "from": int(start), "to": int(end),
            "partial": end > now, **out,
            "new_artists_count": len(new),
            "new_artists": [{"name": a["name"], "id": a["id"], "count": a["count"]} for a in new[:5]],
            "top_track_play_time": top["minutes"] if top else 0,
            "longest_session_min": round(max(sessions(in_window(items, start, end)), default=0) / 60, 1)}
