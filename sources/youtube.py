"""YouTube: search via YouTube Music (ytmusic.py) then yt-dlp (search.py); stream URLs via yt-dlp.

D3 hook: append callables to `search_fallbacks` (`f(q, limit) -> [track]`) and `resolve_fallbacks`
(`f(video_id) -> {"url", "headers", "mime", "ext"}`). They are tried in order, only after yt-dlp
raised; the first that returns without raising wins, otherwise yt-dlp's error is re-raised.
Search tries YouTube Music first (when REEF_YTMUSIC is on) and drops to that chain on an error or no results.
Set `last_backend` to say which one answered (reported by /api/health).
Piped (sources/piped.py) and Invidious (sources/invidious.py) are registered by sources/__init__.py
when REEF_PIPED_INSTANCES / REEF_INVIDIOUS_INSTANCES are set.
Client gating (P0.2): streamer reports upstream failures through YouTube.failed(); `gates` then leaves the
failing player clients out of later resolves (yt-dlp's `-client` syntax). reset() is "Reset YouTube session".
"""
import os
import threading
import time
from urllib.parse import parse_qs, urlsplit

import yt_dlp

import config
import errors
import search as _yt_search
import ytmusic

from .base import Source

# A muxed mp4 (the last-resort pick, e.g. format 18) is served as audio/mp4: Safari's <audio> plays its audio track.
_MIME = {"m4a": "audio/mp4", "mp4": "audio/mp4", "webm": "audio/webm", "opus": "audio/ogg", "mp3": "audio/mpeg"}
# googlevideo's `c=` (InnerTube clientName) -> the yt-dlp player_client name that produced the URL.
_CLIENTS = {"ANDROID_VR": "android_vr", "TVHTML5": "tv", "TVHTML5_SIMPLY": "tv_simply", "MWEB": "mweb", "WEB": "web",
            "WEB_REMIX": "web_music", "WEB_EMBEDDED_PLAYER": "web_embedded", "IOS": "ios", "VISIONOS": "visionos"}
_SURROUND = ("ec-3", "ac-3", "ac-4", "eac3")  # Dolby; iOS Safari won't play these from a bare m4a URL

VIDEO_GATE_S = 600     # a client that failed one video sits that video out this long
GLOBAL_GATE_S = 1800   # ... and every video, once it was denied twice within DENIAL_WINDOW_S
DENIAL_WINDOW_S = 1800
AUTO_RESET_EVERY_S = 600  # a walled IP would otherwise reset (and drop every cached URL) on each resolve
_clock = time.monotonic  # tests patch this

search_fallbacks = []
resolve_fallbacks = []
last_backend = {"search": None, "resolve": None}
reset_hooks = []  # callables run by reset(), each returning {name: count} (streamer clears its resolve cache)


def fallback_names():
    return [getattr(fb, "backend", getattr(fb, "__name__", "fallback")) for fb in resolve_fallbacks]


def client_of(url):
    """The yt-dlp client name a googlevideo URL was issued to (its `c=` param), else None."""
    c = (parse_qs(urlsplit(url or "").query).get("c") or [""])[0]
    return _CLIENTS.get(c, c.lower()) or None


FORMAT = ("bestaudio[ext=m4a][acodec^=mp4a]/bestaudio[ext=m4a]/bestaudio[acodec!=none]"
          "/best[ext=mp4][acodec^=mp4a]/best")


def ydl_options(clients=None, exclude=()):
    # iOS Safari plays AAC/m4a natively; webm/opus support is spotty, so prefer m4a (and AAC within it).
    # Last resort, for videos (or clients, e.g. android_vr) with no audio-only formats: a muxed mp4 with AAC.
    return {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "format": FORMAT,
        "extractor_args": config.yt_extractor_args(clients, exclude),
    }


class ClientGates:
    """Clients to leave out of a resolve: per (video, client) for VIDEO_GATE_S after its URL was denied, missing
    or a preview, and for every video for GLOBAL_GATE_S once a client was denied twice within DENIAL_WINDOW_S.
    A rejected PO token ("token") only counts on its second strike: a fresh token often works."""

    def __init__(self):
        self._lock = threading.Lock()
        self.video = {}    # (video_id, client) -> until
        self.glob = {}     # client -> until
        self.denials = {}  # client -> [times]
        self.strikes = {}  # (video_id, client) -> time of a first rejected-token strike

    def strike(self, video_id, client, verdict):
        """Record a failure of `client`'s URL for `video_id`. True when it gated the client."""
        if not client or verdict not in ("denied", "token", "missing", "preview"):
            return False
        now, key = _clock(), (video_id, client)
        with self._lock:
            if verdict == "token":
                first = self.strikes.pop(key, None)
                if first is None or now - first > DENIAL_WINDOW_S:
                    self.strikes[key] = now
                    return False
            self.video[key] = now + VIDEO_GATE_S
            if verdict in ("denied", "token"):
                times = [t for t in self.denials.get(client, []) if now - t < DENIAL_WINDOW_S] + [now]
                self.denials[client] = times
                if len(times) >= 2:
                    self.glob[client] = now + GLOBAL_GATE_S
        print(f"yt gate client={client} id={video_id} verdict={verdict}"
              f"{' global' if client in self.glob else ''}", flush=True)
        return True

    def _prune(self, now):
        for d in (self.video, self.glob):
            for k in [k for k, until in d.items() if until <= now]:
                del d[k]

    def excluded(self, video_id):
        """Gated clients for this video (its own gates + global ones), sorted."""
        with self._lock:
            self._prune(_clock())
            return sorted({c for (v, c) in self.video if v == video_id} | set(self.glob))

    def gated(self, video_id, client):
        return bool(client) and client in self.excluded(video_id)

    def release(self, video_id):
        """Drop this video's own gates (every client is gated: better to try them again than give up)."""
        with self._lock:
            for k in [k for k in self.video if k[0] == video_id]:
                del self.video[k]

    def snapshot(self):
        with self._lock:
            now = _clock()
            self._prune(now)
            return {"global": {c: int(until - now) for c, until in self.glob.items()}, "per_video": len(self.video)}

    def clear(self):
        with self._lock:
            counts = {"global_gates": len(self.glob), "video_gates": len(self.video)}
            for d in (self.video, self.glob, self.denials, self.strikes):
                d.clear()
        return counts


gates = ClientGates()

# YoutubeDL is not thread-safe; one lazily built instance keeps its player JS / nsig caches warm. Resolves that
# leave gated clients out use their own instances (a few, keyed by the excluded set), behind the same lock.
_ydl = None
_alt = {}
_ALT_MAX = 4
_ydl_lock = threading.Lock()
_last_auto_reset = None


def _ydl_for(exclude):
    global _ydl
    if not exclude:
        if _ydl is None:
            _ydl = yt_dlp.YoutubeDL(ydl_options())
        return _ydl
    key = tuple(exclude)
    if key not in _alt:
        if len(_alt) >= _ALT_MAX:
            _close(_alt.values())
            _alt.clear()
        _alt[key] = yt_dlp.YoutubeDL(ydl_options(exclude=key))
    return _alt[key]


def _close(instances):
    for y in list(instances):
        try:
            y.close()
        except Exception:
            pass


def _info(video_id, turn, exclude):
    while True:
        if turn is not None:
            turn.wait()
        with _ydl_lock:
            if turn is not None and turn.should_yield():
                continue
            return _ydl_for(exclude).extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)


def _surround(f):
    return (f.get("acodec") or "").lower().startswith(_SURROUND) or (f.get("audio_channels") or 0) > 2


def _choose(info):
    """The format to stream: yt-dlp's pick unless it is Dolby / more than stereo, then the best plain audio-only
    one, else a muxed mp4 with AAC (the same last resort as the format string)."""
    if not _surround(info):
        return info
    plain = [f for f in info.get("formats") or [] if f.get("url") and (f.get("acodec") or "none") != "none"
             and not _surround(f)]
    ok = [f for f in plain if f.get("vcodec") == "none"] or [
        f for f in plain if f.get("ext") == "mp4" and (f.get("acodec") or "").startswith("mp4a")]
    if not ok:
        raise errors.StreamError("UNKNOWN", "only surround (Dolby / 5.1) audio is available")
    return max(ok, key=lambda f: (f.get("ext") == "m4a" and (f.get("acodec") or "").startswith("mp4a"),
                                  f.get("ext") == "m4a", f.get("abr") or f.get("tbr") or 0))


def _preview(fmt, duration):
    """True for a short preview of a longer track: googlevideo's `dur=` (the file's own length) is at most
    min(90 s, 90 %) of a track over 90 s. No `dur=` (or no track length) skips the check."""
    try:
        own = float((parse_qs(urlsplit(fmt.get("url") or "").query).get("dur") or [""])[0])
    except ValueError:
        return False
    return bool(duration) and duration > 90 and 0 < own <= min(90, 0.9 * duration)


def _may_auto_reset():
    global _last_auto_reset
    now = _clock()
    if _last_auto_reset is not None and now - _last_auto_reset < AUTO_RESET_EVERY_S:
        return False
    _last_auto_reset = now
    return True


def _extract(video_id, turn=None):
    """Resolve with yt-dlp, leaving gated clients out. Previews gate their client and try the rest; a bot wall
    resets the YouTube session once; GONE is only final once every client (no exclusions) said so."""
    full = False      # set once exclusions were dropped
    reset_done = False
    preview = None
    for _ in range(5):
        exclude = () if full else gates.excluded(video_id)
        try:
            info = _info(video_id, turn, exclude)
        except Exception as e:
            msg = str(e)
            block = errors.classify_exc(e)
            if "no player clients have been requested" in msg.lower() and not full:
                gates.release(video_id)  # every client is gated for it; the global ones too: try them all
                full = True
                continue
            if block == "BOT_WALL" and not reset_done and _may_auto_reset():
                print(f"yt bot wall id={video_id}; resetting the YouTube session", flush=True)
                reset_done = True
                reset()
                continue
            if block == "GONE" and exclude and not full:
                full = True  # the gated clients were not asked; ask everyone before calling it gone
                continue
            if block in ("BOT_WALL", "GONE"):
                raise errors.StreamError(block, msg) from e
            raise
        fmt = _choose(info)
        if _preview(fmt, info.get("duration")):
            preview = client_of(fmt.get("url"))
            print(f"yt preview id={video_id} client={preview}", flush=True)
            if gates.strike(video_id, preview, "preview"):
                full = False
                continue
            break
        return {
            "url": fmt["url"],
            "headers": dict(fmt.get("http_headers") or info.get("http_headers") or {}),
            "mime": _MIME.get(fmt.get("ext"), "application/octet-stream"),
            "ext": fmt.get("ext"),
            "duration": info.get("duration"),
        }
    if preview is not None:
        raise errors.StreamError("MEMBERS_OR_PAID", f"only a preview is available for {video_id}")
    raise errors.StreamError("UNKNOWN", f"no playable format for {video_id}")


def _clear_ydl_cache():
    """Delete yt-dlp's cache dir (player JS, signature/nsig code). Returns how many files it held."""
    try:
        ydl = yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True})
        root = ydl.cache._get_root_dir()
        n = sum(len(files) for _, _, files in os.walk(root))
        ydl.cache.remove()
        return n
    except Exception as e:
        print(f"yt reset: couldn't clear the yt-dlp cache: {str(e)[:160]!r}", flush=True)
        return 0


def reset():
    """Reset the YouTube session: yt-dlp's cache dir, every shared YoutubeDL (rebuilt lazily), client gates,
    and whatever reset_hooks clear (the resolve cache). Returns what was cleared, as counts."""
    global _ydl, _flat
    with _ydl_lock:
        built = [y for y in (_ydl, *_alt.values()) if y is not None]
        _ydl = None
        _alt.clear()
    with _flat_lock:
        built += [_flat] if _flat is not None else []
        _flat = None
    with _yt_search._ydl_lock:
        built += [_yt_search._ydl] if _yt_search._ydl is not None else []
        _yt_search._ydl = None
    _close(built)
    cleared = {"instances": len(built), "cache_files": _clear_ydl_cache(), **gates.clear()}
    for hook in reset_hooks:
        cleared.update(hook())
    print("yt reset " + " ".join(f"{k}={v}" for k, v in cleared.items()), flush=True)
    return cleared


def _with_fallbacks(kind, primary, fallbacks, *args):
    try:
        out = primary(*args)
        last_backend[kind] = "yt-dlp"
        return out
    except Exception as err:
        for fb in list(fallbacks):
            try:
                out = fb(*args)
            except Exception as e:
                print(f"yt {kind} fallback {getattr(fb, '__name__', fb)} failed: {str(e)[:160]!r}", flush=True)
                continue
            last_backend[kind] = getattr(fb, "backend", getattr(fb, "__name__", "fallback"))
            print(f"yt {kind} via {last_backend[kind]} (yt-dlp failed: {str(err)[:120]!r})", flush=True)
            return out
        raise err


class YouTube(Source):
    key = "yt"
    name = "YouTube"
    can_keep = True
    codec_label = "AAC"

    def track_id(self, native_id):
        return native_id  # bare ids, for history.json / localStorage / tiles.json back-compat

    def search(self, q, limit=config.SEARCH_LIMIT):
        results = None
        if ytmusic.enabled():
            try:
                results = ytmusic.search(q, limit)
            except Exception as e:
                print(f"yt search via ytmusic failed: {str(e)[:160]!r}", flush=True)
        if results:
            last_backend["search"] = "ytmusic"
        else:
            results = _with_fallbacks("search", _yt_search.search, search_fallbacks, q, limit)
        for r in results:
            r["source"] = self.key
        return results

    def resolve(self, native_id, turn=None):
        return _with_fallbacks("resolve", lambda vid: _extract(vid, turn), resolve_fallbacks, native_id)

    def failed(self, native_id, entry, verdict):
        gates.strike(native_id, entry.get("client"), verdict)

    def usable(self, native_id, entry):
        return not gates.gated(native_id, entry.get("client"))

    def art(self, native_id, size):
        square = ytmusic.square_art(native_id, size)  # known once a YT Music response listed the track
        if square:
            return ("redirect", square)
        # mq 320x180, hq 480x360 (always exists), maxres 1280x720 (may be missing; the client falls back)
        name = "mqdefault" if size <= 200 else "maxresdefault" if size >= 600 else "hqdefault"
        return ("redirect", f"https://i.ytimg.com/vi/{native_id}/{name}.jpg")


SOURCE = YouTube()


def lookup(video_id):
    """Basic metadata for one video via yt-dlp (pasted links when YouTube Music is off or failed).
    Uses the resolver's YoutubeDL, so it costs one full extraction."""
    with _ydl_lock:
        info = _ydl_for(()).extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False, process=False)
    return {
        "id": video_id,
        "title": info.get("track") or info.get("title") or "",
        "channel": info.get("artist") or info.get("channel") or info.get("uploader") or "",
        "album": info.get("album"),
        "duration": info.get("duration"),
        "thumb": f"https://i.ytimg.com/vi/{video_id}/mqdefault.jpg",
        "source": "yt",
    }


_flat = None
_flat_lock = threading.Lock()


def playlist_flat(list_id, limit=100):
    """(title, tracks) for a playlist via yt-dlp flat extraction: ids, titles, channels, durations."""
    global _flat
    with _flat_lock:
        if _flat is None:
            _flat = yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "extract_flat": "in_playlist",
                                      "skip_download": True, "extractor_args": config.yt_extractor_args()})
        _flat.params["playlistend"] = limit
        info = _flat.extract_info(f"https://www.youtube.com/playlist?list={list_id}", download=False)
    tracks = []
    for e in info.get("entries") or []:
        vid = (e or {}).get("id")
        if isinstance(vid, str) and len(vid) == 11 and all(c.isalnum() or c in "-_" for c in vid):
            tracks.append({"id": vid, "title": e.get("title") or "", "duration": e.get("duration"),
                           "channel": e.get("channel") or e.get("uploader") or "",
                           "thumb": f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg", "source": "yt"})
    return info.get("title"), tracks[:limit]
