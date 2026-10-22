"""UI remake step 5 server bits: grouped search (/api/search/all), per-kind search (/api/search?filter=), the
same-origin thumbnail proxy (/api/thumb) and the extra page fields (artist videos / radio ids / audience, album
and playlist descriptions, the Charts shelf's playlist id). Offline: the YTMusic client is a fake."""
import pytest

import app
import blocklist
import config
import history
import streamer
import ytmusic

DAFT = "UCRr1xG_2WIDs18a6cIiCxeA"
WEEKND = "UClYV6hHlupm_S_ObS1W-DYw"
ALBUM = "MPREb_K8qWMWVqXGi"
SQ = "https://lh3.googleusercontent.com/abc"


def thumbs(base=SQ):
    return [{"url": f"{base}=w60-h60-l90-rj", "width": 60}, {"url": f"{base}=w120-h120-l90-rj", "width": 120}]


def song(vid, title="Song", artists=(("Daft Punk", DAFT),), kind="song", **kw):
    return {"resultType": kind, "category": None, "videoId": vid, "title": title,
            "artists": [{"name": n, "id": i} for n, i in artists], "duration_seconds": 200, "thumbnails": thumbs(),
            **kw}


MIXED = [
    {"category": "Top result", "resultType": "artist", "subscribers": "68.7M",
     "artists": [{"name": "Daft Punk", "id": DAFT}], "thumbnails": thumbs(SQ + "-p")},
    {**song("YzTwRiqELqU", "Reflections"), "artists": None},  # the top artist's songs: no artists of their own
    song("OWiVJMgms9E", "Voyager", artists=()),
    {"category": None, "resultType": "artist", "artist": "The Weeknd", "browseId": WEEKND,
     "radioId": "RDEMHSpo_Uv9STIRtF73zMywLg", "shuffleId": "RDAOHSpo_Uv9STIRtF73zMywLg", "thumbnails": thumbs()},
    {"category": None, "resultType": "album", "title": "Random Access Memories", "type": "Album", "year": "2013",
     "artists": [{"name": "Daft Punk", "id": DAFT}], "browseId": ALBUM, "thumbnails": thumbs()},
    song("ARfJ55bUAw0", "Around the World"),
    song("YzTwRiqELqU", "Reflections"),  # a duplicate id is listed once
    {"category": None, "resultType": "playlist", "title": "Daft Punk Sunset Funk", "author": "Dafunk Lounge",
     "itemCount": "40", "browseId": "VLPLIJYHtqRODNEdGQDG4YVoEuVLRBKe4iCg",
     "thumbnails": [{"url": "https://i.ytimg.com/vi/Df19OXuMy68/hqdefault.jpg?sqp=x", "width": 400}]},
    song("ySLc8gZ3oEc", "Veridis Quo (Music Video)", kind="video"),
    {"category": None, "resultType": "episode", "title": "A podcast episode", "videoId": "28JBN18Z0XM"},
    {"category": None, "resultType": "podcast", "title": "A podcast", "browseId": "MPSPPLx"},
    {"category": None, "resultType": "playlist", "title": "no id", "browseId": "nope"},
]


class FakeYT:
    def __init__(self):
        self.calls = []
        self.fail = None
        self.mixed = MIXED

    def search(self, q, filter=None, limit=20):
        self.calls.append((q, filter, limit))
        if self.fail:
            raise self.fail
        if filter is None:
            return self.mixed
        if filter == "albums":
            return [x for x in self.mixed if x["resultType"] == "album"] + [song("ARfJ55bUAw0")]
        if filter == "artists":
            return [x for x in self.mixed if x["resultType"] == "artist" and x.get("browseId")]
        if filter == "playlists":
            return [x for x in self.mixed if x["resultType"] == "playlist"]
        if filter == "videos":
            return [x for x in self.mixed if x["resultType"] == "video"]
        return [x for x in self.mixed if x["resultType"] == "song"]


@pytest.fixture
def fake(monkeypatch, tmp_path):
    f = FakeYT()
    monkeypatch.setattr(config, "YTMUSIC", True)
    monkeypatch.setattr(ytmusic, "YTMusic", object)
    monkeypatch.setattr(ytmusic, "_yt", f)
    monkeypatch.setattr(config, "BLOCKLIST_FILE", tmp_path / "blocklist.json")
    ytmusic._search_cache.clear()
    yield f
    ytmusic._search_cache.clear()
    with ytmusic._art_lock:
        ytmusic._art.clear()


@pytest.fixture
def client(fake, monkeypatch):
    prefetched = []
    monkeypatch.setattr(streamer, "prefetch", lambda ids: prefetched.extend(ids) or len(ids))
    c = app.app.test_client()
    c.prefetched = prefetched
    history.HISTORY_FILE.unlink(missing_ok=True)
    yield c
    history.HISTORY_FILE.unlink(missing_ok=True)


# ---------- ytmusic: grouped search ----------

def test_search_all_groups_by_kind(fake):
    r = ytmusic.search_all("Daft Punk ")
    assert fake.calls == [("daft punk", None, 20)]
    # ytmusicapi's "subscribers" on the top artist is the monthly audience (the artist page has both): named as such
    assert r["top"] == {"type": "artist", "id": DAFT, "title": "Daft Punk", "subtitle": "Artist · 68.7M monthly audience",
                        "thumb": SQ + "-p=w544-h544-p-l90-rj", "audience": "68.7M", "radio_id": None,
                        "shuffle_id": None}
    assert "subscribers" not in r["top"]
    assert [t["id"] for t in r["songs"]] == ["YzTwRiqELqU", "OWiVJMgms9E", "ARfJ55bUAw0"]
    # the songs under the top artist get its name; one that names its own artists keeps them
    assert r["songs"][0]["artists"] == [{"name": "Daft Punk", "id": DAFT}] and r["songs"][0]["channel"] == "Daft Punk"
    assert [a["id"] for a in r["artists"]] == [DAFT, WEEKND]
    assert r["artists"][1]["radio_id"] == "RDEMHSpo_Uv9STIRtF73zMywLg"
    assert r["albums"] == [{"type": "album", "id": ALBUM, "title": "Random Access Memories",
                            "subtitle": "Album · Daft Punk · 2013", "thumb": SQ + "=w544-h544-l90-rj",
                            "artists": [{"name": "Daft Punk", "id": DAFT}], "year": "2013", "kind": "Album"}]
    assert r["playlists"][0]["id"] == "PLIJYHtqRODNEdGQDG4YVoEuVLRBKe4iCg"  # "VL" dropped
    assert r["playlists"][0]["subtitle"] == "Playlist · Dafunk Lounge · 40 songs"
    assert len(r["playlists"]) == 1  # the one without a usable id is left out
    assert [v["id"] for v in r["videos"]] == ["ySLc8gZ3oEc"] and r["videos"][0]["video"] is True
    # cached per query (case / spaces folded) and deep-copied
    r["songs"].clear()
    assert len(ytmusic.search_all("daft punk")["songs"]) == 3 and len(fake.calls) == 1


def test_search_all_without_a_top_block_uses_the_first_song(fake):
    fake.mixed = [song("ARfJ55bUAw0", "Around the World"), song("ySLc8gZ3oEc", kind="video")]
    r = ytmusic.search_all("x")
    assert r["top"]["type"] == "track" and r["top"]["track"]["id"] == "ARfJ55bUAw0"
    fake.mixed = [{**song("4NRXx6U8ABQ", "Blinding Lights (Official Video)", kind="video"), "category": "Top result"}]
    ytmusic._search_cache.clear()
    assert ytmusic.search_all("y")["top"]["track"]["video"] is True


def test_search_kind(fake):
    albums = ytmusic.search_kind("daft punk", "albums")
    assert [a["id"] for a in albums] == [ALBUM] and fake.calls[-1] == ("daft punk", "albums", 20)  # a song is dropped
    assert [a["title"] for a in ytmusic.search_kind("daft punk", "artists")] == ["The Weeknd"]
    assert ytmusic.search_kind("daft punk", "videos")[0]["id"] == "ySLc8gZ3oEc"
    with pytest.raises(ValueError):
        ytmusic.search_kind("x", "songs")


# ---------- routes ----------

def test_api_search_all(client, fake):
    body = client.get("/api/search/all?q=daft+punk").get_json()
    assert body["top"]["type"] == "artist" and [t["id"] for t in body["songs"]][:1] == ["YzTwRiqELqU"]
    assert all(t["kept"] is False and t["liked"] is False and t["source"] == "yt" for t in body["songs"] + body["videos"])
    assert client.prefetched == ["YzTwRiqELqU", "OWiVJMgms9E", "ARfJ55bUAw0"]
    assert "fallback" not in body
    assert body["top"]["audience"] == "68.7M" and body["top"]["subtitle"] == "Artist · 68.7M monthly audience"
    assert client.get("/api/search/all?q=").get_json() == {"top": None, "songs": [], "videos": [], "albums": [],
                                                          "artists": [], "playlists": []}


def test_api_search_all_drops_hidden_artists(client, fake):
    blocklist.add(DAFT, "Daft Punk")
    body = client.get("/api/search/all?q=daft+punk").get_json()
    assert [a["id"] for a in body["artists"]] == [WEEKND]
    assert body["albums"] == [] and body["songs"] == [] and body["videos"] == []
    assert body["top"] is None  # the top artist is hidden and there's no song left to stand in


def test_api_search_all_falls_back_to_songs(client, fake, monkeypatch):
    fake.fail = RuntimeError("ytmusic down")
    import sources
    monkeypatch.setattr(sources.get("yt"), "search", lambda q, n: [
        {"id": "dQw4w9WgXcQ", "title": "Never", "channel": "Rick", "duration": 1}])
    body = client.get("/api/search/all?q=rick").get_json()
    assert body["fallback"] is True and body["top"]["track"]["id"] == "dQw4w9WgXcQ"
    assert body["albums"] == [] and body["songs"][0]["source"] == "yt"


def test_api_search_filter(client, fake):
    assert [a["id"] for a in client.get("/api/search?q=daft&filter=albums").get_json()] == [ALBUM]
    vids = client.get("/api/search?q=daft&filter=videos").get_json()
    assert vids[0]["id"] == "ySLc8gZ3oEc" and vids[0]["kept"] is False
    assert client.get("/api/search?q=daft&filter=nope").status_code == 400
    assert client.get("/api/search?q=&filter=albums").get_json() == []
    fake.fail = RuntimeError("down")
    ytmusic._search_cache.clear()
    assert client.get("/api/search?q=daft&filter=artists").status_code == 502


def test_api_search_filter_off(client, monkeypatch):
    monkeypatch.setattr(config, "YTMUSIC", False)
    assert client.get("/api/search?q=daft&filter=albums").status_code == 502


# ---------- /api/thumb: no open proxy ----------

@pytest.mark.parametrize("raw,want", [
    ("https://lh3.googleusercontent.com/abc=w544-h544-p-l90-rj", "https://lh3.googleusercontent.com/abc=w64-h64-p-l90-rj"),
    ("https://yt3.googleusercontent.com/x=w60-h60-l90-rj", "https://yt3.googleusercontent.com/x=w64-h64-l90-rj"),
    ("https://yt3.ggpht.com/x=s88", "https://yt3.ggpht.com/x=w64-h64-l90-rj"),
    ("https://i.ytimg.com/vi/abc/hqdefault.jpg?sqp=a&rs=b", "https://i.ytimg.com/vi/abc/hqdefault.jpg?sqp=a&rs=b"),
    ("http://i.ytimg.com/vi/abc/hqdefault.jpg", None),            # plain http
    ("https://evil.example/x.jpg", None),                          # another host
    ("https://lh3.googleusercontent.com.evil.example/x", None),    # suffix trick
    ("https://googleusercontent.com/x", None),                     # bare domain
    ("https://i.ytimg.com:8443/x", None),                          # another port
    ("https://user:pw@i.ytimg.com/x", None),                       # credentials
    ("https://127.0.0.1/x", None), ("https://[::1]/x", None),      # local addresses
    ("https://lh3.googleusercontent.com/x?redirect=https://evil", None),  # Google art never has a query
    ("https://i.ytimg.com\\@evil.example/x", None),
    ("file:///etc/passwd", None), ("", None), ("https://i.ytimg.com/" + "a" * 2000, None),
])
def test_thumb_source(raw, want):
    assert app.thumb_source(raw, 64) == want


class FakeResp:
    def __init__(self, mime="image/jpeg", data=b"\xff\xd8jpeg", status=200):
        self.status_code, self.headers, self.data = status, {"Content-Type": mime}, data

    def iter_content(self, n):
        yield self.data

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeSession:
    def __init__(self, resp):
        self.resp, self.calls = resp, []

    def get(self, url, **kw):
        self.calls.append((url, kw.get("allow_redirects")))
        return self.resp


@pytest.fixture
def tiny():
    app._tiny_art.clear()
    yield
    app._tiny_art.clear()


def test_api_thumb_proxies_small_art(client, tiny, monkeypatch):
    sess = FakeSession(FakeResp())
    monkeypatch.setattr(app, "_art_session", sess)
    u = "https://lh3.googleusercontent.com/abc=w544-h544-p-l90-rj"
    for _ in range(2):
        r = client.get("/api/thumb", query_string={"u": u, "size": 64})
        assert r.status_code == 200 and r.data == b"\xff\xd8jpeg" and r.mimetype == "image/jpeg"
        assert r.headers["X-Content-Type-Options"] == "nosniff" and "max-age" in r.headers["Cache-Control"]
    assert sess.calls == [("https://lh3.googleusercontent.com/abc=w64-h64-p-l90-rj", False)]  # once, no redirects


def test_api_thumb_size_is_capped(client, tiny, monkeypatch):
    sess = FakeSession(FakeResp())
    monkeypatch.setattr(app, "_art_session", sess)
    client.get("/api/thumb", query_string={"u": "https://yt3.ggpht.com/x=s88", "size": 900})
    assert sess.calls[0][0] == "https://yt3.ggpht.com/x=w128-h128-l90-rj"


def test_api_thumb_refuses(client, tiny, monkeypatch):
    sess = FakeSession(FakeResp(mime="image/svg+xml", data=b"<svg onload=alert(1)>"))
    monkeypatch.setattr(app, "_art_session", sess)
    assert client.get("/api/thumb", query_string={"u": "https://evil.example/x.jpg"}).status_code == 400
    assert sess.calls == []
    # an SVG (or anything but a raster image) from an allowed host isn't served from Reef's origin
    assert client.get("/api/thumb", query_string={"u": "https://i.ytimg.com/vi/x/a.svg"}).status_code == 404
    monkeypatch.setattr(app, "_art_session", FakeSession(FakeResp(mime="text/html", data=b"<html>")))
    assert client.get("/api/thumb", query_string={"u": "https://i.ytimg.com/vi/y/b.jpg"}).status_code == 404
    monkeypatch.setattr(app, "_art_session", FakeSession(FakeResp(status=302)))
    assert client.get("/api/thumb", query_string={"u": "https://i.ytimg.com/vi/z/c.jpg"}).status_code == 404


# ---------- page extras ----------

class PageYT(FakeYT):
    def get_artist(self, channel_id):
        return {"name": "Daft Punk", "channelId": channel_id, "subscribers": "7.2M", "monthlyListeners": "68.7M",
                "radioId": "RDEMni3jl65KF37F9JYsJM8DGg", "shuffleId": "RDAOni3jl65KF37F9JYsJM8DGg",
                "thumbnails": thumbs(), "songs": {"results": [song("ARfJ55bUAw0")]},
                "videos": {"results": [song("a5uQMwRMHcs", "Instant Crush"),
                                       song("bbbbbbbbbbb", "Hidden", artists=(("The Weeknd", WEEKND),))]}}

    def get_album(self, browse_id):
        return {"title": "RAM", "thumbnails": thumbs(), "artists": [{"name": "Daft Punk", "id": DAFT}],
                "description": "word " * 200, "tracks": [song("ARfJ55bUAw0")]}

    def get_playlist(self, list_id, limit=100):
        return {"title": "Funk", "description": "Listen to the heroes", "trackCount": 63, "tracks": [song("ARfJ55bUAw0")]}


@pytest.fixture
def pages(monkeypatch, fake):
    f = PageYT()
    monkeypatch.setattr(ytmusic, "_yt", f)
    for cache in (ytmusic._album_cache, ytmusic._artist_cache, ytmusic._playlist_cache):
        cache.clear()
    yield f
    for cache in (ytmusic._album_cache, ytmusic._artist_cache, ytmusic._playlist_cache):
        cache.clear()


def test_artist_extras(client, pages):
    blocklist.add(WEEKND, "The Weeknd")
    a = client.get(f"/api/artist/{DAFT}").get_json()
    assert (a["subscribers"], a["monthly_listeners"]) == ("7.2M", "68.7M")
    assert (a["radio_id"], a["shuffle_id"]) == ("RDEMni3jl65KF37F9JYsJM8DGg", "RDAOni3jl65KF37F9JYsJM8DGg")
    assert [v["id"] for v in a["videos"]] == ["a5uQMwRMHcs"]  # a hidden artist's video is dropped
    assert a["videos"][0]["video"] is True and a["videos"][0]["kept"] is False


def test_album_and_playlist_descriptions(pages):
    a = ytmusic.album(ALBUM)
    assert a["description"].endswith("…") and len(a["description"]) <= ytmusic.DESCRIPTION_MAX + 1
    p = ytmusic.playlist("PLIJYHtqRODNEdGQDG4YVoEuVLRBKe4iCg")
    assert p["description"] == "Listen to the heroes" and p["track_count"] == 63
