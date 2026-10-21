import hmac
import re

import yt_dlp
from flask import Flask, Response, abort, jsonify, render_template, request

import config
import downloader
import history
import navidrome
import search
import streamer

VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
COVER_ID = re.compile(r"^[\w.-]{1,128}$")

app = Flask(__name__)


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
    return render_template("index.html")


@app.get("/api/health")
def health():
    return jsonify({
        "reef": "ok",
        "navidrome": navidrome.ping(),
        "download_dir": str(config.DOWNLOAD_DIR),
        "ytdlp": yt_dlp.version.__version__,
    })


@app.get("/api/search")
def api_search():
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify([])
    try:
        results = search.search(q)
    except Exception as e:
        return jsonify({"error": str(e)[:300]}), 502
    kept = history.ids()
    for r in results:
        r["kept"] = r["id"] in kept
    return jsonify(results)


@app.get("/api/stream/<video_id>")
def api_stream(video_id):
    if not VIDEO_ID.match(video_id):
        abort(404)
    try:
        return streamer.proxy(video_id)
    except Exception as e:
        return jsonify({"error": str(e)[:300]}), 502


@app.post("/api/download")
def api_download():
    body = request.get_json(silent=True) or {}
    video_id = str(body.get("id", ""))
    if not VIDEO_ID.match(video_id):
        return jsonify({"error": "invalid video id"}), 400
    return jsonify(downloader.enqueue(video_id, str(body.get("title", ""))[:200])), 202


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


@app.get("/api/cover/<cover_id>")
def api_cover(cover_id):
    if not COVER_ID.match(cover_id) or not navidrome.configured():
        abort(404)
    size = min(max(request.args.get("size", 300, type=int), 64), 1000)
    try:
        data, mime = navidrome.cover_art(cover_id, size)
    except Exception:
        abort(404)
    if not mime.startswith("image/"):
        abort(404)
    return Response(data, content_type=mime, headers={"Cache-Control": "public, max-age=86400"})


if __name__ == "__main__":
    downloader.start_worker()
    print(f"Reef listening on http://{config.HOST}:{config.PORT}  (downloads -> {config.DOWNLOAD_DIR})")
    app.run(host=config.HOST, port=config.PORT, threaded=True)
