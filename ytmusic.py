"""YouTube Music (anonymous InnerTube via ytmusicapi): song search with artist/album, suggestions, radio,
moods & genres, album / artist / playlist pages, and metadata for pasted links. yt-dlp still resolves the
stream; this only finds and describes tracks.

Every call raises on failure; callers decide the fallback (sources/youtube.py falls back to yt-dlp search).
"""
import copy
import json
import logging
import os
import re
import threading
import zlib
import time
from collections import OrderedDict
from functools import partial

import requests

import config
import personal
import settings

logger = logging.getLogger(__name__)

try:  # optional at runtime: a phone that pulled new code but hasn't re-run pip keeps yt-dlp search
    import ytmusicapi
    from ytmusicapi import YTMusic
except ImportError:  # pragma: no cover - exercised only without the package
    ytmusicapi = YTMusic = None

VERSION = getattr(ytmusicapi, "__version__", None)
TIMEOUT = (5, 10)  # connect, read; the library default is 30 s, too long for a search box
THUMB_SIZE = 544
SUGGEST_TTL, SUGGEST_MAX = 600, 8
SEARCH_TTL = 600     # grouped and per-kind search results (the songs-only search is not cached here)
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
_yt_visitor = None  # the kept visitor id _yt was built with (personal.py), None = ytmusicapi's own throwaway id
_lock = threading.Lock()


def enabled():
    return settings.get("ytmusic") and YTMusic is not None


_last_429 = None  # (monotonic time, Retry-After header or None) of YouTube Music's last 429


def _note_429(resp, *args, **kwargs):
    """ytmusicapi's error keeps only the status line, so remember a 429's Retry-After for callers that back off."""
    global _last_429
    if resp.status_code == 429:
        _last_429 = (time.monotonic(), resp.headers.get("Retry-After"))


def retry_after(max_age=300):
    """Seconds YouTube Music asked to wait in its last 429 (seen within `max_age` s), else None. Only the
    delta-seconds form is read; an HTTP-date Retry-After counts as absent."""
    hit = _last_429
    if not hit or time.monotonic() - hit[0] > max_age or not hit[1] or not str(hit[1]).strip().isdigit():
        return None
    return int(str(hit[1]).strip())


def _client():
    """The shared client. While personalisation is on it carries the kept visitor id (header and context), so
    YouTube Music answers for that visitor; when the setting or the id changes, the client is rebuilt."""
    global _yt, _yt_visitor
    want = personal.client_visitor()
    if _yt is None or want != _yt_visitor:
        session = requests.Session()
        session.request = partial(session.request, timeout=TIMEOUT)
        session.hooks["response"].append(_note_429)
        yt = YTMusic(requests_session=session)
        if want:
            _use_visitor(yt, want)
        _yt, _yt_visitor = yt, want
    return _yt


def _use_visitor(yt, visitor):
    """Give a fresh YTMusic our visitor id. `base_headers` is a cached_property that fetches a throwaway id from
    the homepage on first use; filling its cache first skips that request."""
    from ytmusicapi.helpers import initialize_headers

    headers = initialize_headers()
    headers["X-Goog-Visitor-Id"] = visitor
    yt.__dict__["base_headers"] = headers
    yt.context["context"]["client"]["visitorData"] = visitor


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


# ---------- search by kind (UI remake §10.1: results grouped by type) ----------

SEARCH_KINDS = ("songs", "videos", "albums", "artists", "playlists")
GROUP_MAX = 8  # results per kind from the mixed search
_search_cache = _TTLCache(SEARCH_TTL, 128)


def _list_id(value):
    """A playlist id from a search result's browseId / playlistId ("VL" browse prefix dropped), else None."""
    s = str(value or "")
    s = s[2:] if s.startswith("VL") else s
    return s if _LIST_ID.match(s) else None


def _result_card(item):
    """A search result that isn't a song or video as a card {type: album|artist|playlist, id, title, subtitle, thumb,
    ...}, else None. Albums add `artists`, `year`, `kind` (Album / Single / EP); artists add `radio_id` /
    `shuffle_id` (YouTube's RD… mixes, playable as playlists) and `audience`; playlists add `author`.
    The only artist result with a count is the "Top result" one, and ytmusicapi files that count as `subscribers`
    although it is YouTube Music's monthly audience (68.7M for Daft Punk, whose page says 7.2M subscribers)."""
    if not isinstance(item, dict):
        return None
    kind = item.get("resultType")
    thumb = _largest(item.get("thumbnails"))
    if kind == "album" and str(item.get("browseId") or "").startswith("MPRE"):
        artists = [{"name": a.get("name") or "", "id": a.get("id")}
                   for a in item.get("artists") or [] if isinstance(a, dict) and a.get("name")]
        year = str(item["year"]) if item.get("year") else None
        typ = item.get("type") if item.get("type") in ("Album", "Single", "EP") else "Album"
        return {"type": "album", "id": item["browseId"], "title": item.get("title") or "",
                "subtitle": " · ".join(x for x in (typ, ", ".join(a["name"] for a in artists), year) if x),
                "thumb": _sized(thumb), "artists": artists, "year": year, "kind": typ}
    if kind == "artist":
        cid = item.get("browseId")
        name = item.get("artist") or item.get("title")
        if not _CHANNEL_ID.match(str(cid or "")):  # the "Top result" artist names itself in `artists`
            first = next((a for a in item.get("artists") or [] if isinstance(a, dict) and a.get("name")), {})
            cid, name = first.get("id"), name or first.get("name")
        if not _CHANNEL_ID.match(str(cid or "")) or not name:
            return None
        aud = item.get("subscribers") if isinstance(item.get("subscribers"), str) else None
        return {"type": "artist", "id": cid, "title": str(name),
                "subtitle": f"Artist · {aud} monthly audience" if aud else "Artist",
                "thumb": _sized(thumb, crop=True), "audience": aud or None,
                "radio_id": _list_id(item.get("radioId")), "shuffle_id": _list_id(item.get("shuffleId"))}
    if kind == "playlist":
        pid = _list_id(item.get("browseId") or item.get("playlistId"))
        if not pid:
            return None
        author = item.get("author")
        author = author.get("name") if isinstance(author, dict) else author
        count = item.get("itemCount")
        return {"type": "playlist", "id": pid, "title": item.get("title") or "",
                "subtitle": " · ".join(str(x) for x in ("Playlist", author, f"{count} songs" if count else None) if x),
                "thumb": _sized(thumb), "author": author if isinstance(author, str) else None}
    return None


def _search_item(item):
    """("songs" | "videos", track) or (kind, card) for one search result, else None (podcasts, episodes, profiles)."""
    kind = item.get("resultType") if isinstance(item, dict) else None
    if kind in ("song", "video"):
        t = normalize(item)
        if not t:
            return None
        if kind == "video":
            t["video"] = True
        return ("songs" if kind == "song" else "videos", t)
    card = _result_card(item)
    return (card["type"] + "s", card) if card else None


def search_all(q):
    """One mixed YouTube Music search, grouped: {top: {type, ...card} | {type: "track", track} | None, songs, videos,
    albums, artists, playlists} (each ≤ GROUP_MAX, ids unique). Songs listed right under an artist top result belong to
    it: YouTube leaves their artists out, so they get the top artist. Cached SEARCH_TTL per query."""
    key = q.strip().lower()
    hit = _search_cache.get(("all", key))
    if hit is None:
        hit = _search_all(_call("search", key))
        _search_cache.put(("all", key), hit)
    return copy.deepcopy(hit)


def _search_all(raw):
    out = {k: [] for k in SEARCH_KINDS}
    seen, top, top_artist = set(), None, None
    for item in raw or []:
        found = _search_item(item)
        if not found:
            continue
        kind, x = found
        if kind == "songs" and not x["artists"] and top_artist:
            x["artists"] = [{"name": top_artist["title"], "id": top_artist["id"]}]
            x["channel"] = top_artist["title"]
        if item.get("category") == "Top result" and top is None:
            top = {"type": "track", "track": x} if kind in ("songs", "videos") else x
            if kind == "artists":
                top_artist = x
        if x["id"] in seen or len(out[kind]) >= GROUP_MAX:
            continue
        seen.add(x["id"])
        out[kind].append(x)
    if top is None:  # no "Top result" block: the first song stands in
        first = (out["songs"] or out["videos"] or [None])[0]
        top = {"type": "track", "track": first} if first else None
    out["top"] = top
    return out


def search_kind(q, kind, limit=20):
    """One kind of result ("videos" tracks, or "albums" / "artists" / "playlists" cards) for a query."""
    if kind not in SEARCH_KINDS or kind == "songs":
        raise ValueError(f"unknown search kind {kind!r}")
    key = (kind, q.strip().lower(), limit)
    hit = _search_cache.get(key)
    if hit is None:
        hit, seen = [], set()
        for item in _call("search", q.strip(), filter=kind, limit=limit) or []:
            found = _search_item(item)
            if found and found[0] == kind and found[1]["id"] not in seen:
                seen.add(found[1]["id"])
                hit.append(found[1])
        hit = hit[:limit]
        _search_cache.put(key, hit)
    return copy.deepcopy(hit)


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
        logger.warning(f"ytmusic get_song {video_id} failed: {str(e)[:160]!r}")
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


def lyrics(video_id):
    """YouTube Music's own lyrics for a song (the "Lyrics" tab: LyricFind and the like), or None when it has none:
    {"lines": [(start s, text)] | None (line timing, when the mobile client gets it), "text": str | None, "source"}.
    The watch "next" call names the lyrics page; a second call reads it. Raises on failure like every call here."""
    browse = (_call("get_watch_playlist", videoId=video_id, limit=1) or {}).get("lyrics")
    if not browse:
        return None
    found = _call("get_lyrics", browse, timestamps=True)
    if not found:
        return None
    raw = found.get("lyrics")
    if found.get("hasTimestamps") and isinstance(raw, list):
        lines = [(round(ln.start_time / 1000, 3), str(ln.text or "").strip()) for ln in raw
                 if isinstance(getattr(ln, "start_time", None), int)]
        if lines:
            return {"lines": lines, "text": "\n".join(text for _, text in lines), "source": found.get("source")}
    if isinstance(raw, str) and raw.strip():
        return {"lines": None, "text": raw, "source": found.get("source")}
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
        "description": _trim(a.get("description")) or None,
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
        logger.warning(f"ytmusic: album audio playlist {audio_list} failed, keeping video ids: {str(e)[:160]!r}")
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
    count = data.get("trackCount")
    return {"id": list_id, "title": data.get("title"), "author": author if isinstance(author, str) else None,
            "thumb": _sized(_largest(data.get("thumbnails"))), "tracks": _tracks(data.get("tracks"), limit),
            "description": _trim(data.get("description")) or None,
            "track_count": count if isinstance(count, int) and not isinstance(count, bool) else None}


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
        "videos": [dict(t, video=True) for t in _tracks((a.get("videos") or {}).get("results"), 10)],
        "related": related,
        "subscribers": a.get("subscribers") if isinstance(a.get("subscribers"), str) else None,
        "monthly_listeners": a.get("monthlyListeners") if isinstance(a.get("monthlyListeners"), str) else None,
        "radio_id": _list_id(a.get("radioId")),
        "shuffle_id": _list_id(a.get("shuffleId")),
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
        logger.warning(f"ytmusic: couldn't save {path.name}: {e}")


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
            logger.warning(f"ytmusic: tolerant mood parse failed, using get_mood_playlists: {str(e)[:120]!r}")
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
            logger.warning(f"ytmusic: mood art failed: {str(e)[:160]!r}")
        time.sleep(pause)
    return done


# ---------- YouTube Music's own home (with personalisation on) ----------

HOME_ROWS = 6  # get_home's `limit`: shelves wanted (it pages in threes)


def _card(item):
    """A home item as a card {type: track|album|playlist|artist, id, title, subtitle, thumb} (+ `track`), else None."""
    if not isinstance(item, dict):
        return None
    if item.get("videoId"):
        t = normalize(item)
        return t and {"type": "track", "id": t["id"], "title": t["title"], "subtitle": t["channel"],
                      "thumb": t["thumb"], "track": t}
    bid, pid = str(item.get("browseId") or ""), str(item.get("playlistId") or "")
    title = str(item.get("title") or "")
    if bid.startswith("MPRE"):
        names = [a.get("name") for a in item.get("artists") or [] if isinstance(a, dict) and a.get("name")]
        return {"type": "album", "id": bid, "title": title, "subtitle": ", ".join(names) or item.get("year") or None,
                "thumb": _sized(_largest(item.get("thumbnails")))}
    if _LIST_ID.match(pid):
        return {"type": "playlist", "id": pid, "title": title, "subtitle": item.get("description") or None,
                "thumb": _sized(_largest(item.get("thumbnails")))}
    if _CHANNEL_ID.match(bid):
        return {"type": "artist", "id": bid, "title": title, "subtitle": item.get("subscribers") or None,
                "thumb": _sized(_largest(item.get("thumbnails")), crop=True)}
    return None


def home(limit=HOME_ROWS):
    """YouTube Music's home shelves, as its visitor id (personal.py) gets them: [{title, kind: tracks|cards,
    items}]. A shelf of songs only is a track list; mixed shelves (playlists, albums, artists) are cards."""
    shelves = []
    for row in _call("get_home", limit=limit) or []:
        if not isinstance(row, dict) or not row.get("title"):
            continue
        cards = [c for c in (_card(x) for x in row.get("contents") or []) if c]
        if not cards:
            continue
        if all(c["type"] == "track" for c in cards):
            shelves.append({"title": str(row["title"]), "kind": "tracks", "items": [c["track"] for c in cards]})
        else:
            shelves.append({"title": str(row["title"]), "kind": "cards", "items": cards})
    return shelves


# ---------- charts ----------

CHART_LIST_PREFIX = "PL4fGSI1pDJn"  # every "Top 100 …" playlist the YouTube Charts account publishes
SONGS_GLOBAL = "PL4fGSI1pDJn6puJdseH2Rt9sMvt9E2M4i"  # "Top 100 Songs Global"


def _chart_artist(item):
    from ytmusicapi.parsers.explore import parse_chart_artist

    a = parse_chart_artist(item)
    if not _CHANNEL_ID.match(str(a.get("browseId") or "")):
        return None
    rank = str(a.get("rank") or "")
    return {"id": a["browseId"], "name": a.get("title") or "", "rank": int(rank) if rank.isdigit() else None,
            "trend": a.get("trend"), "subscribers": a.get("subscribers") or None,
            "thumb": _sized(_largest(a.get("thumbnails")), crop=True)}


def _charts_tolerant(country):
    """The FEmusic_charts page read by carousel: what get_charts parses, but an albums or podcasts carousel
    (US has both) can't be taken for the artists one (ytmusicapi 1.12.3 takes the first list carousel)."""
    from ytmusicapi.navigation import (CAROUSEL_CONTENTS, CAROUSEL_TITLE, FRAMEWORK_MUTATIONS, MRLIR, MTRIR,
                                       MUSIC_SHELF, SECTION_LIST, SINGLE_COLUMN_TAB, TITLE, nav)
    from ytmusicapi.parsers.explore import parse_chart_playlist

    if not enabled():
        raise RuntimeError("YouTube Music is disabled")
    with _lock:
        response = _client()._send_request("browse", {"browseId": "FEmusic_charts",
                                                      "formData": {"selectedValues": [country]}})
    results = nav(response, SINGLE_COLUMN_TAB + SECTION_LIST)
    menu = nav(results[0], [*MUSIC_SHELF, "subheaders", 0, "musicSideAlignedItemRenderer", "startItems", 0,
                            "musicSortFilterButtonRenderer"], True)
    name = (nav(menu, TITLE, True) or {}).get("text") if menu else None
    options = [nav(m, ["payload", "musicFormBooleanChoice", "opaqueToken"], True)
               for m in nav(response, FRAMEWORK_MUTATIONS, True) or []]
    playlists, artists = [], []
    for section in results[1:]:
        group = nav(section, ["musicCarouselShelfRenderer", *CAROUSEL_TITLE, "text"], True)
        found = []
        for item in nav(section, CAROUSEL_CONTENTS, True) or []:
            try:
                if MTRIR in item:
                    p = parse_chart_playlist(item[MTRIR])
                    if _LIST_ID.match(str(p.get("playlistId") or "")):
                        playlists.append({"id": p["playlistId"], "title": p.get("title") or "", "group": group})
                elif MRLIR in item and not artists:  # the first carousel of channels is the artists chart
                    a = _chart_artist(item[MRLIR])
                    if a:
                        found.append(a)
            except (KeyError, IndexError, TypeError):
                continue
        artists = artists or found
    return {"country": country, "name": name, "countries": sorted({o for o in options if isinstance(o, str)}),
            "playlists": playlists, "artists": artists}


def charts(country="ZZ"):
    """{country, name, countries, playlists: [{id, title, group}], artists: [{id, name, rank, trend, subscribers,
    thumb}]} for a country code (ZZ = global). Falls back to ytmusicapi's own parser if the page moved."""
    try:
        return _charts_tolerant(country)
    except (ImportError, AttributeError, KeyError, IndexError, TypeError) as e:
        logger.warning(f"ytmusic: tolerant charts parse failed, using get_charts: {str(e)[:120]!r}")
    raw = _call("get_charts", country) or {}
    sel = (raw.get("countries") or {}).get("selected") or {}
    playlists = [{"id": p["playlistId"], "title": p.get("title") or "", "group": group}
                 for group in ("videos", "genres", "languages", "daily", "weekly")
                 for p in raw.get(group) or [] if isinstance(p, dict) and _LIST_ID.match(str(p.get("playlistId") or ""))]
    artists = []
    for a in raw.get("artists") or []:
        if isinstance(a, dict) and _CHANNEL_ID.match(str(a.get("browseId") or "")):
            rank = str(a.get("rank") or "")
            artists.append({"id": a["browseId"], "name": a.get("title") or "", "trend": a.get("trend"),
                            "rank": int(rank) if rank.isdigit() else None, "subscribers": a.get("subscribers"),
                            "thumb": _sized(_largest(a.get("thumbnails")), crop=True)})
    return {"country": country, "name": sel.get("text"),
            "countries": sorted(o for o in (raw.get("countries") or {}).get("options") or [] if isinstance(o, str)),
            "playlists": playlists, "artists": artists}


def chart_playlist(title):
    """The YouTube Charts playlist id with exactly this title ("Top 100 Songs Germany"), found by a playlist
    search, else None. The songs charts aren't on the anonymous charts page, but the playlists exist."""
    want = title.casefold()
    for kind in ("community_playlists", "playlists"):  # the first finds the US one (checked 2026-10-22), the 2nd India's
        for r in _call("search", title, filter=kind, limit=20) or []:
            pid = str((r or {}).get("browseId") or "")
            pid = pid[2:] if pid.startswith("VL") else pid
            if pid.startswith(CHART_LIST_PREFIX) and str(r.get("title") or "").casefold() == want:
                return pid
    return None
