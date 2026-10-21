import threading
import time

import requests
import yt_dlp
from flask import Response, request

# iOS Safari plays AAC/m4a natively; webm/opus support is spotty, so prefer m4a.
_OPTS = {
    "quiet": True,
    "no_warnings": True,
    "noplaylist": True,
    "format": "bestaudio[ext=m4a]/bestaudio/best",
}
_MIME = {"m4a": "audio/mp4", "webm": "audio/webm", "opus": "audio/ogg", "mp3": "audio/mpeg"}
_TTL = 3600
_CHUNK = 64 * 1024

_cache = {}
_lock = threading.Lock()


def _resolve(video_id):
    with _lock:
        hit = _cache.get(video_id)
        if hit and hit["expires"] > time.time():
            return hit
    with yt_dlp.YoutubeDL(_OPTS) as ydl:
        info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
    entry = {
        "url": info["url"],
        "headers": dict(info.get("http_headers") or {}),
        "mime": _MIME.get(info.get("ext"), "application/octet-stream"),
        "expires": time.time() + _TTL,
    }
    with _lock:
        _cache[video_id] = entry
    return entry


def _open(video_id):
    entry = _resolve(video_id)
    headers = dict(entry["headers"])
    if request.headers.get("Range"):
        headers["Range"] = request.headers["Range"]
    return entry, requests.get(entry["url"], headers=headers, stream=True, timeout=(10, 60))


def proxy(video_id):
    entry, upstream = _open(video_id)
    if upstream.status_code >= 400:
        # Resolved URL expired or was bound to a different client; refresh once.
        upstream.close()
        with _lock:
            _cache.pop(video_id, None)
        entry, upstream = _open(video_id)
        if upstream.status_code >= 400:
            upstream.close()
            return Response("upstream error", status=502)

    out_headers = {
        "Content-Type": entry["mime"],
        "Accept-Ranges": "bytes",
        "Cache-Control": "no-store",
    }
    for name in ("Content-Length", "Content-Range"):
        if name in upstream.headers:
            out_headers[name] = upstream.headers[name]

    def generate():
        try:
            for chunk in upstream.iter_content(_CHUNK):
                if chunk:
                    yield chunk
        finally:
            upstream.close()

    return Response(generate(), status=upstream.status_code, headers=out_headers, direct_passthrough=True)
