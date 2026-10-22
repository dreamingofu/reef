"""For you: Quick picks and the personal Home shelves, from Reef's own data (production plan P1.10).

Inputs are the listen log, likes, keeps and "Not interested" marks; the only network call is YouTube Music's
radio per seed (`ytmusic.radio`, cached 30 min), which the caller passes in as `fetch`, so all of this is
tested offline.

- Seeds: the newest counted listens (then the newest keeps), one per artist, up to SEEDS.
- Quick picks: each seed's radio is a lane. A candidate scores Σ (1 − i/LANE)(1 − s/n) over the lanes it shows
  up in, i = its place among that lane's *eligible* tracks (so a lane full of songs heard yesterday still
  yields LANE picks), s = the seed's index of n. The sum is scaled by the artist's affinity. Tracks heard in the
  last FRESH_DAYS, kept, disliked or by hidden artists are dropped; the best PICKS are kept with at most
  PER_ARTIST per artist, then interleaved lane by lane so one seed doesn't fill the shelf.
- Artist affinity: an EMA per artist over the log. A counted play moves it EMA toward 1, a skip a third of that
  toward −1; it decays ×DECAY per day without plays, so it follows what's being played lately.
- Dislikes ("Not interested", `dislikes.json`): the track sits out COOLDOWN; a second disliked track by the same
  artist within ESCALATE hides the artist (blocklist), as Flow / Musify do.
"""
import re
import time

import blocklist
import config
import store
from listens import artists_of

DAY = 86400
SEEDS = 5
PICKS = 15
PER_ARTIST = 3
LANE = 8              # places per lane that score: (1 − i/8) reaches 0 at the 9th
FRESH_DAYS = 14       # heard this recently = not a pick, and not "jump back in"
EMA = 0.15
SKIP_SHARE = 1 / 3    # a skip pulls a third as hard as a play
DECAY = 0.85          # per day
AFFINITY_FLOOR = 0.2  # a much-skipped artist's tracks still score a fifth, not zero
COOLDOWN = 14 * DAY
ESCALATE = 30 * DAY
DISLIKES_MAX = 1000
SHELF = 15
REPEAT_DAYS, REPEAT_MIN = 30, 2
JUMP_FROM, JUMP_TO, JUMP_MIN = 180, 30, 2  # days ago; counted plays in that window
LIKED = 10
TOP_ARTISTS, TOP_ARTISTS_DAYS = 10, 90
MIX_WITH = 3          # "with A, B, C" names under a Mixed for you card

_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_dislikes = store.JsonFile(lambda: config.DISLIKES_FILE, list)


def lead(t):
    """(name key, display name, artist id|None) of a track's first credited artist, or ("", "", None)."""
    credited = artists_of(t)
    if not credited:
        return "", "", None
    name, aid = credited[0]
    return blocklist.key(name), name, aid


def _title_key(t):
    return re.sub(r"[\W_]+", "", str(t.get("title") or "").casefold())


# ---------- dislikes ----------

def dislikes(now=None):
    """Marks from the last ESCALATE days, newest first: track + {at, until, blocked?}."""
    now = time.time() if now is None else now
    with _dislikes.lock:
        return [dict(e) for e in _dislikes.load() if isinstance(e, dict) and e.get("at", 0) > now - ESCALATE]


def disliked_ids(now=None):
    """Ids still in their cooldown."""
    now = time.time() if now is None else now
    return {e["id"] for e in dislikes(now) if e.get("at", 0) > now - COOLDOWN}


def dislike(track, now=None):
    """Mark a cleaned track (store.clean_track) "not interested": (entry, created, blocked entry|None; only a
    block this mark created, not one that was already there).
    A second disliked track by the same artist within ESCALATE hides the artist; disliking the same track
    again only restarts its cooldown."""
    now = time.time() if now is None else now
    k, name, aid = lead(track)
    with _dislikes.lock:
        items = [e for e in _dislikes.load() if isinstance(e, dict) and e.get("at", 0) > now - ESCALATE]
        prev = next((e for e in items if e.get("id") == track["id"]), None)
        items = [e for e in items if e.get("id") != track["id"]]
        aid = aid if isinstance(aid, str) and aid.startswith("UC") else None
        entry = {**track, "at": now, "until": now + COOLDOWN, "artist_key": k, "artist_id": aid}
        blocked = None
        same = [e for e in items if k and e.get("artist_key") == k]
        if prev and prev.get("blocked"):
            entry["blocked"] = prev["blocked"]
        elif same:
            # The channel id from whichever mark has it, so the block matches by id too.
            hit, created = blocklist.add(aid or next((e["artist_id"] for e in same if e.get("artist_id")), None), name)
            if created:  # an artist hidden before stays the user's own block: undislike must not lift it
                blocked = hit
                entry["blocked"] = {"id": hit.get("id"), "name": hit["name"]}
        items.insert(0, entry)
        _dislikes.save(items[:DISLIKES_MAX])
    return dict(entry), prev is None, blocked


def undislike(track_id):
    """Undo a mark, and the artist block it caused (if that's still there). False when there was no mark."""
    with _dislikes.lock:
        items = _dislikes.load()
        gone = [e for e in items if isinstance(e, dict) and e.get("id") == track_id]
        if not gone:
            return False
        _dislikes.save([e for e in items if not (isinstance(e, dict) and e.get("id") == track_id)])
    b = gone[0].get("blocked")
    if b:
        blocklist.remove(b.get("id"), b.get("name"))
    return True


# ---------- affinity ----------

def affinity(items, now=None):
    """{artist key: weight in (−1, 1)} from the log (oldest first): EMA toward 1 per counted play, toward −1
    (a third as strong) per skip, decayed ×DECAY per day between updates and up to `now`."""
    now = time.time() if now is None else now
    aff, last = {}, {}
    for e in items:
        counted, skipped = bool(e.get("counted")), bool(e.get("skipped"))
        if not (counted or skipped):
            continue
        at = e.get("started_at", 0)
        for name, _ in artists_of(e):
            k = blocklist.key(name)
            if not k:
                continue
            a = aff.get(k, 0.0) * DECAY ** max(0.0, (at - last.get(k, at)) / DAY)
            a += (EMA * (1 - a)) if counted else (EMA * SKIP_SHARE * (-1 - a))
            aff[k], last[k] = a, at
    return {k: a * DECAY ** max(0.0, (now - last[k]) / DAY) for k, a in aff.items()}


def boost(aff, t):
    return max(AFFINITY_FLOOR, 1 + aff.get(lead(t)[0], 0.0))


# ---------- seeds and Quick picks ----------

def heard_since(items, since):
    """Ids played (counted or not) since `since`."""
    out = set()
    for e in reversed(items):
        if e.get("started_at", 0) < since:
            break
        out.add(e.get("id"))
    return out


def seeds(items, kept=(), blocked=None, disliked=()):
    """Up to SEEDS YouTube tracks to base Quick picks on: newest counted listens first, then the newest keeps;
    one per artist, none disliked or by a hidden artist."""
    out, artists, ids = [], set(), set()
    for t in [e for e in reversed(items) if e.get("counted")] + list(kept):
        if len(out) >= SEEDS:
            break
        tid = t.get("id")
        if not isinstance(tid, str) or not _VIDEO_ID.match(tid) or tid in ids or tid in disliked:
            continue
        if blocked and blocked.track(t):
            continue
        k = lead(t)[0]
        if not k or k in artists:
            continue
        clean = store.clean_track(t)
        if clean:
            artists.add(k)
            ids.add(tid)
            out.append(clean)
    return out


def quick_picks(seed_list, fetch, exclude=(), blocked=None, aff=None, stop=None):
    """(picks, lanes, errors). `fetch(video_id)` returns the seed's radio tracks or raises; an error for which
    `stop(error)` is true (a 429) ends the run, other errors only lose that lane. `lanes[s]` = {seed, artists}:
    the other artists in seed s's lane, for the Mixed for you card."""
    aff = aff or {}
    exclude = set(exclude) | {s["id"] for s in seed_list}
    n = len(seed_list)
    found, errors, lanes = {}, [], []  # dedupe key -> {t, score, lane, best, order}
    by_title = {}  # (title key, artist key) -> dedupe key: the same song under a video id and an audio id
    for s, seed in enumerate(seed_list):
        lanes.append({"seed": seed, "artists": []})
        try:
            radio = fetch(seed["id"])
        except Exception as e:  # one dead lane doesn't sink the rest
            errors.append(e)
            if stop and stop(e):
                break
            continue
        i, seen = 0, set()
        for t in radio or []:
            if i >= LANE:
                break
            tid = t.get("id")
            if not isinstance(tid, str) or tid in exclude or (blocked and blocked.track(t)):
                continue
            k, name, _ = lead(t)
            key = by_title.setdefault((_title_key(t), k), tid)
            if key in seen:
                continue
            seen.add(key)
            w = (1 - i / LANE) * (1 - s / n)
            i += 1
            c = found.get(key)
            if c is None:
                c = found[key] = {"t": t, "score": 0.0, "lane": s, "best": 0.0, "order": len(found)}
            c["score"] += w
            if w > c["best"]:
                c["best"], c["lane"] = w, s
            if name and k != lead(seed)[0] and name not in lanes[s]["artists"]:
                lanes[s]["artists"].append(name)
    ranked = sorted(found.values(), key=lambda c: (-c["score"] * boost(aff, c["t"]), c["order"]))
    picked, per_artist = [], {}
    for c in ranked:
        k = lead(c["t"])[0]
        if per_artist.get(k, 0) >= PER_ARTIST:
            continue
        per_artist[k] = per_artist.get(k, 0) + 1
        picked.append(c)
        if len(picked) >= PICKS:
            break
    queues = [[c["t"] for c in picked if c["lane"] == s] for s in range(n)]  # each in score order
    out = []
    while any(queues):
        for q in queues:
            if q:
                out.append(q.pop(0))
    return out, lanes, errors


# ---------- shelves from the log ----------

def _plays(items, start, end=None):
    """{id: [counted plays, newest entry]} for entries started in [start, end)."""
    out = {}
    for e in items:
        at = e.get("started_at", 0)
        if at < start or (end is not None and at >= end) or not e.get("counted"):
            continue
        row = out.setdefault(e.get("id"), [0, e])
        row[0] += 1
        if at >= row[1].get("started_at", 0):
            row[1] = e
    return out


def _ranked(plays, minimum, skip=()):
    rows = [(n, e) for tid, (n, e) in plays.items() if n >= minimum and tid not in skip]
    rows.sort(key=lambda r: (-r[0], -r[1].get("started_at", 0)))
    out = []
    for n, e in rows[:SHELF]:
        t = store.clean_track(e)
        if t:
            out.append({**t, "plays": n})
    return out


def on_repeat(items, now):
    """Most played (>= REPEAT_MIN counted plays) in the last REPEAT_DAYS."""
    return _ranked(_plays(items, now - REPEAT_DAYS * DAY), REPEAT_MIN)


def jump_back_in(items, now):
    """Played a lot JUMP_FROM..JUMP_TO days ago and not at all in the last FRESH_DAYS."""
    plays = _plays(items, now - JUMP_FROM * DAY, now - JUMP_TO * DAY)
    return _ranked(plays, JUMP_MIN, heard_since(items, now - FRESH_DAYS * DAY))


def top_artists(items, now):
    """[{name, id, count, cover_id}] by counted plays over TOP_ARTISTS_DAYS (all time when that's empty);
    cover_id is the artist's most played track there, for the card art."""
    for start in (now - TOP_ARTISTS_DAYS * DAY, 0):
        table, tracks = {}, {}
        for e in items:
            if not e.get("counted") or e.get("started_at", 0) < start:
                continue
            for name, aid in artists_of(e):
                k = blocklist.key(name) or name
                row = table.setdefault(k, {"name": name, "id": None, "count": 0})
                row["count"] += 1
                if aid and str(aid).startswith("UC"):
                    row["id"] = aid
                per = tracks.setdefault(k, {})
                per[e.get("id")] = per.get(e.get("id"), 0) + 1
        if table:
            rows = sorted(table.items(), key=lambda kv: -kv[1]["count"])[:TOP_ARTISTS]
            return [{**r, "cover_id": max(tracks[k].items(), key=lambda kv: kv[1])[0]} for k, r in rows]
    return []


def mixes(lanes):
    """Mixed for you: a card per seed artist that starts radio from the seed."""
    out = []
    for lane in lanes:
        seed = lane["seed"]
        _, name, aid = lead(seed)
        out.append({"name": name, "artist_id": aid if aid and str(aid).startswith("UC") else None, "seed": seed,
                    "with": lane["artists"][:MIX_WITH]})
    return out
