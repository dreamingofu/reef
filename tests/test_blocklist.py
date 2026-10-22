"""Blocked artists: the list itself, its routes, and filtering of search / radio / link playlists / tiles."""
import json
import threading

import pytest

import app
import blocklist
import config
import sources
import streamer
import tiles
import ytmusic

DAFT = "UCRr1xG_2WIDs18a6cIiCxeA"
ROMAN = "UCaaaaaaaaaaaaaaaaaaaaaa"


def track(vid, artists=None, channel=""):
    t = {"id": vid, "title": "t", "channel": channel, "duration": 1, "thumb": "x", "source": "yt"}
    if artists is not None:
        t["artists"] = [{"name": n, "id": i} for n, i in artists]
        t["channel"] = ", ".join(n for n, _ in artists)
    return t


@pytest.fixture(autouse=True)
def blfile(monkeypatch, tmp_path):
    path = tmp_path / "blocklist.json"
    monkeypatch.setattr(config, "BLOCKLIST_FILE", path)
    return path


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(streamer, "prefetch", lambda ids: len(ids))
    return app.app.test_client()


# ---------- the list ----------

def test_add_dedupes_by_id_or_name_and_persists(blfile):
    e, created = blocklist.add(None, "  Daft Punk ")
    assert created and e["name"] == "Daft Punk" and e["id"] is None and isinstance(e["at"], float)
    again, created = blocklist.add(DAFT, "DAFT PUNK")
    assert not created and again["id"] == DAFT  # gains the id it lacked
    assert not blocklist.add(DAFT, "Thomas & Guy")[1]  # same id, another spelling
    assert blocklist.add(ROMAN, "Romanthony")[1]
    saved = json.loads(blfile.read_text(encoding="utf-8"))
    assert [(x["id"], x["name"]) for x in saved] == [(DAFT, "Daft Punk"), (ROMAN, "Romanthony")]
    assert not blfile.with_name("blocklist.json.tmp").exists()


def test_remove_by_id_or_name(blfile):
    blocklist.add(DAFT, "Daft Punk")
    blocklist.add(None, "Romanthony")
    assert blocklist.remove(name="romanthony") and not blocklist.remove(name="romanthony")
    assert blocklist.remove(artist_id=DAFT) and blocklist.entries() == []
    assert not blocklist.remove()


def test_corrupt_file_reads_as_empty(blfile):
    blfile.write_text("{nope", encoding="utf-8")
    assert blocklist.entries() == [] and not blocklist.matcher()
    blfile.write_text(json.dumps([{"name": "ok"}, {"id": "x"}, "junk"]), encoding="utf-8")
    assert [e["name"] for e in blocklist.entries()] == ["ok"]


def test_concurrent_adds_keep_every_entry():
    start = threading.Barrier(12)

    def add(i):
        start.wait()
        blocklist.add(None, f"Artist {i}")
    threads = [threading.Thread(target=add, args=(i,)) for i in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(blocklist.entries()) == 12


def test_matcher_rules():
    blocklist.add(DAFT, "Daft Punk")
    blocklist.add(None, "Rick Astley")
    blocklist.add(None, "!!!")
    m = blocklist.matcher()
    assert m.track(track("a" * 11, [("Someone", None), ("Anything", DAFT)]))           # by id
    assert m.track(track("a" * 11, [("RICK ASTLEY", "UCzzzzzzzzzzzzzzzzzzzzzz")]))     # by name
    assert not m.track(track("a" * 11, [("Rick Astleys", None)]))
    # yt-dlp results: the channel, without " - Topic" / VEVO
    for channel in ("Daft Punk - Topic", "DaftPunkVEVO", "Rick Astley", "rickastley", "!!!"):
        assert m.track(track("a" * 11, channel=channel)), channel
    assert not m.track(track("a" * 11, channel="Daft Punk Fan Club"))
    # A track with artists is judged by them, not by the joined channel string.
    assert not m.track(track("a" * 11, [("Daft", None), ("Punk", None)]))
    assert not blocklist.Matcher([]) and blocklist.Matcher([]).tracks([1, 2]) == [1, 2]


# ---------- routes ----------

def test_routes(client):
    assert client.get("/api/blocklist").get_json() == []
    r = client.post("/api/blocklist", json={"id": DAFT, "name": "Daft Punk"})
    assert r.status_code == 201 and r.get_json()["created"] is True and r.get_json()["entry"]["id"] == DAFT
    r = client.post("/api/blocklist", json={"name": "daft punk"})
    assert r.status_code == 200 and r.get_json()["created"] is False
    assert client.post("/api/blocklist", json={"name": "Romanthony"}).status_code == 201
    assert [e["name"] for e in client.get("/api/blocklist").get_json()] == ["Daft Punk", "Romanthony"]
    assert client.delete("/api/blocklist", query_string={"name": "ROMANTHONY"}).status_code == 204
    assert client.delete("/api/blocklist", query_string={"id": DAFT}).status_code == 204
    assert client.delete("/api/blocklist", query_string={"id": DAFT}).status_code == 404
    assert client.delete("/api/blocklist").status_code == 400


@pytest.mark.parametrize("body", [{}, {"name": ""}, {"name": "   "}, {"name": 5}, {"name": "x" * 201},
                                  {"name": "A", "id": "UCshort"}, {"name": "A", "id": 7}])
def test_add_route_validation(client, body):
    assert client.post("/api/blocklist", json=body).status_code == 400
    assert blocklist.entries() == []


# ---------- filtering at the boundary ----------

class _Results(list):
    pass


@pytest.fixture
def ytm(monkeypatch):
    monkeypatch.setattr(config, "YTMUSIC", True)
    monkeypatch.setattr(ytmusic, "YTMusic", object)
    results = _Results([track("aaaaaaaaaaa", [("Daft Punk", DAFT)]), track("bbbbbbbbbbb", [("Justice", None)]),
                        track("ccccccccccc", [("Justice", None), ("Romanthony", ROMAN)])])
    results.limits = []
    monkeypatch.setattr(ytmusic, "search", lambda q, limit=10: results.limits.append(limit) or [dict(t) for t in results])
    monkeypatch.setattr(ytmusic, "radio", lambda vid, limit=50: [dict(t) for t in results])
    monkeypatch.setattr(ytmusic, "playlist", lambda list_id: {"title": "Mix", "tracks": [dict(t) for t in results]})
    monkeypatch.setattr(ytmusic, "song", lambda vid: track(vid, [("Daft Punk", DAFT)]))
    return results


def test_search_block_then_unblock(client, ytm):
    ids = lambda: [t["id"] for t in client.get("/api/search", query_string={"q": "x"}).get_json()]
    assert ids() == ["aaaaaaaaaaa", "bbbbbbbbbbb", "ccccccccccc"]
    client.post("/api/blocklist", json={"id": DAFT, "name": "Daft Punk"})
    client.post("/api/blocklist", json={"name": "Romanthony"})
    assert ids() == ["bbbbbbbbbbb"]
    assert ytm.limits == [config.SEARCH_LIMIT, config.SEARCH_LIMIT * 2]  # over-fetch while artists are blocked
    client.delete("/api/blocklist", query_string={"id": DAFT})
    assert ids() == ["aaaaaaaaaaa", "bbbbbbbbbbb"]


def test_search_ytdlp_fallback_filtered_by_channel(client, monkeypatch):
    monkeypatch.setattr(config, "YTMUSIC", False)
    monkeypatch.setattr(sources.youtube._yt_search, "search", lambda q, limit: [
        track("aaaaaaaaaaa", channel="Daft Punk - Topic"), track("bbbbbbbbbbb", channel="Other")])
    blocklist.add(DAFT, "Daft Punk")
    assert [t["id"] for t in client.get("/api/search", query_string={"q": "x"}).get_json()] == ["bbbbbbbbbbb"]


def test_radio_and_link_playlist_filtered_single_link_not(client, ytm):
    blocklist.add(DAFT, "Daft Punk")
    assert [t["id"] for t in client.get("/api/radio/aaaaaaaaaaa").get_json()] == ["bbbbbbbbbbb", "ccccccccccc"]
    body = client.get("/api/link", query_string={"url": "https://www.youtube.com/playlist?list=PLabcdefghijkl12"})
    assert [t["id"] for t in body.get_json()["tracks"]] == ["bbbbbbbbbbb", "ccccccccccc"]
    body = client.get("/api/link", query_string={"url": "https://youtu.be/aaaaaaaaaaa"}).get_json()
    assert body["tracks"][0]["id"] == "aaaaaaaaaaa"  # a pasted single track is explicit


def test_link_playlist_all_blocked_404(client, ytm):
    for name in ("Daft Punk", "Justice"):
        blocklist.add(None, name)
    r = client.get("/api/link", query_string={"url": "https://www.youtube.com/playlist?list=PLabcdefghijkl12"})
    assert r.status_code == 404


def test_search_tiles_skip_blocked(monkeypatch, tmp_path, ytm):
    monkeypatch.setattr(config, "YTMUSIC", False)
    monkeypatch.setattr(tiles, "TILES_FILE", tmp_path / "tiles.json")
    monkeypatch.setattr(tiles.youtube.SOURCE, "search", lambda q, limit: [dict(t) for t in ytm])
    blocklist.add(DAFT, "Daft Punk")
    tiles.refresh()
    assert all(t["ids"] == ["bbbbbbbbbbb", "ccccccccccc"] for t in tiles.load())
