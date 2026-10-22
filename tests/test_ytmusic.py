"""ytmusic.py and its use in the YouTube source. Offline: the YTMusic client is a fake."""
import pytest

import config
import ytmusic
from sources import youtube

SQ = "https://lh3.googleusercontent.com/abc_DEF-1"


def song(vid="dQw4w9WgXcQ", **kw):
    item = {"videoId": vid, "title": "One More Time",
            "artists": [{"name": "Daft Punk", "id": "UC1"}, {"name": "Romanthony", "id": None}],
            "album": {"name": "Discovery", "id": "MPREb_x"}, "duration_seconds": 320,
            "thumbnails": [{"url": SQ + "=w60-h60-l90-rj", "width": 60}, {"url": SQ + "=w120-h120-l90-rj", "width": 120}],
            "isExplicit": False}
    item.update(kw)
    return item


class FakeYT:
    def __init__(self):
        self.calls = []
        self.songs = [song()]
        self.fail = None

    def _rec(self, name, *a, **kw):
        self.calls.append((name, a, kw))
        if self.fail:
            raise self.fail

    def search(self, q, filter=None, limit=20):
        self._rec("search", q, filter=filter, limit=limit)
        return self.songs

    def get_search_suggestions(self, q):
        self._rec("suggest", q)
        return [f"{q} {i}" for i in range(12)] + [{"not": "a string"}]

    def get_watch_playlist(self, videoId=None, playlistId=None, limit=25, radio=False):
        self._rec("watch", videoId=videoId, playlistId=playlistId, limit=limit, radio=radio)
        return {"tracks": [song(videoId or "aaaaaaaaaaa", length="3:34", duration_seconds=None),
                           song("bbbbbbbbbbb", length="1:02:03", duration_seconds=None)]}

    def get_song(self, vid):
        self._rec("song", vid)
        return {"videoDetails": {"videoId": vid, "title": "Never Gonna Give You Up", "author": "Rick Astley",
                                 "lengthSeconds": "213", "channelId": "UC2",
                                 "thumbnail": {"thumbnails": [{"url": "https://i.ytimg.com/vi/x/sddefault.jpg"}]}}}

    def get_playlist(self, list_id, limit=100):
        self._rec("playlist", list_id, limit=limit)
        return {"title": "Mix", "tracks": [song("ccccccccccc"), {"videoId": None, "title": "gone"}]}

    def get_album(self, browse_id):
        self._rec("album", browse_id)
        return {"title": "Discovery", "thumbnails": [{"url": SQ + "=w544-h544-l90-rj", "width": 544}],
                "tracks": [song("ddddddddddd", album="Discovery", thumbnails=None, duration="5:21",
                                duration_seconds=321)]}


@pytest.fixture
def fake(monkeypatch):
    f = FakeYT()
    monkeypatch.setattr(config, "YTMUSIC", True)
    monkeypatch.setattr(ytmusic, "_yt", f)
    ytmusic._suggest_cache.clear()
    ytmusic._radio_cache.clear()
    with ytmusic._art_lock:
        ytmusic._art.clear()
    return f


def test_normalize_full():
    t = ytmusic.normalize(song())
    assert t == {"id": "dQw4w9WgXcQ", "title": "One More Time", "channel": "Daft Punk, Romanthony",
                 "artists": [{"name": "Daft Punk", "id": "UC1"}, {"name": "Romanthony", "id": None}],
                 "album": "Discovery", "album_id": "MPREb_x", "duration": 320,
                 "thumb": SQ + "=w544-h544-l90-rj", "explicit": False, "source": "yt"}


def test_normalize_album_none_and_length():
    t = ytmusic.normalize(song(album=None, duration_seconds=None, length="4:05", isExplicit=True))
    assert t["album"] is None and t["album_id"] is None
    assert t["duration"] == 245 and t["explicit"] is True


@pytest.mark.parametrize("value,secs", [("3:34", 214), ("1:02:03", 3723), (200, 200), ("", None),
                                        ("abc", None), ("1:2:3:4", None), (None, None), (0, None), (True, None)])
def test_parse_duration(value, secs):
    assert ytmusic.parse_duration(value) == secs


@pytest.mark.parametrize("bad", [None, "", "short", "dQw4w9WgXcQ1", "dQw4w9WgXc!", 123])
def test_normalize_skips_bad_ids(bad):
    assert ytmusic.normalize(song(vid=bad)) is None


def test_normalize_thumb_forms():
    s192 = ytmusic.normalize(song(thumbnails=[{"url": "https://yt3.ggpht.com/xyz=s192"}]))
    assert s192["thumb"] == "https://yt3.ggpht.com/xyz=w544-h544-l90-rj"
    video = ytmusic.normalize(song("eeeeeeeeeee", thumbnail=[{"url": "https://i.ytimg.com/vi/e/hq.jpg"}], thumbnails=None))
    assert video["thumb"] == "https://i.ytimg.com/vi/eeeeeeeeeee/mqdefault.jpg"
    assert ytmusic.square_art("eeeeeeeeeee", 300) is None  # 16:9 video art isn't remembered as square
    other = ytmusic.normalize(song("fffffffffff", thumbnails=[{"url": "https://evil.example/x=w60-h60"}]))
    assert other["thumb"].startswith("https://i.ytimg.com/")


def test_square_art_cache_and_bound(fake, monkeypatch):
    ytmusic.normalize(song())
    assert ytmusic.square_art("dQw4w9WgXcQ", 300) == SQ + "=w300-h300-l90-rj"
    assert ytmusic.square_art("zzzzzzzzzzz", 300) is None
    monkeypatch.setattr(ytmusic, "ART_MAX", 3)
    for vid in ("aaaaaaaaaaa", "bbbbbbbbbbb"):
        ytmusic.normalize(song(vid))
    ytmusic.square_art("dQw4w9WgXcQ", 100)  # touch: now most recent
    ytmusic.normalize(song("ccccccccccc"))  # evicts the least recently used, aaaa...
    assert ytmusic.square_art("aaaaaaaaaaa", 100) is None
    assert ytmusic.square_art("dQw4w9WgXcQ", 100)
    assert len(ytmusic._art) == 3


def test_search_uses_songs_filter_and_limit(fake):
    fake.songs = [song(), song("aaaaaaaaaaa"), song("dQw4w9WgXcQ"), song("bbbbbbbbbbb")]
    out = ytmusic.search("daft punk", 2)
    assert [t["id"] for t in out] == ["dQw4w9WgXcQ", "aaaaaaaaaaa"]
    assert fake.calls[0] == ("search", ("daft punk",), {"filter": "songs", "limit": 2})


def test_suggestions_cached_capped_strings(fake):
    out = ytmusic.suggestions(" Daft Pu ")
    assert len(out) == 8 and all(isinstance(s, str) for s in out)
    out.append("mutated")
    assert ytmusic.suggestions("daft pu") == out[:8]
    assert [c[0] for c in fake.calls] == ["suggest"]
    assert ytmusic.suggestions("   ") == []


def test_radio_cached_keeps_order(fake):
    out = ytmusic.radio("dQw4w9WgXcQ")
    assert [t["id"] for t in out] == ["dQw4w9WgXcQ", "bbbbbbbbbbb"]
    assert [t["duration"] for t in out] == [214, 3723]
    out[0]["kept"] = True  # callers mutate their copy only
    again = ytmusic.radio("dQw4w9WgXcQ")
    assert "kept" not in again[0]
    assert len(fake.calls) == 1 and fake.calls[0][2]["radio"] is True and fake.calls[0][2]["limit"] == 50


def test_song_prefers_video_details_then_watch(fake, monkeypatch):
    t = ytmusic.song("dQw4w9WgXcQ")
    assert (t["id"], t["title"], t["channel"], t["duration"]) == ("dQw4w9WgXcQ", "Never Gonna Give You Up", "Rick Astley", 213)
    monkeypatch.setattr(fake, "get_song", lambda vid: {"playabilityStatus": {"status": "ERROR"}})
    t = ytmusic.song("aaaaaaaaaaa")
    assert t["id"] == "aaaaaaaaaaa" and t["album"] == "Discovery"


def test_playlist_album_and_mix(fake):
    title, tracks = ytmusic.playlist("PLxxxxxxxxxxxx")
    assert title == "Mix" and [t["id"] for t in tracks] == ["ccccccccccc"]
    title, tracks = ytmusic.playlist("RDAMVMdQw4w9WgXcQ")
    assert title is None and fake.calls[-1][2]["playlistId"] == "RDAMVMdQw4w9WgXcQ"
    title, tracks = ytmusic.album("MPREb_7ltM34kr0mH")
    assert title == "Discovery"
    assert tracks[0]["album"] == "Discovery" and tracks[0]["album_id"] == "MPREb_7ltM34kr0mH"
    assert tracks[0]["thumb"] == SQ + "=w544-h544-l90-rj" and tracks[0]["duration"] == 321


def test_disabled_raises(fake, monkeypatch):
    monkeypatch.setattr(config, "YTMUSIC", False)
    assert ytmusic.enabled() is False
    with pytest.raises(RuntimeError):
        ytmusic.search("x", 5)
    assert fake.calls == []


# --- YouTube source integration ---

@pytest.fixture
def ytdlp(monkeypatch):
    calls = []

    def fake_search(q, limit):
        calls.append((q, limit))
        return [{"id": "zzzzzzzzzzz", "title": "yt-dlp", "channel": "c", "duration": 1, "thumb": "t"}]

    monkeypatch.setattr(youtube._yt_search, "search", fake_search)
    monkeypatch.setattr(youtube, "search_fallbacks", [])
    return calls


def test_youtube_search_prefers_ytmusic(fake, ytdlp):
    out = youtube.SOURCE.search("daft punk", 5)
    assert out[0]["id"] == "dQw4w9WgXcQ" and out[0]["album"] == "Discovery" and out[0]["source"] == "yt"
    assert ytdlp == [] and youtube.last_backend["search"] == "ytmusic"


def test_youtube_search_falls_back_on_error(fake, ytdlp):
    fake.fail = RuntimeError("boom")
    out = youtube.SOURCE.search("daft punk", 5)
    assert [r["id"] for r in out] == ["zzzzzzzzzzz"] and out[0]["source"] == "yt"
    assert ytdlp == [("daft punk", 5)] and youtube.last_backend["search"] == "yt-dlp"


def test_youtube_search_falls_back_on_empty(fake, ytdlp):
    fake.songs = []
    youtube.SOURCE.search("nothing", 5)
    assert ytdlp == [("nothing", 5)] and youtube.last_backend["search"] == "yt-dlp"


def test_youtube_search_disabled_uses_ytdlp_only(fake, ytdlp, monkeypatch):
    monkeypatch.setattr(config, "YTMUSIC", False)
    youtube.SOURCE.search("x", 3)
    assert fake.calls == [] and ytdlp == [("x", 3)]


def test_youtube_search_ytdlp_failure_uses_piped(fake, monkeypatch):
    fake.fail = RuntimeError("ytm down")

    def broken(q, limit):
        raise RuntimeError("yt-dlp down")

    def piped(q, limit):
        return [{"id": "ppppppppppp", "title": "p"}]

    piped.backend = "piped"
    monkeypatch.setattr(youtube._yt_search, "search", broken)
    monkeypatch.setattr(youtube, "search_fallbacks", [piped])
    assert youtube.SOURCE.search("x", 3)[0]["id"] == "ppppppppppp"
    assert youtube.last_backend["search"] == "piped"


def test_youtube_art_prefers_square(fake):
    assert youtube.SOURCE.art("dQw4w9WgXcQ", 300) == ("redirect", "https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg")
    ytmusic.search("x", 5)
    assert youtube.SOURCE.art("dQw4w9WgXcQ", 300) == ("redirect", SQ + "=w300-h300-l90-rj")
