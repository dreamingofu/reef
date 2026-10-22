"""Test audio mode, for checking the UI without touching YouTube streams.

`REEF_TEST_AUDIO=<file or directory>`: `/api/stream/<id>` for YouTube ids serves a local audio file instead of
resolving the video (the same file per id: the id's hash picks one from the directory), with Range / 206 from
Flask's `send_file`. Prefetches of YouTube ids are dropped too, so nothing warms the resolver. Search, browse,
art and lyrics are untouched (YouTube Music isn't the part that bot-walls).

It must never switch on by accident on the phone, so it is refused unless the server only listens on loopback
(`REEF_HOST=127.0.0.1` / `::1` / `localhost`) or `REEF_TEST_AUDIO_I_KNOW=1` is also set. Startup logs it loudly.
"""
import hashlib
import ipaddress
import logging
import os
from pathlib import Path

import config

logger = logging.getLogger(__name__)

AUDIO_EXT = {".m4a": "audio/mp4", ".mp4": "audio/mp4", ".aac": "audio/aac", ".mp3": "audio/mpeg",
             ".opus": "audio/ogg", ".ogg": "audio/ogg", ".oga": "audio/ogg", ".flac": "audio/flac",
             ".wav": "audio/wav", ".webm": "audio/webm"}

_files = None  # list[Path] once scanned ([] = off)
_reason = ""   # why it's off when REEF_TEST_AUDIO is set but refused


def _loopback(host):
    host = (host or "").strip().strip("[]")
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def allowed(host=None, i_know=None):
    """True when the bind address (or the explicit opt-in) allows test audio."""
    host = config.HOST if host is None else host
    i_know = os.environ.get("REEF_TEST_AUDIO_I_KNOW", "0").strip() == "1" if i_know is None else i_know
    return _loopback(host) or bool(i_know)


def scan(path):
    """The audio files at `path` (a file, or a directory's audio files, sorted), else []."""
    if not path:
        return []
    p = Path(path).expanduser()
    if p.is_file():
        return [p] if p.suffix.lower() in AUDIO_EXT else []
    if p.is_dir():
        return sorted(f for f in p.iterdir() if f.is_file() and f.suffix.lower() in AUDIO_EXT)
    return []


def setup(path=None, host=None, i_know=None):
    """(Re)read the environment; returns the files in use ([] when off). Tests call it with explicit values."""
    global _files, _reason
    path = os.environ.get("REEF_TEST_AUDIO", "").strip() if path is None else path
    _files, _reason = [], ""
    if not path:
        return _files
    if not allowed(host, i_know):
        _reason = (f"refused: REEF_HOST={config.HOST if host is None else host} is not loopback "
                   "(bind to 127.0.0.1 or also set REEF_TEST_AUDIO_I_KNOW=1)")
        return _files
    found = scan(path)
    if not found:
        _reason = f"refused: no audio files at {path}"
        return _files
    _files = found
    return _files


def files():
    if _files is None:
        setup()
    return _files


def enabled():
    return bool(files())


def covers(source_key):
    """True when streams of this source are replaced (YouTube only)."""
    return source_key == "yt" and enabled()


def pick(native_id):
    """The local file for a YouTube id: stable per id across restarts."""
    fs = files()
    n = int(hashlib.sha1(native_id.encode("utf-8")).hexdigest(), 16)
    return fs[n % len(fs)]


def mimetype(path):
    return AUDIO_EXT.get(Path(path).suffix.lower(), "application/octet-stream")


def announce():
    """Log the mode at startup: loud when on, an error when set but refused."""
    fs = files()
    if fs:
        bar = "!" * 72
        logger.warning(bar)
        logger.warning(f"TEST AUDIO MODE: YouTube streams are replaced by {len(fs)} local file(s) from "
                       f"{os.environ.get('REEF_TEST_AUDIO', '')!r}. Never run this on the phone.")
        logger.warning(bar)
    elif _reason:
        logger.error(f"REEF_TEST_AUDIO is set but test audio mode is off: {_reason}")


def status():
    """For /api/diagnostics: {on, files, reason}."""
    fs = files()
    return {"on": bool(fs), "files": len(fs), "reason": _reason or None}
