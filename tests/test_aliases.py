"""Artist aliases (aliases.py, `/api/aliases`): the store and its validation, the resolver (id before name, names
compared like the blocklist's, caches left alone), display names in lists / pages / Home, and the tags of new keeps
(yt-dlp and generic paths) while lyrics lookups keep the source's names."""
import pytest

import aliases
import app
import config
import downloader
import history
import home
import likes
import streamer
import ytmusic
from tagging import music_tags

BEY = "UC" + "b" * 22
OTHER = "UC" + "o" * 22


@pytest.fixture(autouse=True)
def files(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "ALIASES_FILE", tmp_path / "aliases.json")
    monkeypatch.setattr(config, "BLOCKLIST_FILE", tmp_path / "blocklist.json")
    monkeypatch.setattr(config, "LIKES_FILE", tmp_path / "likes.json")
    monkeypatch.setattr(streamer, "prefetch", lambda ids: len(ids))


@pytest.fixture
def client():
    return app.app.test_client()


def tr(vid, artists, channel=None):
    return {"id": vid, "title": "Song", "channel": channel if channel is not None else ", ".join(a for a, _ in artists),
            "artists": [{"name": a, "id": i} for a, i in artists], "duration": 200, "source": "yt"}


# ---------- store and routes ----------

def test_round_trip(client):
    assert client.get("/api/aliases").get_json() == []
    r = client.put("/api/aliases", json={"match": "BeyonceVEVO", "name": "Beyoncé"})
    assert r.status_code == 201 and r.get_json()["entry"]["match"] == "BeyonceVEVO"
    r = client.put("/api/aliases", json={"match": "beyonce - topic", "name": "Beyoncé Knowles", "id": BEY})
    assert r.status_code == 200 and r.get_json()["created"] is False  # same name key: the entry gains the id
    entry = r.get_json()["entry"]
    assert entry["id"] == BEY and entry["name"] == "Beyoncé Knowles"
    assert client.put("/api/aliases", json={"id": OTHER, "name": "Другой"}).status_code == 201
    assert [e["name"] for e in client.get("/api/aliases").get_json()] == ["Beyoncé Knowles", "Другой"]
    assert client.delete("/api/aliases", query_string={"match": "BEYONCE"}).status_code == 204
    assert client.delete("/api/aliases", query_string={"id": OTHER}).status_code == 204
    assert client.delete("/api/aliases", query_string={"id": OTHER}).status_code == 404
    assert client.delete("/api/aliases").status_code == 400
    assert client.get("/api/aliases").get_json() == []


@pytest.mark.parametrize("body", [{}, {"name": "X"}, {"name": "", "match": "a"}, {"name": "X", "match": "  "},
                                  {"name": "X", "id": "UCshort"}, {"name": "x" * 201, "match": "a"}, ["x"]])
def test_put_validation(client, body):
    assert client.put("/api/aliases", json=body).status_code == 400
    assert aliases.entries() == []


# ---------- resolver ----------

def test_resolver_id_first_then_name_key():
    aliases.put("Beyoncé", match="BeyonceVEVO")
    aliases.put("By Id", artist_id=BEY)
    names = aliases.resolver()
    assert names.name("Beyonce - Topic") == "Beyoncé" and names.name("BEYONCÉ") == "BEYONCÉ"
    assert names.name("beyonce", BEY) == "By Id" and names.name("Someone") == "Someone"
    assert not aliases.Resolver([]) and aliases.Resolver([]).name("X") == "X"


def test_track_renames_artists_without_touching_shared_lists():
    aliases.put("Beyoncé", match="BeyonceVEVO")
    shared = [{"name": "Beyonce", "id": None}, {"name": "Jay-Z", "id": None}]
    t = {"id": "aaaaaaaaaaa", "channel": "Beyonce, Jay-Z", "artists": shared}
    aliases.resolver().track(t)
    assert t["channel"] == "Beyoncé, Jay-Z" and t["artists"][0]["name"] == "Beyoncé"
    assert shared[0]["name"] == "Beyonce"  # a radio / playlist cache keeps the source's name
    plain = {"id": "bbbbbbbbbbb", "channel": "BeyonceVEVO"}  # yt-dlp results only know the channel
    assert aliases.resolver().track(plain)["channel"] == "Beyoncé"


# ---------- lists, pages, Home ----------

@pytest.fixture
def ytm(monkeypatch):
    monkeypatch.setattr(config, "YTMUSIC", True)
    monkeypatch.setattr(ytmusic, "YTMusic", object)
    rows = [tr("aaaaaaaaaaa", [("BeyonceVEVO", None)]), tr("ccccccccccc", [("Jay-Z", BEY)])]
    cache = [dict(t) for t in rows]
    monkeypatch.setattr(ytmusic, "search", lambda q, limit=10: [dict(t) for t in rows])
    monkeypatch.setattr(ytmusic, "radio", lambda vid, limit=50: [dict(t) for t in cache])
    monkeypatch.setattr(ytmusic, "album", lambda bid: {"id": bid, "title": "Lemonade", "artist": "BeyonceVEVO",
                                                       "artists": [{"name": "BeyonceVEVO", "id": None}],
                                                       "tracks": [dict(t) for t in rows]})
    monkeypatch.setattr(ytmusic, "artist", lambda cid: {"id": cid, "channel_id": cid, "name": "Jay Z", "songs": [],
                                                        "related": [{"id": OTHER, "name": "Other", "thumb": None}],
                                                        "albums": [], "singles": []})
    return cache


def test_search_radio_and_pages_show_aliases(client, ytm):
    aliases.put("Beyoncé", match="BeyonceVEVO")
    aliases.put("JAY-Z", artist_id=BEY)
    aliases.put("Другой", artist_id=OTHER)
    found = client.get("/api/search", query_string={"q": "x"}).get_json()
    assert [t["channel"] for t in found] == ["Beyoncé", "JAY-Z"]
    assert found[0]["artists"] == [{"name": "Beyoncé", "id": None}]
    radio = client.get("/api/radio/aaaaaaaaaaa").get_json()
    assert [t["channel"] for t in radio] == ["Beyoncé", "JAY-Z"]
    assert ytm[0]["artists"][0]["name"] == "BeyonceVEVO"  # the cached copy is untouched
    album = client.get("/api/album/MPREb_abcdefghij").get_json()
    assert album["artist"] == "Beyoncé" and album["artists"][0]["name"] == "Beyoncé"
    page = client.get(f"/api/artist/{BEY}").get_json()
    assert page["name"] == "JAY-Z" and page["related"][0]["name"] == "Другой"
    client.delete("/api/aliases", query_string={"match": "BeyonceVEVO"})
    assert client.get("/api/search", query_string={"q": "x"}).get_json()[0]["channel"] == "BeyonceVEVO"


def test_home_names_follow_aliases(client, monkeypatch, tmp_path):
    for name in ("LISTENS_FILE", "DISLIKES_FILE", "HOME_FILE"):
        monkeypatch.setattr(config, name, tmp_path / f"{name.lower()}.json")
    monkeypatch.setattr(history, "HISTORY_FILE", tmp_path / "history.json")
    monkeypatch.setattr(home.tiles, "load", lambda: [])
    monkeypatch.setattr(home.charts, "_cache", {})
    seed = tr("aaaaaaaaaaa", [("BeyonceVEVO", None)])
    home._file.save({"generated_at": 1.0, "next_at": 9e12, "source": "fresh", "reason": None, "ytm": False,
                     "seeds": 1, "shelves": [
                         {"id": "on_repeat", "kind": "tracks", "items": [seed]},
                         {"id": "mixed", "kind": "seeds", "items": [{"name": "BeyonceVEVO", "artist_id": None,
                                                                     "seed": seed, "with": ["BeyonceVEVO"]}]},
                         {"id": "top_artists", "kind": "artists", "items": [{"name": "Jay Z", "id": BEY, "count": 3}]}]})
    likes.add({**tr("ddddddddddd", [("BeyonceVEVO", None)]), "album": ""})
    aliases.put("Beyoncé", match="BeyonceVEVO")
    aliases.put("JAY-Z", artist_id=BEY)
    monkeypatch.setattr(ytmusic, "enabled", lambda: False)
    shelves = {s["id"]: s for s in client.get("/api/home").get_json()["shelves"]}
    assert shelves["on_repeat"]["items"][0]["channel"] == "Beyoncé"
    assert shelves["liked"]["items"][0]["channel"] == "Beyoncé"
    mix = shelves["mixed"]["items"][0]
    assert mix["name"] == "Beyoncé" and mix["seed"]["channel"] == "Beyoncé" and mix["with"] == ["Beyoncé"]
    assert shelves["top_artists"]["items"][0]["name"] == "JAY-Z"
    assert home.load()["shelves"][0]["items"][0]["channel"] == "BeyonceVEVO"  # the stored document isn't changed


# ---------- tags of new keeps ----------

def test_music_tags_with_aliases():
    names = aliases.Resolver([{"id": BEY, "match": None, "name": "Beyoncé"},
                              {"id": None, "match": "Jay Z", "name": "JAY-Z"}])
    info = {"title": "Song", "artists": ["Jay Z", "Kanye"], "album_artist": "Jay Z"}
    tags = music_tags(info, names.name)
    assert tags["meta_artist"] == "JAY-Z, Kanye" and tags["meta_album_artist"] == "JAY-Z"
    # Only the uploading channel is known: its id names the artist.
    tags = music_tags({"title": "Song", "uploader": "BeyonceVEVO", "channel_id": BEY}, names.name)
    assert tags["meta_artist"] == "Beyoncé" and tags["meta_album_artist"] == "Beyoncé"
    assert music_tags({"title": "Song", "uploader": "BeyonceVEVO"})["meta_artist"] == "Beyonce"  # no alias given


def test_downloader_tags_apply_aliases_and_hints():
    aliases.put("Beyoncé", match="Beyonce")
    info = {"title": "Halo", "artists": ["Beyonce"], "album": "I Am"}
    tags = downloader._tags(info, {"album_artist": "BeyonceVEVO"})
    assert tags["meta_artist"] == "Beyoncé" and tags["meta_album_artist"] == "Beyoncé"
    raw = downloader._tags(info, {"album_artist": "BeyonceVEVO"}, alias=False)
    assert raw["meta_artist"] == "Beyonce"  # what lyrics are looked up by


def test_generic_keep_tags_the_alias(monkeypatch, tmp_path):
    aliases.put("Beyoncé", match="Beyonce")
    seen = {}

    class Src:
        can_keep = True

        def info(self, native, hints):
            return {"title": "Halo", "artist": "Beyonce", "duration": 200}

    monkeypatch.setattr(downloader.sources, "parse", lambda tid: ("mc", "123"))
    monkeypatch.setattr(downloader.sources, "get", lambda key: Src())
    monkeypatch.setattr(downloader.streamer, "resolve", lambda tid: {"ext": "flac"})
    monkeypatch.setattr(downloader, "_fetch", lambda job, entry, dest: dest.write_bytes(b"x"))
    monkeypatch.setattr(downloader, "_convert", lambda src, ext, out, fmt=None: out.write_bytes(b"y"))
    monkeypatch.setattr(downloader, "_cover_bytes", lambda src, native: (None, None))
    monkeypatch.setattr(downloader, "_length", lambda path: 200.0)
    monkeypatch.setattr(downloader, "_tag", lambda path, title, artist, album, *a: seen.update(artist=artist,
                                                                                               album_artist=a[3]))
    monkeypatch.setattr(downloader, "TMP_DIR", tmp_path)
    monkeypatch.setattr(downloader, "DOWNLOAD_DIR", tmp_path / "Reef")
    (tmp_path / "Reef").mkdir()
    monkeypatch.setattr(downloader.history, "add", lambda entry: seen.update(history=entry))
    job = {"id": "j1", "video_id": "mc:123", "title": "Halo", "channel": "Beyonce",
           "hints": {"album_artist": "Beyonce"}}
    meta = downloader._run_generic(job)
    assert seen["artist"] == "Beyoncé" and seen["album_artist"] == "Beyoncé"
    assert meta["artist"] == "Beyonce" and seen["history"]["channel"] == "Beyonce"  # lyrics / history: source names
