"""UI remake step 4 (sheets) server bits: Song info's GET /api/stream/<id>/info (resolve cache + kept file, never
the network) and Fix wrong match's POST /api/match/candidates (key, pin, top 5 scored). Offline."""
import json
import wave

import pytest

import app
import config
import history
import matching
import sources
import streamer
import testaudio
import ytmusic

VID = "dQw4w9WgXcQ"
SONG = {"id": "kJQP7kiw5Fk", "title": "Despacito", "channel": "Luis Fonsi", "duration": 229, "source": "yt"}
COVER = {**SONG, "id": "aaaaaaaaaaa", "title": "Despacito (Cover)", "channel": "Someone Else"}
REMIX = {**SONG, "id": "bbbbbbbbbbb", "title": "Despacito - Remix"}
OTHER = {"id": "ccccccccccc", "title": "Gangnam Style", "channel": "PSY", "duration": 219, "source": "yt"}


@pytest.fixture
def client():
    return app.app.test_client()


@pytest.fixture(autouse=True)
def _clean(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "MATCHES_FILE", tmp_path / "matches.json")
    history.HISTORY_FILE.unlink(missing_ok=True)
    with streamer._lock:
        saved = dict(streamer._cache)
        streamer._cache.clear()
    testaudio.setup(path="")
    yield
    history.HISTORY_FILE.unlink(missing_ok=True)
    with streamer._lock:
        streamer._cache.clear()
        streamer._cache.update(saved)


def post(client, path, body):
    return client.post(path, data=json.dumps(body), content_type="application/json")


def cache(track_id, **extra):
    entry = {"url": "https://x.googlevideo.com/videoplayback?itag=140", "headers": {}, "mime": "audio/mp4", "ext": "m4a",
             "duration": 213.0, "client": "web", "has_pot": False, "itag": "140", "lmt": "1", "clen": 3456789,
             "expire": None, "expires": streamer._now() + 600, "gen": 1, **extra}
    with streamer._lock:
        streamer._cache[track_id] = entry


# ---------- GET /api/stream/<id>/info ----------

def test_info_bad_ids_404(client):
    assert client.get("/api/stream/not a track/info").status_code == 404
    assert client.get("/api/stream/short/info").status_code == 404  # a bare id must be an 11-char video id


def test_info_nothing_known(client):
    r = client.get(f"/api/stream/{VID}/info")
    assert r.status_code == 200
    assert r.get_json() == {"id": VID, "source": "yt", "stream": None, "kept": None, "test_audio": False}


def test_info_from_the_resolve_cache_without_resolving(client, monkeypatch):
    monkeypatch.setattr(streamer, "_resolve", lambda *a, **k: pytest.fail("info must never resolve"))
    cache(VID)  # no codec from the source: the itag says AAC 128 kbps 44.1 kHz
    s = client.get(f"/api/stream/{VID}/info").get_json()["stream"]
    assert (s["codec"], s["bitrate"], s["sample_rate"], s["itag"], s["client"], s["size"]) == ("aac", 128, 44100, "140", "web", 3456789)
    assert 0 < s["ttl_s"] <= 600
    cache(VID, itag="251", codec="opus", bitrate=135.7, sample_rate=48000, ext="webm")
    s = client.get(f"/api/stream/{VID}/info").get_json()["stream"]
    assert (s["codec"], s["bitrate"], s["sample_rate"]) == ("opus", 136, 48000)
    cache("mc:123", itag=None, ext="flac", mime="audio/flac")
    s = client.get("/api/stream/mc:123/info").get_json()["stream"]
    assert s["codec"] == "flac" and s["bitrate"] is None
    with streamer._lock:
        streamer._cache[VID]["dead"] = True
    assert client.get(f"/api/stream/{VID}/info").get_json()["stream"] is None


def test_extract_keeps_what_the_source_said(monkeypatch):
    class Src:
        def resolve(self, native, turn=None):
            return {"url": "https://h/x?itag=251", "mime": "audio/webm", "ext": "webm", "duration": 200,
                    "codec": "opus", "bitrate": 160.2, "sample_rate": 48000}
    monkeypatch.setattr(sources, "get", lambda key: Src())
    e = streamer._extract(VID, True)
    assert (e["codec"], e["bitrate"], e["sample_rate"]) == ("opus", 160.2, 48000)


def _wav(path, rate=44100, bits=16, seconds=1):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(bits // 8)
        w.setframerate(rate)
        w.writeframes(b"\0" * (rate * 2 * bits // 8 * seconds))


def test_info_kept_file(client):
    f = config.DOWNLOAD_DIR / "Rick Astley - Never.wav"
    f.parent.mkdir(parents=True, exist_ok=True)
    _wav(f)
    history.add({"id": VID, "title": "Never", "channel": "Rick Astley", "file": str(f), "nd_id": "nd123"})
    try:
        k = client.get(f"/api/stream/{VID}/info").get_json()["kept"]
    finally:
        f.unlink()
    assert k["file"] == f"{config.DOWNLOAD_DIR.name}/Rick Astley - Never.wav"  # relative to the music folder
    assert (k["format"], k["exists"], k["sample_rate"], k["bits"], k["channels"], k["nd_id"]) == ("wav", True, 44100, 16, 2, "nd123")
    assert k["size"] > 0 and k["length"] == 1.0 and k["kept_at"]
    gone = client.get(f"/api/stream/{VID}/info").get_json()["kept"]
    assert gone["exists"] is False and "size" not in gone


def test_info_library_copy_and_upgrade(client):
    history.add({"id": "mc:77", "title": "Never", "channel": "Rick Astley", "file": "/elsewhere/x.flac",
                 "upgraded_from": VID, "nd_id": "n9"})
    cache("nd:n9", itag=None, ext="flac", mime=None)
    body = client.get(f"/api/stream/{VID}/info").get_json()
    assert body["kept"]["upgraded_from"] == VID and body["kept"]["id"] == "mc:77" and body["kept"]["file"] == "x.flac"
    assert body["stream"]["codec"] == "flac"  # the library copy is what plays


def test_info_says_test_audio(client, tmp_path):
    (tmp_path / "a.m4a").write_bytes(b"x" * 300)
    testaudio.setup(path=str(tmp_path), host="127.0.0.1", i_know=False)
    assert client.get(f"/api/stream/{VID}/info").get_json()["test_audio"] is True
    assert client.get("/api/stream/mc:1/info").get_json()["test_audio"] is False


# ---------- POST /api/match/candidates ----------

@pytest.fixture
def ytm(monkeypatch):
    monkeypatch.setattr(config, "YTMUSIC", True)
    monkeypatch.setattr(ytmusic, "YTMusic", object)
    asked = []

    def search(q, limit=10):
        asked.append(q)
        return [dict(t) for t in (COVER, OTHER, REMIX, SONG, {"id": "bad id"}, *[{**OTHER, "id": f"x{k:010d}"} for k in range(4)])]
    monkeypatch.setattr(ytmusic, "search", search)
    return asked


def test_candidates_bad_input(client, ytm):
    assert post(client, "/api/match/candidates", {"artist": "x"}).status_code == 400
    assert post(client, "/api/match/candidates", {"title": "x", "key": "a/b"}).status_code == 400
    assert ytm == []


def test_candidates_scored_best_first(client, ytm):
    r = post(client, "/api/match/candidates", {"title": "Despacito", "artist": "Luis Fonsi", "duration": 229})
    body = r.get_json()
    assert r.status_code == 200 and body["key"] == matching.key_for("Despacito", "Luis Fonsi") and body["pinned"] is None
    ids = [c["track"]["id"] for c in body["candidates"]]
    assert len(ids) == 5 and ids[0] == SONG["id"] and ids[1] == REMIX["id"]  # the remix loses on its version word
    assert body["candidates"][0]["score"] == 1.0 and all(0 <= c["score"] <= 1 for c in body["candidates"])
    assert body["candidates"][1]["ranked"] < body["candidates"][1]["score"]  # the remix's version word costs it
    assert "kept" in body["candidates"][0]["track"] and "bad id" not in ids
    assert ytm == ["Despacito Luis Fonsi"]
    post(client, "/api/match/candidates", {"title": "Despacito", "artist": "Luis Fonsi", "q": "despacito acoustic"})
    assert ytm[-1] == "despacito acoustic"


def test_candidates_key_and_pin(client, ytm):
    matching.remember("pq-row-1", SONG, None, manual=True)
    body = post(client, "/api/match/candidates", {"title": "Despacito", "artist": "Fonsi", "key": "pq-row-1"}).get_json()
    assert body["key"] == "pq-row-1" and body["pinned"]["id"] == SONG["id"] and body["pinned"]["kept"] is False
    matching.remember("pq-row-2", SONG, 0.9)  # automatic: not a pin
    assert post(client, "/api/match/candidates", {"title": "D", "key": "pq-row-2"}).get_json()["pinned"] is None


def test_candidates_search_failure_still_gives_the_key(client, monkeypatch, ytm):
    def boom(q, limit=10):
        raise RuntimeError("HTTP Error 503")
    monkeypatch.setattr(ytmusic, "search", boom)
    body = post(client, "/api/match/candidates", {"title": "Despacito", "artist": "Luis Fonsi"}).get_json()
    assert body["key"] and body["candidates"] == [] and "503" in body["error"]
    monkeypatch.setattr(config, "YTMUSIC", False)
    body = post(client, "/api/match/candidates", {"title": "Despacito"}).get_json()
    assert body["key"] == matching.key_for("Despacito", "") and body["error"] == "YouTube Music is off"


def test_candidate_then_pin_round_trip(client, ytm, monkeypatch):
    monkeypatch.setattr(app, "_link_track", lambda vid: {**SONG} if vid == SONG["id"] else None)
    key = post(client, "/api/match/candidates", {"title": "Despacito", "artist": "Luis Fonsi"}).get_json()["key"]
    r = client.put(f"/api/match/{key}", data=json.dumps({"id": f"https://music.youtube.com/watch?v={SONG['id']}"}),
                   content_type="application/json")
    assert r.status_code == 200 and r.get_json()["manual"] is True
    assert post(client, "/api/match/candidates", {"title": "Despacito", "artist": "Luis Fonsi"}).get_json()["pinned"]["id"] == SONG["id"]
