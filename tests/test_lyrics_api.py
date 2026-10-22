"""GET /api/lyrics/<id> and navidrome.lyrics. Offline: lyrics.for_track and the Subsonic call are faked."""
import pytest

import app
import history
import lyrics
import navidrome
import sources
from sources import navidrome_src

FOUND = {"synced": "[00:01.00]a\n[00:02.00]b\n[00:03.00]c", "plain": None, "instrumental": False}


@pytest.fixture
def client():
    return app.app.test_client()


@pytest.fixture(autouse=True)
def clean_history():
    history.HISTORY_FILE.unlink(missing_ok=True)
    yield
    history.HISTORY_FILE.unlink(missing_ok=True)


class _Calls(list):
    result = FOUND


@pytest.fixture
def calls(monkeypatch):
    """Records lyrics.for_track calls; answers with `calls.result`."""
    rec = _Calls()

    def fake(track_id, title, artist, album=None, duration=None):
        rec.append((track_id, title, artist, album, duration))
        return rec.result

    monkeypatch.setattr(lyrics, "for_track", fake)
    return rec


@pytest.fixture
def library(monkeypatch):
    """Enable the Navidrome library source without a server."""
    monkeypatch.setitem(sources.SOURCES, "nd", navidrome_src.SOURCE)


def test_invalid_id_404(client, calls):
    for bad in ("short", "zz:123", "a" * 200, "yt:x"):
        assert client.get(f"/api/lyrics/{bad}?title=x").status_code == 404
    assert calls == []


def test_youtube_hints_are_cleaned(client, calls):
    r = client.get("/api/lyrics/dQw4w9WgXcQ", query_string={
        "title": "Rick Astley - Never Gonna Give You Up (Official Music Video)", "artist": "RickAstleyVEVO",
        "duration": "213.5"})
    assert r.status_code == 200
    assert r.get_json() == FOUND
    assert r.headers["Cache-Control"] == "private, max-age=3600"
    assert calls == [("dQw4w9WgXcQ", "Never Gonna Give You Up", "Rick Astley", None, 213.5)]


def test_hints_are_capped_and_duration_parsed_safely(client, calls):
    client.get("/api/lyrics/mc:123", query_string={"title": "t" * 500, "artist": "a", "duration": "nan"})
    client.get("/api/lyrics/mc:124", query_string={"title": "t", "artist": "a", "duration": "abc"})
    client.get("/api/lyrics/mc:125", query_string={"title": "t", "artist": "a", "album": "Al", "duration": "-3"})
    assert calls[0] == ("mc:123", "t" * 200, "a", None, None)
    assert calls[1][4] is None
    assert calls[2] == ("mc:125", "t", "a", "Al", None)


def test_other_sources_use_hints_as_is(client, calls):
    client.get("/api/lyrics/mc:123", query_string={"title": "Song (Live) - Edit", "artist": "A, B", "duration": "200"})
    assert calls == [("mc:123", "Song (Live) - Edit", "A, B", None, 200.0)]


def test_kept_track_uses_history_entry(client, calls):
    history.add({"id": "mc:77", "title": "Kept Song", "channel": "Kept Artist", "album": "Kept Album", "duration": 181})
    assert client.get("/api/lyrics/mc:77").status_code == 200
    assert calls[-1] == ("mc:77", "Kept Song", "Kept Artist", "Kept Album", 181.0)
    # Hints the client sent win over the entry, field by field.
    client.get("/api/lyrics/mc:77", query_string={"artist": "Other"})
    assert calls[-1] == ("mc:77", "Kept Song", "Other", "Kept Album", 181.0)


def test_kept_youtube_track_is_cleaned_like_a_keep(client, calls):
    history.add({"id": "dQw4w9WgXcQ", "title": "Daft Punk - One More Time (Official Video)", "channel": "Daft Punk",
                 "duration": 320})
    client.get("/api/lyrics/dQw4w9WgXcQ")
    assert calls == [("dQw4w9WgXcQ", "One More Time", "Daft Punk", None, 320.0)]


def test_not_found_404(client, calls):
    calls.result = None
    r = client.get("/api/lyrics/mc:1", query_string={"title": "Nothing", "artist": "Nobody"})
    assert r.status_code == 404
    assert r.get_json() == {"error": "No lyrics found"}


def test_no_title_skips_lookup(client, calls):
    # for_track would cache an empty-title lookup as a miss under this id for a day.
    assert client.get("/api/lyrics/mc:1").status_code == 404
    assert calls == []


def test_library_prefers_navidrome(client, calls, library, monkeypatch):
    nd = {"synced": None, "plain": "from\nthe\nfile", "instrumental": False}
    asked = []
    monkeypatch.setattr(navidrome, "lyrics", lambda sid: asked.append(sid) or nd)
    r = client.get("/api/lyrics/nd:abc123", query_string={"title": "Song", "artist": "Band"})
    assert r.status_code == 200 and r.get_json() == nd
    assert asked == ["abc123"] and calls == []


def test_library_falls_back_to_lrclib(client, calls, library, monkeypatch):
    monkeypatch.setattr(navidrome, "lyrics", lambda sid: None)
    r = client.get("/api/lyrics/nd:abc123", query_string={"title": "Song", "artist": "Band", "album": "LP",
                                                          "duration": "99"})
    assert r.status_code == 200
    assert calls == [("nd:abc123", "Song", "Band", "LP", 99.0)]


# ---------- navidrome.lyrics ----------

def _subsonic(monkeypatch, body=None, error=None):
    monkeypatch.setattr(navidrome, "configured", lambda: True)

    def call(endpoint, **params):
        assert endpoint == "getLyricsBySongId" and params == {"id": "s1"}
        if error:
            raise error
        return body

    monkeypatch.setattr(navidrome, "_call", call)


def test_navidrome_synced_to_lrc_with_offset(monkeypatch):
    _subsonic(monkeypatch, {"lyricsList": {"structuredLyrics": [
        {"lang": "xxx", "synced": False, "line": [{"value": "plain one"}]},
        {"lang": "eng", "synced": True, "offset": 100, "line": [
            {"start": 0, "value": "zero"},
            {"start": 1500, "value": " first "},
            {"start": 62340, "value": ""},
            {"start": 3723456, "value": "late"},
        ]},
    ]}})
    assert navidrome.lyrics("s1") == {
        "synced": "[00:00.00]zero\n[00:01.40]first\n[01:02.24]\n[62:03.35]late", "plain": None, "instrumental": False}


def test_navidrome_unsynced_to_plain(monkeypatch):
    _subsonic(monkeypatch, {"lyricsList": {"structuredLyrics": [
        {"lang": "eng", "synced": False, "line": [{"value": "one"}, {"value": ""}, {"value": "two"}]}]}})
    assert navidrome.lyrics("s1") == {"synced": None, "plain": "one\n\ntwo", "instrumental": False}


@pytest.mark.parametrize("body", [{}, {"lyricsList": {}}, {"lyricsList": {"structuredLyrics": []}},
                                  {"lyricsList": {"structuredLyrics": [{"synced": True, "line": []}]}}])
def test_navidrome_no_lyrics(monkeypatch, body):
    _subsonic(monkeypatch, body)
    assert navidrome.lyrics("s1") is None


def test_navidrome_unsupported_endpoint(monkeypatch):
    _subsonic(monkeypatch, error=RuntimeError("Unknown API method"))
    assert navidrome.lyrics("s1") is None


def test_navidrome_unconfigured_makes_no_call(monkeypatch):
    monkeypatch.setattr(navidrome, "_call", lambda *a, **k: pytest.fail("called Navidrome while unconfigured"))
    assert navidrome.lyrics("s1") is None
