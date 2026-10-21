import base64
import os
import queue
import shutil
import subprocess
import threading
import time
import uuid

import yt_dlp
from mutagen import File as MutagenFile
from mutagen.flac import FLAC, Picture
from mutagen.id3 import APIC, ID3, TALB, TIT2, TPE1
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4, MP4Cover
from mutagen.ogg import OggFileType
from yt_dlp.postprocessor.metadataparser import MetadataParserPP
from yt_dlp.utils import sanitize_filename, sanitize_path

import history
import navidrome
import sources
import streamer
from config import AUDIO_FORMAT, DOWNLOAD_DIR, TMP_DIR

ACTIVE = ("queued", "downloading", "converting")

_jobs = {}
_queue = queue.Queue()
_lock = threading.Lock()


def enqueue(track_id, title="", channel=""):
    job = {
        "id": uuid.uuid4().hex[:8],
        "video_id": track_id,  # the full track id ("mc:..." for non-YouTube sources); name kept for the client
        "source": sources.parse(track_id)[0],
        "title": title,
        "channel": channel,
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


def _run_ytdlp(job):
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


# ---------- generic keep path (non-YouTube sources): fetch, ffmpeg, mutagen ----------

# AUDIO_FORMAT -> (file extension, ffmpeg codec args). Unknown formats let ffmpeg pick from the extension.
_FORMATS = {
    "opus": ("opus", ["-c:a", "libopus", "-b:a", "160k"]),
    "mp3": ("mp3", ["-c:a", "libmp3lame", "-q:a", "0"]),
    "m4a": ("m4a", ["-c:a", "aac", "-b:a", "256k"]),
    "aac": ("m4a", ["-c:a", "aac", "-b:a", "256k"]),
    "alac": ("m4a", ["-c:a", "alac"]),
    "flac": ("flac", ["-c:a", "flac"]),
    "vorbis": ("ogg", ["-c:a", "libvorbis", "-q:a", "6"]),
    "wav": ("wav", []),
}


def _filename(title):
    # Same rules as yt-dlp's `windowsfilenames`, so both keep paths name files alike.
    name = sanitize_path(sanitize_filename(title or "untitled"), force=True).strip() or "untitled"
    return name[:180]


def _fetch(job, entry, dest):
    r = streamer.session.get(entry["url"], headers=entry.get("headers") or {}, stream=True, timeout=(10, 60))
    try:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        with open(dest, "wb") as f:
            for chunk in r.iter_content(256 * 1024):
                f.write(chunk)
                done += len(chunk)
                if total:
                    job["percent"] = min(99, int(done * 100 / total))
    finally:
        r.close()


def _convert(src_path, src_ext, dest):
    ext, codec = _FORMATS.get(AUDIO_FORMAT, (AUDIO_FORMAT, []))
    if (src_ext or "").lower() == ext:
        codec = ["-c", "copy"]
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(src_path),
           "-map", "0:a:0", "-vn", *codec, str(dest)]
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError("ffmpeg: " + (p.stderr.strip().splitlines() or ["failed"])[-1][:200])


def _cover_bytes(src, native):
    try:
        found = src.art(native, 1000)
        if not found:
            return None, None
        if found[0] == "bytes":
            return found[1], found[2]
        r = streamer.session.get(found[1], timeout=15)
        r.raise_for_status()
        mime = r.headers.get("Content-Type", "image/jpeg").split(";")[0]
        return (r.content, mime) if mime.startswith("image/") else (None, None)
    except Exception:
        return None, None


def _tag(path, title, artist, album, cover, cover_mime, isrc=""):
    f = MutagenFile(path)
    if f is None:
        return
    if f.tags is not None:
        f.tags.clear()  # the source's own tags (compilation album artist, track numbers) would contradict ours
    if isinstance(f, MP3):
        if f.tags is None:
            f.add_tags()
        t = f.tags
        t.setall("TIT2", [TIT2(encoding=3, text=title)])
        if artist:
            t.setall("TPE1", [TPE1(encoding=3, text=artist)])
        if album:
            t.setall("TALB", [TALB(encoding=3, text=album)])
        if cover:
            t.setall("APIC", [APIC(encoding=3, mime=cover_mime, type=3, desc="Cover", data=cover)])
    elif isinstance(f, MP4):
        if f.tags is None:
            f.add_tags()
        f.tags["\xa9nam"] = [title]
        if artist:
            f.tags["\xa9ART"] = [artist]
        if album:
            f.tags["\xa9alb"] = [album]
        if cover:
            fmt = MP4Cover.FORMAT_PNG if cover_mime == "image/png" else MP4Cover.FORMAT_JPEG
            f.tags["covr"] = [MP4Cover(cover, imageformat=fmt)]
    elif isinstance(f, (FLAC, OggFileType)):
        if f.tags is None:
            f.add_tags()
        f["title"] = [title]
        if artist:
            f["artist"] = [artist]
        if album:
            f["album"] = [album]
        if isrc:
            f["isrc"] = [isrc]
        if cover:
            pic = Picture()
            pic.type, pic.mime, pic.desc, pic.data = 3, cover_mime, "Cover", cover
            if isinstance(f, FLAC):
                f.clear_pictures()
                f.add_picture(pic)
            else:
                f["metadata_block_picture"] = [base64.b64encode(pic.write()).decode("ascii")]
    else:
        return
    f.save()


def _run_generic(job):
    key, native = sources.parse(job["video_id"])
    src = sources.get(key)
    if src is None or not src.can_keep:
        raise RuntimeError(f"can't keep tracks from source {key!r}")
    info = src.info(native, {"title": job["title"], "artist": job.get("channel") or ""}) or {}
    title = info.get("title") or job["title"] or native
    artist = info.get("artist") or job.get("channel") or ""
    album = info.get("album") or ""
    job["title"] = title

    entry = streamer.resolve(job["video_id"])
    raw = TMP_DIR / f"{job['id']}.{entry.get('ext') or 'bin'}"
    ext = _FORMATS.get(AUDIO_FORMAT, (AUDIO_FORMAT,))[0]
    out = TMP_DIR / f"{job['id']}.out.{ext}"
    try:
        _fetch(job, entry, raw)
        job["percent"] = 100
        job["status"] = "converting"
        _convert(raw, entry.get("ext"), out)
        cover, cover_mime = _cover_bytes(src, native)
        _tag(out, title, artist, album, cover, cover_mime, info.get("isrc") or "")
        length = getattr(getattr(MutagenFile(out), "info", None), "length", None)
        dest = DOWNLOAD_DIR / f"{_filename(title)}.{ext}"
        part = dest.with_name(dest.name + ".part")
        shutil.move(str(out), str(part))  # may cross filesystems (tmp -> shared storage)
        os.replace(part, dest)
    finally:
        for p in (raw, out):
            try:
                p.unlink()
            except OSError:
                pass
    job["file"] = str(dest)
    history.add({
        "id": job["video_id"],
        "title": title,
        "channel": artist,
        "duration": info.get("duration") or (round(length) if length else None),
        "thumb": info.get("art") or "",
        "art": info.get("art") or "",
        "album": album,
        "file": job["file"],
        "source": key,
    })
    job["status"] = "done"

def _run(job):
    job["status"] = "downloading"
    try:
        DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
        TMP_DIR.mkdir(parents=True, exist_ok=True)
        if job["source"] == "yt":
            _run_ytdlp(job)
        else:
            _run_generic(job)
        job["rescanned"] = navidrome.rescan()
    except Exception as e:
        job["status"] = "error"
        job["error"] = str(e)[:300]
        return
    # The scan is async, so this usually misses; /api/art retries lazily.
    try:
        song = navidrome.find_song(job["title"], os.path.basename(job["file"] or ""))
        if song and song.get("coverArt"):
            history.update(job["video_id"], nd_cover=song["coverArt"], nd_id=song.get("id"))
    except Exception:
        pass


def _worker():
    while True:
        job_id = _queue.get()
        job = _jobs.get(job_id)
        if job:
            _run(job)
        _queue.task_done()


def start_worker():
    threading.Thread(target=_worker, daemon=True, name="reef-downloader").start()
