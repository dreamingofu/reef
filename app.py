import hmac
import ipaddress
import logging
import os
import platform
import re
import subprocess
import threading
import time
from datetime import timedelta
from datetime import timezone as dt_timezone
from urllib.parse import parse_qs, parse_qsl, quote, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

import yt_dlp
from flask import Flask, Response, abort, jsonify, redirect, render_template, request

import blocklist
import config
import downloader
import errors
import history
import likes
import listens
import log
import lyrics
import navidrome
import playlists
import settings
import sources
import store
import streamer
import tagging
import tiles
import ytmusic

logger = logging.getLogger(__name__)

VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
TRACK_ID = re.compile(r"^(?:[a-z]{2}:)?[\w.-]{1,128}$")
COVER_ID = re.compile(r"^[\w.-]{1,128}$")
ART_CACHE = "public, max-age=86400"
ART_LOOKUP_BACKOFF = 600  # seconds before retrying a Navidrome lookup that found nothing
HINT_MAX = 200
LINK_MAX = 2048
LIST_ID = re.compile(r"^[A-Za-z0-9_-]{12,80}$")
ALBUM_ID = re.compile(r"^MPRE[A-Za-z0-9_-]{6,60}$")
AUDIO_LIST_ID = re.compile(r"^OLAK5uy_[A-Za-z0-9_-]{6,60}$")  # an album's audio playlist, opens the album
CHANNEL_ID = re.compile(r"^UC[A-Za-z0-9_-]{22}$")
MOOD_PARAMS = re.compile(r"^[A-Za-z0-9_%=-]{8,300}$")
BATCH_MAX = 100
YT_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"}
SHORT_HOSTS = {"youtu.be", "www.youtu.be"}
# A YouTube link inside shared text ("Check this out https://youtu.be/... via app").
SHARED_LINK = re.compile(r"(?<![\w./-])(?:https?://)?(?:(?:www|m|music)\.)?(?:youtube\.com|youtu\.be)/[^\s<>\"']+", re.I)

# Loopback and Tailscale (CGNAT range + Tailscale's IPv6 ULA) are trusted; any other network needs REEF_PASSWORD.
TRUSTED_NETS = [ipaddress.ip_network(n) for n in ("127.0.0.0/8", "::1/128", "100.64.0.0/10", "fd7a:115c:a1e0::/48")]
OFF_TAILNET = "Reef is only open on Tailscale. Set REEF_PASSWORD in reef.env to allow other networks.\n"
STARTED = time.time()

app = Flask(__name__)
# The biggest legitimate body is a few thousand tracks added to a playlist; refuse anything far beyond that.
app.config["MAX_CONTENT_LENGTH"] = 4 * 1024 * 1024


def _track(track_id):
    """(source, canonical id, native id) for a valid track id of an enabled source, else None."""
    if not isinstance(track_id, str) or not TRACK_ID.match(track_id):
        return None
    key, native = sources.parse(track_id)
    src = sources.get(key)
    if src is None or (key == "yt" and not VIDEO_ID.match(native)):
        return None
    return src, sources.track_id(key, native), native


def trusted(addr):
    """True for a loopback or Tailscale client address. Nothing proxies Reef, so the socket peer
    (REMOTE_ADDR) is the client; X-Forwarded-For is never read, a client could forge it."""
    try:
        ip = ipaddress.ip_address((addr or "").split("%")[0])
    except ValueError:
        return False
    ip = getattr(ip, "ipv4_mapped", None) or ip  # ::ffff:100.x.y.z from a dual-stack socket
    return any(ip in net for net in TRUSTED_NETS)


@app.before_request
def require_password():
    if not config.OPEN and trusted(request.remote_addr):
        return None
    if not config.PASSWORD:
        if config.OPEN:
            return None
        return Response(OFF_TAILNET, 403, {"Content-Type": "text/plain; charset=utf-8"})
    auth = request.authorization
    if auth and hmac.compare_digest((auth.password or "").encode(), config.PASSWORD.encode()):
        return None
    return Response("Password required", 401, {"WWW-Authenticate": 'Basic realm="reef"'})


@app.get("/")
def index():
    return render_template("index.html", tiles=tiles.load())


@app.get("/api/health")
def health():
    return jsonify({
        "reef": "ok",
        "navidrome": navidrome.ping(),
        "download_dir": str(config.DOWNLOAD_DIR),
        "ytdlp": yt_dlp.version.__version__,
        "yt_client": config.YT_CLIENT,
        "yt_backend": sources.youtube.last_backend,
        "yt_fallbacks": sources.youtube.fallback_names(),
        "stream_timing": log.redact_obj(streamer.last_timing),
        "stream_cache": streamer.stats(),
        "yt_gates": sources.youtube.gates.snapshot(),
        "failover_cooling": sources.failover.cooling(),
        "sources": list(sources.SOURCES),
        "ytmusic": {"enabled": ytmusic.enabled(), "version": ytmusic.VERSION},
    })


_git_sha = None


def _version():
    """Short git sha of the checkout (the phone runs `git pull`), or None without git."""
    global _git_sha
    if _git_sha is None:
        try:
            out = subprocess.run(["git", "-C", str(config.BASE_DIR), "rev-parse", "--short", "HEAD"],
                                 capture_output=True, text=True, timeout=5)
            _git_sha = out.stdout.strip() if out.returncode == 0 else ""
        except (OSError, subprocess.SubprocessError):
            _git_sha = ""
    return _git_sha or None


def _safe_url(url):
    """A configured instance URL without user:pass@ and with every query value masked (an API key, a token)."""
    try:
        u = urlsplit(str(url))
        host = u.hostname or ""
        netloc = (f"[{host}]" if ":" in host else host) + (f":{u.port}" if u.port else "")
    except ValueError:
        return "<invalid url>"
    query = "&".join(f"{k}={log.mask(v)}" for k, v in parse_qsl(u.query, keep_blank_values=True))
    return urlunsplit((u.scheme, netloc, u.path, query, ""))


def _config_summary():
    """The settings that change behaviour, never secrets: passwords only as set/unset, yt-dlp extras by key."""
    return {
        "host": config.HOST, "port": config.PORT, "server": "flask-dev" if config.DEV else "waitress",
        "open": config.OPEN, "password_set": bool(config.PASSWORD),
        "download_dir": str(config.DOWNLOAD_DIR), "default_source": settings.get("default_source"),
        "yt_client": config.YT_CLIENT, "ytmusic": settings.get("ytmusic"),
        "tracks_api": [_safe_url(u) for u in config.TRACKS_API],
        "piped": [_safe_url(u) for u in config.PIPED_INSTANCES],
        "invidious": [_safe_url(u) for u in config.INVIDIOUS_INSTANCES],
        "navidrome": {"url": _safe_url(config.NAVIDROME_URL), "configured": navidrome.configured()},
        "sponsorblock": settings.get("sponsorblock_keep"), "replaygain": settings.get("replaygain"),
        "lrc_sidecar": settings.get("lrc_sidecar"), "prefer_library": settings.get("prefer_library"),
        "settings_overridden": sorted(r["key"] for r in settings.schema() if r["overridden"]),
        "ydl_extra_keys": sorted(config.YDL_EXTRA), "fake_wall": config.FAKE_WALL,
        "log_dir": str(config.LOG_DIR) if config.LOG_DIR else None,
    }


@app.get("/api/diagnostics")
def api_diagnostics():
    """Everything worth pasting into a bug report, from the status sheet's "Copy diagnostics"."""
    return jsonify(log.redact_obj({
        "version": {"reef": _version(), "ytdlp": yt_dlp.version.__version__, "ytmusicapi": ytmusic.VERSION,
                    "python": platform.python_version(), "platform": platform.platform(terse=True)},
        "uptime_s": int(time.time() - STARTED),
        "config": _config_summary(),
        "stream_cache": streamer.stats(),
        "last_stream": streamer.last_timing,
        "yt_backend": sources.youtube.last_backend,
        "yt_gates": sources.youtube.gates.snapshot(),
        "failover_cooling": sources.failover.cooling(),
        "jobs": [{k: j.get(k) for k in ("id", "status", "error")} for j in downloader.list_jobs()][:20],
        "log": log.recent(),
    }))


@app.get("/api/search")
def api_search():
    q = request.args.get("q", "").strip()
    src = sources.get(request.args.get("src") or sources.default_key())
    if src is None:
        return jsonify({"error": "unknown source"}), 400
    if not q:
        return jsonify([])
    blocked = blocklist.matcher() if src.key == "yt" else blocklist.Matcher([])
    try:
        # Ask for extra rows while artists are blocked, so a query they dominate still fills the page.
        results = src.search(q, config.SEARCH_LIMIT * 2 if blocked else config.SEARCH_LIMIT)
    except Exception as e:
        return jsonify({"error": str(e)[:300]}), 502
    results = blocked.tracks(results)[:config.SEARCH_LIMIT]
    streamer.prefetch([r["id"] for r in results[:3] if _track(r["id"])])
    for r in results:
        r.setdefault("source", src.key)
    return jsonify(_mark_kept(results))


@app.get("/api/suggest")
def api_suggest():
    """Search-box suggestions. Called per keystroke, so it never fails: errors are an empty list."""
    q = request.args.get("q", "").strip()[:HINT_MAX]
    out = []
    if q and ytmusic.enabled():
        try:
            out = ytmusic.suggestions(q)
        except Exception as e:
            logger.warning(f"suggest failed: {str(e)[:160]!r}")
    resp = jsonify(out)
    resp.headers["Cache-Control"] = "private, max-age=600"
    return resp


def _mark_kept(tracks):
    """Mark `kept` and `liked` (one file read each, not one per track). A kept track Navidrome has scanned
    also gets `nd_id`: the player streams `nd:<nd_id>` instead (production plan P1.5)."""
    kept = history.nd_ids()  # every kept track, not just the newest
    liked = likes.ids()
    for t in tracks:
        tid = t.get("id")
        t["kept"] = tid in kept or str(tid).startswith("nd:")  # library tracks are the library itself
        t["liked"] = tid in liked
        if kept.get(tid):
            t["nd_id"] = kept[tid]
    return tracks


@app.get("/api/radio/<track_id>")
def api_radio(track_id):
    """The YT Music radio mix for a YouTube track (seed usually first; the client dedupes)."""
    t = _track(track_id)
    if not t or t[0].key != "yt":
        abort(404)
    if not ytmusic.enabled():
        return jsonify({"error": "YouTube Music is off (REEF_YTMUSIC=0)"}), 502
    try:
        tracks = blocklist.filter_tracks(ytmusic.radio(t[2]))
    except Exception as e:
        return jsonify({"error": str(e)[:300]}), 502
    streamer.prefetch([r["id"] for r in tracks if r["id"] != t[1]][:2])
    return jsonify(_mark_kept(tracks))


def parse_link(raw):
    """What a pasted YouTube / YT Music link points at, parsed only (never fetched):
    ("track", video_id, list_id|None), ("playlist", list_id), ("album", browse_id), or None."""
    if not isinstance(raw, str):
        return None
    raw = raw.strip()
    if not raw or len(raw) > LINK_MAX:
        return None
    if VIDEO_ID.match(raw):
        return ("track", raw, None)
    if "://" not in raw and re.match(r"^(?:[\w-]+\.)*(?:youtube\.com|youtu\.be)/", raw, re.I):
        raw = "https://" + raw
    try:
        u = urlsplit(raw)
        port = u.port
    except ValueError:
        return None
    host = (u.hostname or "").lower()
    if u.scheme.lower() not in ("http", "https") or port is not None or u.username or u.password:
        return None
    qs = parse_qs(u.query)
    v = (qs.get("v") or [""])[0]
    lst = (qs.get("list") or [""])[0]
    lst = lst if LIST_ID.match(lst) else None
    parts = [p for p in u.path.split("/") if p]
    if host in SHORT_HOSTS:
        return ("track", parts[0], lst) if len(parts) == 1 and VIDEO_ID.match(parts[0]) else None
    if host not in YT_HOSTS or not parts:
        return None
    if parts == ["watch"]:
        if VIDEO_ID.match(v):
            return ("track", v, lst)
        return ("playlist", lst) if lst else None
    if len(parts) == 2 and parts[0] in ("shorts", "live", "embed", "v") and VIDEO_ID.match(parts[1]):
        return ("track", parts[1], lst)
    if parts == ["playlist"] and lst:
        return ("playlist", lst)
    if len(parts) == 2 and parts[0] == "browse":
        if ALBUM_ID.match(parts[1]):
            return ("album", parts[1])
        if parts[1].startswith("VL") and LIST_ID.match(parts[1][2:]):
            return ("playlist", parts[1][2:])
    return None


def _link_track(video_id):
    if ytmusic.enabled():
        try:
            found = ytmusic.song(video_id)
            if found:
                return found
        except Exception as e:
            logger.warning(f"link: ytmusic song {video_id} failed: {str(e)[:160]!r}")
    return sources.youtube.lookup(video_id)


def _link_playlist(list_id):
    if ytmusic.enabled():
        try:
            page = ytmusic.playlist(list_id)
            if page["tracks"]:
                return page["title"], page["tracks"]
        except Exception as e:
            logger.warning(f"link: ytmusic playlist {list_id} failed: {str(e)[:160]!r}")
    return sources.youtube.playlist_flat(list_id)


@app.get("/api/link")
def api_link():
    """Tracks behind a pasted YouTube / YT Music link (watch, youtu.be, shorts, playlist, album, bare id)."""
    parsed = parse_link(request.args.get("url", ""))
    if not parsed:
        return jsonify({"error": "Not a YouTube or YouTube Music link"}), 400
    kind = parsed[0]
    try:
        if kind == "track":
            body = {"kind": "track", "tracks": [_link_track(parsed[1])], "title": None, "list": parsed[2]}
        elif kind == "album":
            if not ytmusic.enabled():
                return jsonify({"error": "Album links need YouTube Music (REEF_YTMUSIC=1)"}), 502
            page = ytmusic.album(parsed[1])
            body = {"kind": "playlist", "tracks": page["tracks"], "title": page["title"]}
        else:
            title, tracks = _link_playlist(parsed[1])
            body = {"kind": "playlist", "tracks": tracks, "title": title}
    except Exception as e:
        return jsonify({"error": str(e)[:300]}), 502
    body["tracks"] = [t for t in body["tracks"] or [] if t]
    if kind != "track":  # a pasted single track is explicit, like opening a blocked artist's page
        body["tracks"] = blocklist.filter_tracks(body["tracks"])
    if not body["tracks"]:
        msg = "Video not found or unavailable" if kind == "track" else "Playlist is empty or unavailable"
        return jsonify({"error": msg}), 404
    _mark_kept(body["tracks"])
    streamer.prefetch([t["id"] for t in body["tracks"][:1]])
    return jsonify(body)


# ---------- YouTube Music browse: moods, album / artist / playlist pages ----------

def _ytm_off():
    return jsonify({"error": "YouTube Music is off (REEF_YTMUSIC=0)"}), 502


def _page(body, tracks_key="tracks"):
    """Mark kept, warm the first track, send. Blocked tracks were already dropped (or the page is flagged)."""
    _mark_kept(body[tracks_key])
    streamer.prefetch([t["id"] for t in body[tracks_key][:1]])
    return jsonify(body)


@app.get("/api/moods")
def api_moods():
    """YouTube Music's moods & genres, grouped by section. A failed refetch serves the last list (`stale`)."""
    stale = False
    try:
        if not ytmusic.enabled():
            raise RuntimeError("YouTube Music is off (REEF_YTMUSIC=0)")
        items = ytmusic.moods()
    except Exception as e:
        items, stale = ytmusic.cached_moods(), True
        if not items:
            return jsonify({"error": str(e)[:300]}), 502
    sections = {}
    for m in items:
        sections.setdefault(m["section"], []).append(
            {"title": m["title"], "params": m["params"], "color": m["color"], "art": m["art"]})
    body = {"sections": [{"title": k, "items": v} for k, v in sections.items()]}
    if stale:
        body["stale"] = True
    return jsonify(body)


@app.get("/api/mood")
def api_mood():
    params = request.args.get("params", "")
    if not MOOD_PARAMS.match(params):
        return jsonify({"error": "invalid params"}), 400
    if not ytmusic.enabled():
        return _ytm_off()
    try:
        playlists = ytmusic.mood_playlists(params)
    except Exception as e:
        return jsonify({"error": str(e)[:300]}), 502
    return jsonify({"title": ytmusic.mood_title(params), "playlists": playlists})


@app.get("/api/album/<browse_id>")
def api_album(browse_id):
    """An album page. A blocked artist's album still opens (explicit navigation), flagged `blocked`;
    other albums (compilations) drop blocked artists' tracks."""
    if not (ALBUM_ID.match(browse_id) or AUDIO_LIST_ID.match(browse_id)):
        abort(404)
    if not ytmusic.enabled():
        return _ytm_off()
    try:
        page = ytmusic.album(browse_id)
    except Exception as e:
        return jsonify({"error": str(e)[:300]}), 502
    blocked = blocklist.matcher()
    page["blocked"] = blocked.any_artist(page["artists"])
    if not page["blocked"]:
        page["tracks"] = blocked.tracks(page["tracks"])
    return _page(page)


@app.get("/api/artist/<channel_id>")
def api_artist(channel_id):
    """An artist page; a blocked artist's still loads, flagged `blocked`. Blocked related artists are hidden."""
    if not CHANNEL_ID.match(channel_id):
        abort(404)
    if not ytmusic.enabled():
        return _ytm_off()
    try:
        page = ytmusic.artist(channel_id)
    except Exception as e:
        return jsonify({"error": str(e)[:300]}), 502
    blocked = blocklist.matcher()
    page["blocked"] = blocked.artist(page["id"], page["name"]) or blocked.artist(page["channel_id"])
    if not page["blocked"]:
        page["songs"] = blocked.tracks(page["songs"])
    page["related"] = [r for r in page["related"] if not blocked.artist(r["id"], r["name"])]
    return _page(page, "songs")


@app.get("/api/playlist/<list_id>")
def api_playlist(list_id):
    if not LIST_ID.match(list_id):
        abort(404)
    if not ytmusic.enabled():
        return _ytm_off()
    try:
        page = ytmusic.playlist(list_id)
    except Exception as e:
        return jsonify({"error": str(e)[:300]}), 502
    page["tracks"] = blocklist.filter_tracks(page["tracks"])
    return _page(page)


# ---------- blocked artists ----------

@app.get("/api/blocklist")
def api_blocklist():
    return jsonify(blocklist.entries())


@app.post("/api/blocklist")
def api_blocklist_add():
    """Block an artist by name (required) and channel id (optional, UC...). Re-blocking returns the entry (200)."""
    body = request.get_json(silent=True) or {}
    name, artist_id = body.get("name"), body.get("id") or None
    if not isinstance(name, str) or not name.strip() or len(name) > blocklist.NAME_MAX:
        return jsonify({"error": "name is required"}), 400
    if artist_id is not None and not (isinstance(artist_id, str) and CHANNEL_ID.match(artist_id)):
        return jsonify({"error": "invalid artist id"}), 400
    entry, created = blocklist.add(artist_id, name)
    return jsonify({"entry": entry, "created": created}), 201 if created else 200


@app.delete("/api/blocklist")
def api_blocklist_remove():
    artist_id, name = request.args.get("id", "").strip(), request.args.get("name", "").strip()[:blocklist.NAME_MAX]
    if not artist_id and not name:
        return jsonify({"error": "id or name is required"}), 400
    if not blocklist.remove(artist_id or None, name or None):
        abort(404)
    return "", 204


@app.get("/share")
def share_target():
    """PWA Web Share Target (GET): open the first shared YouTube link in Search as `/?link=<url>#search`;
    otherwise search for the shared text as `/?q=<text>#search`."""
    fields = [str(request.args.get(k) or "")[:LINK_MAX] for k in ("url", "text", "title")]
    for field in fields:
        for m in SHARED_LINK.finditer(field):
            link = m.group(0).rstrip(".,;:!?)]}")
            link = link if "://" in link else "https://" + link
            if parse_link(link):
                return redirect("/?link=" + quote(link, safe="") + "#search", 302)
    # Other apps share "Song by Artist https://..."; search for the words, without the link.
    text = next((t for t in (" ".join(re.sub(r"https?://\S+", " ", f).split()) for f in fields[1:]) if t), "")
    return redirect(("/?q=" + quote(text[:HINT_MAX], safe="") if text else "/") + "#search", 302)


@app.get("/api/sources")
def api_sources():
    return jsonify(sources.enabled())


@app.post("/api/prefetch")
def api_prefetch():
    body = request.get_json(silent=True) or {}
    ids = body.get("ids")
    if not isinstance(ids, list):
        return jsonify({"error": "ids must be a list"}), 400
    ids = [t[1] for t in map(_track, ids) if t][:5]
    return jsonify({"queued": streamer.prefetch(ids)}), 202


@app.get("/api/stream/<track_id>")
def api_stream(track_id):
    t = _track(track_id)
    if not t:
        abort(404)
    # ?fresh=<token>: evict the cached URL and resolve again (the player's recovery). The first request
    # carrying a given token evicts; the reloaded element's later Range requests repeat it harmlessly.
    fresh = request.args.get("fresh", "").strip()[:64]
    try:
        return streamer.proxy(t[1], fresh=fresh if fresh not in ("", "0") else None)
    except Exception as e:
        return jsonify(errors.payload(e)), 502


@app.post("/api/yt/reset")
def api_yt_reset():
    """Reset the YouTube session: yt-dlp cache dir, YoutubeDL instances, client gates, resolve cache."""
    return jsonify({"ok": True, "cleared": streamer.reset_youtube()})


def _keep_hints(item):
    """Album tags sent with a keep from an album page: {album?, album_artist?, track_number?, year?}.
    {} when there are none, None when one is malformed."""
    hints = {}
    for key in ("album", "album_artist"):
        v = item.get(key)
        if v is None or v == "":
            continue
        if not isinstance(v, str) or len(v) > HINT_MAX:
            return None
        if v.strip():
            hints[key] = v.strip()
    n = item.get("track_number")
    if n is not None and n != "":
        if isinstance(n, str) and re.fullmatch(r"[0-9]{1,3}", n.strip()):
            n = int(n)
        if not isinstance(n, int) or isinstance(n, bool) or not 0 < n < 1000:
            return None
        hints["track_number"] = n
    y = item.get("year")
    if y is not None and y != "":
        y = str(y) if isinstance(y, int) and not isinstance(y, bool) else y
        if not isinstance(y, str) or not re.fullmatch(r"[0-9]{4}", y.strip()):
            return None
        hints["year"] = y.strip()
    return hints


def _keep(item, force=False):
    """Queue one keep through the duplicate guard: (outcome, payload, http status), outcome being
    "queued" (payload = job), "kept" (history entry), "keeping" (running job) or "invalid" (message)."""
    t = _track(str(item.get("id", "")))
    if not t:
        return "invalid", "invalid track id", 400
    if not t[0].can_keep:
        return "invalid", f"{t[0].name} tracks are already in your library", 400
    hints = _keep_hints(item)
    if hints is None:
        return "invalid", "invalid album hints", 400
    # `force` re-keeps a library track (e.g. to pick up better tags), but never runs alongside a keep
    # of the same track that's still going: both would write the same file.
    entry = history.get(t[1])
    if entry and not force and entry.get("file") and os.path.exists(entry["file"]):
        return "kept", entry, 409
    job, created = downloader.enqueue_unique(t[1], str(item.get("title", ""))[:200],
                                             str(item.get("channel", ""))[:200], hints)
    return ("queued", job, 202) if created else ("keeping", job, 409)


@app.post("/api/download")
def api_download():
    body = request.get_json(silent=True) or {}
    outcome, payload, status = _keep(body, bool(body.get("force")))
    if outcome == "invalid":
        return jsonify({"error": payload}), status
    if outcome == "kept":
        return jsonify({"error": "Already in your library", "entry": payload}), status
    if outcome == "keeping":
        return jsonify({"error": "Already keeping", "job": payload}), status
    return jsonify(payload), status


@app.post("/api/download/batch")
def api_download_batch():
    """Keep a whole album (or any selection): each track goes through the same duplicate guard as a single keep."""
    body = request.get_json(silent=True) or {}
    items = body.get("tracks")
    if not isinstance(items, list) or not items:
        return jsonify({"error": "tracks must be a non-empty list"}), 400
    if len(items) > BATCH_MAX:
        return jsonify({"error": f"at most {BATCH_MAX} tracks per batch"}), 400
    queued, skipped = [], []
    for item in items:
        if not isinstance(item, dict):
            skipped.append({"id": None, "reason": "invalid"})
            continue
        outcome, payload, _ = _keep(item)
        if outcome == "queued":
            queued.append(payload)
        else:
            raw = item.get("id")
            skipped.append({"id": raw[:128] if isinstance(raw, str) else None, "reason": outcome})
    return jsonify({"queued": queued, "skipped": skipped}), 202


@app.get("/api/jobs")
def api_jobs():
    return jsonify(downloader.list_jobs())


@app.delete("/api/jobs/<job_id>")
def api_job_delete(job_id):
    if not downloader.remove(job_id):
        abort(404)
    return "", 204


@app.get("/api/history")
def api_history():
    return jsonify(history.list_recent())


# ---------- listen log, stats, recap ----------

def _int_arg(name, default, lo, hi):
    v = request.args.get(name, default, type=int)
    return default if v is None else min(max(v, lo), hi)


def _tz():
    """The listener's time zone for hours / weekdays / days: `tz` (IANA name, from the browser's
    Intl API), else `utc_offset` (minutes east of UTC), else the server's own (Termux may not know it)."""
    name = request.args.get("tz", "").strip()
    if name and len(name) <= 64:
        try:
            return ZoneInfo(name)
        except Exception:  # unknown name, or no tz database on this device
            pass
    offset = request.args.get("utc_offset", type=int)
    if offset is not None and -16 * 60 <= offset <= 16 * 60:
        return dt_timezone(timedelta(minutes=offset))
    return None


@app.post("/api/listens")
def api_listens_add():
    """A play the player finished or left. Sent with navigator.sendBeacon, whose Content-Type may not be JSON."""
    body = request.get_json(force=True, silent=True)
    try:
        entry, created = listens.record(body)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"entry": entry, "created": created}), 201 if created else 200


@app.get("/api/listens")
def api_listens():
    before = request.args.get("before", type=float)
    counted = {"1": True, "0": False}.get(request.args.get("counted", ""))
    return jsonify(listens.recent(_int_arg("limit", 50, 1, 500), before, counted))


@app.delete("/api/listens")
def api_listens_clear():
    listens.clear()
    return "", 204


@app.get("/api/stats")
def api_stats():
    period = request.args.get("period", "week")
    if period not in listens.PERIODS:
        return jsonify({"error": "period must be week, month, year or all"}), 400
    return jsonify(listens.stats(listens.entries(), period, tz=_tz(), limit=_int_arg("limit", 10, 1, 50)))


@app.get("/api/recap")
def api_recap():
    period = request.args.get("period", "month")
    if period not in ("month", "year"):
        return jsonify({"error": "period must be month or year"}), 400
    offset = _int_arg("offset", 0, -1, 1200)
    return jsonify(listens.recap(listens.entries(), period, offset, tz=_tz(), limit=_int_arg("limit", 10, 1, 50)))


# ---------- likes ----------

def _id_or_404(raw):
    tid = store.track_id(raw)
    if not tid:
        abort(404)
    return tid


@app.get("/api/likes")
def api_likes():
    return jsonify(_mark_kept(likes.entries()))


@app.put("/api/likes/<track_id>")
def api_like(track_id):
    """Like a track; the body is the track as the client shows it (title, channel, ...). Idempotent."""
    tid = _id_or_404(track_id)
    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict) or store.track_id(body.get("id", tid)) != tid:
        return jsonify({"error": "body id doesn't match the URL"}), 400
    entry, created = likes.add(store.clean_track({**body, "id": tid}))
    return jsonify({"entry": entry, "created": created}), 201 if created else 200


@app.delete("/api/likes/<track_id>")
def api_unlike(track_id):
    if not likes.remove(_id_or_404(track_id)):
        abort(404)
    return "", 204


# ---------- playlists ----------

def _pid_or_404(pid):
    if not playlists.valid_id(pid):
        abort(404)
    return pid


def _playlist_body(p):
    return jsonify({**p, "tracks": _mark_kept(p["tracks"])})


@app.get("/api/playlists")
def api_playlists():
    return jsonify(playlists.all_playlists())


@app.post("/api/playlists")
def api_playlist_create():
    body = request.get_json(silent=True) or {}
    try:
        p = playlists.create(body.get("name"), body.get("tracks"), body.get("description") or "")
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return _playlist_body(p), 201


@app.get("/api/playlists/<pid>.m3u")
def api_playlist_m3u(pid):
    p = playlists.get(_pid_or_404(pid))
    if not p:
        abort(404)
    text, _, _ = playlists.m3u(p)
    name = playlists.export_name(p["name"]) + ".m3u"
    ascii_name = re.sub(r"[^A-Za-z0-9 ._-]", "_", name)
    return Response(text, content_type="audio/x-mpegurl; charset=utf-8", headers={
        "Content-Disposition": f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name, safe='')}"})


@app.get("/api/playlists/<pid>")
def api_playlist_get(pid):
    p = playlists.get(_pid_or_404(pid))
    if not p:
        abort(404)
    return _playlist_body(p)


@app.patch("/api/playlists/<pid>")
def api_playlist_patch(pid):
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": "send a JSON object"}), 400
    try:
        p = playlists.update(_pid_or_404(pid), body)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    if not p:
        abort(404)
    return _playlist_body(p)


@app.delete("/api/playlists/<pid>")
def api_playlist_delete(pid):
    if not playlists.delete(_pid_or_404(pid)):
        abort(404)
    return "", 204


@app.post("/api/playlists/<pid>/tracks")
def api_playlist_add(pid):
    """Add {tracks: [...], position?: int, dedupe?: bool}. Invalid track ids are skipped, not an error."""
    body = request.get_json(silent=True) or {}
    try:
        found = playlists.add_tracks(_pid_or_404(pid), body.get("tracks"), body.get("position"),
                                     body.get("dedupe") is True)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    if not found:
        abort(404)
    p, added, skipped = found
    return jsonify({**p, "tracks": _mark_kept(p["tracks"]), "added": added, "skipped": skipped})


@app.delete("/api/playlists/<pid>/tracks/<int:index>")
def api_playlist_remove(pid, index):
    """Remove the track at `index`; `?id=<track id>` makes it a 409 when another track is there now."""
    try:
        p = playlists.remove_track(_pid_or_404(pid), index, request.args.get("id") or None)
    except IndexError:
        abort(404)
    except ValueError as e:
        return jsonify({"error": str(e)}), 409
    if not p:
        abort(404)
    return _playlist_body(p)


@app.post("/api/playlists/<pid>/move")
def api_playlist_move(pid):
    body = request.get_json(silent=True) or {}
    src, dst = body.get("from"), body.get("to")
    if not all(isinstance(v, int) and not isinstance(v, bool) for v in (src, dst)):
        return jsonify({"error": "from and to must be track indexes"}), 400
    try:
        p = playlists.move(_pid_or_404(pid), src, dst)
    except IndexError:
        return jsonify({"error": "index out of range"}), 400
    if not p:
        abort(404)
    return _playlist_body(p)


@app.post("/api/playlists/<pid>/export")
def api_playlist_export(pid):
    """Write the kept tracks to <music>/Reef/Playlists/<name>.m3u and rescan, so Navidrome imports it."""
    try:
        out = playlists.export(_pid_or_404(pid))
    except ValueError as e:
        return jsonify({"error": str(e)}), 409
    except OSError as e:
        logger.warning(f"playlist export {pid} failed: {e}")
        return jsonify({"error": f"couldn't write the playlist file: {e.strerror or e}"}), 500
    if out is None:
        abort(404)
    return jsonify({**out, "rescanned": navidrome.rescan()})


# ---------- settings ----------

def _settings_body():
    return jsonify({"values": settings.public(), "schema": settings.schema()})


@app.get("/api/settings")
def api_settings():
    return _settings_body()


@app.patch("/api/settings")
def api_settings_patch():
    try:
        settings.update(request.get_json(silent=True))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return _settings_body()


@app.get("/api/library/recent")
def api_library_recent():
    try:
        return jsonify(navidrome.newest_albums())
    except Exception:
        return jsonify([])


def _art_size():
    return min(max(request.args.get("size", 300, type=int), 64), 1000)


def _navidrome_cover(cover_id, size):
    """Navidrome cover art as a cacheable response, or None when it can't be fetched."""
    try:
        data, mime = navidrome.cover_art(cover_id, size)
    except Exception:
        return None
    if not mime.startswith("image/"):
        return None
    return Response(data, content_type=mime, headers={"Cache-Control": ART_CACHE})


@app.get("/api/cover/<cover_id>")
def api_cover(cover_id):
    if not COVER_ID.match(cover_id) or not navidrome.configured():
        abort(404)
    resp = _navidrome_cover(cover_id, _art_size())
    if resp is None:
        abort(404)
    return resp


_lookup_misses = {}  # track id -> monotonic time of the last lookup that found nothing
_lookup_lock = threading.Lock()


def _lookup_cover(entry):
    """Find a kept track's Navidrome cover once (then at most every ART_LOOKUP_BACKOFF s) and persist it."""
    track_id = entry["id"]
    with _lookup_lock:
        last = _lookup_misses.get(track_id)
        if last is not None and time.monotonic() - last < ART_LOOKUP_BACKOFF:
            return None
        _lookup_misses[track_id] = time.monotonic()  # also stops concurrent duplicate lookups
    try:
        song = navidrome.find_song(entry.get("title") or "", os.path.basename(entry.get("file") or ""))
    except Exception:
        return None
    if not song or not song.get("coverArt"):
        return None
    history.update(track_id, nd_cover=song["coverArt"], nd_id=song.get("id"))
    with _lookup_lock:
        _lookup_misses.pop(track_id, None)
    return song["coverArt"]


@app.get("/api/art/<track_id>")
def api_art(track_id):
    """Artwork for a track: its Navidrome cover once kept and scanned, else the source's own artwork."""
    t = _track(track_id)
    if not t:
        abort(404)
    src, track_id, native = t
    size = _art_size()
    if src.can_keep and navidrome.configured():
        entry = history.get(track_id)
        if entry:
            cover = entry.get("nd_cover") or _lookup_cover(entry)
            if cover and COVER_ID.match(cover):
                resp = _navidrome_cover(cover, size)
                if resp is not None:
                    return resp
    found = src.art(native, size)
    if not found:
        abort(404)
    if found[0] == "bytes":
        return Response(found[1], content_type=found[2], headers={"Cache-Control": ART_CACHE})
    resp = redirect(found[1], 302)
    resp.headers["Cache-Control"] = "public, max-age=3600"
    return resp


def _hint(value):
    return str(value or "").strip()[:HINT_MAX]


def _seconds(value):
    """A plausible track length in seconds, else None (also for nan/inf, which float() accepts)."""
    try:
        d = float(value)
    except (TypeError, ValueError):
        return None
    return d if 0 < d < 86400 else None


@app.get("/api/lyrics/<track_id>")
def api_lyrics(track_id):
    """Lyrics for the Now Playing view. The client sends what it shows (title, artist, ...) as hints,
    since only kept tracks are known server-side."""
    t = _track(track_id)
    if not t:
        abort(404)
    src, track_id, native = t
    args = request.args
    title, artist, album = _hint(args.get("title")), _hint(args.get("artist")), _hint(args.get("album"))
    duration = _seconds(args.get("duration"))
    found = None
    if src.key == "nd":
        found = navidrome.lyrics(native)  # what Navidrome reads from the file's tags (Reef embeds them on keep)
    else:
        entry = history.get(track_id)
        if entry:  # normally a cache hit: the keep looked lyrics up under this id
            title, artist = title or _hint(entry.get("title")), artist or _hint(entry.get("channel"))
            album, duration = album or _hint(entry.get("album")), duration or _seconds(entry.get("duration"))
        if src.key == "yt":
            # "Artist - Song (Official Video)" from channel "ArtistVEVO" -> ("Song", "Artist"), as a keep tags it.
            title, artist = tagging.guess(title, artist)
    # No title means nothing to search by; for_track would cache that as a miss for a day.
    if not found and title:
        found = lyrics.for_track(track_id, title, artist, album or None, duration)
    if not found:
        return jsonify({"error": "No lyrics found"}), 404
    resp = jsonify({"synced": found.get("synced"), "plain": found.get("plain"),
                    "instrumental": bool(found.get("instrumental"))})
    resp.headers["Cache-Control"] = "private, max-age=3600"
    return resp


@app.get("/api/tiles")
def api_tiles():
    return jsonify(tiles.load())


def main():
    log.setup()
    downloader.start_worker()
    tiles.start_refresher()
    server = "flask-dev" if config.DEV else "waitress"
    logger.info(f"reef start host={config.HOST} port={config.PORT} server={server} version={_version()} "
                f"open={int(config.OPEN)} password={int(bool(config.PASSWORD))} downloads={config.DOWNLOAD_DIR}")
    if config.DEV:
        app.run(host=config.HOST, port=config.PORT, threaded=True)
        return
    from waitress import serve
    # 16 threads: every playing <audio> holds one for its whole Range response, plus art, search and prefetch
    # calls in parallel. waitress flushes each chunk as it's written (send_bytes=1 by default), so /api/stream
    # still streams; channel_timeout only closes idle keep-alive connections, never a response in progress.
    # outbuf_high_watermark: by default waitress lets a response run 16 MB ahead of a slow client (spilling to
    # temp files), so a Range Safari aborts after the first KB would still pull MBs from googlevideo over the
    # phone's data. 1 MiB keeps the proxy close to what the client actually reads, like the dev server did.
    serve(app, host=config.HOST, port=config.PORT, threads=16, channel_timeout=120, connection_limit=100,
          outbuf_high_watermark=1024 * 1024, ident=None)


if __name__ == "__main__":
    main()
