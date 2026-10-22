"""lyrics.py (LRCLIB lookup, cache, embedding) and tagging.guess. Offline: the HTTP layer is faked."""
import json
import shutil
import subprocess
import time
import types

import pytest
import requests
from mutagen import File as MutagenFile

import config
import lyrics
import tagging

SYNCED = "[00:01.00] Never gonna give you up\n[00:03.50] Never gonna let you down\n[00:06.20] Never gonna run around"
PLAIN = "Never gonna give you up\nNever gonna let you down\nNever gonna run around"


def _lrc(first):
    return f"[00:00.10] {first}\n[00:02.00] and then\n[00:04.00] the end"


class _R:
    def __init__(self, status, body=None):
        self.status_code, self.body = status, body

    def json(self):
        return self.body


class _Lrclib:
    """Stands in for lyrics._session: /get returns `rec` (404 when None), /search returns `found`."""

    def __init__(self, rec=None, found=(), error=None):
        self.rec, self.found, self.error, self.calls = rec, list(found), error, []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append((url.rsplit("/", 1)[1], dict(params or {}), headers))
        if self.error:
            raise self.error
        if url.endswith("/get"):
            return _R(200, self.rec) if self.rec else _R(404, {"code": 404, "name": "TrackNotFound"})
        return _R(200, self.found)


def _rec(duration, synced=None, plain=None, instrumental=False):
    return {"trackName": "Song", "artistName": "Rick Astley", "duration": duration,
            "instrumental": instrumental, "syncedLyrics": synced, "plainLyrics": plain}


@pytest.fixture(autouse=True)
def cache_file(tmp_path, monkeypatch):
    path = tmp_path / "lyrics.json"
    monkeypatch.setattr(config, "LYRICS_FILE", path)
    monkeypatch.setattr(lyrics, "_cache", None)
    monkeypatch.setattr(lyrics, "_down_until", 0.0)
    monkeypatch.setattr(config, "LRC_SIDECAR", False)
    return path


def _serve(monkeypatch, **kw):
    fake = _Lrclib(**kw)
    monkeypatch.setattr(lyrics, "_session", fake)
    return fake


# ---------- lookup ----------

def test_get_hit(monkeypatch):
    fake = _serve(monkeypatch, rec=_rec(213.0, SYNCED, PLAIN))
    found = lyrics.lookup("Never Gonna Give You Up", "Rick Astley", "Whenever You Need Somebody", 213.4)
    assert found == {"synced": SYNCED, "plain": PLAIN, "instrumental": False}
    assert len(fake.calls) == 1
    path, params, headers = fake.calls[0]
    assert path == "get" and params == {"track_name": "Never Gonna Give You Up", "artist_name": "Rick Astley",
                                        "album_name": "Whenever You Need Somebody", "duration": 213}
    assert headers["User-Agent"].startswith("Reef (")


def test_get_404_falls_back_to_search_closest_synced(monkeypatch):
    fake = _serve(monkeypatch, found=[
        _rec(230.0, SYNCED, PLAIN),       # synced but 17 s off: another version
        _rec(213.0, None, PLAIN),  # closest, but plain only
        _rec(209.0, _lrc("far")),
        _rec(211.5, _lrc("near")),
        _rec(214.0, None, None),           # empty record, not a candidate
    ])
    found = lyrics.lookup("Song (Remastered 2011) feat. Someone", "Rick Astley", None, 213)
    assert found["synced"] == _lrc("near")
    assert [c[0] for c in fake.calls] == ["get", "search"]
    assert "album_name" not in fake.calls[0][1]
    assert fake.calls[1][1] == {"track_name": "Song", "artist_name": "Rick Astley"}


def test_search_plain_when_nothing_synced_fits(monkeypatch):
    _serve(monkeypatch, found=[_rec(230.0, SYNCED), _rec(212.0, None, PLAIN)])
    assert lyrics.lookup("Song", "Rick Astley", duration=213) == {"synced": None, "plain": PLAIN,
                                                                  "instrumental": False}


def test_search_without_duration_prefers_synced_in_lrclib_order(monkeypatch):
    _serve(monkeypatch, found=[_rec(100.0, None, PLAIN), _rec(300.0, _lrc("a")), _rec(200.0, _lrc("b"))])
    assert lyrics.lookup("Song", "Rick Astley")["synced"] == _lrc("a")


def test_junk_record_is_not_lyrics(monkeypatch):
    # LRCLIB is crowd-edited: the top /get record for this very song once read "[00:00.00]probe".
    fake = _serve(monkeypatch, rec=_rec(214.0, "[00:00.00]probe", "probe"), found=[_rec(213.0, SYNCED, PLAIN)])
    assert lyrics.lookup("Never Gonna Give You Up", "Rick Astley", None, 213)["synced"] == SYNCED
    assert [c[0] for c in fake.calls] == ["get", "search"]
    assert lyrics._found(_rec(200.0, "[ar:X]\n[00:01.00]\n[00:02.00] one\n[00:03.00] two", None)) is None


def test_no_artist_skips_get(monkeypatch):
    fake = _serve(monkeypatch, found=[_rec(200.0, None, PLAIN)])
    assert lyrics.lookup("Song", "")["plain"] == PLAIN
    assert [c[0] for c in fake.calls] == ["search"] and "artist_name" not in fake.calls[0][1]
    assert lyrics.lookup("", "Rick Astley") is None


def test_multi_artist_retries_with_first(monkeypatch):
    class Fake(_Lrclib):
        def get(self, url, params=None, headers=None, timeout=None):
            r = super().get(url, params, headers, timeout)
            return r if url.endswith("/get") or params["artist_name"] == "A" else _R(200, [])
    fake = Fake(found=[_rec(200.0, SYNCED)])
    monkeypatch.setattr(lyrics, "_session", fake)
    assert lyrics.lookup("Song", "A, B", duration=200)["synced"] == SYNCED
    assert [c[1].get("artist_name") for c in fake.calls] == ["A, B", "A, B", "A"]


def test_instrumental_counts_as_found(monkeypatch):
    _serve(monkeypatch, found=[_rec(200.0, instrumental=True)])
    found = lyrics.for_track("t1", "Song", "Rick Astley", duration=200)
    assert found == {"synced": None, "plain": None, "instrumental": True}
    _serve(monkeypatch, error=AssertionError("cached hit must not refetch"))
    assert lyrics.for_track("t1", "Song", "Rick Astley") == found


def test_lookup_swallows_network_errors(monkeypatch):
    _serve(monkeypatch, error=requests.ConnectionError("down"))
    assert lyrics.lookup("Song", "Rick Astley", None, 200) is None


def test_server_error_is_not_a_miss(monkeypatch, cache_file):
    class Fake(_Lrclib):
        def get(self, url, params=None, headers=None, timeout=None):
            super().get(url, params, headers, timeout)
            return _R(503)
    monkeypatch.setattr(lyrics, "_session", Fake())
    assert lyrics.for_track("t1", "Song", "Rick Astley") is None
    assert not cache_file.exists()


# ---------- cache ----------

def test_cache_hit_avoids_http(monkeypatch, cache_file):
    fake = _serve(monkeypatch, rec=_rec(213.0, SYNCED, PLAIN))
    first = lyrics.for_track("dQw4w9WgXcQ", "Song", "Rick Astley", None, 213)
    assert first["synced"] == SYNCED and len(fake.calls) == 1
    _serve(monkeypatch, error=AssertionError("cache hit must not touch the network"))
    assert lyrics.for_track("dQw4w9WgXcQ", "Song", "Rick Astley", None, 213) == first
    entry = json.loads(cache_file.read_text())["dQw4w9WgXcQ"]
    assert entry["synced"] == SYNCED and entry["at"] > 0
    monkeypatch.setattr(lyrics, "_cache", None)  # a restart reads the file back
    assert lyrics.for_track("dQw4w9WgXcQ", "x", "y") == first


def test_no_candidate_is_cached_as_miss(monkeypatch, cache_file):
    fake = _serve(monkeypatch, found=[_rec(300.0, SYNCED)])
    assert lyrics.for_track("t1", "Song", "Rick Astley", None, 200) is None
    assert json.loads(cache_file.read_text())["t1"]["miss"] is True
    calls = len(fake.calls)
    assert lyrics.for_track("t1", "Song", "Rick Astley", None, 200) is None
    assert len(fake.calls) == calls  # the miss was served from the cache


def test_network_error_is_not_cached(monkeypatch, cache_file):
    fake = _serve(monkeypatch, error=requests.Timeout("slow"))
    assert lyrics.for_track("t1", "Song", "Rick Astley") is None
    assert not cache_file.exists() or "t1" not in json.loads(cache_file.read_text())
    # Right after a failure LRCLIB is skipped for a while instead of making every caller wait.
    calls = len(fake.calls)
    assert lyrics.for_track("t1", "Song", "Rick Astley") is None and len(fake.calls) == calls
    monkeypatch.setattr(lyrics, "_down_until", 0.0)
    _serve(monkeypatch, rec=_rec(200.0, None, PLAIN))
    assert lyrics.for_track("t1", "Song", "Rick Astley")["plain"] == PLAIN


def test_miss_expires_after_a_day(monkeypatch):
    clock = [1_000_000.0]
    monkeypatch.setattr(lyrics, "time", types.SimpleNamespace(time=lambda: clock[0], monotonic=time.monotonic))
    fake = _serve(monkeypatch)
    assert lyrics.for_track("t1", "Song", "Rick Astley") is None
    calls = len(fake.calls)
    clock[0] += 23 * 3600
    assert lyrics.for_track("t1", "Song", "Rick Astley") is None and len(fake.calls) == calls
    clock[0] += 2 * 3600
    fake.rec = _rec(200.0, SYNCED)
    assert lyrics.for_track("t1", "Song", "Rick Astley")["synced"] == SYNCED
    assert len(fake.calls) > calls


def test_cache_is_capped(monkeypatch, cache_file):
    monkeypatch.setattr(lyrics, "_MAX", 3)
    _serve(monkeypatch, rec=_rec(200.0, None, PLAIN))
    for i in range(5):
        lyrics.for_track(f"t{i}", "Song", "Rick Astley")
    assert sorted(json.loads(cache_file.read_text())) == ["t2", "t3", "t4"]


# ---------- embed ----------

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
_CODECS = {"opus": ["-c:a", "libopus"], "flac": ["-c:a", "flac"], "m4a": ["-c:a", "aac"],
           "mp3": ["-c:a", "libmp3lame"]}


def _make(tmp_path, ext):
    path = tmp_path / f"tone.{ext}"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "sine=d=1",
                    *_CODECS[ext], str(path)], check=True)
    return path


def _read_lyrics(path):
    f = MutagenFile(path)
    if path.suffix == ".mp3":
        return f.tags.getall("USLT")[0].text
    if path.suffix == ".m4a":
        return f.tags["\xa9lyr"][0]
    return f["lyrics"][0]


@needs_ffmpeg
@pytest.mark.parametrize("ext", ["opus", "flac", "m4a", "mp3"])
def test_embed_round_trip(tmp_path, ext):
    path = _make(tmp_path, ext)
    assert lyrics.embed(path, {"synced": SYNCED, "plain": PLAIN, "instrumental": False}) is True
    assert _read_lyrics(path) == SYNCED
    assert lyrics.embed(path, {"synced": None, "plain": PLAIN, "instrumental": False}) is True
    assert _read_lyrics(path) == PLAIN  # replaced, not appended
    assert not path.with_suffix(".lrc").exists()


@needs_ffmpeg
def test_embed_mp3_uslt_fields(tmp_path):
    path = _make(tmp_path, "mp3")
    lyrics.embed(path, {"synced": SYNCED, "plain": None, "instrumental": False})
    uslt = MutagenFile(path).tags.getall("USLT")
    assert len(uslt) == 1 and uslt[0].lang == "eng" and uslt[0].desc == ""


@needs_ffmpeg
def test_embed_sidecar(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "LRC_SIDECAR", True)
    path = _make(tmp_path, "opus")
    lyrics.embed(path, {"synced": None, "plain": PLAIN, "instrumental": False})
    assert not path.with_suffix(".lrc").exists()  # plain text has no timestamps to sync by
    lyrics.embed(path, {"synced": SYNCED, "plain": PLAIN, "instrumental": False})
    assert path.with_suffix(".lrc").read_text() == SYNCED


def test_embed_nothing_or_unsupported(tmp_path):
    assert lyrics.embed(tmp_path / "x.opus", {"synced": None, "plain": None, "instrumental": True}) is False
    assert lyrics.embed(tmp_path / "x.opus", None) is False
    junk = tmp_path / "junk.bin"
    junk.write_bytes(b"not audio")
    assert lyrics.embed(junk, {"synced": SYNCED, "plain": None, "instrumental": False}) is False
    assert lyrics.embed(tmp_path / "missing.opus", {"synced": SYNCED, "plain": None, "instrumental": False}) is False


# ---------- tagging.guess ----------

@pytest.mark.parametrize("title, channel, expected", [
    ("Rick Astley - Never Gonna Give You Up (Official Music Video)", "Rick Astley",
     ("Never Gonna Give You Up", "Rick Astley")),
    ("Never Gonna Give You Up", "Rick Astley", ("Never Gonna Give You Up", "Rick Astley")),
    ("Song (Lyrics)", "RickAstleyVEVO", ("Song", "RickAstley")),
    ("Around the World", "Daft Punk - Topic", ("Around the World", "Daft Punk")),
    ("Daft Punk - Around the World [Official Music Video]", "SomeUploader", ("Around the World", "Daft Punk")),
    ("A - B - C (Official Audio)", "Chan", ("A - B - C", "Chan")),
    ("Song (Remastered 2009)", "X", ("Song (Remastered 2009)", "X")),
    ("Untitled jam", "", ("Untitled jam", "")),
    ("", "", ("", "")),
    (None, None, ("", "")),
])
def test_guess(title, channel, expected):
    assert tagging.guess(title, channel) == expected
