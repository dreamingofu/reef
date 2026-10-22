"""Logging: one `op k=v` line per event to stderr and logs/reef.log (1 MB x 5, rotated), the last 300 lines kept in
memory for /api/diagnostics. Every line goes through redact(), so signed stream URLs, Subsonic tokens and cookies
never reach a file or a copied diagnostics blob (Meld's Fix403 idea: keep length + short hash to compare URLs)."""
import hashlib
import logging
import re
import sys
import threading
from collections import deque
from logging.handlers import RotatingFileHandler

import config

FORMAT = "%(asctime)s %(levelname).1s %(message)s"
RECENT_MAX = 300
FILE_BYTES = 1024 * 1024
FILE_COUNT = 5

# googlevideo's signature / PO token / n-challenge params, Subsonic's token, salt and password, and the usual API
# key / token / password params of a configured instance URL. Whole names only, in any case (`keyword=` stays).
_SECRET_PARAMS = ("sig", "signature", "lsig", "pot", "n", "t", "s", "p",
                  "api_key", "key", "token", "access_token", "password", "auth")
_QUERY = re.compile(r"(?i)([?&](?:%s)=)([^&#\s'\"]+)" % "|".join(_SECRET_PARAMS))
_PATH = re.compile(r"(/(?:sig|lsig|signature)/)([^/?&#\s'\"]+)")  # older googlevideo path-style params
_HEADER = re.compile(r"(?i)(\b(?:set-cookie|cookie|authorization)['\"]?\s*[:=]\s*['\"]?)([^'\"\r\n]+)")
# user:pass@ in any scheme://authority, up to its last "@" (an unencoded "@" in a password stays inside).
_USERINFO = re.compile(r"(?i)(\b[a-z][a-z0-9+.-]*://)([^/?#@\s'\"<>]*(?:@[^/?#@\s'\"<>]*)*)(?=@)")
_MASKED = re.compile(r"len=\d+,sha1=[0-9a-f]{6}")

_recent = deque(maxlen=RECENT_MAX)
_setup_lock = threading.Lock()
_ready = False


def mask(value):
    return f"len={len(value)},sha1={hashlib.sha1(value.encode()).hexdigest()[:6]}"


def redact(text):
    """`text` with URL userinfo, secret URL params and header values replaced by `len=N,sha1=xxxxxx`."""
    if not isinstance(text, str) or not text:
        return text
    for rx in (_USERINFO, _QUERY, _PATH, _HEADER):
        text = rx.sub(_masked, text)
    return text


def _masked(m):
    """The match with its secret group masked; a value that's already a mask stays (redacting twice changes nothing)."""
    value = m.group(2)
    return m.group(1) + (value if _MASKED.fullmatch(value) else mask(value))


def redact_obj(obj):
    """redact() over every string in a JSON-ish structure, dict keys included (for payloads that echo error
    messages, and maps keyed by a configured URL such as failover's cooling hosts)."""
    if isinstance(obj, str):
        return redact(obj)
    if isinstance(obj, dict):
        return {redact(k) if isinstance(k, str) else k: redact_obj(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [redact_obj(v) for v in obj]
    return obj


class _Formatter(logging.Formatter):
    def format(self, record):
        return redact(super().format(record))


class _Recent(logging.Handler):
    def emit(self, record):
        try:
            _recent.append(self.format(record))
        except Exception:
            self.handleError(record)


def recent():
    """The last RECENT_MAX log lines, oldest first (already redacted)."""
    return list(_recent)


def setup(log_dir=None, stderr=None):
    """Attach the handlers to the root logger once. `stderr=False` (REEF_LOG_STDERR=0, set by termux/reef-run.sh)
    leaves the console quiet so the supervisor's capture file doesn't grow a second copy of reef.log."""
    global _ready
    with _setup_lock:
        if _ready:
            return
        _ready = True
        fmt = _Formatter(FORMAT)
        root = logging.getLogger()
        root.setLevel(logging.INFO)
        handlers = [_Recent()]
        if config.LOG_STDERR if stderr is None else stderr:
            handlers.append(logging.StreamHandler(sys.stderr))
        log_dir = config.LOG_DIR if log_dir is None else log_dir
        if log_dir:
            try:
                log_dir.mkdir(parents=True, exist_ok=True)
                handlers.append(RotatingFileHandler(log_dir / "reef.log", maxBytes=FILE_BYTES,
                                                    backupCount=FILE_COUNT, encoding="utf-8"))
            except OSError as e:
                print(f"reef: can't write logs to {log_dir}: {e}", file=sys.stderr)
        for h in handlers:
            h.setFormatter(fmt)
            root.addHandler(h)
        # A crash in a request thread or a background worker lands in reef.log, not only on a console nobody reads.
        sys.excepthook = lambda *exc: logging.getLogger("reef").critical("uncaught exception", exc_info=exc)
        threading.excepthook = _thread_crash


def _thread_crash(a):
    if a.exc_type is SystemExit:  # like the default hook: a thread calling sys.exit() is not a crash
        return
    logging.getLogger("reef").error("uncaught exception thread=%s", a.thread.name if a.thread else "?",
                                    exc_info=(a.exc_type, a.exc_value, a.exc_traceback))
