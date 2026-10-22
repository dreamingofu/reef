"""/api/match + "Wrong song?" override, /api/replace (dead-track recovery) and /api/segments (SponsorBlock), offline."""
import json

import pytest
import requests

import app
import config
import history
import segments
import settings
import sources
import ytmusic

DESPACITO = {"id": "kJQP7kiw5Fk", "title": "Despacito", "channel": "Luis Fonsi, Daddy Yankee",
             "artists": [{"name": "Luis Fonsi", "id": None}, {"name": "Daddy Yankee", "id": None}],
             "duration": 229, "thumb": "x", "source": "yt"}
REMIX = {**DESPACITO, "id": "aaaaaaaaaaa", "title": "Despacito - Remix", "duration": 230}
OTHER = {**DESPACITO, "id": "bbbbbbbbbbb", "title": "Despacito", "duration": 228}
UNRELATED = {"id": "ccccccccccc", "title": "Gangnam Style", "channel": "PSY", "duration": 219, "source": "yt"}


class Search:
    """Stands in for ytmusic.search / a Monochrome search: answers per query, records what was asked."""

    def __init__(self, answers=None, default=(), error=None):
        self.answers, self.default, self.error, self.asked = answers or {}, list(default), error, []

    def __call__(self, q, limit=10):
        self.asked.append(q)
        if self.error:
            raise self.error
        return [dict(t) for t in self.answers.get(q, self.default)]


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "MATCHES_FILE", tmp_path / "matches.json")
    monkeypatch.setattr(config, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(config, "YTMUSIC", True)
    monkeypatch.setattr(ytmusic, "YTMusic", object)  # enabled() also needs the package
    search = Search()
    monkeypatch.setattr(ytmusic, "search", search)
    monkeypatch.setattr(sources, "SOURCES", {k: v for k, v in sources.SOURCES.items() if k != "mc"})
    history.HISTORY_FILE.unlink(missing_ok=True)
    segments.clear()
    yield search
    history.HISTORY_FILE.unlink(missing_ok=True)
    segments.clear()


@pytest.fixture
def client():
    return app.app.test_client()


def post(client, path, body):
    return client.post(path, data=json.dumps(body), content_type="application/json")


# ---------- /api/match ----------

def test_match_returns_best_song_and_caches_it(client, env):
    env.default = [REMIX, OTHER, UNRELATED]
    r = post(client, "/api/match", {"title": "Despacito", "artist": "Luis Fonsi", "duration": 229})
    assert r.status_code == 200
    body = r.get_json()
    assert body["track"]["id"] == "bbbbbbbbbbb" and body["track"]["kept"] is False
    assert body["score"] == pytest.approx(1.0) and body["manual"] is False and body["cached"] is False
    assert body["key"] == "q:luis fonsi|despacito"
    assert env.asked == ["Despacito Luis Fonsi"]
    again = post(client, "/api/match", {"title": "Despacito (feat. Daddy Yankee)", "artist": "Luis Fonsi"}).get_json()
    assert again["cached"] is True and again["track"]["id"] == "bbbbbbbbbbb"
    assert env.asked == ["Despacito Luis Fonsi"]  # same normalised key: no second search


def test_match_tries_title_alone_then_404(client, env):
    env.answers = {"Despacito": [OTHER]}
    r = post(client, "/api/match", {"title": "Despacito", "artist": "L. Fonsi", "duration": 228, "key": "imp:1"})
    assert r.status_code == 200 and r.get_json()["track"]["id"] == "bbbbbbbbbbb"
    assert env.asked == ["Despacito L. Fonsi", "Despacito"]
    env.default, env.answers = [UNRELATED], {}
    r = post(client, "/api/match", {"title": "Nothing like it", "artist": "Nobody"})
    assert r.status_code == 404 and r.get_json()["key"] == "q:nobody|nothing like it"


@pytest.mark.parametrize("body", [{}, {"title": ""}, {"title": 3}, {"title": "x", "artist": 5}, [1],
                                  {"title": "x", "key": "a/b"}, {"title": "x", "key": ""}])
def test_match_validates(client, env, body):
    assert post(client, "/api/match", body).status_code == 400


def test_match_errors_and_off(client, env, monkeypatch):
    env.error = RuntimeError("HTTP 429")
    assert post(client, "/api/match", {"title": "x"}).status_code == 502
    monkeypatch.setattr(config, "YTMUSIC", False)
    assert post(client, "/api/match", {"title": "x"}).status_code == 502


def test_manual_fix_with_pasted_link_wins(client, env, monkeypatch):
    env.default = [OTHER]
    key = post(client, "/api/match", {"title": "Despacito", "artist": "Luis Fonsi"}).get_json()["key"]
    looked = []
    monkeypatch.setattr(app, "_link_track", lambda vid: looked.append(vid) or {**DESPACITO, "id": vid})
    r = client.put(f"/api/match/{key}", json={"id": "https://music.youtube.com/watch?v=kJQP7kiw5Fk&si=abc"})
    assert r.status_code == 200
    body = r.get_json()
    assert body["track"]["id"] == "kJQP7kiw5Fk" and body["manual"] is True and looked == ["kJQP7kiw5Fk"]
    # the automatic matcher answers from the fix from now on, and can't overwrite it
    again = post(client, "/api/match", {"title": "Despacito", "artist": "Luis Fonsi"}).get_json()
    assert again["track"]["id"] == "kJQP7kiw5Fk" and again["manual"] is True and again["cached"] is True
    assert post(client, "/api/replace", {"track": OTHER, "key": key}).status_code in (200, 404)
    assert post(client, "/api/match", {"title": "Despacito", "artist": "Luis Fonsi"}).get_json()["track"]["id"] == \
        "kJQP7kiw5Fk"


def test_manual_fix_without_lookup_and_bad_input(client, env, monkeypatch):
    def boom(vid):
        raise RuntimeError("offline")
    monkeypatch.setattr(app, "_link_track", boom)
    r = client.put("/api/match/imp:7", json={"id": "youtu.be/dQw4w9WgXcQ", "title": "Never", "artist": "Rick"})
    assert r.status_code == 200 and r.get_json()["track"]["id"] == "dQw4w9WgXcQ"
    assert r.get_json()["track"]["title"] == "Never"
    assert client.put("/api/match/imp:7", json={"id": "mc:123"}).get_json()["track"]["id"] == "mc:123"
    assert client.put("/api/match/imp:7", json={"id": "https://example.com/x"}).status_code == 400
    assert client.put("/api/match/imp:7", json={}).status_code == 400
    assert client.put("/api/match/" + "x" * 201, json={"id": "dQw4w9WgXcQ"}).status_code == 404


# ---------- /api/replace ----------

DEAD = {**DESPACITO, "id": "deaddeadead"}


def test_replace_excludes_dead_id_and_picks_same_song(client, env):
    env.default = [{**DEAD}, REMIX, OTHER]
    r = post(client, "/api/replace", {"track": DEAD})
    assert r.status_code == 200
    body = r.get_json()
    assert body["track"]["id"] == "bbbbbbbbbbb" and body["source"] == "yt" and body["replaced"] == "deaddeadead"
    assert body["score"] >= app.REPLACE_ACCEPT
    # ids already tried come back excluded too
    r = post(client, "/api/replace", {"track": DEAD, "exclude": ["bbbbbbbbbbb", 5, "nope"]})
    assert r.get_json()["track"]["id"] == "aaaaaaaaaaa"  # the remix is all that's left (ranked lower, still a match)


def test_replace_threshold_is_stricter_than_match(client, env):
    weak = {"id": "eeeeeeeeeee", "title": "Despacitos", "channel": "Street Band", "duration": 400}
    env.default = [weak]
    import matching
    raw, _ = matching.score(matching.query_of(DEAD), weak)
    assert matching.ACCEPT <= raw < app.REPLACE_ACCEPT  # good enough for a match, not for a replacement
    assert post(client, "/api/replace", {"track": DEAD}).status_code == 404


def test_replace_uses_monochrome_when_youtube_falls_short(client, env, monkeypatch):
    env.default = []

    class FakeMC:
        key, name, can_keep = "mc", "Monochrome", True
        search = Search(default=[{"id": "mc:42", "title": "Despacito", "channel": "Luis Fonsi, Daddy Yankee",
                                  "duration": 229, "source": "mc"}])
    monkeypatch.setitem(sources.SOURCES, "mc", FakeMC())
    body = post(client, "/api/replace", {"track": DEAD}).get_json()
    assert body["track"]["id"] == "mc:42" and body["source"] == "mc"
    # a near-certain YouTube Music hit never asks Monochrome
    FakeMC.search.asked.clear()
    env.default = [OTHER]
    assert post(client, "/api/replace", {"track": DEAD}).get_json()["source"] == "yt"
    assert FakeMC.search.asked == []


def test_replace_errors(client, env, monkeypatch):
    assert post(client, "/api/replace", {"track": {"id": "nope", "title": "x"}}).status_code == 400
    assert post(client, "/api/replace", {"track": {"id": "dQw4w9WgXcQ", "title": ""}}).status_code == 400
    assert post(client, "/api/replace", {"track": {"id": "nd:1", "title": "x"}}).status_code == 400
    assert post(client, "/api/replace", {}).status_code == 400
    env.error = requests.ConnectionError("offline")
    assert post(client, "/api/replace", {"track": DEAD}).status_code == 502  # couldn't ask: not "no such song"


def test_replace_updates_an_import_key(client, env):
    env.default = [OTHER]
    r = post(client, "/api/replace", {"track": DEAD, "key": "imp:9"})
    assert r.status_code == 200
    assert post(client, "/api/match", {"title": "whatever", "key": "imp:9"}).get_json()["track"]["id"] == "bbbbbbbbbbb"


# ---------- SponsorBlock ----------

class SB:
    """Fake SponsorBlock session: records the URL + params, answers with `status` / `body`."""

    def __init__(self, status=200, body=None, error=None):
        self.status, self.body, self.error, self.calls = status, body, error, []

    def get(self, url, params=None, **kw):
        self.calls.append((url, params))
        if self.error:
            raise self.error
        resp = requests.Response()
        resp.status_code = self.status
        resp._content = json.dumps(self.body).encode() if self.body is not None else b"oops"
        return resp


BUCKET = [
    {"videoID": "zzzzzzzzzzz", "segments": [{"category": "music_offtopic", "actionType": "skip", "segment": [0, 50]}]},
    {"videoID": "kJQP7kiw5Fk", "segments": [
        {"category": "music_offtopic", "actionType": "skip", "segment": [249.38, 281.52]},
        {"category": "music_offtopic", "actionType": "skip", "segment": [0, 21.808434]},
        {"category": "sponsor", "actionType": "skip", "segment": [30, 40]},
        {"category": "music_offtopic", "actionType": "mute", "segment": [50, 60]},
        {"category": "music_offtopic", "actionType": "skip", "segment": [70, 70.5]},  # too short to seek for
        {"category": "music_offtopic", "actionType": "skip", "segment": "junk"},
    ]},
]


def test_segments_hash_prefix_and_pick(client, env, monkeypatch):
    sb = SB(body=BUCKET)
    monkeypatch.setattr(segments, "_session", sb)
    r = client.get("/api/segments/kJQP7kiw5Fk")
    assert r.status_code == 200
    assert r.get_json() == [{"start": 0.0, "end": 21.808, "category": "music_offtopic"},
                            {"start": 249.38, "end": 281.52, "category": "music_offtopic"}]
    url, params = sb.calls[0]
    assert url == "https://sponsor.ajay.app/api/skipSegments/f1d9"  # sha256("kJQP7kiw5Fk")[:4], never the id
    assert "kJQP7kiw5Fk" not in url and "kJQP7kiw5Fk" not in json.dumps(params)
    assert params == {"categories": '["music_offtopic"]', "actionTypes": '["skip"]', "service": "YouTube"}
    client.get("/api/segments/kJQP7kiw5Fk")
    assert len(sb.calls) == 1  # cached


def test_segments_none_is_cached_errors_briefly(client, env, monkeypatch):
    sb = SB(status=404)
    monkeypatch.setattr(segments, "_session", sb)
    assert client.get("/api/segments/dQw4w9WgXcQ").get_json() == []
    assert client.get("/api/segments/dQw4w9WgXcQ").get_json() == []
    assert len(sb.calls) == 1
    sb2 = SB(body=BUCKET)  # in the bucket answer, but not our video: none
    monkeypatch.setattr(segments, "_session", sb2)
    assert client.get("/api/segments/FGBhQbmPwH8").get_json() == []

    clock = [1000.0]
    monkeypatch.setattr(segments.time, "monotonic", lambda: clock[0])
    bad = SB(status=503)
    monkeypatch.setattr(segments, "_session", bad)
    assert client.get("/api/segments/L93-7vRfxNs").status_code == 502
    assert client.get("/api/segments/L93-7vRfxNs").status_code == 502
    assert len(bad.calls) == 1  # negative-cached for ERROR_TTL
    clock[0] += segments.ERROR_TTL + 1
    monkeypatch.setattr(segments, "_session", SB(body=BUCKET))
    assert client.get("/api/segments/L93-7vRfxNs").get_json() == []  # asked again after the short cache
    for err in (SB(error=requests.Timeout("slow")), SB(status=429), SB(status=200, body=None)):
        segments.clear()
        monkeypatch.setattr(segments, "_session", err)
        assert client.get("/api/segments/L93-7vRfxNs").status_code == 502


def test_segments_only_youtube_and_setting(client, env, monkeypatch):
    sb = SB(body=BUCKET)
    monkeypatch.setattr(segments, "_session", sb)
    assert client.get("/api/segments/mc:123").status_code == 404
    assert client.get("/api/segments/nd:abc").status_code == 404
    settings.update({"sponsorblock_play": False})
    assert client.get("/api/segments/kJQP7kiw5Fk").get_json() == []
    assert sb.calls == []


def test_replace_never_plays_another_artists_song(client, env):
    cover = {**OTHER, "id": "fffffffffff", "channel": "Some Cover Band", "artists": [{"name": "Some Cover Band"}]}
    env.default = [cover]
    assert post(client, "/api/replace", {"track": DEAD}).status_code == 404
    assert post(client, "/api/match", {"title": "Despacito", "artist": "Luis Fonsi"}).status_code == 200  # a match may
