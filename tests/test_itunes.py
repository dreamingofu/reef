"""iTunes suggestions (production plan P2): mapping, dedupe, cap, cache, the failure pause, and /api/suggest falling
back to it when YouTube Music is off or failing (setting `itunes_suggestions`). Offline: the session is faked."""
import pytest
import requests

import app
import config
import itunes
import settings
import ytmusic

RESULTS = [
    {"wrapperType": "artist", "artistName": "Daft Punk"},
    {"wrapperType": "track", "kind": "song", "artistName": "Daft Punk", "trackName": "One More Time"},
    {"wrapperType": "track", "kind": "song", "artistName": "Daft Punk", "trackName": "one more  time"},  # dupe
    {"wrapperType": "track", "kind": "music-video", "artistName": "Daft Punk", "trackName": "Around the World"},
    {"wrapperType": "collection", "collectionName": "Discovery"},
    *({"wrapperType": "track", "kind": "song", "artistName": "Daft Punk", "trackName": f"Song {i}"} for i in range(10)),
]


class Resp:
    def __init__(self, status=200, body=None):
        self.status_code, self.body = status, body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code), response=self)

    def json(self):
        return self.body


class Session:
    def __init__(self, *answers):
        self.answers, self.calls = list(answers), []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params, timeout))
        a = self.answers.pop(0)
        if isinstance(a, Exception):
            raise a
        return a


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(ytmusic, "YTMusic", object)

    def use(*answers):
        s = Session(*answers)
        monkeypatch.setattr(itunes, "_session", s)
        return s
    return use


def test_mapping_dedupe_cap_and_cache(env):
    s = env(Resp(body={"results": RESULTS}))
    out = itunes.suggestions("  Daft  PUNK ")
    assert out[:2] == ["Daft Punk", "Daft Punk - One More Time"] and len(out) == 8
    assert "Daft Punk - Around the World" not in out  # music videos aren't songs
    url, params, timeout = s.calls[0]
    assert url == "https://itunes.apple.com/search" and timeout == 3
    assert params == {"term": "daft punk", "media": "music", "entity": "song,musicArtist", "limit": 10}
    assert itunes.suggestions("daft punk") == out and len(s.calls) == 1  # cached


def test_failure_answers_empty_and_pauses(env):
    s = env(requests.Timeout("slow"), Resp(body={"results": RESULTS}))
    assert itunes.suggestions("x") == []
    assert itunes.suggestions("y") == [] and len(s.calls) == 1  # paused: no request storm per keystroke
    itunes.clear()
    assert itunes.suggestions("y")[0] == "Daft Punk"
    s = env(Resp(503))
    itunes.clear()
    assert itunes.suggestions("z") == []
    env(Resp(body="not a dict"))
    itunes.clear()
    assert itunes.suggestions("w") == []


def test_suggest_falls_back_when_ytmusic_is_off_or_failing(env, monkeypatch):
    client = app.app.test_client()
    monkeypatch.setattr(ytmusic, "suggestions", lambda q: ["from youtube music"])
    s = env(Resp(body={"results": RESULTS[:2]}), Resp(body={"results": RESULTS[1:2]}))
    settings.update({"ytmusic": True})
    assert client.get("/api/suggest?q=daft").get_json() == ["from youtube music"] and not s.calls
    settings.update({"ytmusic": False})
    r = client.get("/api/suggest?q=daft")
    assert r.get_json() == ["Daft Punk", "Daft Punk - One More Time"]
    assert r.headers["Cache-Control"] == "private, max-age=600"

    def boom(q):
        raise RuntimeError("ytmusic down")
    monkeypatch.setattr(ytmusic, "suggestions", boom)
    settings.update({"ytmusic": True})
    assert client.get("/api/suggest?q=one more").get_json() == ["Daft Punk - One More Time"]
    settings.update({"itunes_suggestions": False})
    assert client.get("/api/suggest?q=else").get_json() == [] and len(s.calls) == 2
    assert client.get("/api/suggest?q=").get_json() == []


def test_setting_schema():
    row = next(r for r in settings.schema() if r["key"] == "itunes_suggestions")
    assert row["type"] == "bool" and row["default"] is True
