"""ListenBrainz scrobbling: payload shape, the queue (retry, backoff, drops, expiry), no double submits, token
validation, and the token never showing up in answers, logs, diagnostics or backups."""
import json
import logging

import pytest
import requests

import app
import backup
import config
import listens
import scrobble
import settings

TOKEN = "lb-0123456789abcdef-secret"
YT = "dQw4w9WgXcQ"
T0 = 1_760_000_000


class Resp:
    def __init__(self, status=200, body=None, headers=None):
        self.status_code, self._body, self.headers = status, body, headers or {}
        self.reason = "x"

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


class FakeLB:
    """Records submit-listens / validate-token calls; `answers` is a queue of Resp or exceptions (default 200)."""
    headers = {}

    def __init__(self):
        self.posts, self.gets, self.answers = [], [], []
        self.valid = {"code": 200, "message": "Token valid.", "valid": True, "user_name": "marianne"}

    def post(self, url, json=None, headers=None, timeout=None):
        self.posts.append({"url": url, "json": json, "headers": headers})
        a = self.answers.pop(0) if self.answers else Resp(200, {"status": "ok"})
        if isinstance(a, Exception):
            raise a
        return a

    def get(self, url, headers=None, timeout=None):
        self.gets.append({"url": url, "headers": headers})
        return Resp(200, self.valid)


@pytest.fixture(autouse=True)
def files(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "SCROBBLES_FILE", tmp_path / "scrobbles.json")
    monkeypatch.setattr(config, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(config, "LISTENS_FILE", tmp_path / "listens.json")
    monkeypatch.setattr(config, "BACKUP_DIR", tmp_path / "backups")


@pytest.fixture
def lb(monkeypatch):
    fake = FakeLB()
    monkeypatch.setattr(scrobble, "_session", fake)
    return fake


@pytest.fixture
def token():
    settings.update({"listenbrainz_token": TOKEN})


@pytest.fixture
def client():
    return app.app.test_client()


def entry(tid=YT, at=T0, played=200, **kw):
    e = {"id": tid, "title": "Never Gonna Give You Up", "channel": "Rick Astley", "duration": 213, "source": "yt",
         "artists": [{"name": "Rick Astley", "id": "UCuAXFkgsw1L7xaCfnd5JJOw"}], "album": "Whenever You Need Somebody",
         "played_s": played, "started_at": at, "ended_at": at + played, "counted": True, "skipped": False}
    e.update(kw)
    return e


def report(client, at=T0, played=200, **kw):
    body = {"id": YT, "title": "Never Gonna Give You Up", "channel": "Rick Astley", "duration": 213,
            "artists": [{"name": "Rick Astley"}], "album": "Whenever You Need Somebody", "played_s": played,
            "started_at": at, "ended_at": at + played, "source": "yt", **kw}
    return client.post("/api/listens", json=body)


def pending():
    return scrobble._file.load().get("pending") or []


# ---------- payload ----------

def test_payload_shape():
    meta = scrobble.metadata(entry())
    assert meta == {"artist_name": "Rick Astley", "track_name": "Never Gonna Give You Up",
                    "release_name": "Whenever You Need Somebody",
                    "additional_info": {"submission_client": "Reef", "media_player": "Reef", "duration_ms": 213000,
                                        "origin_url": f"https://www.youtube.com/watch?v={YT}",
                                        "music_service": "youtube.com"}}
    body = scrobble.payload([{"listened_at": T0, "track_metadata": meta}])
    assert body == {"listen_type": "single", "payload": [{"listened_at": T0, "track_metadata": meta}]}


def test_payload_from_a_plain_youtube_title_and_other_sources():
    meta = scrobble.metadata(entry(artists=None, title="Rick Astley - Never Gonna Give You Up (Official Video)",
                                   channel="RickAstleyVEVO", album=None, duration=None))
    assert (meta["artist_name"], meta["track_name"]) == ("Rick Astley", "Never Gonna Give You Up")
    assert "release_name" not in meta and "duration_ms" not in meta["additional_info"]
    mc = scrobble.metadata(entry(tid="mc:123", source="mc", artists=None, channel="Daft Punk - Topic"))
    assert mc["artist_name"] == "Daft Punk" and "origin_url" not in mc["additional_info"]
    assert scrobble.metadata(entry(title="", artists=None, channel="")) is None


# ---------- queueing ----------

def test_nothing_is_queued_without_a_token_or_when_disabled(lb):
    assert not scrobble.listened(entry())
    settings.update({"listenbrainz_token": TOKEN, "scrobble_enabled": False})
    assert not scrobble.listened(entry())
    assert pending() == []


def test_skips_are_not_scrobbled(lb, token):
    assert not scrobble.listened(entry(counted=False, played=5))
    assert pending() == []


def test_counted_listen_is_submitted_once_and_marked(client, lb, token):
    r = report(client)
    assert r.status_code == 201 and len(pending()) == 1
    report(client, played=210)  # the player reports the same play again (page hidden, then next track)
    assert len(pending()) == 1
    assert scrobble.flush(now=T0 + 300) == 1
    assert len(lb.posts) == 1
    post = lb.posts[0]
    assert post["url"] == "https://api.listenbrainz.org/1/submit-listens"
    assert post["headers"] == {"Authorization": f"Token {TOKEN}"}
    assert post["json"]["listen_type"] == "single" and post["json"]["payload"][0]["listened_at"] == T0
    assert pending() == []
    stored = listens.entries()[0]
    assert stored["scrobbled"] is True
    # Another report of the same play after the submit: kept as scrobbled, not queued again.
    r = report(client, played=213)
    assert r.get_json()["entry"]["scrobbled"] is True
    assert pending() == [] and scrobble.flush(now=T0 + 400) == 0 and len(lb.posts) == 1
    # Even when the listen log forgot the flag, the remembered key stops a second submit.
    assert not scrobble.listened(entry())


def test_network_errors_back_off_and_retry(lb, token):
    scrobble.listened(entry(), now=T0)
    scrobble.listened(entry(at=T0 + 300), now=T0)
    lb.answers = [requests.ConnectionError("offline")]
    assert scrobble.flush(now=T0) == 0
    data = scrobble._file.load()
    assert len(data["pending"]) == 2 and data["fails"] == 1 and data["retry_at"] == T0 + 30
    assert data["last_error"].startswith("network")
    assert len(lb.posts) == 1  # the round stops at the first failure
    assert scrobble.flush(now=T0 + 10) == 0 and len(lb.posts) == 1  # not due yet
    lb.answers = [Resp(503)]
    scrobble.flush(now=T0 + 31)
    assert scrobble._file.load()["retry_at"] == T0 + 31 + 60  # doubled
    lb.answers = [Resp(429, {"error": "slow down"}, {"Retry-After": "600"})]
    scrobble.flush(now=T0 + 100)
    assert scrobble._file.load()["retry_at"] == T0 + 100 + 600  # the server's wait wins over 120 s
    assert scrobble.flush(now=T0 + 701) == 2
    data = scrobble._file.load()
    assert data["pending"] == [] and data["fails"] == 0 and data["last_error"] is None and data["last_ok_at"] == T0 + 701


def test_backoff_is_capped(lb, token):
    scrobble.listened(entry(), now=T0)
    now = T0
    for _ in range(12):
        lb.answers = [Resp(500)]
        now = max(now, scrobble._file.load().get("retry_at") or 0)
        scrobble.flush(now=now)
    data = scrobble._file.load()
    assert data["retry_at"] - now == scrobble.BACKOFF_MAX and len(data["pending"]) == 1


def test_client_errors_drop_the_listen_and_log(lb, token, caplog):
    scrobble.listened(entry(), now=T0)
    scrobble.listened(entry(at=T0 + 300), now=T0)
    lb.answers = [Resp(400, {"code": 400, "error": "bad listen"})]
    with caplog.at_level(logging.WARNING):
        assert scrobble.flush(now=T0) == 1  # the second one still goes through
    assert pending() == [] and "scrobble drop" in caplog.text and "bad listen" in caplog.text
    assert scrobble.status()["last_error"].startswith("400")


def test_401_keeps_the_listens_until_a_new_token(lb, token):
    scrobble.listened(entry(), now=T0)
    lb.answers = [Resp(401, {"error": "Invalid authorization token."})]
    scrobble.flush(now=T0)
    data = scrobble._file.load()
    assert len(data["pending"]) == 1 and data["token_valid"] is False and data["retry_at"] == T0 + scrobble.BACKOFF_MAX
    settings.update({"listenbrainz_token": TOKEN + "x"})
    scrobble.token_changed()  # validates the new token (FakeLB says valid) and lifts the pause
    assert scrobble._file.load()["retry_at"] == 0
    assert scrobble.flush(now=T0 + 5) == 1


def test_old_listens_are_dropped_after_14_days(lb, token):
    scrobble.listened(entry(), now=T0)
    lb.answers = [requests.ConnectionError("offline")]
    scrobble.flush(now=T0)
    assert scrobble.flush(now=T0 + 15 * 86400) == 0
    assert pending() == [] and len(lb.posts) == 1


def test_queue_is_capped(monkeypatch, lb, token):
    monkeypatch.setattr(scrobble, "QUEUE_MAX", 3)
    for i in range(5):
        scrobble.listened(entry(at=T0 + i * 300), now=T0)
    assert [p["listened_at"] for p in pending()] == [T0 + 600, T0 + 900, T0 + 1200]


# ---------- token: validation, never shown ----------

def test_saving_the_token_validates_it(client, lb):
    r = client.patch("/api/settings", json={"listenbrainz_token": TOKEN})
    assert r.status_code == 200 and TOKEN not in r.get_data(as_text=True)
    assert lb.gets[0]["url"] == "https://api.listenbrainz.org/1/validate-token"
    assert lb.gets[0]["headers"] == {"Authorization": f"Token {TOKEN}"}
    s = client.get("/api/scrobble/status").get_json()
    assert s["user_name"] == "marianne" and s["token_valid"] is True and s["enabled"] is True
    lb.valid = {"code": 200, "message": "Token invalid.", "valid": False}
    client.patch("/api/settings", json={"listenbrainz_token": TOKEN + "zz"})
    s = client.get("/api/scrobble/status").get_json()
    assert s["user_name"] is None and s["token_valid"] is False and "valid" in s["last_error"]


def test_validate_fails_gracefully_offline(client):
    settings.update({"listenbrainz_token": TOKEN})  # conftest's session raises ConnectionError
    s = client.post("/api/scrobble/validate").get_json()
    assert s["token_valid"] is None and s["last_error"].startswith("Couldn't reach ListenBrainz")
    assert TOKEN not in json.dumps(s)


def test_token_never_leaks(client, lb, caplog, monkeypatch):
    with caplog.at_level(logging.INFO):
        client.patch("/api/settings", json={"listenbrainz_token": TOKEN})
        report(client)
        lb.answers = [Resp(500, {"error": "boom"})]
        scrobble.flush()
        scrobble.flush(now=10**10)
    monkeypatch.setattr(app.log, "recent", lambda: caplog.text.splitlines())
    for path in ("/api/scrobble/status", "/api/settings", "/api/diagnostics"):
        assert TOKEN not in client.get(path).get_data(as_text=True), path
    assert TOKEN not in caplog.text
    assert TOKEN not in config.SCROBBLES_FILE.read_text()
    assert TOKEN not in json.dumps(backup.build())
