"""Anonymous YouTube Music personalisation (personal.py, production plan P1.2): nothing is sent while it's off, the
play-start sequence (visitor id → player → videostats) and the watchtime heartbeats when it's on, the 4xx pause,
the YouTube Music client carrying the kept visitor id, and YouTube Music's own shelves on Home. Offline: a fake
HTTP session records every request."""
import base64
import json
import re
import time
from collections import deque
from urllib.parse import parse_qs, urlsplit

import pytest
import requests

import app
import config
import history
import home
import personal
import settings
import ytmusic

YT = "dQw4w9WgXcQ"
COOKIE = "abcdefghijk"
VISITOR = base64.urlsafe_b64encode(b"\x0a\x0b" + COOKIE.encode() + b"(\x90\xa5\xe7\xd6\x06").decode().replace("=", "%3D")
STATS = "https://s.youtube.com/api/stats/playback?cl=1&docid=" + YT + "&ei=EI&ns=yt&plid=PL&el=detailpage"


class Resp:
    def __init__(self, status=200, body=None, text=None):
        self.status_code, self._body = status, body
        self.text = text if text is not None else json.dumps(body) if body is not None else ""

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


class FakeYouTube:
    """Answers like music.youtube.com; `status` overrides the answer per request kind."""

    def __init__(self):
        self.calls, self.status = [], {}

    def _kind(self, url):
        if url.endswith("/sw.js_data"):
            return "sw"
        if url.endswith("/base.js"):
            return "basejs"
        if url == personal.ORIGIN + "/":
            return "homepage"
        if "/youtubei/v1/player" in url:
            return "player"
        if "/api/stats/playback" in url:
            return "videostats"
        if "/api/stats/watchtime" in url:
            return "watchtime"
        return "other"

    def _answer(self, kind):
        status = self.status.get(kind, 200)
        if kind == "sw":
            return Resp(status, text=")]}'\n" + json.dumps([["yt.sw.adr", None, [[["en", "US", None, VISITOR]]]]]))
        if kind == "homepage":
            return Resp(status, text='<script>ytcfg.set({"jsUrl":"/s/player/x/base.js","VISITOR_DATA":"x"});</script>')
        if kind == "basejs":
            return Resp(status, text="var a={signatureTimestamp:20745,b:1}")
        if kind == "player":
            return Resp(status, {"playbackTracking": {"videostatsPlaybackUrl": {"baseUrl": STATS}}})
        return Resp(204 if status == 200 else status, text="")

    def get(self, url, headers=None, cookies=None, timeout=None):
        kind = self._kind(url)
        self.calls.append({"kind": kind, "method": "GET", "url": url, "headers": headers or {}, "cookies": cookies or {}})
        return self._answer(kind)

    def post(self, url, json=None, headers=None, cookies=None, timeout=None):
        kind = self._kind(url)
        self.calls.append({"kind": kind, "method": "POST", "url": url, "json": json, "headers": headers or {},
                           "cookies": cookies or {}})
        return self._answer(kind)

    def kinds(self):
        return [c["kind"] for c in self.calls]


@pytest.fixture
def yt(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(config, "YTMUSIC", True)
    fake = FakeYouTube()
    monkeypatch.setattr(personal, "_session", fake)
    monkeypatch.setattr(personal, "_state", {"play": None, "paused_until": 0.0, "sent": 0, "failed": 0, "dropped": 0,
                                             "recent": deque(maxlen=personal.RECENT_MAX), "visitor_failed_at": 0.0,
                                             "sts": None})
    monkeypatch.setattr(ytmusic, "_yt", None)
    monkeypatch.setattr(ytmusic, "_yt_visitor", None)
    yield fake
    assert personal.flush()  # nothing left running into the next test


@pytest.fixture
def client():
    return app.app.test_client()


def turn_on():
    settings.update({"yt_personalize": True})


# ---------- off: nothing is sent ----------

def test_off_by_default_sends_nothing(yt, client):
    assert settings.get("yt_personalize") is False and not personal.active()
    r = client.post("/api/personal/play", json={"id": YT})
    assert r.status_code == 200 and r.get_json() == {"queued": False, "reason": "off"}
    r = client.post("/api/personal/progress", json={"id": YT, "t": 30})
    assert r.get_json() == {"queued": False, "reason": "off"}
    assert personal.flush() and yt.calls == []
    assert settings.get("yt_visitor_id") == ""  # no id is even made
    assert ytmusic._client() is not None and ytmusic._yt_visitor is None  # the client keeps a throwaway id
    assert yt.calls == []


def test_off_needs_youtube_music_too(yt):
    turn_on()
    settings.update({"ytmusic": False})
    assert not personal.active() and personal.play(YT) == (False, "off")
    assert personal.flush() and yt.calls == []


def test_turned_off_after_queueing_drops_the_work(yt):
    turn_on()
    settings.update({"yt_personalize": False})
    personal._run(("play", YT, "c" * 16))
    personal._run(("watch", YT, "c" * 16, 10))
    assert yt.calls == [] and personal.status()["dropped"] == 2


# ---------- on: the play-start sequence and heartbeats ----------

def test_play_start_sequence(yt, client, caplog):
    turn_on()
    caplog.set_level("INFO", logger="personal")
    r = client.post("/api/personal/play", data=json.dumps({"id": YT}), content_type="text/plain")  # a beacon
    assert r.status_code == 202 and r.get_json() == {"queued": True}
    assert personal.flush()
    assert yt.kinds() == ["sw", "homepage", "basejs", "player", "videostats"]
    assert settings.get("yt_visitor_id") == VISITOR  # kept for good

    player = yt.calls[3]
    assert player["method"] == "POST" and player["url"].startswith("https://music.youtube.com/youtubei/v1/player")
    assert player["json"]["videoId"] == YT
    assert player["json"]["context"]["client"]["visitorData"] == VISITOR
    assert player["json"]["context"]["client"]["clientName"] == "WEB_REMIX"
    assert player["json"]["playbackContext"] == {"contentPlaybackContext": {"signatureTimestamp": 20745}}
    assert player["headers"]["X-Goog-Visitor-Id"] == VISITOR and player["headers"]["X-YouTube-Client-Name"] == "67"
    assert player["cookies"]["VISITOR_INFO1_LIVE"] == COOKIE

    stats = yt.calls[4]
    assert stats["url"].startswith(STATS + "&ver=2&c=WEB_REMIX&cpn=")
    cpn = parse_qs(urlsplit(stats["url"]).query)["cpn"][0]
    assert re.fullmatch(r"[A-Za-z0-9_-]{16}", cpn)
    assert stats["headers"]["X-Goog-Visitor-Id"] == VISITOR and stats["cookies"]["VISITOR_INFO1_LIVE"] == COOKIE
    assert f"personal play id={YT} player=200 videostats=204" in caplog.text
    assert VISITOR not in caplog.text

    # A second play reuses the kept id and the day's signatureTimestamp: player + videostats only.
    personal.play("ccccccccccc")
    assert personal.flush() and yt.kinds()[5:] == ["player", "videostats"]
    cpn2 = parse_qs(urlsplit(yt.calls[-1]["url"]).query)["cpn"][0]
    assert cpn2 != cpn


def test_heartbeats_throttled_and_tied_to_the_play(yt, client):
    turn_on()
    client.post("/api/personal/play", json={"id": YT})
    assert personal.flush()
    cpn = parse_qs(urlsplit(yt.calls[-1]["url"]).query)["cpn"][0]
    n = len(yt.calls)
    assert client.post("/api/personal/progress", json={"id": YT, "t": 4}).get_json() == {"queued": False, "reason": "throttled"}
    assert client.post("/api/personal/progress", json={"id": "ccccccccccc", "t": 4}).get_json()["reason"] == "no_play"
    assert client.post("/api/personal/progress", json={"id": YT, "t": "x"}).get_json()["reason"] == "bad_time"
    for beat, t in ((1, 10.6), (2, 20)):
        personal._state["play"]["beat"] -= personal.BEAT_S  # 10 s later
        r = client.post("/api/personal/progress", json={"id": YT, "t": t})
        assert r.status_code == 202 and r.get_json() == {"queued": True}
        assert personal.flush() and len(yt.calls) == n + beat
        ping = yt.calls[-1]
        parts = urlsplit(ping["url"])
        assert ping["kind"] == "watchtime" and f"{parts.scheme}://{parts.netloc}{parts.path}" == personal.WATCHTIME
        assert parse_qs(parts.query) == {"ns": ["yt"], "ver": ["2"], "c": ["WEB_REMIX"], "cmt": [str(int(t))],
                                         "docid": [YT], "cpn": [cpn]}
        assert ping["headers"]["X-Goog-Visitor-Id"] == VISITOR
    status = client.get("/api/personal/status").get_json()
    assert status["active"] and status["sent"] == 4 and status["current"]["id"] == YT
    assert VISITOR not in json.dumps(status) and status["visitor"] == settings.mask(VISITOR)


@pytest.mark.parametrize("track_id", ["mc:12345", "nd:abc", "short", ""])
def test_youtube_ids_only(yt, client, track_id):
    turn_on()
    assert client.post("/api/personal/play", json={"id": track_id}).get_json() == {"queued": False,
                                                                                    "reason": "not_youtube"}
    assert personal.flush() and yt.calls == []


def test_bad_bodies(yt, client):
    assert client.post("/api/personal/play", json=["x"]).status_code == 400
    assert client.post("/api/personal/progress", json={"t": 3}).status_code == 400


def test_a_4xx_pauses_everything_for_ten_minutes(yt):
    turn_on()
    yt.status["player"] = 403
    assert personal.play(YT) == (True, None)
    assert personal.flush()
    assert yt.kinds()[-1] == "player" and personal.paused()
    n = len(yt.calls)
    assert personal.play(YT) == (False, "paused") and personal.progress(YT, 30) == (False, "paused")
    assert personal.flush() and len(yt.calls) == n
    assert 590 < personal._state["paused_until"] - time.time() <= 600
    personal._state["paused_until"] = 0  # ten minutes later
    yt.status.clear()
    assert personal.play(YT) == (True, None)
    assert personal.flush() and yt.kinds()[-2:] == ["player", "videostats"]


def test_network_errors_are_dropped_quietly(yt, monkeypatch):
    turn_on()
    settings.update({"yt_visitor_id": VISITOR})

    def boom(*a, **k):
        raise requests.ConnectionError("offline")
    monkeypatch.setattr(yt, "post", boom)
    monkeypatch.setattr(yt, "get", boom)
    assert personal.play(YT) == (True, None)
    assert personal.flush()
    st = personal.status()
    assert st["failed"] >= 1 and not personal.paused() and "ConnectionError" in st["recent"][-1]["error"]


def test_visitor_id_paste_and_reset(yt, client):
    r = client.patch("/api/settings", json={"yt_visitor_id": "not an id"})
    assert r.status_code == 400
    body = client.patch("/api/settings", json={"yt_visitor_id": VISITOR, "yt_personalize": True}).get_json()
    assert body["values"]["yt_visitor_id"] == {"set": True, "masked": settings.mask(VISITOR)}
    row = next(x for x in body["schema"] if x["key"] == "yt_visitor_id")
    assert row["masked"] is True and row["default"] is None
    assert VISITOR not in json.dumps(body)
    personal.play(YT)
    assert personal.flush() and "sw" not in yt.kinds()  # the pasted id is used as is
    body = client.patch("/api/settings", json={"yt_visitor_id": ""}).get_json()  # reset: a new id next time
    assert body["values"]["yt_visitor_id"] == {"set": False, "masked": ""}


def test_failed_visitor_fetch_waits_before_retrying(yt):
    turn_on()
    yt.status["sw"] = yt.status["homepage"] = 503
    assert personal.visitor_id() is None
    n = len(yt.calls)
    assert personal.visitor_id() is None and len(yt.calls) == n  # no new fetch within VISITOR_RETRY


def test_cookie_value_from_visitor_data():
    assert personal._cookie_value(VISITOR) == COOKIE
    assert personal._cookie_value("Cgtnotbase64!!") is None


# ---------- the YouTube Music client carries the kept id ----------

def test_ytmusic_client_uses_the_visitor_id_only_when_on(yt):
    if ytmusic.YTMusic is None:
        pytest.skip("ytmusicapi missing")
    plain = ytmusic._client()
    assert "base_headers" not in plain.__dict__ and ytmusic._yt_visitor is None
    turn_on()
    settings.update({"yt_visitor_id": VISITOR})
    yt_client = ytmusic._client()
    assert yt_client is not plain
    assert yt_client.base_headers["X-Goog-Visitor-Id"] == VISITOR  # no homepage fetch: the cache is filled
    assert yt_client.context["context"]["client"]["visitorData"] == VISITOR
    assert ytmusic._client() is yt_client
    settings.update({"yt_personalize": False})
    back = ytmusic._client()
    assert back is not yt_client and "base_headers" not in back.__dict__
    assert yt.calls == []


# ---------- YouTube Music's own shelves on Home ----------

def _song(n, artist="YT Artist"):
    return {"id": f"y{n:010d}", "title": f"YT {n}", "channel": artist, "artists": [{"name": artist, "id": None}],
            "duration": 200, "source": "yt", "thumb": None}


@pytest.fixture
def home_files(monkeypatch, tmp_path, yt):
    for name in ("LISTENS_FILE", "LIKES_FILE", "DISLIKES_FILE", "HOME_FILE", "BLOCKLIST_FILE"):
        monkeypatch.setattr(config, name, tmp_path / f"{name.lower()}.json")
    monkeypatch.setattr(history, "HISTORY_FILE", tmp_path / "history.json")
    monkeypatch.setattr(ytmusic, "enabled", lambda: True)
    monkeypatch.setattr(ytmusic, "retry_after", lambda max_age=300: None)
    monkeypatch.setattr(home.tiles, "load", lambda: [])
    monkeypatch.setattr(home.charts, "_cache", {})
    rows = [{"title": "Quick picks", "kind": "tracks", "items": [_song(1), _song(2)]},
            {"title": "Mixed for you", "kind": "cards",
             "items": [{"type": "playlist", "id": "RDCLAK5uy_abcdefghijkl", "title": "Mix", "subtitle": None,
                        "thumb": None},
                       {"type": "track", "id": _song(2)["id"], "title": "YT 2", "subtitle": "YT Artist", "thumb": None,
                        "track": _song(2)}]}]
    calls = []
    monkeypatch.setattr(ytmusic, "home", lambda limit=ytmusic.HOME_ROWS: calls.append(limit) or [
        {**r, "items": [dict(i) for i in r["items"]]} for r in rows])
    monkeypatch.setattr(ytmusic, "radio", lambda vid, limit=50: [])
    return calls


def test_home_has_no_youtube_shelves_while_off(home_files):
    doc = home.build()
    assert home_files == [] and doc["personal"] is False
    assert not any(s["id"].startswith("ytm:") for s in home._present(doc, "cache", False, doc["generated_at"])["shelves"])


def test_home_adds_youtube_shelves_when_on_deduped(home_files, client):
    turn_on()
    settings.update({"yt_visitor_id": VISITOR})
    doc = home.refresh()
    assert home_files == [ytmusic.HOME_ROWS] and doc["personal"] is True
    body = client.get("/api/home").get_json()
    ids = [s["id"] for s in body["shelves"]]
    assert ids == ["ytm:quick-picks", "ytm:mixed-for-you"]
    quick, mixed = body["shelves"]
    assert quick["title"] == "Quick picks" and quick["source"] == "ytmusic" and quick["kind"] == "tracks"
    assert quick["items"][0]["kept"] is False
    assert [c["type"] for c in mixed["items"]] == ["playlist"]  # YT 2 is already on the Quick picks shelf
    assert [s["id"] for s in client.get("/api/home?ytm=0").get_json()["shelves"]] == []
    settings.update({"yt_personalize": False})  # flipping the setting makes a rebuild due
    assert home.due(home.load())
