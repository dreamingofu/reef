"""YouTube Music (anonymous InnerTube via ytmusicapi): song search with artist/album, suggestions, radio,
moods & genres, album / artist / playlist pages, and metadata for pasted links. yt-dlp still resolves the
stream; this only finds and describes tracks.

Every call raises on failure; callers decide the fallback (sources/youtube.py falls back to yt-dlp search).
"""
import copy
import json
import os
import re
import threading
import zlib
import time
from collections import OrderedDict
from functools import partial

import requests

import config

try:  # optional at runtime: a phone that pulled new code but hasn't re-run pip keeps yt-dlp search
    import ytmusicapi
    from ytmusicapi import YTMusic
except ImportError:  # pragma: no cover - exercised only without the package
    ytmusicapi = YTMusic = None

VERSION = getattr(ytmusicapi, "__version__", None)
TIMEOUT = (5, 10)  # connect, read; the library default is 30 s, too long for a search box
THUMB_SIZE = 544
SUGGEST_TTL, SUGGEST_MAX = 600, 8
RADIO_TTL = 1800
MOODS_TTL = ART_TTL = 7 * 86400  # YouTube's mood catalogue and its first-playlist art change slowly
PAGE_TTL = 86400      # a mood's playlists, album and artist pages
PLAYLIST_TTL = 3600   # curated playlists are re-ordered daily; an hour keeps a page consistent while browsing
DESCRIPTION_MAX = 400
# YouTube gives mood tiles no colour; each title gets a stable one from the home tiles' palette.
PALETTE = ("#d9a90a", "#e2577f", "#4f7fe0", "#d9801c", "#22a874", "#d9603f", "#df2f78", "#7a63d6", "#2f8f9e",
           "#b8336a")
CACHE_MAX = 256       # entries per response cache
ART_MAX = 5000        # remembered square thumbs

_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_LIST_ID = re.compile(r"^[A-Za-z0-9_-]{12,80}$")
_CHANNEL_ID = re.compile(r"^UC[A-Za-z0-9_-]{22}$")
# googleusercontent / ggpht art is square and sized by its "=w120-h120-l90-rj" or "=s192" suffix.
_SIZED = re.compile(r"=(?:w\d+-h\d+|s\d+)(?:-[\w-]*)?$")
_ART_HOSTS = (".googleusercontent.com", ".ggpht.com")

# YTMusic holds one requests.Session; share one instance and serialise calls on it.
_yt = None
_lock = threading.Lock()


def enabled():
    return config.YTMUSIC and YTMusic is not None


def _client():
    global _yt
    if _yt is None:
        session = requests.Session()
        session.request = partial(session.request, timeout=TIMEOUT)
        _yt = YTMusic(requests_session=session)
    return _yt


def _call(method, *args, **kwargs):
    if not enabled():
        raise RuntimeError("YouTube Music is disabled")
    with _lock:
        return getattr(_client(), method)(*args, **kwargs)


class _TTLCache:
    """Small bounded TTL cache; values are stored and returned as-is (callers copy)."""

    def __init__(self, ttl, size=CACHE_MAX):
        self.ttl, self.size, self.data, self.lock = ttl, size, OrderedDict(), threading.Lock()

    def get(self, key):
        with self.lock:
            hit = self.data.get(key)
            if hit is None or time.monotonic() - hit[0] > self.ttl:
                return None
            return hit[1]

    def put(self, key, value):
        with self.lock:
            self.data[key] = (time.monotonic(), value)
            self.data.move_to_end(key)
            while len(self.data) > self.size:
                self.data.popitem(last=False)

    def clear(self):
        with self.lock:
            self.data.clear()


_suggest_cache = _TTLCache(SUGGEST_TTL)
_radio_cache = _TTLCache(RADIO_TTL)
_mood_cache = _TTLCache(PAGE_TTL)
_album_cache = _TTLCache(PAGE_TTL, 64)
_artist_cache = _TTLCache(PAGE_TTL, 64)
_playlist_cache = _TTLCache(PLAYLIST_TTL, 64)
_art = OrderedDict()  # video id -> square art URL without its size suffix
_art_lock = threading.Lock()


def _remember_art(video_id, base):
    with _art_lock:
        _art[video_id] = base
        _art.move_to_end(video_id)
        while len(_art) > ART_MAX:
            _art.popitem(last=False)


def square_art(video_id, size):
    """Square art URL at `size` px for a track seen in a YT Music response, else None."""
    with _art_lock:
        base = _art.get(video_id)
        if base is not None:
            _art.move_to_end(video_id)
    return f"{base}=w{size}-h{size}-l90-rj" if base else None


def _square_base(url):
    """The URL minus its size suffix when it's resizable square art, else None."""
    if not isinstance(url, str) or not url.startswith("https://"):
        return None
    host = url[8:].split("/", 1)[0]
    if not host.endswith(_ART_HOSTS) or not _SIZED.search(url):
        return None
    return _SIZED.sub("", url)


def _largest(thumbs):
    if not isinstance(thumbs, list):
        return None
    thumbs = [t for t in thumbs if isinstance(t, dict) and t.get("url")]
    return max(thumbs, key=lambda t: t.get("width") or 0)["url"] if thumbs else None


def _sized(url, crop=False):
    """Square art at THUMB_SIZE when the URL is resizable, else the URL as is. `crop` asks Google to
    center-crop (artist art is a wide banner; the "-p" option makes it square)."""
    base = _square_base(url)
    if not base:
        return url
    return f"{base}=w{THUMB_SIZE}-h{THUMB_SIZE}{'-p' if crop else ''}-l90-rj"


def parse_duration(value):
    """Seconds from an int or "m:ss" / "h:mm:ss", else None."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return int(value) if value > 0 else None
    if not isinstance(value, str) or not re.fullmatch(r"\d{1,3}(?::\d{1,2}){1,2}", value.strip()):
        return None
    secs = 0
    for part in value.strip().split(":"):
        secs = secs * 60 + int(part)
    return secs or None


def normalize(item, album=None, thumb=None):
    """A Reef track dict from a ytmusicapi song/track dict, or None without a valid videoId.

    `album` ({name, id}) and `thumb` fill in what album-page tracks leave out (they carry neither art
    nor the album id)."""
    if not isinstance(item, dict):
        return None
    vid = item.get("videoId")
    if not isinstance(vid, str) or not _VIDEO_ID.match(vid):
        return None
    artists = [{"name": a.get("name") or "", "id": a.get("id")}
               for a in item.get("artists") or [] if isinstance(a, dict) and a.get("name")]
    alb = item.get("album")
    if isinstance(alb, str):
        alb = {"name": alb, "id": (album or {}).get("id")}
    elif not isinstance(alb, dict):
        alb = album or {}
    duration = item.get("duration_seconds")
    if not isinstance(duration, int) or isinstance(duration, bool) or duration <= 0:
        duration = parse_duration(item.get("length") or item.get("duration"))
    url = _largest(item.get("thumbnails") or item.get("thumbnail")) or thumb
    base = _square_base(url)
    if base:
        _remember_art(vid, base)
        url = f"{base}=w{THUMB_SIZE}-h{THUMB_SIZE}-l90-rj"
    else:
        url = f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg"  # 16:9 video art, as yt-dlp search gives
    return {
        "id": vid,
        "title": item.get("title") or "",
        "channel": ", ".join(a["name"] for a in artists),
        "artists": artists,
        "album": alb.get("name") or None,
        "album_id": alb.get("id") or None,
        "duration": duration,
        "thumb": url,
        "explicit": bool(item.get("isExplicit")),
        "source": "yt",
    }


def _tracks(items, limit, **fill):
    out, seen = [], set()
    for item in items or []:
        t = normalize(item, **fill)
        if t and t["id"] not in seen:
            seen.add(t["id"])
            out.append(t)
            if len(out) >= limit:
                break
    return out


def search(q, limit=config.SEARCH_LIMIT):
    """Songs (not videos): official audio with artist/album and square art."""
    return _tracks(_call("search", q, filter="songs", limit=limit), limit)


def suggestions(q):
    key = q.strip().lower()
    if not key:
        return []
    hit = _suggest_cache.get(key)
    if hit is None:
        raw = _call("get_search_suggestions", key)
        hit = [s for s in raw or [] if isinstance(s, str)][:SUGGEST_MAX]
        _suggest_cache.put(key, hit)
    return list(hit)


def radio(video_id, limit=50):
    """The song's radio mix. The first track is usually the seed itself; order is kept, the client dedupes."""
    key = (video_id, limit)
    hit = _radio_cache.get(key)
    if hit is None:
        hit = _tracks(_call("get_watch_playlist", videoId=video_id, radio=True, limit=limit).get("tracks"), limit)
        _radio_cache.put(key, hit)
    return [dict(t) for t in hit]


def song(video_id):
    """{id, title, channel, duration, thumb, ...} for one video id, else None."""
    try:
        details = (_call("get_song", video_id) or {}).get("videoDetails") or {}
    except Exception as e:
        if not enabled():
            raise
        print(f"ytmusic get_song {video_id} failed: {str(e)[:160]!r}", flush=True)
        details = {}
    if details.get("videoId") == video_id and details.get("title"):
        t = normalize({
            "videoId": video_id, "title": details["title"],
            "artists": [{"name": details.get("author") or "", "id": details.get("channelId")}],
            "duration_seconds": int(details["lengthSeconds"]) if str(details.get("lengthSeconds", "")).isdigit() else None,
            "thumbnails": (details.get("thumbnail") or {}).get("thumbnails"),
        })
        if t:
            return t
    # get_song can come back without videoDetails (e.g. playability errors); the watch "next" call
    # still describes the track, with artists and album.
    tracks = (_call("get_watch_playlist", videoId=video_id, limit=1) or {}).get("tracks") or []
    for t in _tracks(tracks, 1):
        if t["id"] == video_id:
            return t
    return None


def _cached(cache, key, fetch):
    """A deep copy of the cached page (routes mark tracks kept / filter them), fetched on a miss."""
    hit = cache.get(key)
    if hit is None:
        hit = fetch()
        cache.put(key, hit)
    return copy.deepcopy(hit)


def album(browse_id):
    """{id, title, artist, artists, year, thumb, track_count, duration, audio_playlist_id, tracks} for an
    album browse id (MPRE...) or its audio playlist id (OLAK5uy_...). Tracks carry `track_number`."""
    return _cached(_album_cache, browse_id, lambda: _album(browse_id))


def _album(browse_id):
    if browse_id.startswith("OLAK5uy_"):
        browse_id = _call("get_album_browse_id", browse_id)
        if not isinstance(browse_id, str) or not browse_id.startswith("MPRE"):
            raise LookupError("No album for that playlist")
    a = _call("get_album", browse_id) or {}
    if not a.get("title"):
        raise LookupError("Album not found")
    alb = {"name": a.get("title"), "id": browse_id}
    thumb = _largest(a.get("thumbnails"))
    tracks = []
    for pos, item in enumerate(a.get("tracks") or [], 1):
        t = normalize(item, album=alb, thumb=thumb)
        if t and all(t["id"] != o["id"] for o in tracks):
            n = item.get("trackNumber")
            t["track_number"] = n if isinstance(n, int) and not isinstance(n, bool) and n > 0 else pos
            tracks.append(t)
    audio_list = a.get("audioPlaylistId")
    if isinstance(audio_list, str) and audio_list.startswith("OLAK5uy_") and tracks:
        _use_audio_ids(tracks, audio_list)
    artists = [{"name": x.get("name") or "", "id": x.get("id")}
               for x in a.get("artists") or [] if isinstance(x, dict) and x.get("name")]
    count = a.get("trackCount")
    duration = a.get("duration_seconds")
    return {
        "id": browse_id,
        "title": a.get("title") or "",
        "artist": ", ".join(x["name"] for x in artists),
        "artists": artists,
        "year": str(a["year"]) if a.get("year") else None,
        "thumb": _sized(thumb),
        "track_count": count if isinstance(count, int) and not isinstance(count, bool) else len(tracks),
        "duration": duration if isinstance(duration, int) and not isinstance(duration, bool) else None,
        "audio_playlist_id": a.get("audioPlaylistId") or None,
        "tracks": tracks,
    }


def _title_key(title):
    return re.sub(r"[\W_]+", "", str(title or "").casefold())


def _titles_match(a, b):
    """Same song title: equal keys, or one containing the other ("One More Time (Remastered)")."""
    a, b = _title_key(a), _title_key(b)
    return bool(a and b) and (a == b or a in b or b in a)


def _pair_by_title(tracks, audio):
    """(album row, audio row) pairs, each audio row used once: exact title keys first, then containment."""
    left, pairs = list(audio), []
    for t in tracks:
        found = next((x for x in left if _title_key(x["title"]) == _title_key(t["title"])), None) \
            or next((x for x in left if _titles_match(x["title"], t["title"])), None)
        if found:
            left.remove(found)
            pairs.append((t, found))
    return pairs


def _use_audio_ids(tracks, audio_list):
    """Swap album rows that are official music videos (OMV: video intro/outro, "(Official Video)" titles,
    which a keep would name the file after) for the audio-only track ids of the album's OLAK5uy_ playlist.
    Same count and every pair's titles agree: by position. Otherwise (a reordered or partial playlist) by
    title, each audio track used once; unmatched rows keep their id. Album-side track_number / album /
    artists stay; thumb and duration are taken only when the album row lacks them.
    Best effort: on any error the album keeps its own ids."""
    try:
        audio = playlist(audio_list, limit=max(100, len(tracks)))["tracks"]
    except Exception as e:
        print(f"ytmusic: album audio playlist {audio_list} failed, keeping video ids: {str(e)[:160]!r}", flush=True)
        return
    if not audio:
        return
    pairs = list(zip(tracks, audio))
    if len(audio) != len(tracks) or not all(_titles_match(t["title"], x["title"]) for t, x in pairs):
        pairs = _pair_by_title(tracks, audio)
    for t, found in pairs:
        t["id"] = found["id"]
        if not _square_base(t["thumb"]) and _square_base(found["thumb"]):
            t["thumb"] = found["thumb"]
        t["duration"] = t["duration"] or found["duration"]
        base = _square_base(t["thumb"])
        if base:
            _remember_art(t["id"], base)


def playlist(list_id, limit=100):
    """{id, title, author, thumb, tracks} for a playlist id. "VL" browse prefixes are dropped; RD... mixes
    only exist as watch playlists (no title), except RDCLAK5uy_... which are YouTube's curated playlists."""
    if list_id.startswith("VL"):
        list_id = list_id[2:]
    return _cached(_playlist_cache, (list_id, limit), lambda: _playlist(list_id, limit))


def _playlist(list_id, limit):
    if list_id.startswith("RD") and not list_id.startswith("RDCLAK"):
        data = _call("get_watch_playlist", playlistId=list_id, limit=limit) or {}
        return {"id": list_id, "title": None, "author": None, "thumb": None, "tracks": _tracks(data.get("tracks"), limit)}
    data = _call("get_playlist", list_id, limit=limit) or {}
    author = data.get("author")
    author = author.get("name") if isinstance(author, dict) else author
    return {"id": list_id, "title": data.get("title"), "author": author if isinstance(author, str) else None,
            "thumb": _sized(_largest(data.get("thumbnails"))), "tracks": _tracks(data.get("tracks"), limit)}


# ---------- artist pages ----------

def _trim(text, limit=DESCRIPTION_MAX):
    text = " ".join(str(text or "").split())
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0]
    return cut.rstrip(",;:-") + "…"


def _releases(shelf, default_type):
    out = []
    for r in (shelf or {}).get("results") or []:
        if not isinstance(r, dict) or not str(r.get("browseId") or "").startswith("MPRE"):
            continue
        kind = r.get("type") if r.get("type") in ("Album", "Single", "EP") else default_type
        out.append({"id": r["browseId"], "title": r.get("title") or "", "year": str(r["year"]) if r.get("year") else None,
                    "thumb": _sized(_largest(r.get("thumbnails"))), "type": kind})
    return out


def artist(channel_id):
    """{id, name, thumb, description, songs, songs_playlist_id, albums, singles, related} for a UC... id.
    `id` is the id asked for (what track artist links use); `channel_id` is the one YouTube answers with,
    which can differ (a "Topic" artist id resolves to the artist's own channel)."""
    return _cached(_artist_cache, channel_id, lambda: _artist(channel_id))


def _artist(channel_id):
    a = _call("get_artist", channel_id) or {}
    if not a.get("name"):
        raise LookupError("Artist not found")
    songs = a.get("songs") or {}
    songs_list = str(songs.get("browseId") or "")
    songs_list = songs_list[2:] if songs_list.startswith("VL") else songs_list
    related = []
    for r in (a.get("related") or {}).get("results") or []:
        if isinstance(r, dict) and _CHANNEL_ID.match(str(r.get("browseId") or "")):
            related.append({"id": r["browseId"], "name": r.get("title") or "",
                            "thumb": _sized(_largest(r.get("thumbnails")), crop=True)})
    return {
        "id": channel_id,
        "channel_id": a.get("channelId") or channel_id,
        "name": a["name"],
        "thumb": _sized(_largest(a.get("thumbnails")), crop=True),
        "description": _trim(a.get("description")) or None,
        "songs": _tracks(songs.get("results"), 10),
        "songs_playlist_id": songs_list if _LIST_ID.match(songs_list) else None,
        "albums": _releases(a.get("albums"), "Album"),
        "singles": _releases(a.get("singles"), "Single"),
        "related": related,
    }


# ---------- moods & genres (persisted, so a restart doesn't refetch) ----------

_browse = None  # {"moods": {"at", "items": [{section, title, params}]}|None, "art": {params: {"url", "at"}}}
_browse_lock = threading.Lock()


def _browse_state():
    """The persisted browse cache, read from BROWSE_FILE once. Caller holds _browse_lock."""
    global _browse
    if _browse is None:
        try:
            data = json.loads(config.BROWSE_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        data = data if isinstance(data, dict) else {}
        moods = data.get("moods")
        ok = isinstance(moods, dict) and isinstance(moods.get("at"), (int, float)) and isinstance(moods.get("items"), list)
        art = data.get("art") if isinstance(data.get("art"), dict) else {}
        _browse = {"moods": moods if ok else None,
                   "art": {k: v for k, v in art.items() if isinstance(v, dict) and isinstance(v.get("url"), str)}}
    return _browse


def _save_browse():
    path = config.BROWSE_FILE
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(json.dumps(_browse, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
    except OSError as e:  # a read-only checkout still browses, it just refetches after a restart
        print(f"ytmusic: couldn't save {path.name}: {e}", flush=True)


def _color(title):
    return PALETTE[zlib.crc32(title.encode("utf-8")) % len(PALETTE)]  # crc32, not hash(): stable across restarts


def _with_art(items):
    with _browse_lock:
        art = _browse_state()["art"]
        return [{**m, "color": _color(m["title"]), "art": (art.get(m["params"]) or {}).get("url")} for m in items]


def cached_moods():
    """The last fetched moods (any age, art filled in) without touching the network; [] if never fetched."""
    with _browse_lock:
        moods = _browse_state()["moods"]
        items = list(moods["items"]) if moods else []
    return _with_art(items)


def moods():
    """[{section, title, params, color, art}] in YouTube's order (sections "Moods & moments", "Genres", ...).
    Fetched at most every MOODS_TTL; art is the first playlist's cover, filled in by refresh_mood_art."""
    with _browse_lock:
        cached = _browse_state()["moods"]
        fresh = cached and time.time() - cached["at"] < MOODS_TTL
    if fresh:
        return cached_moods()
    raw = _call("get_mood_categories") or {}
    items = [{"section": str(section), "title": m["title"], "params": m["params"]}
             for section, entries in raw.items() if isinstance(entries, list)
             for m in entries if isinstance(m, dict) and isinstance(m.get("title"), str) and isinstance(m.get("params"), str)]
    if not items:
        raise RuntimeError("YouTube Music returned no moods")
    with _browse_lock:
        _browse_state()["moods"] = {"at": time.time(), "items": items}
        _save_browse()
    return _with_art(items)


def mood_title(params):
    return next((m["title"] for m in cached_moods() if m["params"] == params), None)


def _set_art(params, url):
    with _browse_lock:
        art = _browse_state()["art"]
        if (art.get(params) or {}).get("url") == url and time.time() - art[params].get("at", 0) < ART_TTL:
            return
        art[params] = {"url": url, "at": time.time()}
        _save_browse()


def _mood_playlists_tolerant(params):
    """ytmusicapi's get_mood_playlists without its crash on genre pages whose carousels mix songs in with
    the playlists (1.12.3 parses every two-row item as a playlist, so most genres, "Pop" included, raise
    KeyError). Same request and parser, but items that don't parse as a playlist are skipped."""
    from ytmusicapi.navigation import CAROUSEL_CONTENTS, GRID_ITEMS, MTRIR, SECTION_LIST, SINGLE_COLUMN_TAB, nav
    from ytmusicapi.parsers.browsing import parse_playlist

    if not enabled():
        raise RuntimeError("YouTube Music is disabled")
    with _lock:
        response = _client()._send_request("browse", {"browseId": "FEmusic_moods_and_genres_category", "params": params})
    shelves = {"gridRenderer": GRID_ITEMS, "musicCarouselShelfRenderer": CAROUSEL_CONTENTS,
               "musicImmersiveCarouselShelfRenderer": ["musicImmersiveCarouselShelfRenderer", "contents"]}
    out = []
    for section in nav(response, SINGLE_COLUMN_TAB + SECTION_LIST, True) or []:
        path = next((v for k, v in shelves.items() if k in section), None)
        for item in (nav(section, path, True) or []) if path else []:
            try:
                out.append(parse_playlist(item[MTRIR]))
            except (KeyError, IndexError, TypeError):
                continue
    return out


def mood_playlists(params):
    """[{id, title, subtitle, thumb}] for a mood/genre. Also records the first cover as the mood's art."""
    def fetch():
        try:
            raw = _mood_playlists_tolerant(params)
        except (ImportError, AttributeError, KeyError, IndexError, TypeError) as e:  # ytmusicapi internals moved
            print(f"ytmusic: tolerant mood parse failed, using get_mood_playlists: {str(e)[:120]!r}", flush=True)
            raw = None
        if not raw:
            raw = _call("get_mood_playlists", params)
        out = []
        for p in raw or []:
            if isinstance(p, dict) and _LIST_ID.match(str(p.get("playlistId") or "")):
                out.append({"id": p["playlistId"], "title": p.get("title") or "",
                            "subtitle": p.get("description") or None, "thumb": _sized(_largest(p.get("thumbnails")))})
        return out

    hit = _mood_cache.get(params)
    if hit is None:
        hit = fetch()
        _mood_cache.put(params, hit)
        if hit and hit[0]["thumb"]:
            _set_art(params, hit[0]["thumb"])
    return [dict(p) for p in hit]


def refresh_mood_art(first=(), pause=0.5):
    """Fill in missing or week-old mood art, `first` params first (the home tiles). Background only: it's one
    call per mood, paused between calls so searches sharing the client lock aren't held up. Returns the count."""
    with _browse_lock:
        art = dict(_browse_state()["art"])
    now = time.time()
    stale = [m["params"] for m in moods() if now - (art.get(m["params"]) or {}).get("at", 0) >= ART_TTL]
    todo = [p for p in first if p in stale] + [p for p in stale if p not in first]
    done = 0
    for params in todo:
        try:
            found = mood_playlists(params)
            if found and found[0]["thumb"]:
                _set_art(params, found[0]["thumb"])  # also re-stamps art served from the playlists cache
                done += 1
        except Exception as e:
            print(f"ytmusic: mood art failed: {str(e)[:160]!r}", flush=True)
        time.sleep(pause)
    return done
