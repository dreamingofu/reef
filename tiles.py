"""Home "moods & genres" tiles and a daily cache of a few YouTube results per tile (for artwork)."""
import json
import os
import re
import threading
import time

from sources import youtube
from config import TILES_FILE

# (label, color, search query). The single source of truth; the frontend gets it via /api/tiles.
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


def load():
    """Tiles with their cached ids (`[]` until the first refresh). Keyed by query, so edits to TILES apply at once."""
    ids = _cached_ids()
    return [{"label": l, "color": c, "query": q, "ids": ids.get(q, [])} for l, c, q in TILES]


def refresh():
    start = time.monotonic()
    prev = _cached_ids()
    out, ok = [], 0
    for label, color, query in TILES:
        try:
            ids = [r["id"] for r in youtube.SOURCE.search(query, PER_TILE) if _VIDEO_ID.match(r["id"])][:PER_TILE]
            ok += 1
        except Exception as e:
            print(f"tiles: search failed for {query!r}: {str(e)[:200]}", flush=True)
            ids = prev.get(query, [])
        out.append({"label": label, "color": color, "query": query, "ids": ids})
    tmp = TILES_FILE.with_name(TILES_FILE.name + ".tmp")
    tmp.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, TILES_FILE)
    print(f"tiles refreshed n={ok} ms={int((time.monotonic() - start) * 1000)}", flush=True)


def _loop():
    time.sleep(BOOT_DELAY)
    while True:
        try:
            refresh()
        except Exception as e:
            print(f"tiles: refresh failed: {str(e)[:200]}", flush=True)
        time.sleep(REFRESH_EVERY)


def start_refresher():
    threading.Thread(target=_loop, daemon=True, name="reef-tiles").start()
