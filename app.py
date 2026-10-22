import hmac
import ipaddress
import json
import logging
import os
import platform
import re
import subprocess
import threading
import time
from collections import OrderedDict
from datetime import timedelta
from datetime import timezone as dt_timezone
from pathlib import Path
from urllib.parse import parse_qs, parse_qsl, quote, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

import requests
import yt_dlp
from flask import Flask, Response, abort, jsonify, redirect, render_template, request, send_file
from mutagen import File as MutagenFile
from werkzeug.exceptions import BadRequest, RequestEntityTooLarge

import aliases
import backup
import blocklist
import charts
import config
import downloader
import errors
import health as source_health
import history
import home
import importer
import itunes
import likes
import listens
import log
import lyrics
import matching
import navidrome
import personal
import playlists
import recommend
import scrobble
import segments
import settings
import sources
import store
import streamer
import tagging
import testaudio
import tiles
import updater
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
        "navidrome": {**navidrome.ping(), "configured": navidrome.configured()},
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
        "test_audio": testaudio.enabled(),
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
        "tracks_api": [_safe_url(u) for u in settings.get("tracks_api")],
        "piped": [_safe_url(u) for u in settings.get("piped_instances")],
        "invidious": [_safe_url(u) for u in settings.get("invidious_instances")],
        "navidrome": {"url": _safe_url(config.NAVIDROME_URL), "configured": navidrome.configured()},
        "sponsorblock": settings.get("sponsorblock_keep"), "replaygain": settings.get("replaygain"),
        "lrc_sidecar": settings.get("lrc_sidecar"), "prefer_library": settings.get("prefer_library"),
        "keep_lossless": settings.get("keep_lossless"), "sponsorblock_play": settings.get("sponsorblock_play"),
        "replace_dead_tracks": settings.get("replace_dead_tracks"),
        "yt_personalize": settings.get("yt_personalize"), "charts_country": settings.get("charts_country"),
        "settings_overridden": sorted(r["key"] for r in settings.schema() if r["overridden"]),
        "ydl_extra_keys": sorted(config.YDL_EXTRA), "fake_wall": config.FAKE_WALL, "test_audio": testaudio.status(),
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
        "home": home.status(),
        "personal": {k: v for k, v in personal.status().items() if k != "recent"},
        "scrobble": {k: v for k, v in scrobble.status().items()
                     if k in ("enabled", "token_set", "pending", "submitted", "last_ok_at", "last_error", "retry_at")},
        "update": {k: v for k, v in updater.status().items() if k != "log_tail"},
        "backup": {"last_auto_at": backup.status()["last_auto_at"]},
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
        return jsonify(errors.payload(e)), 502
    results = blocked.tracks(results)[:config.SEARCH_LIMIT]
    streamer.prefetch([r["id"] for r in results[:3] if _track(r["id"])])
    for r in results:
        r.setdefault("source", src.key)
    return jsonify(_mark_kept(results))


@app.get("/api/suggest")
def api_suggest():
    """Search-box suggestions. Called per keystroke, so it never fails: errors are an empty list. YouTube Music
    first; when it's off or failed, the iTunes Search API (setting `itunes_suggestions`)."""
    q = request.args.get("q", "").strip()[:HINT_MAX]
    out, failed = [], False
    if q and ytmusic.enabled():
        try:
            out = ytmusic.suggestions(q)
        except Exception as e:
            failed = True
            logger.warning(f"suggest failed: {str(e)[:160]!r}")
    if q and (failed or not ytmusic.enabled()) and settings.get("itunes_suggestions"):
        out = itunes.suggestions(q)
    resp = jsonify(out)
    resp.headers["Cache-Control"] = "private, max-age=600"
    return resp


def _kept_ids():
    """{track id: Navidrome id or None} for every kept track, not just the newest. A lossless upgrade (P1.7) is
    kept under its Monochrome id; the YouTube id the user kept counts too, so it shows as kept (and plays from
    the library) wherever that YouTube track appears."""
    kept = history.nd_ids()
    if any(k and k.startswith("mc:") for k in kept):  # only Monochrome entries can be upgrades: skip the 2nd pass
        for e in history.list_recent(None):
            if e.get("upgraded_from"):
                kept.setdefault(e["upgraded_from"], e.get("nd_id"))
    return kept


def _kept_entry(track_id):
    """The history entry that keeps `track_id`: its own, or the lossless upgrade made from it."""
    entry = history.get(track_id)
    if entry is None and sources.parse(track_id)[0] == "yt":
        entry = next((e for e in history.list_recent(None) if e.get("upgraded_from") == track_id), None)
    return entry


def _mark_kept(tracks, names=None):
    """Mark `kept` and `liked` (one file read each, not one per track). A kept track Navidrome has scanned
    also gets `nd_id`: the player streams `nd:<nd_id>` instead (production plan P1.5). Every list the client
    gets passes here, so artist aliases (display names) are applied here too."""
    kept = _kept_ids()
    liked = likes.ids()
    (names or aliases.resolver()).tracks(tracks)
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
        return jsonify(errors.payload(e)), 502
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
        return jsonify(errors.payload(e)), 502
    body["tracks"] = [t for t in body["tracks"] or [] if t]
    if kind != "track":  # a pasted single track is explicit, like opening a blocked artist's page
        body["tracks"] = blocklist.filter_tracks(body["tracks"])
    if not body["tracks"]:
        msg = "Video not found or unavailable" if kind == "track" else "Playlist is empty or unavailable"
        return jsonify({"error": msg}), 404
    _mark_kept(body["tracks"])
    streamer.prefetch([t["id"] for t in body["tracks"][:1]])
    return jsonify(body)


# ---------- matching: songs by title + artist (P1.6), dead-track replacement (P1.8), SponsorBlock (P1.9) ----------

MATCH_LIMIT = 10      # search results scored per query
REPLACE_ACCEPT = 0.45  # a replacement plays in place of what the user picked: stricter than a plain match
REPLACE_STOP = 0.93
EXCLUDE_MAX = 20


def _match_query(body):
    """{title, artist, album, duration} from a JSON body, or None without a usable title."""
    if not isinstance(body, dict) or not isinstance(body.get("title"), str) or not body["title"].strip():
        return None
    if any(body.get(k) is not None and not isinstance(body.get(k), str) for k in ("artist", "album")):
        return None
    return {"title": _hint(body["title"]), "artist": _hint(body.get("artist")), "album": _hint(body.get("album")),
            "duration": _seconds(body.get("duration"))}


def _find_song(query, accept=matching.ACCEPT, stop=matching.STOP, exclude=(), same_artist=False, loose=False,
               search=None):
    """(track, score) of the YouTube Music song that best matches `query`, else None. When title + artist
    finds nothing acceptable, the title alone gets a second search (the artist may be spelled another way).
    `loose` (looking up a song by name: /api/match, imports) also accepts importer.loose_best's word-subset titles;
    `search` replaces ytmusic.search (the import pass throttles its requests)."""
    search = search or ytmusic.search
    texts = [matching.search_text(query)]
    if query.get("artist"):
        texts.append(matching.search_text({"title": query["title"]}))
    for text in texts:
        found_list = blocklist.filter_tracks(search(text, MATCH_LIMIT))
        found = matching.best(query, found_list, accept, stop, exclude, same_artist)
        if not found and loose:
            found = importer.loose_best(query, found_list, exclude)
        if found:
            return found
    return None


def _match_body(track, score, key, manual, cached):
    return jsonify({"track": _mark_kept([dict(track)])[0], "score": score, "key": key, "manual": manual,
                    "cached": cached})


@app.post("/api/match")
def api_match():
    """{title, artist?, duration?, album?, key?} -> the YouTube Music song it is: {track, score, key, manual,
    cached}, 404 when nothing scores high enough. `key` (an import's own id for the row) defaults to the
    normalised artist + title; what it resolved to is remembered in matches.json, a manual fix first."""
    body = request.get_json(silent=True)
    query = _match_query(body)
    if not query:
        return jsonify({"error": "title is required (artist and album must be text)"}), 400
    key = body.get("key")
    if key is not None and not matching.valid_key(key):
        return jsonify({"error": f"key must be 1-{matching.KEY_MAX} printable characters without /"}), 400
    key = key or matching.key_for(query["title"], query["artist"])
    hit = matching.cached(key)
    if hit and hit.get("track"):
        return _match_body(hit["track"], hit.get("score"), key, bool(hit.get("manual")), True)
    if not ytmusic.enabled():
        return _ytm_off()
    try:
        found = _find_song(query, loose=True)
    except Exception as e:
        return jsonify(errors.payload(e)), 502
    if not found:
        return jsonify({"error": "No match found", "key": key}), 404
    track, score = found
    entry = matching.remember(key, track, score)
    if entry and entry.get("manual"):  # someone pasted a fix while we searched: theirs wins
        return _match_body(entry["track"], entry.get("score"), key, True, True)
    return _match_body(track, score, key, False, False)


@app.put("/api/match/<key>")
def api_match_fix(key):
    """"Wrong song? Paste link": {id} (a YouTube / YT Music link or video id, or an mc:/nd: id) becomes what
    `key` resolves to, for good: the automatic matcher never overwrites it."""
    if not matching.valid_key(key):
        abort(404)
    body = request.get_json(silent=True) or {}
    raw = body.get("id") if isinstance(body, dict) else None
    parsed = parse_link(raw)
    tid = parsed[1] if parsed and parsed[0] == "track" else store.track_id(raw.strip()) if isinstance(raw, str) else None
    if not tid:
        return jsonify({"error": "Paste a YouTube or YouTube Music song link"}), 400
    track = None
    if sources.parse(tid)[0] == "yt":
        try:
            track = _link_track(tid)
        except Exception as e:
            logger.warning(f"match: lookup of {tid} failed: {str(e)[:160]!r}")
    if not track or track.get("id") != tid:
        track = {"id": tid, "title": _hint(body.get("title")), "channel": _hint(body.get("artist"))}
    entry = matching.remember(key, track, None, manual=True)
    if not entry:
        return jsonify({"error": "invalid track"}), 400
    return _match_body({**track, **entry["track"]}, None, key, True, False)


CANDIDATES_MAX = 5


@app.post("/api/match/candidates")
def api_match_candidates():
    """"Fix wrong match" (UI_REMAKE_PLAN §8.4): {title, artist?, duration?, album?, key?, q?} -> {key, pinned
    (the manual fix for `key`, or null), candidates: [{track, score, ranked}] (YouTube Music's top 5 for `q`, else
    title + artist, scored like /api/match: `score` raw, `ranked` minus the version-word penalty, which orders them),
    error?}. The key and pin come back even when the search fails."""
    body = request.get_json(silent=True)
    query = _match_query(body)
    if not query:
        return jsonify({"error": "title is required (artist and album must be text)"}), 400
    key = body.get("key")
    if key is not None and not matching.valid_key(key):
        return jsonify({"error": f"key must be 1-{matching.KEY_MAX} printable characters without /"}), 400
    key = key or matching.key_for(query["title"], query["artist"])
    hit = matching.cached(key)
    pinned = _mark_kept([dict(hit["track"])])[0] if hit and hit.get("manual") and hit.get("track") else None
    out = {"key": key, "pinned": pinned, "candidates": []}
    q = _hint(body.get("q")) or matching.search_text(query)
    if not ytmusic.enabled():
        return jsonify({**out, "error": "YouTube Music is off"})
    try:
        found = blocklist.filter_tracks(ytmusic.search(q, MATCH_LIMIT))
    except Exception as e:
        return jsonify({**out, "error": errors.payload(e).get("error") or "YouTube Music didn't answer"})
    scored = []
    for k, c in enumerate(found or []):
        if isinstance(c, dict) and store.track_id(c.get("id")):
            raw, ranked = matching.score(query, c)
            scored.append((ranked, -k, raw, c))
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    tracks = _mark_kept([dict(x[3]) for x in scored[:CANDIDATES_MAX]])
    out["candidates"] = [{"track": t, "score": x[2], "ranked": max(0.0, x[0])} for t, x in zip(tracks, scored)]
    return jsonify(out)


@app.post("/api/replace")
def api_replace():
    """{track, exclude?: [ids], key?} for a track that can't play (GONE / REGION / MEMBERS_OR_PAID) -> the same song
    from another upload: {track, score, source, replaced}. YouTube Music songs first, then Monochrome (when on and
    YouTube had nothing near-certain). Never the dead id or one in `exclude` (replacements already tried), and never
    another artist's song of the same title (matching.best same_artist)."""
    body = request.get_json(silent=True)
    dead = store.clean_track(body.get("track")) if isinstance(body, dict) else None
    if not dead or not dead["title"]:
        return jsonify({"error": "track with an id and a title is required"}), 400
    if dead["source"] == "nd":
        return jsonify({"error": "library tracks aren't replaced"}), 400
    extra = body.get("exclude") if isinstance(body.get("exclude"), list) else []
    exclude = {dead["id"], *filter(None, (store.track_id(x) for x in extra[:EXCLUDE_MAX]))}
    query = matching.query_of(dead)
    best, failures, tried, t0 = None, [], 0, time.monotonic()
    if ytmusic.enabled():
        tried += 1
        try:
            found = _find_song(query, REPLACE_ACCEPT, REPLACE_STOP, exclude, same_artist=True)
            best = (*found, "yt") if found else None
        except Exception as e:
            failures.append(f"YouTube Music: {str(e)[:160]}")
    mc = sources.get("mc")
    if mc is not None and (best is None or best[1] < REPLACE_STOP):
        tried += 1
        try:
            found = matching.best(query, mc.search(matching.search_text(query), MATCH_LIMIT), REPLACE_ACCEPT,
                                  REPLACE_STOP, exclude, same_artist=True)
            if found and (best is None or found[1] > best[1]):
                best = (*found, "mc")
        except Exception as e:
            failures.append(f"Monochrome: {str(e)[:160]}")
    logger.info(f"replace id={dead['id']} found={best[0]['id'] if best else None} score={best[1] if best else None} "
                f"source={best[2] if best else None} ms={int((time.monotonic() - t0) * 1000)}")
    if not best:
        if failures and len(failures) == tried:  # nothing could be asked: not a "no such song"
            return jsonify({"error": "; ".join(failures)}), 502
        return jsonify({"error": "No replacement found"}), 404
    track, score, source = best
    key = body.get("key")
    if matching.valid_key(key):
        matching.remember(key, track, score)  # an import row that pointed at the dead id; a manual fix stays
    return jsonify({"track": _mark_kept([dict(track)])[0], "score": score, "source": source, "replaced": dead["id"]})


@app.get("/api/segments/<video_id>")
def api_segments(video_id):
    """SponsorBlock's non-music parts of a YouTube video: [{start, end, category}] ([] = none, or the setting is
    off). 502 when SponsorBlock can't be asked; the player then just plays everything."""
    if not VIDEO_ID.match(video_id):
        abort(404)
    if not settings.get("sponsorblock_play"):
        return jsonify([])
    try:
        found = segments.for_video(video_id)
    except segments.Unavailable as e:
        return jsonify({"error": str(e)[:200]}), 502
    resp = jsonify(found)
    resp.headers["Cache-Control"] = "private, max-age=3600"
    return resp


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
            return jsonify(errors.payload(e)), 502
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
        return jsonify(errors.payload(e)), 502
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
        return jsonify(errors.payload(e)), 502
    blocked = blocklist.matcher()
    page["blocked"] = blocked.any_artist(page["artists"])
    if not page["blocked"]:
        page["tracks"] = blocked.tracks(page["tracks"])
    names = aliases.resolver()
    if names:
        page["artists"] = [{**a, "name": names.name(a["name"], a.get("id"))} for a in page["artists"]]
        page["artist"] = ", ".join(a["name"] for a in page["artists"]) or page["artist"]
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
        return jsonify(errors.payload(e)), 502
    blocked = blocklist.matcher()
    page["blocked"] = blocked.artist(page["id"], page["name"]) or blocked.artist(page["channel_id"])
    if not page["blocked"]:
        page["songs"] = blocked.tracks(page["songs"])
    page["related"] = [r for r in page["related"] if not blocked.artist(r["id"], r["name"])]
    names = aliases.resolver()
    if names:
        page["name"] = names.name(names.name(page["name"], page["channel_id"]), page["id"])
        for r in page["related"]:
            r["name"] = names.name(r["name"], r["id"])
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
        return jsonify(errors.payload(e)), 502
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
    if testaudio.covers(t[0].key):
        # REEF_TEST_AUDIO (loopback only): a local file per id, Range/206 from send_file; YouTube is never asked.
        path = testaudio.pick(t[2])
        return send_file(path, mimetype=testaudio.mimetype(path), conditional=True, max_age=0)
    # ?fresh=<token>: evict the cached URL and resolve again (the player's recovery). The first request
    # carrying a given token evicts; the reloaded element's later Range requests repeat it harmlessly.
    fresh = request.args.get("fresh", "").strip()[:64]
    try:
        return streamer.proxy(t[1], fresh=fresh if fresh not in ("", "0") else None)
    except Exception as e:
        return jsonify(errors.payload(e)), 502


def _file_info(path):
    """A kept file's format for Song info: {file (relative to the music folder), format, size, codec, bitrate (kbps),
    sample_rate, bits, channels, length, replaygain}. Local reads only; missing parts are left out."""
    p = Path(str(path))
    try:
        rel = str(p.relative_to(config.MUSIC_DIR))
    except ValueError:
        rel = p.name
    out = {"file": rel, "format": p.suffix.lstrip(".").lower() or None, "exists": p.is_file()}
    if not out["exists"]:
        return out
    try:
        out["size"] = p.stat().st_size
        f = MutagenFile(str(p))
    except Exception:
        return out
    meta = getattr(f, "info", None)
    if meta is not None:
        codec = getattr(meta, "codec", None) or type(meta).__module__.rsplit(".", 1)[-1]
        out["codec"] = str(codec).split(".")[0].lower() or None
        for key, attr in (("sample_rate", "sample_rate"), ("bits", "bits_per_sample"), ("channels", "channels")):
            v = getattr(meta, attr, None)
            if isinstance(v, int) and v > 0:
                out[key] = v
        br = getattr(meta, "bitrate", None)
        if isinstance(br, (int, float)) and br > 0:
            out["bitrate"] = round(br / 1000)
        length = getattr(meta, "length", None)
        if isinstance(length, (int, float)) and length > 0:
            out["length"] = round(length, 2)
    tags = getattr(f, "tags", None)
    if tags is not None:
        for k in ("REPLAYGAIN_TRACK_GAIN", "replaygain_track_gain", "TXXX:REPLAYGAIN_TRACK_GAIN",
                  "----:com.apple.iTunes:replaygain_track_gain"):
            try:
                v = tags.get(k)
            except Exception:
                v = None
            if v:
                v = v[0] if isinstance(v, list) else getattr(v, "text", [v])[0]
                out["replaygain"] = (v.decode() if isinstance(v, bytes) else str(v))[:32]
                break
    return out


@app.get("/api/stream/<track_id>/info")
def api_stream_info(track_id):
    """Song info's technical part (UI_REMAKE_PLAN §8.3) without touching the network: what the resolve cache knows
    about the stream (codec, bitrate, sample rate, client; null when it isn't cached), the kept file (path, format,
    size, codec, ReplayGain) and whether test audio stands in for it."""
    if not isinstance(track_id, str) or not TRACK_ID.match(track_id):
        abort(404)
    key, native = sources.parse(track_id)
    if key == "yt" and not VIDEO_ID.match(native):
        abort(404)
    tid = sources.track_id(key, native)
    entry = _kept_entry(tid)
    kept = None
    if entry:
        kept = {"kept_at": entry.get("kept_at"), "nd_id": entry.get("nd_id"), "upgraded_from": entry.get("upgraded_from"),
                "id": entry.get("id")}
        if entry.get("file"):
            kept.update(_file_info(entry["file"]))
    nd_id = (entry or {}).get("nd_id")
    stream = streamer.info(tid) or (streamer.info(f"nd:{nd_id}") if nd_id else None)
    return jsonify({"id": tid, "source": key, "stream": stream, "kept": kept,
                    "test_audio": testaudio.covers(key)})


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
    entry = _kept_entry(t[1])
    if entry and not force and entry.get("file") and os.path.exists(entry["file"]):
        return "kept", entry, 409
    job, created = downloader.enqueue_unique(t[1], str(item.get("title", ""))[:200],
                                             str(item.get("channel", ""))[:200], hints,
                                             duration=_seconds(item.get("duration")))
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
    if entry["counted"]:
        try:
            home.listened()  # enough new listens: rebuild Home in the background
        except Exception as e:
            logger.warning(f"home: tick after listen failed: {str(e)[:200]}")
        try:
            scrobble.listened(entry)  # queued for ListenBrainz when a token is set (no-op when it's in already)
        except Exception as e:
            logger.warning(f"scrobble: queueing failed: {str(e)[:200]}")
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


# ---------- Home, "Not interested" ----------

@app.get("/api/home")
def api_home():
    """Home shelves from home.json (rebuilt in the background); see home.py. `ytm=0` leaves out YouTube Music's
    own shelves (served with personalisation on)."""
    body = home.serve(ytm=request.args.get("ytm", "1") != "0")
    names = aliases.resolver()
    for s in body["shelves"]:
        if s["kind"] == "tracks":
            _mark_kept(s["items"], names)
        elif s["kind"] == "cards":
            _mark_kept([c["track"] for c in s["items"] if c.get("type") == "track" and c.get("track")], names)
            for c in s["items"]:
                if c.get("type") == "track" and c.get("track"):
                    c["subtitle"] = c["track"].get("channel") or c.get("subtitle")
                elif c.get("type") == "artist" and names:
                    c["title"] = names.name(c.get("title"), c.get("id"))
        elif s["kind"] == "artists" and names:
            for a in s["items"]:
                a["name"] = names.name(a.get("name"), a.get("id"))
        elif s["kind"] == "seeds" and names:
            for m in s["items"]:
                m["name"] = names.name(m.get("name"), m.get("artist_id"))
                m["seed"] = names.track(dict(m["seed"]))
                m["with"] = [names.name(n) for n in m.get("with") or []]
    resp = jsonify(body)
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.get("/api/dislikes")
def api_dislikes():
    return jsonify(recommend.dislikes())


@app.post("/api/dislikes/<track_id>")
def api_dislike(track_id):
    """"Not interested": the track sits out 14 days; a second track by the same artist within 30 days hides
    the artist (`blocked` in the answer, so the UI can say so). The body is the track as the client shows it."""
    tid = _id_or_404(track_id)
    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict) or store.track_id(body.get("id", tid)) != tid:
        return jsonify({"error": "body id doesn't match the URL"}), 400
    entry, created, blocked = recommend.dislike(store.clean_track({**body, "id": tid}))
    return jsonify({"entry": entry, "created": created, "blocked": blocked}), 201 if created else 200


@app.delete("/api/dislikes/<track_id>")
def api_undislike(track_id):
    """Undo "Not interested" (and the artist block it caused)."""
    if not recommend.undislike(_id_or_404(track_id)):
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
    pid = _pid_or_404(pid)
    p = playlists.get(pid)
    if not p:
        abort(404)
    if any(importer.is_placeholder(t) for t in p["tracks"]) and importer.materialize(pid):
        p = playlists.get(pid) or p  # placeholders the player (or a manual fix) matched meanwhile
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
    importer.cancel(pid)
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


# ---------- playlist import: CSV / Google Takeout (production plan P2 -> IL 4.4) ----------

def _import_find(query):
    """The import pass's lookup: /api/match's, with every YouTube Music request throttled."""
    def search(text, limit):
        importer.throttle.wait()
        return ytmusic.search(text, limit)
    return _find_song(query, loose=True, search=search)


def _import_lookup(video_id):
    """Title / artists / length for a Takeout row that only has the video id."""
    importer.throttle.wait()
    found = ytmusic.song(video_id)
    return found if found and found.get("id") == video_id else None


def _import_resolve(pid, retry_missing=False):
    return importer.resolve(pid, _import_find, _import_lookup, home.rate_limited, home.retry_after,
                            retry_missing=retry_missing)


@app.post("/api/import")
def api_import():
    """A CSV as a multipart `file` (+ optional `name`, `resolve`), or JSON {text, filename?, name?, resolve?}
    (the CSV <= 5 MB either way; a JSON body may be up to importer.REQUEST_MAX for its escaping) -> 201 {playlist (summary), kind: csv|takeout, rows, skipped, to_match, status}. Rows that aren't
    YouTube ids become placeholders; `resolve` (default true) starts matching them in the background."""
    request.max_content_length = importer.REQUEST_MAX  # per request: Flask >= 3.1 (requirements.txt)
    too_big = jsonify({"error": "the file is larger than 5 MB"}), 413
    if (request.content_length or 0) > importer.REQUEST_MAX:
        return too_big
    try:
        if request.files.get("file"):
            f = request.files["file"]
            raw, filename = f.read(importer.MAX_BYTES + 1), f.filename or ""
            name, want = request.form.get("name"), request.form.get("resolve", "1") not in ("0", "false")
        else:
            body = request.get_json(silent=True)
            if not isinstance(body, dict) or not isinstance(body.get("text"), str):
                return jsonify({"error": "send a CSV file, or JSON {text}"}), 400
            raw, filename = body["text"], str(body.get("filename") or "")
            name, want = body.get("name"), body.get("resolve") is not False
            if len(raw.encode("utf-8", "replace")) > importer.MAX_BYTES:
                return too_big
    except RequestEntityTooLarge:
        return too_big
    if len(raw) > importer.MAX_BYTES:  # bytes of the file / UTF-8 bytes of the text: the same 5 MB either way
        return too_big
    try:
        parsed = importer.parse(raw, filename)
        p = importer.create(parsed, name if isinstance(name, str) else None)
    except ValueError as e:  # importer.ImportFailed, or playlists' limits
        return jsonify({"error": str(e)}), 400
    meta = p.get("import") or {}
    status = _import_resolve(p["id"]) if want and meta.get("total") and ytmusic.enabled() else importer.status(p["id"])
    return jsonify({"playlist": playlists.summary(p), "kind": parsed["kind"], "rows": meta.get("rows"),
                    "skipped": meta.get("skipped"), "to_match": meta.get("total"), "status": status}), 201


@app.post("/api/import/<pid>/resolve")
def api_import_resolve(pid):
    """Match the playlist's placeholders in the background ({retry_missing?: true} asks again for the ones nothing
    matched) -> 202 status; 502 when YouTube Music is off."""
    pid = _pid_or_404(pid)
    if playlists.get(pid) is None:
        abort(404)
    if not ytmusic.enabled():
        return _ytm_off()
    body = request.get_json(silent=True)
    out = _import_resolve(pid, retry_missing=isinstance(body, dict) and body.get("retry_missing") is True)
    if out is None:
        abort(404)
    return jsonify(out), 202


@app.get("/api/import/<pid>/status")
def api_import_status(pid):
    """{total, resolved, pending, missing: [{id, title, artist, album?}], running, rate_limited, retry_at, error,
    failed, tracks, started_at, finished_at}."""
    pid = _pid_or_404(pid)
    p = playlists.get(pid)
    if p is None:
        abort(404)
    if any(importer.is_placeholder(t) and not t.get("miss") for t in p["tracks"]):
        importer.materialize(pid)  # rows the player matched meanwhile count as resolved
    return jsonify(importer.status(pid))


# ---------- anonymous YouTube Music personalisation (production plan P1.2) ----------

def _personal_body():
    body = request.get_json(force=True, silent=True)  # sendBeacon may not send a JSON Content-Type
    if not isinstance(body, dict) or not isinstance(body.get("id"), str) or len(body["id"]) > 140:
        return None
    return body


def _personal_answer(queued, reason):
    return jsonify({"queued": queued, **({"reason": reason} if reason else {})}), 202 if queued else 200


@app.post("/api/personal/play")
def api_personal_play():
    """A track started playing. With personalisation on (and a YouTube id) the play is reported to YouTube Music
    in the background; otherwise nothing is sent. Answers at once: {queued, reason?: off|not_youtube|paused}."""
    body = _personal_body()
    if body is None:
        return jsonify({"error": "send {id}"}), 400
    return _personal_answer(*personal.play(body["id"]))


@app.post("/api/personal/progress")
def api_personal_progress():
    """Heartbeat while playing {id, t (s)}: a watchtime ping goes out at most every 10 s for the play /play
    started. {queued, reason?: off|not_youtube|paused|no_play|throttled|bad_time}."""
    body = _personal_body()
    if body is None:
        return jsonify({"error": "send {id, t}"}), 400
    return _personal_answer(*personal.progress(body["id"], body.get("t")))


@app.get("/api/personal/status")
def api_personal_status():
    return jsonify(personal.status())


# ---------- charts (YouTube Music) ----------

@app.get("/api/charts")
def api_charts():
    """YouTube Music charts for `country` (ZZ = global; default: the charts_country setting); see charts.py."""
    code = (request.args.get("country") or charts.country()).strip().upper()
    if not charts.valid(code):
        return jsonify({"error": "country must be a two-letter code (ZZ = global)"}), 400
    if not ytmusic.enabled():
        return _ytm_off()
    known = (charts.cached(charts.country()) or charts.cached("ZZ") or {}).get("countries")
    if known and code not in known:
        return jsonify({"error": f"no YouTube Music charts for {code}", "countries": known}), 400
    try:
        doc = charts.get(code)
    except Exception as e:
        return jsonify(errors.payload(e)), 502
    blocked, names = blocklist.matcher(), aliases.resolver()
    for s in doc["sections"]:
        if s["kind"] == "artists":
            s["items"] = [a for a in s["items"] if not blocked.artist(a["id"], a["name"])]
            for a in s["items"]:
                a["name"] = names.name(a["name"], a["id"])
        else:
            s["items"] = _mark_kept(blocked.tracks(s["items"]), names)
    first = next((s["items"][0]["id"] for s in doc["sections"] if s["kind"] != "artists" and s["items"]), None)
    if first:
        streamer.prefetch([first])
    return jsonify(doc)


# ---------- artist aliases ----------

@app.get("/api/aliases")
def api_aliases():
    return jsonify(aliases.entries())


@app.put("/api/aliases")
def api_alias_put():
    """{name: display name, id?: channel id (UC…), match?: the name sources give}: add or rename an alias."""
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": "send {name, id?, match?}"}), 400
    try:
        entry, created = aliases.put(body.get("name"), body.get("id"), body.get("match"))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"entry": entry, "created": created}), 201 if created else 200


@app.delete("/api/aliases")
def api_alias_delete():
    artist_id = request.args.get("id", "").strip()
    match = request.args.get("match", "").strip()[:aliases.NAME_MAX]
    if not artist_id and not match:
        return jsonify({"error": "id or match required"}), 400
    if not aliases.remove(artist_id or None, match or None):
        abort(404)
    return "", 204


# ---------- settings ----------

def _settings_body():
    return jsonify({"values": settings.public(), "schema": settings.schema()})


@app.get("/api/settings")
def api_settings():
    return _settings_body()


@app.patch("/api/settings")
def api_settings_patch():
    body = request.get_json(silent=True)
    try:
        settings.update(body)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    if "listenbrainz_token" in body:
        scrobble.token_changed()  # checks the new token with ListenBrainz (a few seconds at most)
    return _settings_body()


# ---------- scrobbling (ListenBrainz) ----------

@app.get("/api/scrobble/status")
def api_scrobble_status():
    return jsonify(scrobble.status())


@app.post("/api/scrobble/validate")
def api_scrobble_validate():
    return jsonify(scrobble.validate())


# ---------- update check, in-app update ----------

@app.get("/api/version")
def api_version():
    """This checkout and yt-dlp vs the newest on GitHub / PyPI (cached 6 h; ?refresh=1 asks again)."""
    return jsonify(updater.check(force=request.args.get("refresh") == "1"))


@app.post("/api/update")
def api_update():
    """{what: reef|ytdlp|both, confirm: true}: starts the update in a detached child and answers at once.
    Only from loopback / Tailscale, even when REEF_PASSWORD lets other networks in."""
    if not trusted(request.remote_addr):
        return jsonify({"error": "updates can only be started over Tailscale or on the phone itself"}), 403
    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        return jsonify({"error": "send a JSON object"}), 400
    try:
        return jsonify(updater.start(body.get("what"), body.get("confirm"))), 202
    except updater.UpdateError as e:
        return jsonify({"error": str(e)}), e.status


@app.get("/api/update/status")
def api_update_status():
    resp = jsonify(updater.status())
    resp.headers["Cache-Control"] = "no-store"
    return resp


# ---------- backup and restore ----------

@app.get("/api/backup")
def api_backup():
    try:
        name, raw = backup.download()
    except Exception as e:
        logger.warning(f"backup: download failed: {str(e)[:200]}")
        return jsonify({"error": f"couldn't build the backup: {str(e)[:200]}"}), 500
    return Response(raw, content_type="application/json; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"})


@app.get("/api/backup/status")
def api_backup_status():
    return jsonify(backup.status())


@app.post("/api/restore")
def api_restore():
    """A backup as a multipart `file` (+ `mode` field) or as the JSON body (?mode=); mode replace|merge."""
    request.max_content_length = backup.MAX_BYTES  # per request: Flask >= 3.1 (requirements.txt)
    if (request.content_length or 0) > backup.MAX_BYTES:
        return jsonify({"error": "the backup is larger than 20 MB"}), 413
    mode = request.args.get("mode") or "merge"
    try:
        if request.files.get("file"):
            mode = request.form.get("mode") or mode
            raw = request.files["file"].read(backup.MAX_BYTES + 1)
            if len(raw) > backup.MAX_BYTES:
                return jsonify({"error": "the backup is larger than 20 MB"}), 413
            doc = json.loads(raw)
        else:
            doc = request.get_json(force=True, silent=False)
    except RequestEntityTooLarge:
        return jsonify({"error": "the backup is larger than 20 MB"}), 413
    except (ValueError, UnicodeDecodeError, BadRequest):
        return jsonify({"error": "that file isn't JSON"}), 400
    try:
        return jsonify(backup.restore(doc, mode))
    except backup.BackupError as e:
        return jsonify({"error": str(e)}), 400


# ---------- source health ----------

@app.get("/api/health/sources")
def api_health_sources():
    resp = jsonify(source_health.sources_health(force=request.args.get("refresh") == "1"))
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.get("/api/library/recent")
def api_library_recent():
    try:
        return jsonify(navidrome.newest_albums())
    except Exception:
        return jsonify([])


ART_MIN, ART_MAX = 64, 1000
# Art up to this size is sent as bytes from Reef's own origin instead of a redirect, so the client can read its
# pixels from a canvas (the cover palette samples /api/art/<id>?size=64|96); a cross-origin redirect taints it.
ART_TINY = 128
ART_TINY_BYTES = 512 * 1024  # bigger upstream images aren't proxied (the redirect is kept)
ART_TINY_KEEP = 400  # tiny images kept in memory (a 64 px YT Music cover is ~2-4 KB, a mqdefault ~15 KB)
_tiny_art = OrderedDict()  # upstream url -> (bytes, mime)
_tiny_lock = threading.Lock()
_art_session = requests.Session()


def _art_size():
    return min(max(request.args.get("size", 300, type=int), ART_MIN), ART_MAX)


def _tiny_bytes(url):
    """(data, mime) of a small upstream image, from memory or fetched once; None when it can't be had."""
    with _tiny_lock:
        hit = _tiny_art.get(url)
        if hit is not None:
            _tiny_art.move_to_end(url)
            return hit
    try:
        with _art_session.get(url, timeout=(3, 6), stream=True) as r:
            mime = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if r.status_code != 200 or not mime.startswith("image/"):
                return None
            buf = bytearray()
            for chunk in r.iter_content(16 * 1024):
                buf += chunk
                if len(buf) > ART_TINY_BYTES:
                    return None
    except Exception as e:
        logger.info(f"art tiny fetch failed: {log.redact(str(e))[:160]}")
        return None
    hit = (bytes(buf), mime)
    with _tiny_lock:
        _tiny_art[url] = hit
        while len(_tiny_art) > ART_TINY_KEEP:
            _tiny_art.popitem(last=False)
    return hit


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
    if size <= ART_TINY:
        tiny = _tiny_bytes(found[1])
        if tiny:
            return Response(tiny[0], content_type=tiny[1], headers={"Cache-Control": ART_CACHE})
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


@app.get("/api/lyrics/search")
def api_lyrics_search():
    """LRCLIB's search for the "Wrong lyrics?" sheet. `q` is what the user typed; without it, the current track's
    title + artist hints. With hints, the results are ordered by how well they match the track (stable)."""
    args = request.args
    hint = {"title": _hint(args.get("title")), "artist": _hint(args.get("artist")),
            "duration": _seconds(args.get("duration"))}
    q = _hint(args.get("q")) or (matching.search_text(hint) if hint["title"] else "")
    if not q:
        return jsonify({"error": "Type something to search for"}), 400
    try:
        results = lyrics.search(q)
    except lyrics.Unavailable as e:
        return jsonify({"error": str(e)}), 502
    if hint["title"]:
        for r in results:
            r["score"] = matching.score(hint, {"title": r["track"], "artist": r["artist"], "duration": r["duration"]})[1]
        results.sort(key=lambda r: -r["score"])
    return jsonify({"q": q, "results": results})


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
    found = lyrics.pinned(track_id)  # the user's pick wins over everything, the library's tags too
    if not found and src.key == "nd":
        nd = navidrome.lyrics(native)  # what Navidrome reads from the file's tags (Reef embeds them on keep)
        if nd:
            found = {**nd, "provider": "navidrome", "offset_ms": lyrics.offset(track_id)}
    elif not found:
        entry = history.get(track_id)
        if entry:  # normally a cache hit: the keep looked lyrics up under this id
            title, artist = title or _hint(entry.get("title")), artist or _hint(entry.get("channel"))
            album, duration = album or _hint(entry.get("album")), duration or _seconds(entry.get("duration"))
        if src.key == "yt":
            # "Artist - Song (Official Video)" from channel "ArtistVEVO" -> ("Song", "Artist"), as a keep tags it.
            title, artist = tagging.guess(title, artist)
    # No title means nothing to search by; the chain would cache that as a miss for a day.
    if not found and title:
        found = lyrics.for_display(track_id, title, artist, album or None, duration)
    if not found:
        return jsonify({"error": "No lyrics found", "offset_ms": lyrics.offset(track_id)}), 404
    resp = jsonify(found)
    resp.headers["Cache-Control"] = "private, no-cache"  # the offset and a pin change it; the server cache is cheap
    return resp


@app.put("/api/lyrics/<track_id>/offset")
def api_lyrics_offset(track_id):
    """{ms}: this track's lyrics timing, -10000..10000 (+ = lyrics later). Stored with its lyrics cache entry."""
    t = _track(track_id)
    if not t:
        abort(404)
    body = request.get_json(silent=True)
    try:
        ms = lyrics.set_offset(t[1], body.get("ms") if isinstance(body, dict) else None)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"offset_ms": ms})


@app.put("/api/lyrics/<track_id>/pin")
def api_lyrics_pin(track_id):
    """{lrclib_id}: show (and embed on a later keep) this LRCLIB record for the track; automatic lookups never
    replace it. -> the lyrics, like GET."""
    t = _track(track_id)
    if not t:
        abort(404)
    body = request.get_json(silent=True)
    rid = body.get("lrclib_id") if isinstance(body, dict) else None
    if isinstance(rid, bool) or not isinstance(rid, int) or not 0 < rid < 2 ** 53:
        return jsonify({"error": "lrclib_id must be an LRCLIB record id"}), 400
    try:
        return jsonify(lyrics.pin(t[1], rid))
    except LookupError as e:
        return jsonify({"error": str(e)}), 404
    except lyrics.Unavailable as e:
        return jsonify({"error": str(e)}), 502


@app.delete("/api/lyrics/<track_id>/pin")
def api_lyrics_unpin(track_id):
    t = _track(track_id)
    if not t:
        abort(404)
    return ("", 204) if lyrics.unpin(t[1]) else (jsonify({"error": "Nothing pinned"}), 404)


@app.get("/api/tiles")
def api_tiles():
    return jsonify(tiles.load())


def _chores():
    """Every minute: the update check and the daily backup, each when it's due (they decide)."""
    time.sleep(60)  # let start-up (and the first requests) go first
    while True:
        for name, fn in (("update check", updater.tick), ("backup", backup.tick)):
            try:
                fn()
            except Exception as e:
                logger.warning(f"chores: {name} failed: {str(e)[:200]}")
        time.sleep(60)


def main():
    log.setup()
    downloader.start_worker()
    tiles.start_refresher()
    home.start_refresher()
    scrobble.start_worker()
    threading.Thread(target=_chores, daemon=True, name="reef-chores").start()
    server = "flask-dev" if config.DEV else "waitress"
    logger.info(f"reef start host={config.HOST} port={config.PORT} server={server} version={_version()} "
                f"open={int(config.OPEN)} password={int(bool(config.PASSWORD))} downloads={config.DOWNLOAD_DIR}")
    testaudio.announce()
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
