"""/api/suggest, /api/radio, /api/link, /share and the ytmusic bit of /api/health. Offline: ytmusic is faked."""
from urllib.parse import parse_qs, quote, urlsplit

import pytest

import app
import config
import history
import sources
import streamer
import ytmusic

RICK = "dQw4w9WgXcQ"


class _Calls(list):
    pass


def track(vid, **kw):
    return {"id": vid, "title": "t", "channel": "c", "duration": 1, "thumb": "x", "source": "yt", **kw}


@pytest.fixture
def client():
    return app.app.test_client()


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    """ytmusic on, every network-facing call replaced; `calls` records what was asked."""
    calls = _Calls()
    monkeypatch.setattr(config, "YTMUSIC", True)
    monkeypatch.setattr(ytmusic, "YTMusic", object)  # enabled() also needs the package

    def rec(name, result):
        def f(*a, **kw):
            calls.append((name, a))
            if isinstance(result, Exception):
                raise result
            return result() if callable(result) else result
        return f

    calls.rec = lambda name, result, mod=ytmusic: monkeypatch.setattr(mod, name, rec(name, result))
    calls.rec("song", lambda: track(RICK, title="Never Gonna Give You Up"))
    calls.rec("playlist", lambda: {"title": "Mix", "tracks": [track("aaaaaaaaaaa"), track("bbbbbbbbbbb")]})
    calls.rec("album", lambda: {"title": "Discovery", "tracks": [track("ccccccccccc", album="Discovery")]})
    calls.rec("suggestions", lambda: ["daft punk", "daft punk one more time"])
    calls.rec("radio", lambda: [track(RICK), track("aaaaaaaaaaa"), track("bbbbbbbbbbb"), track("ccccccccccc")])
    calls.rec("lookup", lambda: track(RICK, title="via yt-dlp"), sources.youtube)
    calls.rec("playlist_flat", lambda: ("Flat", [track("ddddddddddd")]), sources.youtube)
    calls.prefetched = []
    monkeypatch.setattr(streamer, "prefetch", lambda ids: calls.prefetched.extend(ids) or len(ids))
    history.HISTORY_FILE.unlink(missing_ok=True)
    yield calls
    history.HISTORY_FILE.unlink(missing_ok=True)


def link(client, url):
    return client.get("/api/link", query_string={"url": url})


# --- /api/suggest ---

def test_suggest(client, offline):
    r = client.get("/api/suggest?q=daft")
    assert r.status_code == 200 and r.get_json() == ["daft punk", "daft punk one more time"]
    assert r.headers["Cache-Control"] == "private, max-age=600"


def test_suggest_empty_and_errors_never_5xx(client, offline, monkeypatch):
    assert client.get("/api/suggest?q=").get_json() == []
    assert client.get("/api/suggest").get_json() == []
    offline.rec("suggestions", RuntimeError("boom"))
    r = client.get("/api/suggest?q=daft")
    assert r.status_code == 200 and r.get_json() == []
    monkeypatch.setattr(config, "YTMUSIC", False)
    assert client.get("/api/suggest?q=daft").get_json() == []


# --- /api/radio ---

def test_radio(client, offline):
    history.add({"id": "aaaaaaaaaaa", "title": "kept"})
    r = client.get(f"/api/radio/{RICK}")
    assert r.status_code == 200
    body = r.get_json()
    assert [t["id"] for t in body][:2] == [RICK, "aaaaaaaaaaa"]
    assert body[1]["kept"] is True and body[0]["kept"] is False
    assert offline.prefetched == ["aaaaaaaaaaa", "bbbbbbbbbbb"]  # first two new ids, not the seed


@pytest.mark.parametrize("bad", ["mc:123", "nd:abc", "short", "zz:dQw4w9WgXcQ"])
def test_radio_non_youtube_404(client, bad):
    assert client.get(f"/api/radio/{bad}").status_code == 404


def test_radio_error_and_disabled_502(client, offline, monkeypatch):
    offline.rec("radio", RuntimeError("upstream"))
    r = client.get(f"/api/radio/{RICK}")
    assert r.status_code == 502 and "upstream" in r.get_json()["error"]
    monkeypatch.setattr(config, "YTMUSIC", False)
    assert client.get(f"/api/radio/{RICK}").status_code == 502


# --- /api/link ---

TRACK_URLS = [
    f"https://www.youtube.com/watch?v={RICK}",
    f"https://youtube.com/watch?v={RICK}&feature=share",
    f"http://m.youtube.com/watch?v={RICK}",
    f"https://music.youtube.com/watch?v={RICK}&si=abc",
    f"https://youtu.be/{RICK}",
    f"https://youtu.be/{RICK}?si=xyz&t=42",
    f"https://www.youtube.com/shorts/{RICK}",
    f"https://www.youtube.com/live/{RICK}?feature=share",
    f"https://www.youtube.com/embed/{RICK}",
    f"youtu.be/{RICK}",
    f"www.youtube.com/watch?v={RICK}",
    f"  {RICK}  ",
]


@pytest.mark.parametrize("url", TRACK_URLS)
def test_link_track_forms(client, offline, url):
    r = link(client, url)
    assert r.status_code == 200, url
    body = r.get_json()
    assert body["kind"] == "track" and body["title"] is None and body["list"] is None
    assert [t["id"] for t in body["tracks"]] == [RICK]
    assert ("song", (RICK,)) in offline


def test_link_watch_with_list_is_track(client, offline):
    body = link(client, f"https://www.youtube.com/watch?v={RICK}&list=PLabcdefghijkl12").get_json()
    assert body["kind"] == "track" and body["list"] == "PLabcdefghijkl12"
    assert body["tracks"][0]["id"] == RICK


@pytest.mark.parametrize("url,list_id", [
    ("https://www.youtube.com/playlist?list=PLabcdefghijkl12", "PLabcdefghijkl12"),
    ("https://music.youtube.com/playlist?list=OLAK5uy_mz6eafmqdRHSaR4IwG0ll6J6rgv0_ZpGw",
     "OLAK5uy_mz6eafmqdRHSaR4IwG0ll6J6rgv0_ZpGw"),
    ("https://www.youtube.com/watch?list=PLabcdefghijkl12", "PLabcdefghijkl12"),
    ("https://music.youtube.com/browse/VLPLabcdefghijkl12", "PLabcdefghijkl12"),
])
def test_link_playlist(client, offline, url, list_id):
    body = link(client, url).get_json()
    assert body == {"kind": "playlist", "title": "Mix",
                    "tracks": [{**track("aaaaaaaaaaa"), "kept": False}, {**track("bbbbbbbbbbb"), "kept": False}]}
    assert ("playlist", (list_id,)) in offline


def test_link_album(client, offline):
    body = link(client, "https://music.youtube.com/browse/MPREb_7ltM34kr0mH").get_json()
    assert body["kind"] == "playlist" and body["title"] == "Discovery"
    assert body["tracks"][0]["album"] == "Discovery"
    assert ("album", ("MPREb_7ltM34kr0mH",)) in offline


def test_link_falls_back_to_ytdlp(client, offline, monkeypatch):
    offline.rec("song", RuntimeError("ytm down"))
    offline.rec("playlist", RuntimeError("ytm down"))
    assert link(client, f"https://youtu.be/{RICK}").get_json()["tracks"][0]["title"] == "via yt-dlp"
    assert link(client, "https://www.youtube.com/playlist?list=PLabcdefghijkl12").get_json()["title"] == "Flat"
    monkeypatch.setattr(config, "YTMUSIC", False)
    del offline[:]
    assert link(client, f"https://youtu.be/{RICK}").status_code == 200
    assert [c[0] for c in offline] == ["lookup"]
    assert link(client, "https://music.youtube.com/browse/MPREb_7ltM34kr0mH").status_code == 502


def test_link_upstream_failure_502(client, offline):
    offline.rec("song", RuntimeError("x"))
    offline.rec("lookup", RuntimeError("video unavailable"), sources.youtube)
    r = link(client, f"https://youtu.be/{RICK}")
    assert r.status_code == 502 and "unavailable" in r.get_json()["error"]



def test_link_unresolvable_video_404(client, offline):
    offline.rec("song", lambda: None)
    offline.rec("lookup", lambda: None, sources.youtube)
    r = link(client, f"https://youtu.be/{RICK}")
    assert r.status_code == 404 and r.get_json() == {"error": "Video not found or unavailable"}
    assert offline.prefetched == []


def test_link_playlist_drops_missing_items(client, offline):
    offline.rec("playlist", lambda: {"title": "Mix", "tracks": [None, track("aaaaaaaaaaa"), None]})
    body = link(client, "https://www.youtube.com/playlist?list=PLabcdefghijkl12").get_json()
    assert [t["id"] for t in body["tracks"]] == ["aaaaaaaaaaa"]
    offline.rec("album", lambda: {"title": "Discovery", "tracks": [track("ccccccccccc"), None]})
    body = link(client, "https://music.youtube.com/browse/MPREb_7ltM34kr0mH").get_json()
    assert [t["id"] for t in body["tracks"]] == ["ccccccccccc"]


@pytest.mark.parametrize("ytm,flat", [([], []), ([], [None]), ([None], [])])
def test_link_empty_playlist_404(client, offline, ytm, flat):
    offline.rec("playlist", lambda: {"title": "Mix", "tracks": ytm})
    offline.rec("playlist_flat", lambda: ("Flat", flat), sources.youtube)
    r = link(client, "https://www.youtube.com/playlist?list=PLabcdefghijkl12")
    assert r.status_code == 404 and r.get_json() == {"error": "Playlist is empty or unavailable"}
    offline.rec("album", lambda: {"title": "Discovery", "tracks": []})
    assert link(client, "https://music.youtube.com/browse/MPREb_7ltM34kr0mH").status_code == 404
    assert offline.prefetched == []

@pytest.mark.parametrize("url", [
    "", "   ", "garbage", "dQw4w9WgXc", "javascript:alert(1)//youtube.com/watch?v=dQw4w9WgXcQ",
    "https://evil.com/watch?v=dQw4w9WgXcQ", "https://youtube.com.evil.com/watch?v=dQw4w9WgXcQ",
    "https://notyoutube.com/watch?v=dQw4w9WgXcQ", "https://youtube.com@evil.com/watch?v=dQw4w9WgXcQ",
    "https://user@youtube.com/watch?v=dQw4w9WgXcQ", "https://youtube.com:8080/watch?v=dQw4w9WgXcQ",
    "ftp://youtube.com/watch?v=dQw4w9WgXcQ", "file:///etc/passwd", "http://127.0.0.1:5274/api/health",
    "https://www.youtube.com/watch?v=short", "https://www.youtube.com/watch", "https://www.youtube.com/",
    "https://www.youtube.com/@daftpunk", "https://www.youtube.com/playlist?list=short",
    "https://www.youtube.com/playlist?list=PL<script>abcdefgh", "https://youtu.be/", "https://youtu.be/a/b",
    "https://music.youtube.com/browse/UCabcdefghijkl", "https://youtube.com:bad/watch?v=dQw4w9WgXcQ",
    "https://www.youtube.com/watch?v=" + RICK + "&x=" + "a" * 2100,
], ids=lambda u: u[:60])
def test_link_rejects(client, offline, url):
    r = link(client, url)
    assert r.status_code == 400, url
    assert "error" in r.get_json()
    assert offline == []


def test_link_missing_param(client, offline):
    assert client.get("/api/link").status_code == 400


# --- /share ---

def _location(r):
    assert r.status_code == 302
    u = urlsplit(r.headers["Location"])
    return u.path, parse_qs(u.query), u.fragment


@pytest.mark.parametrize("args,expected", [
    ({"url": f"https://youtu.be/{RICK}"}, f"https://youtu.be/{RICK}"),
    ({"text": f"Listen: https://music.youtube.com/watch?v={RICK}&si=x."}, f"https://music.youtube.com/watch?v={RICK}&si=x"),
    ({"title": "x", "text": f"see (youtube.com/watch?v={RICK})"}, f"https://youtube.com/watch?v={RICK}"),
])
def test_share_redirects_link(client, args, expected):
    path, qs, frag = _location(client.get("/share", query_string=args))
    assert (path, qs, frag) == ("/", {"link": [expected]}, "search")


def test_share_link_is_encoded(client):
    r = client.get("/share", query_string={"url": f"https://www.youtube.com/watch?v={RICK}&list=PLabcdefghijkl12"})
    assert r.headers["Location"] == "/?link=" + quote(f"https://www.youtube.com/watch?v={RICK}&list=PLabcdefghijkl12", safe="") + "#search"


def test_share_without_link_searches_text(client):
    path, qs, frag = _location(client.get("/share", query_string={
        "title": "ignored", "text": "One More Time by Daft Punk https://open.spotify.com/track/abc"}))
    assert (path, qs, frag) == ("/", {"q": ["One More Time by Daft Punk"]}, "search")
    path, qs, frag = _location(client.get("/share", query_string={"url": "https://evil.com/youtube.com/watch?v=" + RICK}))
    assert (path, qs, frag) == ("/", {}, "search")


# --- /api/health ---

def test_health_reports_ytmusic(client):
    body = client.get("/api/health").get_json()
    assert body["ytmusic"]["enabled"] is True
    assert "version" in body["ytmusic"]
