"""ListenBrainz scrobbling (production plan P1.4): every counted listen goes to
`POST https://api.listenbrainz.org/1/submit-listens` when a token is set (Settings → `listenbrainz_token`) and
`scrobble_enabled` is on.

Listens wait in `scrobbles.json` (`pending`) and a background thread submits them, so being offline, a 429 or a
5xx only delays them: the queue backs off (30 s doubling to 1 h, or the server's Retry-After) and keeps them up
to 14 days. A 4xx other than 401/429 means ListenBrainz will never take that listen: it's dropped and logged.
401 = the token is wrong or revoked; the listens wait (the 14 days still apply) until a new token is saved.
Submitted keys (`<id>@<started_at>`) are remembered and the listen-log entry gets `scrobbled: true`, so a play the
player reports twice is submitted once. The token is only ever sent as the Authorization header: it's never
logged, returned, put in diagnostics or backups (a short hash tells whether the stored user name is for it).
"""
import hashlib
import logging
import threading
import time

import requests

import config
import listens
import log
import settings
import store
from tagging import clean_artist, guess

logger = logging.getLogger(__name__)

API = "https://api.listenbrainz.org/1"
TIMEOUT = 10
MAX_AGE = 14 * 86400   # a listen that couldn't be submitted for this long is dropped
QUEUE_MAX = 2000
DONE_MAX = 1000        # submitted keys remembered against double submits
ROUND = 20             # listens submitted per worker round (one request each, listen_type "single")
BACKOFF_MIN = 30
BACKOFF_MAX = 3600
IDLE_S = 300           # the worker also wakes this often (expiry, a retry_at that passed while asleep)

_file = store.JsonFile(lambda: config.SCROBBLES_FILE, dict)
_session = requests.Session()
_session.headers["User-Agent"] = "Reef (https://github.com/dreamingofu/reef)"
_wake = threading.Event()
_flush_lock = threading.Lock()  # one submit round at a time (the worker, or a test calling flush)


def _token():
    return settings.get("listenbrainz_token") or ""


def _token_id(token):
    """A short fingerprint of the token: tells whether the saved user name belongs to the current token."""
    return hashlib.sha256(token.encode()).hexdigest()[:12] if token else None


def active():
    return bool(_token()) and settings.get("scrobble_enabled")


def key(entry):
    return f"{entry.get('id')}@{int(entry.get('started_at') or 0)}"


def metadata(entry):
    """ListenBrainz `track_metadata` for a listen-log entry, or None when there's no title or artist to send."""
    names = [a["name"] for a in entry.get("artists") or [] if isinstance(a, dict) and a.get("name")]
    title = (entry.get("title") or "").strip()
    if names:
        artist = ", ".join(names)
    elif entry.get("source", "yt") == "yt":
        # A plain YouTube result: "Artist - Song (Official Video)" by "ArtistVEVO" -> ("Song", "Artist"), as a keep tags it.
        clean, artist = guess(title, entry.get("channel"))
        title = clean or title
    else:
        artist = clean_artist(entry.get("channel"))
    if not title or not artist:
        return None
    info = {"submission_client": "Reef", "media_player": "Reef"}
    if entry.get("duration"):
        info["duration_ms"] = int(entry["duration"] * 1000)
    if entry.get("source", "yt") == "yt" and store.VIDEO_ID.match(str(entry.get("id"))):
        info["origin_url"] = f"https://www.youtube.com/watch?v={entry['id']}"
        info["music_service"] = "youtube.com"
    meta = {"artist_name": artist, "track_name": title, "additional_info": info}
    if entry.get("album"):
        meta["release_name"] = entry["album"]
    return meta


def payload(items, listen_type="single"):
    return {"listen_type": listen_type,
            "payload": [{"listened_at": i["listened_at"], "track_metadata": i["track_metadata"]} for i in items]}


def listened(entry, now=None):
    """Queue a counted listen for submission; False when scrobbling is off, it doesn't count, or it's queued or
    submitted already."""
    now = time.time() if now is None else now
    if not entry.get("counted") or entry.get("scrobbled") or not active():
        return False
    meta = metadata(entry)
    if meta is None:
        return False
    k = key(entry)
    with _file.lock:
        data = dict(_file.load())
        pending = list(data.get("pending") or [])
        if k in (data.get("done") or []) or any(i.get("key") == k for i in pending):
            return False
        pending.append({"key": k, "listened_at": int(entry["started_at"]), "track_metadata": meta, "added_at": now})
        if len(pending) > QUEUE_MAX:
            logger.warning(f"scrobble queue full: dropping {len(pending) - QUEUE_MAX} oldest")
            del pending[:len(pending) - QUEUE_MAX]
        data["pending"] = pending
        _file.save(data)
    _wake.set()
    return True


def _retry_after(r):
    for h in ("Retry-After", "X-RateLimit-Reset-In"):
        try:
            return max(0, int(float(r.headers.get(h))))
        except (TypeError, ValueError):
            continue
    return 0


def _error_text(r):
    try:
        body = r.json()
        msg = body.get("error") if isinstance(body, dict) else None
    except ValueError:
        msg = None
    return f"{r.status_code} {str(msg or r.reason or '')[:160]}".strip()


def _submit(token, item):
    """("ok" | "retry" | "drop" | "auth", detail, retry_after_s) for one listen."""
    try:
        r = _session.post(f"{API}/submit-listens", json=payload([item]), timeout=TIMEOUT,
                          headers={"Authorization": f"Token {token}"})
    except requests.RequestException as e:
        return "retry", f"network: {log.redact(str(e))[:160]}", 0
    if r.status_code == 200:
        return "ok", "", 0
    if r.status_code == 401:
        return "auth", "ListenBrainz rejected the token (401)", 0
    if r.status_code == 429 or r.status_code >= 500:
        return "retry", _error_text(r), _retry_after(r)
    return "drop", _error_text(r), 0


def flush(now=None):
    """One worker round: drop expired listens, then submit due ones (at most ROUND) until one fails.
    Returns how many were accepted. HTTP happens outside the file lock, so new listens queue meanwhile."""
    with _flush_lock:
        now = time.time() if now is None else now
        with _file.lock:
            data = dict(_file.load())
            pending = list(data.get("pending") or [])
            fresh = [i for i in pending if (i.get("added_at") or 0) > now - MAX_AGE]
            if len(fresh) != len(pending):
                logger.warning(f"scrobble drop expired n={len(pending) - len(fresh)} (older than 14 days)")
                data["pending"] = fresh
                _file.save(data)
            batch = fresh[:ROUND]
            due = (data.get("retry_at") or 0) <= now
        token = _token()
        if not batch or not due or not active():
            return 0
        sent, gone, ok = [], set(), 0
        outcome = None
        for item in batch:
            verdict, detail, wait = _submit(token, item)
            if verdict == "ok":
                sent.append(item)
                gone.add(item["key"])
                ok += 1
                continue
            if verdict == "drop":
                logger.warning(f"scrobble drop key={item['key']} why={detail!r}")
                gone.add(item["key"])
                outcome = ("drop", detail, 0)
                continue
            outcome = (verdict, detail, wait)
            break  # offline, rate limited or signed out: the rest would fail the same way
        with _file.lock:
            data = dict(_file.load())
            data["pending"] = [i for i in data.get("pending") or [] if i.get("key") not in gone]
            if sent:
                data["done"] = (list(data.get("done") or []) + [i["key"] for i in sent])[-DONE_MAX:]
                data["submitted"] = int(data.get("submitted") or 0) + len(sent)
                data["last_ok_at"] = now
            if outcome and outcome[0] in ("retry", "auth"):
                fails = int(data.get("fails") or 0) + 1
                backoff = BACKOFF_MAX if outcome[0] == "auth" else min(BACKOFF_MAX, BACKOFF_MIN * 2 ** (fails - 1))
                data.update(fails=fails, retry_at=now + max(backoff, outcome[2]), last_error=outcome[1],
                            last_error_at=now)
                if outcome[0] == "auth":
                    data["token_valid"] = False
                logger.warning(f"scrobble retry_in_s={int(data['retry_at'] - now)} why={outcome[1]!r}")
            else:
                data.update(fails=0, retry_at=0)
                if outcome:  # a dropped listen: worth showing, though the queue moves on
                    data.update(last_error=outcome[1], last_error_at=now)
                elif sent:
                    data["last_error"] = None
            _file.save(data)
        if sent:
            logger.info(f"scrobble ok n={len(sent)} pending={len(data['pending'])}")
            try:
                listens.mark_scrobbled([(i["key"].rsplit("@", 1)[0], i["listened_at"]) for i in sent])
            except Exception as e:
                logger.warning(f"scrobble: couldn't mark listens: {str(e)[:160]}")
        return ok


def validate(now=None):
    """Ask ListenBrainz whether the stored token is valid; keeps the user name for the status sheet."""
    now = time.time() if now is None else now
    token = _token()
    result = {"token_valid": None, "user_name": None, "validated_at": now, "token_id": _token_id(token)}
    error = None
    if token:
        try:
            r = _session.get(f"{API}/validate-token", timeout=TIMEOUT, headers={"Authorization": f"Token {token}"})
            try:
                body = r.json()
            except ValueError:
                body = {}
            if r.status_code == 200 and isinstance(body, dict) and body.get("valid"):
                result.update(token_valid=True, user_name=str(body.get("user_name") or "")[:100] or None)
            elif r.status_code in (200, 400, 401):
                result["token_valid"] = False
                error = "ListenBrainz says the token isn't valid"
            else:
                error = f"ListenBrainz answered {r.status_code}"
        except requests.RequestException as e:
            error = f"Couldn't reach ListenBrainz: {log.redact(str(e))[:160]}"
    with _file.lock:
        data = dict(_file.load())
        data.update(result)
        if result["token_valid"]:
            if data.get("last_error") and "token" in str(data.get("last_error")):
                data["last_error"] = None
            data.update(retry_at=0, fails=0)  # a 401 pause ends with a good token
        elif error:
            data.update(last_error=error, last_error_at=now)
        _file.save(data)
    logger.info(f"scrobble validate valid={result['token_valid']} user={result['user_name'] or '-'}")
    _wake.set()
    return status()


def token_changed():
    """Settings saved a new token (or cleared it): forget what was known about the old one, check the new one."""
    with _file.lock:
        data = dict(_file.load())
        for k in ("user_name", "token_valid", "validated_at", "token_id", "retry_at", "fails", "last_error"):
            data.pop(k, None)
        _file.save(data)
    if _token():
        validate()


def status():
    """GET /api/scrobble/status: never the token, only whether one is set and whose it is."""
    with _file.lock:
        data = dict(_file.load())
    token = _token()
    mine = bool(token) and data.get("token_id") == _token_id(token)
    return {"enabled": active(), "token_set": bool(token), "scrobble_enabled": settings.get("scrobble_enabled"),
            "pending": len(data.get("pending") or []), "submitted": int(data.get("submitted") or 0),
            "last_ok_at": data.get("last_ok_at"), "last_error": data.get("last_error"),
            "last_error_at": data.get("last_error_at"), "retry_at": data.get("retry_at") or None,
            "user_name": data.get("user_name") if mine else None,
            "token_valid": data.get("token_valid") if mine else None,
            "validated_at": data.get("validated_at") if mine else None}


def _loop():
    while True:
        _wake.clear()  # before the round: a listen queued during it wakes the next one at once
        try:
            flush()
        except Exception as e:
            logger.warning(f"scrobble round failed: {str(e)[:200]}")
        with _file.lock:
            data = _file.load()
            retry_at, waiting = data.get("retry_at") or 0, bool(data.get("pending"))
        wait = IDLE_S
        if waiting and active():
            wait = min(IDLE_S, max(5, retry_at - time.time()))
        _wake.wait(wait)


def start_worker():
    threading.Thread(target=_loop, daemon=True, name="reef-scrobble").start()
