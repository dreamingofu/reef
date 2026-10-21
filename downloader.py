import queue
import threading
import time
import uuid

import yt_dlp
from yt_dlp.postprocessor.metadataparser import MetadataParserPP

import history
import navidrome
from config import AUDIO_FORMAT, DOWNLOAD_DIR, TMP_DIR

ACTIVE = ("queued", "downloading", "converting")

_jobs = {}
_queue = queue.Queue()
_lock = threading.Lock()


def enqueue(video_id, title=""):
    job = {
        "id": uuid.uuid4().hex[:8],
        "video_id": video_id,
        "title": title,
        "status": "queued",
        "percent": 0,
        "file": None,
        "error": None,
        "rescanned": False,
        "created": time.time(),
    }
    with _lock:
        _jobs[job["id"]] = job
    _queue.put(job["id"])
    return job


def list_jobs():
    with _lock:
        return sorted(_jobs.values(), key=lambda j: j["created"], reverse=True)


def remove(job_id):
    with _lock:
        job = _jobs.get(job_id)
        if job and job["status"] not in ACTIVE:
            del _jobs[job_id]
            return True
    return False


def _options(job):
    def progress(d):
        if d["status"] == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            if total:
                job["percent"] = int(d.get("downloaded_bytes", 0) * 100 / total)
        elif d["status"] == "finished":
            job["percent"] = 100
            job["status"] = "converting"

    return {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "format": "bestaudio/best",
        "outtmpl": {"default": "%(title)s.%(ext)s"},
        "paths": {"home": str(DOWNLOAD_DIR), "temp": str(TMP_DIR)},
        "windowsfilenames": True,
        "writethumbnail": True,
        "postprocessors": [
            # Blank the description so the whole YouTube blurb doesn't end up in the comment tag.
            {"key": "MetadataParser", "when": "pre_process",
             "actions": [(MetadataParserPP.Actions.INTERPRET, "", "(?P<description>)")]},
            {"key": "FFmpegExtractAudio", "preferredcodec": AUDIO_FORMAT, "preferredquality": "0"},
            {"key": "FFmpegMetadata", "add_metadata": True},
            {"key": "EmbedThumbnail"},
        ],
        "progress_hooks": [progress],
    }


def _run(job):
    job["status"] = "downloading"
    try:
        DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
        TMP_DIR.mkdir(parents=True, exist_ok=True)
        with yt_dlp.YoutubeDL(_options(job)) as ydl:
            info = ydl.extract_info(f"https://www.youtube.com/watch?v={job['video_id']}", download=True)
        requested = (info.get("requested_downloads") or [{}])[0]
        job["file"] = requested.get("filepath")
        job["title"] = info.get("title") or job["title"]
        job["percent"] = 100
        job["status"] = "done"
        history.add({
            "id": job["video_id"],
            "title": job["title"],
            "channel": info.get("channel") or info.get("uploader") or "",
            "duration": info.get("duration"),
            "thumb": f"https://i.ytimg.com/vi/{job['video_id']}/mqdefault.jpg",
            "file": job["file"],
        })
        job["rescanned"] = navidrome.rescan()
    except Exception as e:
        job["status"] = "error"
        job["error"] = str(e)[:300]


def _worker():
    while True:
        job_id = _queue.get()
        job = _jobs.get(job_id)
        if job:
            _run(job)
        _queue.task_done()


def start_worker():
    threading.Thread(target=_worker, daemon=True, name="reef-downloader").start()
