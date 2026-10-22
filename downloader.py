import base64
import logging
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
from mutagen.id3 import APIC, TALB, TDRC, TIT2, TPE1, TPE2, TRCK
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4, MP4Cover
from mutagen.ogg import OggFileType
from yt_dlp.postprocessor.common import PostProcessor
from yt_dlp.postprocessor.ffmpeg import FFmpegPostProcessor
from yt_dlp.postprocessor.metadataparser import MetadataParserPP
from yt_dlp.postprocessor.sponsorblock import SponsorBlockPP
from yt_dlp.utils import sanitize_filename, sanitize_path

import aliases
import config
import errors
import history
import loudness
import lyrics
import matching
import navidrome
import settings
import sources
import streamer
import ytmusic
from config import AUDIO_FORMAT, DOWNLOAD_DIR, TMP_DIR
from tagging import music_tags, with_hints

logger = logging.getLogger(__name__)

ACTIVE = ("queued", "downloading", "converting")

_jobs = {}
_queue = queue.Queue()
_lock = threading.Lock()


def _new_job(track_id, title, channel, hints=None, duration=None):
    return {
        "id": uuid.uuid4().hex[:8],
        "video_id": track_id,  # the full track id ("mc:..." for non-YouTube sources); name kept for the client
        "source": sources.parse(track_id)[0],
        "title": title,
        "channel": channel,
        # Album-page tags ({album, album_artist, track_number, year}, all optional) that override yt-dlp's.
        "hints": dict(hints or {}),
        "duration": duration,  # what the client showed (seconds), for the lossless match; None = unknown
        "lossless": False,  # kept from Monochrome as FLAC instead (keep_lossless); the Downloads tab badges it
        "status": "queued",
        "percent": 0,
        "file": None,
        "error": None,
        "block": None,  # errors.py block + hint for a failed keep, shown in the Downloads tab
        "hint": None,
        "rescanned": False,
        "warning": None,  # the file was saved, but something after that failed (e.g. the history write)
        "created": time.time(),
    }


def enqueue(track_id, title="", channel="", hints=None, duration=None):
    job = _new_job(track_id, title, channel, hints, duration)
    with _lock:
        _jobs[job["id"]] = job
    _queue.put(job["id"])
    return job


def enqueue_unique(track_id, title="", channel="", hints=None, duration=None):
    """(job, created): the track's keep that is still running, else a new queued one. Check and insert
    happen under one lock, so two quick taps can't start two keeps writing the same file."""
    with _lock:
        job = next((j for j in _jobs.values() if j["video_id"] == track_id and j["status"] in ACTIVE), None)
        if job:
            return job, False
        job = _new_job(track_id, title, channel, hints, duration)
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


# ---------- yt-dlp keep path ----------

def _tags(info, hints, alias=True):
    """The tags a keep writes: yt-dlp's metadata cleaned up, album-page hints over it, artist aliases applied
    (aliases.py). `alias=False` gives the source's own names (lyrics are looked up by those)."""
    if not alias:
        return with_hints(music_tags(info), hints)
    names = aliases.resolver()
    tags = with_hints(music_tags(info, names.name if names else None), hints)
    if names and tags.get("meta_album_artist"):  # an album-page hint names the artist as the source does
        tags["meta_album_artist"] = names.name(tags["meta_album_artist"])
    return tags


class _MusicTagsPP(PostProcessor):
    def __init__(self, downloader=None, hints=None):
        super().__init__(downloader)
        self.hints = hints or {}

    def run(self, info):
        info.update(_tags(info, self.hints))
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
    if settings.get("sponsorblock_keep"):
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


def _custom_pps(ydl, hints=None):
    """(postprocessor, when) pairs that need our own classes, so they can't go in the options dict."""
    # add_post_processor appends, so the crop lands after the options' FFmpegThumbnailsConvertor (before_dl).
    pps = [(_MusicTagsPP(ydl, hints), "pre_process"), (_SquareCoverPP(ydl), "before_dl")]
    if settings.get("sponsorblock_keep"):
        pps.append((_SafeSponsorBlockPP(ydl, categories=SPONSOR_CATEGORIES), "after_filter"))
    return pps


def _run_ytdlp(job):
    with yt_dlp.YoutubeDL(_options(job)) as ydl:
        for pp, when in _custom_pps(ydl, job.get("hints")):
            ydl.add_post_processor(pp, when=when)
        info = ydl.extract_info(f"https://www.youtube.com/watch?v={job['video_id']}", download=True)
    requested = (info.get("requested_downloads") or [{}])[0]
    job["file"] = requested.get("filepath")
    job["title"] = info.get("title") or job["title"]
    job["percent"] = 100
    # What the file was tagged with (_MusicTagsPP is deterministic), but with the source's artist names (not an
    # alias): the post steps look lyrics up by them.
    tags = _tags(info, job.get("hints"), alias=False)
    history.add({
        "id": job["video_id"],
        "title": job["title"],
        "channel": info.get("channel") or info.get("uploader") or "",
        "duration": info.get("duration"),
        "thumb": f"https://i.ytimg.com/vi/{job['video_id']}/mqdefault.jpg",
        "album": tags.get("meta_album") or "",
        "file": job["file"],
    })
    return {"title": tags.get("meta_title") or job["title"], "artist": tags.get("meta_artist") or "",
            "album": tags.get("meta_album") or "", "duration": _length(job["file"]) or info.get("duration")}


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
EXPIRY_MIN_S = 60     # a stream URL expiring sooner than this is re-resolved before an attempt starts
PROGRESS_BYTES = 256 * 1024  # an attempt that got this much is progress: it costs no retry and resets the backoff
FETCH_ATTEMPTS = 50          # attempts of one fetch in all, progress or not
_CONTENT_RANGE = re.compile(r"^bytes\s+(\d+)-(\d+)/(\d+|\*)$")


class _Restart(Exception):
    """The server answered a resume with other bytes than asked for: start the file over."""


def _fake_drop():
    """REEF_FAKE_DROP=<bytes>: the first attempt of every generic fetch drops the connection after that many bytes
    (live check of the resume path, like REEF_FAKE_WALL for playback)."""
    raw = os.environ.get("REEF_FAKE_DROP", "").strip()
    return int(raw) if raw.isdigit() and int(raw) > 0 else None


def _fresh_entry(entry, track_id):
    """`entry`, or a re-resolved one when its URL expires within EXPIRY_MIN_S (streamer's entry expiry: the URL's
    own `expire`, else the cache TTL). streamer.resolve hands out a new URL by itself then: an entry that close
    to its `expire` is past its `expires`."""
    if not track_id:
        return entry
    now = streamer._now()
    expire, expires = entry.get("expire"), entry.get("expires")
    if (expire and expire - now < EXPIRY_MIN_S) or (not expire and expires and expires <= now):
        logger.info(f"keep id={track_id} stream URL expires in {int((expire or expires) - now)} s: re-resolving first")
        return streamer.resolve(track_id)
    return entry


def _fetch(job, entry, dest, track_id=None):
    """Download to `dest` through `<dest>.part`, retrying dropped connections, timeouts and 5xx with backoff
    (_RETRY_SLEEPS: three retries). A retry asks for the rest (`Range: bytes=<size>-`) and starts the file over when
    the server answers 200 instead of 206 (or a 206 for other bytes / another file size). An attempt that got at
    least PROGRESS_BYTES doesn't count against the retries and resets the backoff (a flaky server that cuts a big
    FLAC every few MB still finishes); FETCH_ATTEMPTS caps the attempts in all (a server that drips bytes). With
    `track_id`, a URL about to expire is re-resolved before each attempt."""
    part = dest.with_name(dest.name + ".part")
    _unlink(part)
    state = {"total": None, "drop": _fake_drop()}
    steps = list(_RETRY_SLEEPS)
    try:
        for attempt in range(1, FETCH_ATTEMPTS + 1):
            entry = _fresh_entry(entry, track_id)
            before = part.stat().st_size if part.exists() else 0
            try:
                _fetch_once(job, entry, part, state)
                os.replace(part, dest)
                return
            except (requests.ConnectionError, requests.Timeout, requests.exceptions.ChunkedEncodingError) as e:
                err = e
            except requests.HTTPError as e:
                if e.response is None or e.response.status_code < 500:
                    raise
                err = e
            have = part.stat().st_size if part.exists() else 0
            progressed = have - before >= PROGRESS_BYTES
            if progressed:
                steps = list(_RETRY_SLEEPS)
            if not steps or attempt == FETCH_ATTEMPTS:
                raise err
            delay = steps[0] if progressed else steps.pop(0)
            logger.info(f"keep fetch attempt={attempt} have={have} of {state['total']} progressed={int(progressed)} "
                        f"error={type(err).__name__}: retrying in {delay} s")
            _sleep(delay)
    except BaseException:
        _unlink(part)
        raise


def _content_range(value):
    """(start, total or None) of a 206's Content-Range, None when it can't be read."""
    m = _CONTENT_RANGE.match(str(value or "").strip())
    return (int(m.group(1)), None if m.group(3) == "*" else int(m.group(3))) if m else None


def _fetch_once(job, entry, part, state):
    have = part.stat().st_size if part.exists() else 0
    headers = dict(entry.get("headers") or {})
    if have:
        headers["Range"] = f"bytes={have}-"
    r = streamer.session.get(entry["url"], headers=headers, stream=True, timeout=(10, 60))
    try:
        if have and r.status_code == 416:
            if state["total"] == have:
                return  # everything was there already (the drop came after the last byte)
            r.close()  # the server can't serve the rest of what we have: start over
            _unlink(part)
            return _fetch_once(job, entry, part, state)
        r.raise_for_status()
        mode = "wb"
        if have and r.status_code == 206:
            got = _content_range(r.headers.get("Content-Range"))
            if got and got[0] == have and (not state["total"] or got[1] in (None, state["total"])):
                mode = "ab"
                state["total"] = got[1] or state["total"]
            else:  # other bytes, or another file behind a re-resolved URL: start over
                logger.info(f"keep fetch resume refused (Content-Range {r.headers.get('Content-Range')!r}): restarting")
                r.close()
                _unlink(part)
                return _fetch_once(job, entry, part, state)
        else:
            if have:
                logger.info(f"keep fetch resume answered {r.status_code}, not 206: restarting from 0")
            have = 0
            length = int(r.headers.get("Content-Length") or 0)
            state["total"] = length or None
        total, done = state["total"], have
        if mode == "wb":
            job["percent"] = 0
        drop, state["drop"] = state["drop"], None
        with open(part, mode) as f:
            for chunk in r.iter_content(256 * 1024):
                if drop is not None and done + len(chunk) > drop:
                    f.write(chunk[:max(0, drop - done)])
                    raise requests.exceptions.ChunkedEncodingError("REEF_FAKE_DROP: connection dropped")
                f.write(chunk)
                done += len(chunk)
                if total:
                    job["percent"] = min(99, int(done * 100 / total))
        if total and done < total and not r.headers.get("Content-Encoding"):  # closed early without an error
            raise requests.exceptions.ChunkedEncodingError(f"connection closed at {done} of {total} bytes")
    finally:
        r.close()


def _convert(src_path, src_ext, dest, fmt=None):
    fmt = fmt or AUDIO_FORMAT
    ext, codec = _FORMATS.get(fmt, (fmt, []))
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


def _tag(path, title, artist, album, cover, cover_mime, isrc="", album_artist="", track=None, year=None):
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
        if album_artist:
            t.setall("TPE2", [TPE2(encoding=3, text=album_artist)])
        if track:
            t.setall("TRCK", [TRCK(encoding=3, text=str(track))])
        if year:
            t.setall("TDRC", [TDRC(encoding=3, text=str(year))])
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
        if album_artist:
            f.tags["aART"] = [album_artist]
        if track:
            f.tags["trkn"] = [(int(track), 0)]
        if year:
            f.tags["\xa9day"] = [str(year)]
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
        if album_artist:
            f["albumartist"] = [album_artist]
        if track:
            f["tracknumber"] = [str(track)]
        if year:
            f["date"] = [str(year)]
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


class NotLossless(Exception):
    """The lossless candidate turned out unusable (a preview clip, no length): keep from YouTube instead."""


def _run_generic(job, track_id=None, fmt=None, expect=None, extra=None):
    """Keep `track_id` (default: the job's own track). `fmt` overrides AUDIO_FORMAT (the lossless upgrade keeps
    FLAC as is); `expect` (seconds) rejects a download shorter than half of it, the 30-second preview a proxy
    serves for a track it can't stream in full; `extra` goes into the history entry."""
    tid = track_id or job["video_id"]
    key, native = sources.parse(tid)
    src = sources.get(key)
    if src is None or not src.can_keep:
        raise RuntimeError(f"can't keep tracks from source {key!r}")
    info = src.info(native, {"title": job["title"], "artist": job.get("channel") or ""}) or {}
    title = info.get("title") or job["title"] or native
    artist = info.get("artist") or job.get("channel") or ""
    hints = job.get("hints") or {}
    album = hints.get("album") or info.get("album") or ""
    job["title"] = title

    entry = streamer.resolve(tid)
    raw = TMP_DIR / f"{job['id']}.{entry.get('ext') or 'bin'}"
    ext = _FORMATS.get(fmt or AUDIO_FORMAT, (fmt or AUDIO_FORMAT,))[0]
    out = TMP_DIR / f"{job['id']}.out.{ext}"
    try:
        _fetch(job, entry, raw, tid)
        if expect:
            got = _length(raw)
            if not got or got < expect * PREVIEW_SHARE:
                raise NotLossless(f"the file is {got and round(got)} s of {round(expect)} s (a preview?)")
        job["percent"] = 100
        job["status"] = "converting"
        _convert(raw, entry.get("ext"), out, fmt)
        cover, cover_mime = _cover_bytes(src, native)
        names = aliases.resolver()
        _tag(out, title, names.name(artist), album, cover, cover_mime, info.get("isrc") or "",
             names.name(hints.get("album_artist") or ""), hints.get("track_number"), hints.get("year"))
        length = _length(out)
        dest = DOWNLOAD_DIR / f"{_filename(title)}.{ext}"
        part = dest.with_name(dest.name + ".part")
        try:
            shutil.move(str(out), str(part))  # may cross filesystems (tmp -> shared storage)
            os.replace(part, dest)
        except BaseException:
            _unlink(part)  # half a copy on shared storage must never reach Navidrome (nor stay next to a fallback)
            raise
    finally:
        _unlink(raw, out)
    # From here the file is in the library: the keep happened. What's left is bookkeeping, and a failure there is
    # reported on the job (`warning`) instead of failing it; a lossless keep must not fall back to YouTube now, or
    # Navidrome would list the song twice (.flac and .opus).
    job["file"] = str(dest)
    job["kept_id"] = tid  # the history entry's id (a lossless upgrade is kept under its Monochrome id)
    try:
        history.add({
            "id": tid,
            "title": title,
            "channel": artist,
            "duration": info.get("duration") or (round(length) if length else None),
            "thumb": info.get("art") or "",
            "art": info.get("art") or "",
            "album": album,
            "file": job["file"],
            "source": key,
            **(extra or {}),
        })
    except Exception as e:
        logger.warning(f"keep id={tid} file={dest.name} saved, but the history write failed: {type(e).__name__}: {e}")
        job["warning"] = f"Saved, but Reef couldn't record it as kept: {str(e)[:200]}"
    return {"title": title, "artist": artist, "album": album, "duration": info.get("duration") or length}


def _unlink(*paths):
    for p in paths:
        try:
            p.unlink()
        except OSError:
            pass


# ---------- lossless upgrade on keep (production plan P1.7; numbers from Meld's QobuzAudioProvider) ----------
# Points = 500 x matching's raw score (title 0.45 + artist 0.35 + duration 0.20), so Meld's minimum of 330 is a raw
# 0.66: the title and the artist both right, or one of them close plus a matching length. An ISRC match adds 1000.
# Version words that change the recording (remix, live, ...) reject a candidate outright; ones that usually don't
# (remaster, edit, acoustic, deluxe) cost 45 each when only one side has them. A length gap over 25 s rejects too,
# and so does a title below matching's title floor (unless the ISRC says it's the same recording).

LOSSLESS_SCALE = 500
LOSSLESS_MIN = 330
LOSSLESS_ISRC = 1000
LOSSLESS_SOFT = 45
LOSSLESS_MAX_GAP = 25
LOSSLESS_LIMIT = 10
PREVIEW_SHARE = 0.5
_HARD = {"remix", "live", "instrumental", "karaoke", "sped up", "slowed"}
_SOFT = re.compile(r"\b(remaster|edit|acoustic|deluxe)(?:ed)?\b", re.I)  # not "edition"
# "(Radio Edit)", " - Acoustic Version": the soft word is charged once (-45), not again as a title difference.
_SOFT_GROUP = re.compile(r"\s*\([^()]*\b(?:remaster|edit|acoustic|deluxe)(?:ed)?\b[^()]*\)"
                         r"|\s+[-–]\s+[^-–]*\b(?:remaster|edit|acoustic|deluxe)(?:ed)?\b[^-–]*$", re.I)


def _soft_words(*texts):
    return {m.lower() for t in texts for m in _SOFT.findall(str(t or ""))}


def lossless_points(query, candidate, isrc=""):
    """Points for keeping `candidate` (a Monochrome search result) instead of the YouTube `query`; None = rejected."""
    q, c = matching.fields(query), matching.fields(candidate)
    if (matching.versions(q["title"]) ^ matching.versions(c["title"])) & _HARD:
        return None
    if q["duration"] and c["duration"] and abs(q["duration"] - c["duration"]) > LOSSLESS_MAX_GAP:
        return None
    bare_q = {**q, "title": _SOFT_GROUP.sub("", q["title"]) or q["title"]}
    bare_c = {**c, "title": _SOFT_GROUP.sub("", c["title"]) or c["title"]}
    same_isrc = bool(isrc) and str(candidate.get("isrc") or "").upper() == isrc.upper()
    if not same_isrc and not matching.plausible(bare_q, bare_c):  # same title floor as every other match
        return None
    raw, _ = matching.score(bare_q, bare_c)
    soft = _soft_words(q["title"], q["album"]) ^ _soft_words(c["title"], c["album"])
    points = round(raw * LOSSLESS_SCALE) - LOSSLESS_SOFT * len(soft)
    if same_isrc:
        points += LOSSLESS_ISRC
    return points


def pick_lossless(query, candidates, isrc=""):
    """(candidate, points) of the best candidate reaching LOSSLESS_MIN, else None."""
    top = None
    for c in candidates or []:
        points = lossless_points(query, c, isrc) if isinstance(c, dict) else None
        if points is not None and points >= LOSSLESS_MIN and (top is None or points > top[1]):
            top = (c, points)
    return top


def _lossless_query(job):
    """(query, isrc) for the YouTube track a job keeps. The client sends what it showed; YouTube Music fills in
    a missing length. No YouTube metadata Reef reads carries an ISRC today, so that pass runs only with a hint."""
    q = matching.query_of({"id": job["video_id"], "title": job.get("title") or "", "channel": job.get("channel") or ""})
    q["duration"] = matching.fields({"duration": job.get("duration")})["duration"]
    q["album"] = (job.get("hints") or {}).get("album") or ""
    if (not q["duration"] or not q["title"]) and ytmusic.enabled():
        try:
            song = ytmusic.song(job["video_id"]) or {}
        except Exception:
            song = {}
        if not q["title"]:
            q = {**matching.query_of(song), "album": q["album"]}
        q["duration"] = q["duration"] or song.get("duration")
    return q, str((job.get("hints") or {}).get("isrc") or "").strip()


def _lossless_searches(q, isrc):
    """(search text, ISRC-only) in order: the ISRC, then title + artist + album, title + artist, the title."""
    base = matching.search_text(q)
    title = matching.search_text({"title": q["title"]})
    out = [(isrc, True)] if isrc else []
    for text in (f"{base} {q['album']}" if q["album"] else "", base, title):
        if text.strip() and all(text != t for t, _ in out):
            out.append((text, False))
    return out


def _run_lossless(job):
    """Keep the Monochrome FLAC of a YouTube job: the keep's meta, or None to keep from YouTube (silently)."""
    src = sources.get("mc")
    if src is None or not src.can_keep or sources.parse(job["video_id"])[0] != "yt":
        return None
    vid, started = job["video_id"], time.monotonic()
    q, isrc = _lossless_query(job)
    if not q["title"]:
        return None
    found = None
    for text, only_isrc in _lossless_searches(q, isrc):
        try:
            cands = src.search(text, LOSSLESS_LIMIT)
        except Exception as e:
            logger.info(f"keep-lossless id={vid} result=search-failed error={type(e).__name__}: {str(e)[:160]}")
            return None
        if only_isrc:
            cands = [c for c in cands if str(c.get("isrc") or "").upper() == isrc.upper()]
        found = pick_lossless(q, cands, isrc)
        if found:
            break
    if not found:
        logger.info(f"keep-lossless id={vid} result=no-match ms={int((time.monotonic() - started) * 1000)}")
        return None
    cand, points = found
    title = job["title"]
    job["lossless"], job["upgraded_to"] = True, cand["id"]
    try:
        meta = _run_generic(job, cand["id"], fmt="flac", expect=cand.get("duration") or q["duration"],
                            extra={"upgraded_from": vid})
    except Exception as e:
        if job.get("file") and os.path.exists(job["file"]):
            # The FLAC is already in the library: a YouTube keep now would add a second copy. Fail the job instead.
            logger.warning(f"keep-lossless id={vid} mc={cand['id']} result=error-after-save file={job['file']} "
                           f"error={type(e).__name__}: {str(e)[:160]}")
            raise
        logger.info(f"keep-lossless id={vid} mc={cand['id']} result=fallback error={type(e).__name__}: {str(e)[:160]}")
        job.update(lossless=False, upgraded_to=None, title=title, percent=0, status="downloading", file=None)
        job.pop("kept_id", None)
        return None
    logger.info(f"keep-lossless id={vid} mc={cand['id']} points={points} result=ok "
                f"ms={int((time.monotonic() - started) * 1000)}")
    return meta


def _length(path):
    try:
        return getattr(getattr(MutagenFile(path), "info", None), "length", None)
    except Exception:
        return None


# ---------- after the keep: loudness and lyrics, best effort ----------

def _post_steps(job, meta):
    """Gain tags, then lyrics, on the final file. Each step only warns on failure: the track is already kept."""
    path, tid = job["file"], job.get("kept_id") or job["video_id"]
    gain_db = found = None
    t0 = time.monotonic()
    if settings.get("replaygain") and path:
        try:
            m = loudness.measure(path)
            if m and loudness.tag(path, m):
                gain_db = loudness.track_gain(m["lufs"])
        except Exception as e:
            logger.warning(f"reef: loudness skipped for {tid}: {type(e).__name__}: {str(e)[:200]}")
    t1 = time.monotonic()
    try:
        found = lyrics.for_track(tid, meta["title"], meta["artist"], meta.get("album") or None, meta.get("duration"))
        if found and path and not lyrics.embed(path, found) and (found["synced"] or found["plain"]):
            logger.warning(f"reef: lyrics found for {tid} but not embedded in {os.path.basename(path)}")
    except Exception as e:
        logger.warning(f"reef: lyrics skipped for {tid}: {type(e).__name__}: {str(e)[:200]}")
    kind = None
    if found:
        kind = "synced" if found.get("synced") else "plain" if found.get("plain") else "instrumental"
    try:
        history.update(tid, lyrics=kind, gain_db=gain_db)
    except Exception as e:
        logger.warning(f"reef: couldn't record lyrics/gain for {tid}: {e}")
    logger.info(f"keep-post id={tid} loudness_ms={int((t1 - t0) * 1000)} lyrics_ms={int((time.monotonic() - t1) * 1000)} "
                f"lyrics={kind} gain_db={gain_db}")


def _run(job):
    job["status"] = "downloading"
    try:
        DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
        TMP_DIR.mkdir(parents=True, exist_ok=True)
        meta = None
        if job["source"] == "yt" and settings.get("keep_lossless"):
            meta = _run_lossless(job)
        if meta is None:
            meta = _run_ytdlp(job) if job["source"] == "yt" else _run_generic(job)
        job["status"] = "converting"  # still busy: the post steps below rewrite the file's tags
        _post_steps(job, meta)
        job["status"] = "done"
        job["rescanned"] = navidrome.rescan()
    except Exception as e:
        job["status"] = "error"
        typed = errors.payload(e)
        job["error"], job["block"], job["hint"] = typed["error"], typed["block"], typed["hint"]
        return
    # The scan is async, so this usually misses; /api/art retries lazily.
    try:
        song = navidrome.find_song(job["title"], os.path.basename(job["file"] or ""))
        if song and song.get("coverArt"):
            history.update(job.get("kept_id") or job["video_id"], nd_cover=song["coverArt"], nd_id=song.get("id"))
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
