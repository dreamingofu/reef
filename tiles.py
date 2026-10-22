"""Home "moods & genres" tiles. With YouTube Music on they are YouTube's own moods/genres (`params` opens the
mood page, `art` is its first playlist's cover); otherwise, or until moods were fetched once, the hard-coded
search tiles below with a daily cache of a few result ids per tile (for artwork) in `tiles.json`."""
import json
import logging
import os
import re
import threading
import time

import blocklist
import ytmusic
from sources import youtube
from config import TILES_FILE

logger = logging.getLogger(__name__)

# (label, color, search query): the fallback tiles.
TILES = [
    ("Hits", "#d9a90a", "top hits this week"),
    ("Pop", "#e2577f", "pop hits"),
    ("Hip-Hop", "#4f7fe0", "hip hop hits"),
    ("Country", "#d9801c", "country hits"),
    ("Dance", "#22a874", "dance hits"),
    ("Rock", "#d9603f", "rock classics"),
    ("Latin", "#df2f78", "latin hits"),
    ("R&B", "#7a63d6", "r&b hits"),
    ("Chill", "#2f8f9e", "chill lofi"),
    ("Throwback", "#b8336a", "2000s hits"),
]
HOME_TILES = 10
HOME_SECTIONS = ("Moods & moments", "Genres")
# YouTube lists moods/genres alphabetically (African, Arabic, ... Diwali, Halloween); the home row leads with
# broad ones when YouTube has them, then fills up in YouTube's order.
PREFERRED = ("Pop", "Hip-hop", "Chill", "Dance & electronic", "Rock", "Feel good", "R&B & soul", "Latin",
             "Workout", "Focus")
PER_TILE = 4
BOOT_DELAY = 5
REFRESH_EVERY = 24 * 3600
_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")


def _cached_ids():
    try:
        data = json.loads(TILES_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, list):
        return {}
    return {
        t["query"]: [i for i in t.get("ids") or [] if isinstance(i, str) and _VIDEO_ID.match(i)]
        for t in data if isinstance(t, dict) and isinstance(t.get("query"), str)
    }


def _search_tiles():
    ids = _cached_ids()
    return [{"label": l, "color": c, "query": q, "ids": ids.get(q, []), "params": None, "art": None}
            for l, c, q in TILES]


def _mood_tiles():
    """Home tiles from the cached moods (no network call), or [] when YouTube Music is off or never answered.
    `query` is the title, so a client that doesn't know `params` still gets a sensible search."""
    if not ytmusic.enabled():
        return []
    moods = ytmusic.cached_moods()
    picked = [m for m in moods if m["section"] in HOME_SECTIONS] or moods
    rank = {t.casefold(): i for i, t in enumerate(PREFERRED)}
    picked = sorted(picked, key=lambda m: rank.get(m["title"].casefold(), len(rank)))[:HOME_TILES]  # stable sort
    return [{"label": m["title"], "color": m["color"], "query": m["title"], "ids": [], "params": m["params"],
             "art": m["art"]} for m in picked]


def load():
    """[{label, color, query, ids, params, art}]: mood tiles when available, else the search tiles."""
    return _mood_tiles() or _search_tiles()


def _refresh_searches():
    start = time.monotonic()
    prev = _cached_ids()
    blocked = blocklist.matcher()
    out, ok = [], 0
    for label, color, query in TILES:
        try:
            found = blocked.tracks(youtube.SOURCE.search(query, PER_TILE + 2))
            ids = [r["id"] for r in found if _VIDEO_ID.match(r["id"])][:PER_TILE]
            ok += 1
        except Exception as e:
            logger.warning(f"tiles: search failed for {query!r}: {str(e)[:200]}")
            ids = prev.get(query, [])
        out.append({"label": label, "color": color, "query": query, "ids": ids})
    tmp = TILES_FILE.with_name(TILES_FILE.name + ".tmp")
    tmp.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, TILES_FILE)
    logger.info(f"tiles refreshed n={ok} ms={int((time.monotonic() - start) * 1000)}")


def refresh():
    """Moods + their art when YouTube Music answers (weekly at most; a no-op most days), else the search tiles."""
    if ytmusic.enabled():
        start = time.monotonic()
        try:
            ytmusic.moods()
            n = ytmusic.refresh_mood_art(first=[t["params"] for t in _mood_tiles()])
            logger.info(f"tiles: moods ok, art filled={n} ms={int((time.monotonic() - start) * 1000)}")
            return
        except Exception as e:
            logger.warning(f"tiles: moods failed, refreshing search tiles: {str(e)[:200]}")
    _refresh_searches()


def _loop():
    time.sleep(BOOT_DELAY)
    while True:
        try:
            refresh()
        except Exception as e:
            logger.warning(f"tiles: refresh failed: {str(e)[:200]}")
        time.sleep(REFRESH_EVERY)


def start_refresher():
    threading.Thread(target=_loop, daemon=True, name="reef-tiles").start()
