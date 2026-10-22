import hmac
import os
import re
import threading
import time

import yt_dlp
from flask import Flask, Response, abort, jsonify, redirect, render_template, request

import config
import downloader
import history
import navidrome
import sources
import streamer
import tiles

VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
TRACK_ID = re.compile(r"^(?:[a-z]{2}:)?[\w.-]{1,128}$")
COVER_ID = re.compile(r"^[\w.-]{1,128}$")
ART_CACHE = "public, max-age=86400"
ART_LOOKUP_BACKOFF = 600  # seconds before retrying a Navidrome lookup that found nothing

app = Flask(__name__)


def _track(track_id):
    """(source, canonical id, native id) for a valid track id of an enabled source, else None."""
    if not isinstance(track_id, str) or not TRACK_ID.match(track_id):
        return None
    key, native = sources.parse(track_id)
    src = sources.get(key)
    if src is None or (key == "yt" and not VIDEO_ID.match(native)):
        return None
    return src, sources.track_id(key, native), native


@app.before_request
def require_password():
    if not config.PASSWORD:
        return None
    auth = request.authorization
    if auth and hmac.compare_digest(auth.password or "", config.PASSWORD):
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
        "stream_timing": streamer.last_timing,
        "sources": list(sources.SOURCES),
    })


@app.get("/api/search")
def api_search():
    q = request.args.get("q", "").strip()
    src = sources.get(request.args.get("src") or sources.default_key())
    if src is None:
        return jsonify({"error": "unknown source"}), 400
    if not q:
        return jsonify([])
    try:
        results = src.search(q, config.SEARCH_LIMIT)
    except Exception as e:
        return jsonify({"error": str(e)[:300]}), 502
    streamer.prefetch([r["id"] for r in results[:3] if _track(r["id"])])
    kept = history.ids()
    for r in results:
        r.setdefault("source", src.key)
        r["kept"] = r.get("kept") or r["id"] in kept
    return jsonify(results)


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
    try:
        return streamer.proxy(t[1])
    except Exception as e:
        return jsonify({"error": str(e)[:300]}), 502


@app.post("/api/download")
def api_download():
    body = request.get_json(silent=True) or {}
    t = _track(str(body.get("id", "")))
    if not t:
        return jsonify({"error": "invalid track id"}), 400
    if not t[0].can_keep:
        return jsonify({"error": f"{t[0].name} tracks are already in your library"}), 400
    # `force` re-keeps a library track (e.g. to pick up better tags), but never runs alongside a keep
    # of the same track that's still going: both would write the same file.
    entry = history.get(t[1])
    if entry and not body.get("force") and entry.get("file") and os.path.exists(entry["file"]):
        return jsonify({"error": "Already in your library", "entry": entry}), 409
    job, created = downloader.enqueue_unique(t[1], str(body.get("title", ""))[:200], str(body.get("channel", ""))[:200])
    if not created:
        return jsonify({"error": "Already keeping", "job": job}), 409
    return jsonify(job), 202


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


@app.get("/api/tiles")
def api_tiles():
    return jsonify(tiles.load())


if __name__ == "__main__":
    downloader.start_worker()
    tiles.start_refresher()
    print(f"Reef listening on http://{config.HOST}:{config.PORT}  (downloads -> {config.DOWNLOAD_DIR})")
    app.run(host=config.HOST, port=config.PORT, threaded=True)
