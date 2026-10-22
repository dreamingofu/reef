import base64
import os
import queue
import re
import shutil
import subprocess
import threading
import time
import uuid

import requests
import yt_dlp
from mutagen import File as MutagenFile
from mutagen.flac import FLAC, Picture
from mutagen.id3 import APIC, ID3, TALB, TIT2, TPE1
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4, MP4Cover
from mutagen.ogg import OggFileType
from yt_dlp.postprocessor.common import PostProcessor
from yt_dlp.postprocessor.ffmpeg import FFmpegPostProcessor
from yt_dlp.postprocessor.metadataparser import MetadataParserPP
from yt_dlp.postprocessor.sponsorblock import SponsorBlockPP
from yt_dlp.utils import sanitize_filename, sanitize_path

import config
import history
import navidrome
import sources
import streamer
from config import AUDIO_FORMAT, DOWNLOAD_DIR, TMP_DIR

ACTIVE = ("queued", "downloading", "converting")

_jobs = {}
_queue = queue.Queue()
_lock = threading.Lock()


def _new_job(track_id, title, channel):
    return {
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


def enqueue(track_id, title="", channel=""):
    job = _new_job(track_id, title, channel)
    with _lock:
        _jobs[job["id"]] = job
    _queue.put(job["id"])
    return job


def enqueue_unique(track_id, title="", channel=""):
    """(job, created): the track's keep that is still running, else a new queued one. Check and insert
    happen under one lock, so two quick taps can't start two keeps writing the same file."""
    with _lock:
        job = next((j for j in _jobs.values() if j["video_id"] == track_id and j["status"] in ACTIVE), None)
        if job:
            return job, False
        job = _new_job(track_id, title, channel)
        _jobs[job["id"]] = job
    _queue.put(job["id"])
    return job, True


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


# ---------- music tags: what Navidrome shows, without touching the filename ----------

# A trailing (...) / [...] group made only of these words is video noise, not part of the song title.
_NOISE_WORDS = {"official", "oficial", "music", "video", "audio", "lyric", "lyrics", "visualizer", "visualiser",
                "hd", "hq", "4k", "mv"}
_TRAILING_GROUP = re.compile(r"\s*(\(([^()]*)\)|\[([^\[\]]*)\])\s*$")
_SEP = re.compile(r"\s+[-–]\s+")


def _is_noise(group):
    words = re.findall(r"[\w']+", group.lower())
    if "4k" in words:  # "4K Remaster" is about the video; a bare "(Remastered 2009)" is about the recording
        words = [w for w in words if w not in ("remaster", "remastered")]
    return bool(words) and all(w in _NOISE_WORDS for w in words)


def _strip_noise(title):
    while True:
        m = _TRAILING_GROUP.search(title)
        if not m or not _is_noise(m.group(2) if m.group(2) is not None else m.group(3)) or not title[:m.start()].strip():
            return title.strip()
        title = title[:m.start()]


def _clean_artist(name):
    name = re.sub(r"\s+-\s+Topic$", "", str(name or "").strip(), flags=re.I)
    return re.sub(r"\s*VEVO$", "", name).strip()


def _names(value):
    return [n for n in (_clean_artist(v) for v in (value if isinstance(value, list) else [value])) if n]


def _year(info):
    for v in (info.get("release_year"), (info.get("release_date") or "")[:4], (info.get("upload_date") or "")[:4]):
        if v and re.fullmatch(r"\d{4}", str(v)):
            return str(v)
    return None


def music_tags(info):
    """`meta_*` overrides for FFmpegMetadataPP (it prefers them over the plain fields). Never touches
    info["title"], which names the file."""
    artists = _names(info.get("artists")) or _names(info.get("artist")) or _names(info.get("creators")) \
        or _names(info.get("creator"))
    known = bool(artists)
    title = str(info.get("track") or info.get("title") or "").strip()
    if not info.get("track"):
        parts = _SEP.split(title, maxsplit=1)
        if len(parts) == 2 and known:
            # "Rick Astley - Never Gonna..." when yt-dlp already knows the artist is Rick Astley.
            left, first = parts[0].lower(), artists[0].lower()
            if left == ", ".join(artists).lower() or re.match(re.escape(first) + r"(\W|$)", left):
                title = parts[1]
        elif not known and len(_SEP.findall(title)) == 1:
            artists, title = [parts[0].strip()], parts[1]
    if not artists:
        artists = _names(info.get("uploader")) or _names(info.get("channel"))
    tags = {"meta_title": _strip_noise(title) or title}
    if artists:
        tags["meta_artist"] = ", ".join(artists)
        tags["meta_album_artist"] = (_names(info.get("album_artists")) or _names(info.get("album_artist"))
                                     or artists)[0]
    if info.get("album"):
        tags["meta_album"] = str(info["album"])
    if info.get("track_number"):
        tags["meta_track"] = str(info["track_number"])
    tags["meta_date"] = _year(info)
    return {k: v for k, v in tags.items() if v}


class _MusicTagsPP(PostProcessor):
    def run(self, info):
        info.update(music_tags(info))
        return [], info


class _SquareCoverPP(FFmpegPostProcessor):
    """Center-crop the downloaded thumbnails to squares, as music players expect. Runs whatever format
    YouTube served; a failed crop keeps the original cover rather than failing the keep."""

    def run(self, info):
        for t in info.get("thumbnails") or []:
            path = t.get("filepath")
            if not path or not os.path.exists(path):
                continue
            root, ext = os.path.splitext(path)
            tmp = f"{root}.square{ext}"
            try:
                self.real_run_ffmpeg([(path, ["-f", "image2", "-pattern_type", "none"])],
                                     [(tmp, ["-vf", "crop='min(iw,ih)':'min(iw,ih)'", "-q:v", "2", "-update", "1"])])
                os.replace(tmp, path)
            except Exception as e:
                self.report_warning(f"Couldn't crop the cover square, keeping it as is: {e}")
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
        return [], info


class _SafeSponsorBlockPP(SponsorBlockPP):
    """SponsorBlock is a nice-to-have: if its API is down, keep the track uncut instead of failing."""

    def run(self, info):
        try:
            return super().run(info)
        except Exception as e:
            self.report_warning(f"SponsorBlock unavailable, keeping the track uncut: {e}")
            return [], info


SPONSOR_CATEGORIES = ["music_offtopic", "sponsor", "intro", "outro"]
# REEF_YDL_EXTRA can tune yt-dlp but not rewire the keep pipeline or where files land.
_PROTECTED = ("postprocessors", "progress_hooks", "paths", "outtmpl")


def _options(job):
    def progress(d):
        if d["status"] == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            if total:
                job["percent"] = int(d.get("downloaded_bytes", 0) * 100 / total)
        elif d["status"] == "finished":
            job["percent"] = 100
            job["status"] = "converting"

    # Same order as yt-dlp's own get_postprocessors(): convert thumbnails, extract audio, cut, tag, embed.
    pps = [
        # Blank the description so the whole YouTube blurb doesn't end up in the comment tag.
        {"key": "MetadataParser", "when": "pre_process",
         "actions": [(MetadataParserPP.Actions.INTERPRET, "", "(?P<description>)")]},
        # Covers as JPEG; _SquareCoverPP (added in _run_ytdlp, after this) then crops them square.
        {"key": "FFmpegThumbnailsConvertor", "format": "jpg", "when": "before_dl"},
        {"key": "FFmpegExtractAudio", "preferredcodec": AUDIO_FORMAT, "preferredquality": "0"},
    ]
    if config.SPONSORBLOCK:
        # The segments themselves are fetched by _SafeSponsorBlockPP (after_filter), added in _run_ytdlp.
        pps.append({"key": "ModifyChapters", "remove_sponsor_segments": SPONSOR_CATEGORIES})
    pps += [
        {"key": "FFmpegMetadata", "add_metadata": True},
        {"key": "EmbedThumbnail"},
    ]
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "noplaylist": True,
        "format": "bestaudio/best",
        "outtmpl": {"default": "%(title)s.%(ext)s"},
        "paths": {"home": str(DOWNLOAD_DIR), "temp": str(TMP_DIR)},
        "windowsfilenames": True,
        "writethumbnail": True,
        # Flaky cellular / Tailscale links: retry with backoff instead of failing the keep.
        "retries": 10,
        "fragment_retries": 10,
        "extractor_retries": 3,
        "file_access_retries": 3,
        "retry_sleep_functions": {"http": lambda n: min(120, 2 ** n), "fragment": lambda n: min(60, 2 ** n)},
        "socket_timeout": 15,
        "continuedl": True,
        "concurrent_fragment_downloads": 4,
        "postprocessors": pps,
        "progress_hooks": [progress],
    }
    opts.update({k: v for k, v in config.YDL_EXTRA.items() if k not in _PROTECTED})
    return opts


def _custom_pps(ydl):
    """(postprocessor, when) pairs that need our own classes, so they can't go in the options dict."""
    # add_post_processor appends, so the crop lands after the options' FFmpegThumbnailsConvertor (before_dl).
    pps = [(_MusicTagsPP(ydl), "pre_process"), (_SquareCoverPP(ydl), "before_dl")]
    if config.SPONSORBLOCK:
        pps.append((_SafeSponsorBlockPP(ydl, categories=SPONSOR_CATEGORIES), "after_filter"))
    return pps


def _run_ytdlp(job):
    with yt_dlp.YoutubeDL(_options(job)) as ydl:
        for pp, when in _custom_pps(ydl):
            ydl.add_post_processor(pp, when=when)
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


_RETRY_SLEEPS = (2, 4, 8)
_sleep = time.sleep  # tests patch this


def _fetch(job, entry, dest):
    """Download to `dest`, retrying dropped connections, timeouts and 5xx. Each attempt starts the file over."""
    for delay in (*_RETRY_SLEEPS, None):
        try:
            return _fetch_once(job, entry, dest)
        except (requests.ConnectionError, requests.Timeout, requests.exceptions.ChunkedEncodingError):
            if delay is None:
                raise
        except requests.HTTPError as e:
            if delay is None or e.response is None or e.response.status_code < 500:
                raise
        job["percent"] = 0
        _sleep(delay)


def _fetch_once(job, entry, dest):
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
