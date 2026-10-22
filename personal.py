"""Optional anonymous personalisation (production plan P1.2), off unless the `yt_personalize` setting is on.

YouTube Music learns a listener's taste from plays reported against a visitor id, no Google account needed. With
the setting on, Reef keeps one visitorData for good (the `yt_visitor_id` setting: made on first use from
music.youtube.com/sw.js_data, or pasted), the YouTube Music client sends it with every request (ytmusic.py), and
the player reports plays the way the web player does:

- play start (`POST /api/personal/play`): an InnerTube `player` request for the video, then a GET of its
  `playbackTracking.videostatsPlaybackUrl.baseUrl` + `&ver=2&c=WEB_REMIX&cpn=<16 random chars>`;
- every BEAT_S seconds while it plays (`POST /api/personal/progress`): a GET of
  music.youtube.com/api/stats/watchtime?ns=yt&ver=2&c=WEB_REMIX&cmt=<s>&docid=<id>&cpn=<the play's cpn>.

Everything is best effort on one background thread: the routes only queue work and never wait for YouTube; a
failure is logged at debug and dropped, and a 4xx pauses all reporting for PAUSE_4XX. YouTube ids only. With the
setting off nothing is sent (queued work is dropped too) and the YouTube Music client goes back to an id of its own.
"""
import base64
import json
import logging
import queue
import random
import re
import string
import threading
import time
from collections import deque
from urllib.parse import unquote, urlencode

import requests

import settings

logger = logging.getLogger(__name__)

ORIGIN = "https://music.youtube.com"
SW_DATA = ORIGIN + "/sw.js_data"
PLAYER = ORIGIN + "/youtubei/v1/player?prettyPrint=false"
WATCHTIME = ORIGIN + "/api/stats/watchtime"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) Gecko/20100101 Firefox/128.0"
TIMEOUT = (5, 10)
PAUSE_4XX = 600        # seconds without any report after YouTube answered 4xx
BEAT_S = 10            # at most one watchtime ping per play this often (the client asks about every 10 s)
STS_TTL = 86400        # the player's signatureTimestamp (read from base.js) is refreshed daily
VISITOR_RETRY = 600    # a failed visitor id fetch isn't tried again sooner
QUEUE_MAX = 32
RECENT_MAX = 20
CPN_CHARS = string.ascii_letters + string.digits + "-_"
VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
VISITOR = re.compile(r"^Cg[ts][A-Za-z0-9_%=+/-]{8,1000}$")  # visitorData as sw.js_data / ytcfg hand it out
_COOKIE_ID = re.compile(r"^[A-Za-z0-9_-]{6,64}$")

_session = requests.Session()  # tests swap it for a fake
_queue = queue.Queue(QUEUE_MAX)
_lock = threading.Lock()          # guards _state
_visitor_lock = threading.Lock()  # one visitor id fetch at a time
_worker = None
_state = {"play": None, "paused_until": 0.0, "sent": 0, "failed": 0, "dropped": 0, "recent": deque(maxlen=RECENT_MAX),
          "visitor_failed_at": 0.0, "sts": None}


def active():
    """Reports go out (and YouTube Music requests carry the kept visitor id) only with both settings on."""
    return bool(settings.get("yt_personalize") and settings.get("ytmusic"))


def _find_visitor(node):
    if isinstance(node, str):
        return node if VISITOR.match(node) else None
    if isinstance(node, list):
        for item in node:
            found = _find_visitor(item)
            if found:
                return found
    return None


def _fetch_visitor():
    """A new visitorData: sw.js_data (`)]}'` + JSON, the first string that looks like one), else the homepage's
    ytcfg VISITOR_DATA (what ytmusicapi does). Raises when neither gives one."""
    try:
        r = _session.get(SW_DATA, headers={"User-Agent": UA, "Referer": ORIGIN + "/"}, timeout=TIMEOUT)
        r.raise_for_status()
        text = r.text
        found = _find_visitor(json.loads(text[text.index("["):]))
        if found:
            return found
    except (requests.RequestException, ValueError) as e:
        logger.debug(f"personal: sw.js_data failed: {str(e)[:160]}")
    r = _session.get(ORIGIN + "/", headers={"User-Agent": UA}, cookies={"SOCS": "CAI"}, timeout=TIMEOUT)
    r.raise_for_status()
    m = re.search(r'"VISITOR_DATA"\s*:\s*"([^"]+)"', r.text)
    if not m or not VISITOR.match(m.group(1)):
        raise RuntimeError("no visitor id in YouTube Music's answers")
    return m.group(1)


def visitor_id(create=True):
    """The kept visitor id; with `create`, a missing one is fetched and saved (settings.json) first. None when
    there's none and it can't be had right now (a failed fetch waits VISITOR_RETRY)."""
    vid = settings.get("yt_visitor_id")
    if vid or not create:
        return vid or None
    with _visitor_lock:
        vid = settings.get("yt_visitor_id")
        if vid:
            return vid
        if time.time() - _state["visitor_failed_at"] < VISITOR_RETRY:
            return None
        try:
            vid = _fetch_visitor()
            settings.update({"yt_visitor_id": vid})
        except Exception as e:
            _state["visitor_failed_at"] = time.time()
            logger.debug(f"personal: no visitor id: {str(e)[:160]}")
            return None
        logger.info("personal: made a new anonymous visitor id")
        return vid


def client_visitor():
    """The visitor id the YouTube Music client should carry: the kept one while personalisation is on, else None
    (ytmusicapi then fetches a throwaway id of its own). Never raises."""
    try:
        return visitor_id() if active() else None
    except Exception as e:
        logger.debug(f"personal: visitor id unavailable: {str(e)[:160]}")
        return None


def _cookie_value(vid):
    """The VISITOR_INFO1_LIVE cookie inside visitorData (protobuf field 1 of the base64url bytes), or None. The
    stats endpoints are plain GETs: the cookie ties them to the same visitor as the player request's header."""
    try:
        raw = unquote(vid)
        data = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
        if data[0] != 0x0A:
            return None
        value = data[2:2 + data[1]].decode("ascii")
    except Exception:
        return None
    return value if _COOKIE_ID.match(value) else None


def _client_version():
    return "1." + time.strftime("%Y%m%d", time.gmtime()) + ".01.00"


def _headers(vid, json_body=False):
    h = {"User-Agent": UA, "Accept": "*/*", "Accept-Language": "en-US,en;q=0.5", "Origin": ORIGIN,
         "X-Origin": ORIGIN, "Referer": ORIGIN + "/", "X-Goog-Visitor-Id": vid, "X-YouTube-Client-Name": "67",
         "X-YouTube-Client-Version": _client_version()}
    if json_body:
        h["Content-Type"] = "application/json"
    return h


def _cookies(vid):
    out = {"SOCS": "CAI"}
    value = _cookie_value(vid)
    if value:
        out["VISITOR_INFO1_LIVE"] = value
    return out


def _sts():
    """The player's signatureTimestamp (base.js), cached STS_TTL; None when it can't be read (the player request
    then goes without it and may come back without playbackTracking)."""
    hit = _state["sts"]
    if hit and time.time() - hit[0] < STS_TTL:
        return hit[1]
    try:
        page = _session.get(ORIGIN + "/", headers={"User-Agent": UA}, cookies={"SOCS": "CAI"}, timeout=TIMEOUT)
        m = re.search(r'"jsUrl"\s*:\s*"([^"]+)"', page.text)
        if not m:
            raise RuntimeError("no jsUrl")
        js = _session.get(ORIGIN + m.group(1), headers={"User-Agent": UA}, timeout=TIMEOUT)
        found = re.search(r"signatureTimestamp[:=](\d+)", js.text)
        if not found:
            raise RuntimeError("no signatureTimestamp")
        _state["sts"] = (time.time(), int(found.group(1)))
        return _state["sts"][1]
    except Exception as e:
        logger.debug(f"personal: signatureTimestamp unavailable: {str(e)[:160]}")
        _state["sts"] = (time.time() - STS_TTL + VISITOR_RETRY, None)  # don't refetch base.js on every play
        return None


def _cpn():
    return "".join(random.choice(CPN_CHARS) for _ in range(16))


def paused():
    return time.time() < _state["paused_until"]


def _note(kind, video_id, status, error=None):
    with _lock:
        ok = status is not None and status < 400 and not error
        _state["sent" if ok else "failed"] += 1
        entry = {"kind": kind, "id": video_id, "status": status, "at": int(time.time())}
        if error:
            entry["error"] = error[:120]
        _state["recent"].append(entry)
        if status is not None and 400 <= status < 500:
            _state["paused_until"] = time.time() + PAUSE_4XX
    if not ok:
        logger.debug(f"personal: {kind} id={video_id} failed status={status} {error or ''}".rstrip())


def _get(kind, video_id, url, vid):
    r = _session.get(url, headers=_headers(vid), cookies=_cookies(vid), timeout=TIMEOUT)
    _note(kind, video_id, r.status_code)
    return r.status_code


def _send_play(video_id, cpn):
    vid = visitor_id()
    if not vid:
        _note("player", video_id, None, "no visitor id")
        return
    body = {"context": {"client": {"clientName": "WEB_REMIX", "clientVersion": _client_version(), "hl": "en",
                                   "visitorData": vid}},
            "videoId": video_id}
    sts = _sts()
    if sts:
        body["playbackContext"] = {"contentPlaybackContext": {"signatureTimestamp": sts}}
    start = time.monotonic()
    r = _session.post(PLAYER, json=body, headers=_headers(vid, json_body=True), cookies=_cookies(vid), timeout=TIMEOUT)
    if r.status_code >= 400:
        _note("player", video_id, r.status_code)
        return
    try:
        base = r.json()["playbackTracking"]["videostatsPlaybackUrl"]["baseUrl"]
    except (ValueError, KeyError, TypeError):
        _note("player", video_id, r.status_code, "no playbackTracking")
        return
    _note("player", video_id, r.status_code)
    if not isinstance(base, str) or not base.startswith("https://"):
        _note("videostats", video_id, None, "bad baseUrl")
        return
    status = _get("videostats", video_id, f"{base}&ver=2&c=WEB_REMIX&cpn={cpn}", vid)
    logger.info(f"personal play id={video_id} player={r.status_code} videostats={status} "
                f"ms={int((time.monotonic() - start) * 1000)}")


def _send_watch(video_id, cpn, seconds):
    vid = visitor_id(create=False)
    if not vid:
        return
    query = urlencode({"ns": "yt", "ver": 2, "c": "WEB_REMIX", "cmt": seconds, "docid": video_id, "cpn": cpn})
    status = _get("watchtime", video_id, f"{WATCHTIME}?{query}", vid)
    logger.debug(f"personal watchtime id={video_id} cmt={seconds} status={status}")


def _run(task):
    kind, video_id = task[0], task[1]
    if not active() or paused():  # turned off (or paused) since it was queued
        with _lock:
            _state["dropped"] += 1
        return
    try:
        if kind == "play":
            _send_play(video_id, task[2])
        else:
            _send_watch(video_id, task[2], task[3])
    except Exception as e:  # network errors, timeouts: dropped, nothing retries
        _note("player" if kind == "play" else "watchtime", video_id, None, f"{type(e).__name__}: {e}")


def _loop():
    while True:
        task = _queue.get()
        try:
            _run(task)
        finally:
            _queue.task_done()


def _enqueue(task):
    global _worker
    with _lock:
        if _worker is None:
            _worker = threading.Thread(target=_loop, daemon=True, name="reef-personal")
            _worker.start()
    try:
        _queue.put_nowait(task)
        return True
    except queue.Full:
        with _lock:
            _state["dropped"] += 1
        return False


def _refusal(video_id):
    """Why nothing is sent for this id right now, else None."""
    if not active():
        return "off"
    if not isinstance(video_id, str) or not VIDEO_ID.match(video_id):
        return "not_youtube"
    if paused():
        return "paused"
    return None


def play(video_id):
    """A YouTube track started playing: (queued, reason). Starts a new reporting session (cpn) for it."""
    reason = _refusal(video_id)
    if reason:
        return False, reason
    cpn = _cpn()
    with _lock:
        _state["play"] = {"id": video_id, "cpn": cpn, "started": time.time(), "beat": time.monotonic()}
    return _enqueue(("play", video_id, cpn)), None


def progress(video_id, seconds):
    """The current play is at `seconds`: (queued, reason). A ping goes out at most every BEAT_S per play, and only
    for the play that /play started (same cpn)."""
    reason = _refusal(video_id)
    if reason:
        return False, reason
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not 0 <= seconds <= 86400:
        return False, "bad_time"
    now = time.monotonic()
    with _lock:
        cur = _state["play"]
        if not cur or cur["id"] != video_id:
            return False, "no_play"
        if now - cur["beat"] < BEAT_S - 0.5:  # a little slack for the client's timer
            return False, "throttled"
        cur["beat"] = now
        cpn = cur["cpn"]
    return _enqueue(("watch", video_id, cpn, int(seconds))), None


def flush(timeout=5.0):
    """Wait for queued reports to be sent (tests, the live check). True when the queue drained in time."""
    end = time.monotonic() + timeout
    while _queue.unfinished_tasks and time.monotonic() < end:
        time.sleep(0.02)
    return not _queue.unfinished_tasks


def status():
    """For /api/personal/status and diagnostics: never the visitor id itself."""
    vid = settings.get("yt_visitor_id")
    with _lock:
        cur = _state["play"]
        return {"active": active(), "visitor": settings.mask(vid) if vid else None,
                "paused_until": int(_state["paused_until"]) if paused() else None,
                "sent": _state["sent"], "failed": _state["failed"], "dropped": _state["dropped"],
                "current": {"id": cur["id"], "started_at": int(cur["started"])} if cur else None,
                "recent": list(_state["recent"])}
