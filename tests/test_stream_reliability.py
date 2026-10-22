"""Stream URL lifetime, client gating, mid-song swap, ?fresh=, fake wall, mid-stream resume, YouTube reset.

Offline: yt-dlp is replaced by a fake `sources.youtube._info`, the CDN by a fake `streamer._open`.
"""
import threading
import time
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest
import requests

import app
import config
import errors
import search
import sources
import streamer
from sources import youtube

VID = "FGBhQbmPwH8"
OTHER = "dQw4w9WgXcQ"


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    def wipe():
        with streamer._lock:
            for d in (streamer._cache, streamer._gen, streamer._served, streamer._fresh_tokens, streamer._inflight):
                d.clear()
        youtube.gates.clear()
    wipe()
    monkeypatch.setattr(streamer, "_sleep", lambda s: None)
    monkeypatch.setattr(youtube, "_last_auto_reset", None)
    monkeypatch.setattr(config, "FAKE_WALL", False)
    yield
    wipe()


def gv(client="VISIONOS", itag="140", lmt="111", clen=6000, dur=300.0, expire_in=21600, pot=False):
    """A googlevideo-shaped URL."""
    q = {"expire": int(time.time() + expire_in), "itag": itag, "lmt": lmt, "clen": clen, "dur": dur, "c": client}
    if pot:
        q["pot"] = "tok"
    return "https://rr1.googlevideo.com/videoplayback?" + urlencode(q)


def info(url, duration=300, acodec="mp4a.40.2", ext="m4a", channels=2, formats=None):
    return {"url": url, "ext": ext, "acodec": acodec, "audio_channels": channels, "duration": duration,
            "http_headers": {"User-Agent": "yt"}, "formats": formats or []}


class Up:
    """A fake requests response: body in chunks, optionally dropping (raising) after `drop` bytes."""

    def __init__(self, body, status=200, headers=None, drop=None, exc=requests.exceptions.ChunkedEncodingError,
                 mime="audio/mp4"):
        self.body, self.status_code, self.drop, self.exc = body, status, drop, exc
        self.headers = {"Content-Type": mime, "Content-Length": str(len(body)), **(headers or {})}
        self.closed = False

    def iter_content(self, _size):
        stop = len(self.body) if self.drop is None else self.drop
        for i in range(0, stop, 7):
            yield self.body[i:min(i + 7, stop)]
        if self.drop is not None:
            raise self.exc("connection broken: IncompleteRead")

    def close(self):
        self.closed = True


class CDN:
    """Serves one byte string per client (`c=`) or per URL; `dead` clients answer 403; `drops` makes the
    n-th open drop after that many bytes; `reopen_status` answers every reopen (a Range past 0) with a status."""

    def __init__(self, files, mime="audio/mp4"):
        self.files, self.mime = files, mime
        self.dead, self.drops, self.calls, self.reopen_status = set(), [], [], None

    def key(self, url):
        return (parse_qs(urlsplit(url).query).get("c") or [url])[0]

    def open(self, entry, rng=None):
        k = self.key(entry["url"])
        self.calls.append((k, rng))
        if k in self.dead:
            return Up(b"", 403), 0
        if self.reopen_status and len(self.calls) > 1:
            return Up(b"", self.reopen_status), 0
        data = self.files[k]
        drop = self.drops.pop(0) if self.drops else None
        if not rng:
            return Up(data, 200, drop=drop, mime=self.mime), 0
        a, _, b = rng.split("=")[1].partition("-")
        a, b = int(a), min(int(b), len(data) - 1) if b else len(data) - 1
        return Up(data[a:b + 1], 206, {"Content-Range": f"bytes {a}-{b}/{len(data)}"}, drop=drop, mime=self.mime), 0


def get(path, rng=None):
    return app.app.test_client().get(path, headers={"Range": rng} if rng else {})


@pytest.fixture
def yt(monkeypatch):
    """The real YouTube source over a fake yt-dlp: `answers(exclude) -> info` decides per call."""
    state = {"calls": [], "answer": lambda exclude: info(gv())}

    def fake_info(video_id, turn, exclude):
        state["calls"].append(tuple(exclude))
        out = state["answer"](tuple(exclude))
        if isinstance(out, Exception):
            raise out
        return out

    monkeypatch.setattr(youtube, "_info", fake_info)
    return state


# ---------- TTL from the URL ----------

def test_url_meta_reads_googlevideo_params():
    m = streamer._url_meta(gv("ANDROID_VR", pot=True))
    assert m["client"] == "android_vr" and m["has_pot"] and m["itag"] == "140" and m["lmt"] == "111"
    assert m["clen"] == 6000 and m["dur"] == 300.0 and m["expire"] > time.time()


@pytest.mark.parametrize("c,name", [("ANDROID_VR", "android_vr"), ("TVHTML5", "tv"), ("MWEB", "mweb"), ("WEB", "web"),
                                    ("WEB_REMIX", "web_music"), ("IOS", "ios"), ("VISIONOS", "visionos"),
                                    ("WEB_CREATOR", "web_creator")])
def test_client_names(c, name):
    assert youtube.client_of(f"https://x.googlevideo.com/videoplayback?c={c}") == name


@pytest.mark.parametrize("query,expire", [("Expires=1900000000", 1900000000), ("etsp=1900000001", 1900000001),
                                          ("x=1", None), ("expire=12", None)])
def test_url_meta_other_expiry_params(query, expire):
    assert streamer._url_meta(f"https://cdn.example/a.flac?{query}")["expire"] == expire


def test_entry_ttl_from_expire_or_default(monkeypatch):
    now = 1_800_000_000
    monkeypatch.setattr(streamer, "_now", lambda: now)
    url = f"https://x.googlevideo.com/videoplayback?expire={now + 3600}&c=WEB"
    monkeypatch.setattr(sources.get("yt"), "resolve", lambda native, turn=None: {"url": url, "duration": 200})
    entry = streamer._extract(VID, True)
    assert entry["expires"] == now + 3600 - 60 and entry["client"] == "web" and entry["duration"] == 200
    monkeypatch.setattr(sources.get("yt"), "resolve", lambda native, turn=None: {"url": "https://cdn.example/a"})
    assert streamer._extract(VID, True)["expires"] == now + 300


def _entry(now, expire_in, duration, client="web"):
    return {"expires": now + expire_in - 60, "expire": now + expire_in, "duration": duration, "client": client}


def test_cache_hit_needs_life_for_the_whole_song(monkeypatch):
    now = 1_800_000_000
    assert streamer._usable(VID, _entry(now, 400, 300), now)          # 400 >= 300 + 60
    assert not streamer._usable(VID, _entry(now, 350, 300), now)      # 350 <  300 + 60
    assert streamer._usable(VID, _entry(now, 350, None), now)         # unknown length: just the TTL
    no_expire = {"expires": now + 100, "expire": None, "duration": 600, "client": None}
    assert streamer._usable(VID, no_expire, now)                      # an unsigned URL: no song-length rule


def test_cached_url_of_gated_client_is_not_reused(monkeypatch):
    now = time.time()
    assert streamer._usable(VID, _entry(now, 20000, 300, "visionos"), now)
    youtube.gates.strike(VID, "visionos", "denied")
    assert not streamer._usable(VID, _entry(now, 20000, 300, "visionos"), now)
    assert streamer._usable(OTHER, _entry(now, 20000, 300, "visionos"), now)


# ---------- 403 classification and gates ----------

@pytest.mark.parametrize("status,expire_in,pot,verdict", [
    (403, 10, False, "expired"), (403, 25, True, "expired"), (403, 3600, False, "denied"), (403, 3600, True, "token"),
    (403, None, False, "expired"), (404, 3600, False, "missing"), (410, 3600, False, "missing"),
    (416, 3600, False, "missing"), (500, 3600, False, "other")])
def test_verdict_matrix(status, expire_in, pot, verdict):
    entry = {"expire": None if expire_in is None else int(time.time()) + expire_in, "has_pot": pot}
    assert streamer._verdict(entry, status) == verdict


@pytest.fixture
def clock(monkeypatch):
    t = {"now": 1000.0}
    monkeypatch.setattr(youtube, "_clock", lambda: t["now"])
    return t


def test_denied_gates_per_video_then_globally_after_two(clock):
    g = youtube.gates
    assert g.strike(VID, "visionos", "denied")
    assert g.excluded(VID) == ["visionos"] and g.excluded(OTHER) == []
    clock["now"] += 60
    g.strike(OTHER, "visionos", "denied")
    assert g.excluded("zzzzzzzzzzz") == ["visionos"]  # 2 denials within 30 min: global
    assert g.snapshot()["global"] == {"visionos": 1800} and g.snapshot()["per_video"] == 2
    clock["now"] += 601
    assert g.excluded("zzzzzzzzzzz") == ["visionos"] and g.snapshot()["per_video"] == 0  # per-video gates expired
    clock["now"] += 1200
    assert g.excluded(VID) == []  # global gate expired too


def test_denials_far_apart_stay_per_video(clock):
    g = youtube.gates
    g.strike(VID, "web", "denied")
    clock["now"] += 1801
    g.strike(OTHER, "web", "denied")
    assert g.snapshot()["global"] == {} and g.excluded(VID) == []


def test_rejected_token_gates_on_second_strike(clock):
    g = youtube.gates
    assert not g.strike(VID, "web", "token") and g.excluded(VID) == []
    assert g.strike(VID, "web", "token") and g.excluded(VID) == ["web"]


def test_missing_gates_per_video_only(clock):
    g = youtube.gates
    g.strike(VID, "tv", "missing")
    g.strike(OTHER, "tv", "missing")
    assert g.snapshot()["global"] == {} and g.excluded(VID) == ["tv"]
    assert not g.strike(VID, "tv", "expired") and not g.strike(VID, None, "denied")


def test_player_client_exclusion_args(monkeypatch):
    assert "player_client" not in config.yt_extractor_args([])["youtube"]
    assert config.yt_extractor_args([], ["visionos", "web"])["youtube"]["player_client"] == \
        ["default", "-visionos", "-web"]
    monkeypatch.setattr(config, "YT_CLIENT", ["ios", "web"])
    assert config.yt_extractor_args(exclude=["web"])["youtube"]["player_client"] == ["ios", "web", "-web"]
    assert youtube.ydl_options(exclude=["tv"])["format"] == (
        "bestaudio[ext=m4a][acodec^=mp4a]/bestaudio[ext=m4a]/bestaudio[acodec!=none]/best[ext=mp4][acodec^=mp4a]/best")


class FakeYDL:
    built = []

    def __init__(self, params):
        self.params = params
        self.closed = False
        FakeYDL.built.append(self)

    def close(self):
        self.closed = True


def test_gated_resolves_use_their_own_instance(monkeypatch):
    FakeYDL.built = []
    monkeypatch.setattr(youtube.yt_dlp, "YoutubeDL", FakeYDL)
    monkeypatch.setattr(youtube, "_ydl", None)
    monkeypatch.setattr(youtube, "_alt", {})
    shared = youtube._ydl_for(())
    assert youtube._ydl_for(()) is shared and "player_client" not in shared.params["extractor_args"]["youtube"]
    alt = youtube._ydl_for(("visionos",))
    assert alt is not shared and youtube._ydl_for(("visionos",)) is alt
    assert alt.params["extractor_args"]["youtube"]["player_client"] == ["default", "-visionos"]


def test_resolve_excludes_gated_clients(yt):
    youtube.gates.strike(VID, "visionos", "denied")
    yt["answer"] = lambda exclude: info(gv("WEB"))
    assert streamer.resolve(VID)["client"] == "web"
    assert yt["calls"] == [("visionos",)]


def test_all_clients_gated_retries_them_all(yt):
    youtube.gates.strike(VID, "visionos", "denied")
    yt["answer"] = lambda exclude: (Exception("ERROR: No player clients have been requested") if exclude
                                    else info(gv()))
    assert streamer.resolve(VID)["client"] == "visionos"
    assert yt["calls"] == [("visionos",), ()]


# ---------- generation counter ----------

def test_stale_resolve_does_not_overwrite_newer(monkeypatch):
    gate, started = threading.Event(), threading.Event()

    def fake_extract(track_id, foreground):
        if not foreground:  # the slow prefetch: resolves the old URL
            started.set()
            gate.wait(5)
            return {"url": "old", "expires": time.time() + 999, "gen": 0}
        return {"url": "new", "expires": time.time() + 999, "gen": 0}

    monkeypatch.setattr(streamer, "_extract", fake_extract)
    monkeypatch.setattr(streamer, "_usable", lambda tid, hit, now: hit["expires"] > now and not hit.get("dead"))
    slow = threading.Thread(target=streamer._resolve, args=(VID, False))
    slow.start()
    assert started.wait(5)
    streamer._invalidate(VID)  # e.g. ?fresh= arrives while the prefetch is still running
    entry, how = streamer._resolve(VID)
    assert entry["url"] == "new" and how == "miss"
    gate.set()
    slow.join(5)
    assert streamer._cache[VID]["url"] == "new" and streamer._cache[VID]["gen"] == 2


def test_concurrent_refreshes_of_one_url_invalidate_once():
    e = {"url": "u", "expires": time.time() + 999, "gen": 1}
    streamer._cache[VID] = e
    assert streamer._invalidate(VID, e) and not streamer._invalidate(VID, e)
    assert streamer._gen[VID] == 1 and VID not in streamer._cache


# ---------- proxy: mid-song swap, ?fresh=, fake wall ----------

DATA = bytes(range(256)) * 24  # 6144 bytes; ranges past 0 are compared byte for byte


@pytest.fixture
def cdn(monkeypatch):
    c = CDN({"VISIONOS": DATA, "WEB": DATA, "IOS": bytes(reversed(DATA))[:5000]})
    monkeypatch.setattr(streamer, "_open", c.open)
    return c


def test_mid_song_403_swaps_to_same_file_transparently(yt, cdn):
    yt["answer"] = lambda exclude: info(gv("WEB" if "visionos" in exclude else "VISIONOS", clen=len(DATA)))
    assert get(f"/api/stream/{VID}", "bytes=0-1").status_code == 206
    cdn.dead.add("VISIONOS")  # the client is walled mid-song; its URL is still far from expiry
    r = get(f"/api/stream/{VID}", "bytes=3000-")
    assert r.status_code == 206 and r.data == DATA[3000:]
    assert youtube.gates.excluded(VID) == ["visionos"]
    assert streamer.last_timing["verdict"] == "denied" and streamer.last_timing["swap"] == "same-file"
    assert streamer.last_timing["client"] == "web"


def test_mid_song_403_to_other_file_is_502_expired(yt, cdn):
    yt["answer"] = lambda exclude: (info(gv("IOS", lmt="222", clen=5000)) if "visionos" in exclude
                                    else info(gv("VISIONOS", clen=len(DATA))))
    get(f"/api/stream/{VID}", "bytes=0-1")
    cdn.dead.add("VISIONOS")
    r = get(f"/api/stream/{VID}", "bytes=3000-")
    assert r.status_code == 502
    assert r.get_json()["block"] == "EXPIRED" and r.get_json()["hint"]
    assert streamer.last_timing["swap"] == "refused"


def test_403_at_start_refreshes_to_any_file(yt, cdn):
    yt["answer"] = lambda exclude: (info(gv("IOS", lmt="222", clen=5000)) if "visionos" in exclude
                                    else info(gv("VISIONOS", clen=len(DATA))))
    cdn.dead.add("VISIONOS")
    r = get(f"/api/stream/{VID}", "bytes=0-")
    assert r.status_code == 206 and r.data == cdn.files["IOS"]


def test_expired_url_refreshes_without_gating(yt, cdn):
    urls = iter([gv("VISIONOS", expire_in=10), gv("WEB")])
    yt["answer"] = lambda exclude: info(next(urls))
    cdn.dead.add("VISIONOS")
    r = get(f"/api/stream/{VID}", "bytes=0-")
    assert r.status_code == 206 and streamer.last_timing["verdict"] == "expired"
    assert youtube.gates.excluded(VID) == []


def test_upstream_still_failing_is_typed_502(yt, cdn):
    cdn.dead.update({"VISIONOS", "WEB"})
    yt["answer"] = lambda exclude: info(gv("WEB" if "visionos" in exclude else "VISIONOS"))
    r = get(f"/api/stream/{VID}", "bytes=0-")
    assert r.status_code == 502 and r.get_json()["block"] == "BOT_WALL"
    assert streamer.last_timing["retry"] == 2


def test_fresh_evicts_once_per_token(yt, cdn):
    n = lambda: len(yt["calls"])  # noqa: E731
    get(f"/api/stream/{VID}", "bytes=0-1")
    get(f"/api/stream/{VID}", "bytes=0-1")
    assert n() == 1
    r = get(f"/api/stream/{VID}?fresh=a1", "bytes=0-1")
    assert r.status_code == 206 and n() == 2 and streamer.last_timing["fresh"] == 1
    get(f"/api/stream/{VID}?fresh=a1", "bytes=0-")  # the reloaded element's next Range request
    assert n() == 2
    get(f"/api/stream/{VID}?fresh=a2", "bytes=0-")
    assert n() == 3
    get(f"/api/stream/{VID}", "bytes=0-1")      # a plain request (a probe, an older element) in between...
    get(f"/api/stream/{VID}?fresh=a2", "bytes=0-1")
    get(f"/api/stream/{VID}?fresh=a1", "bytes=0-1")
    assert n() == 3                              # ...doesn't make a token evict again


def test_fresh_drops_in_flight_result(monkeypatch):
    streamer._inflight[VID] = threading.Event()
    streamer._invalidate(VID)
    assert VID not in streamer._inflight


def test_cache_change_under_playing_element_is_502(yt, cdn, monkeypatch):
    yt["answer"] = lambda exclude: info(gv("VISIONOS", clen=len(DATA)))
    get(f"/api/stream/{VID}", "bytes=0-1")
    streamer._invalidate(VID)  # e.g. the cached URL aged out
    yt["answer"] = lambda exclude: info(gv("IOS", lmt="222", clen=5000))
    r = get(f"/api/stream/{VID}", "bytes=3000-")
    assert r.status_code == 502 and r.get_json()["block"] == "EXPIRED"
    assert get(f"/api/stream/{VID}?fresh=x", "bytes=3000-").status_code == 206  # a fresh load is fine


def test_fake_wall_on_first_generation_only(yt, cdn, monkeypatch):
    monkeypatch.setattr(config, "FAKE_WALL", True)
    yt["answer"] = lambda exclude: info(gv("VISIONOS", clen=len(DATA)), duration=300)
    wall = len(DATA) * 60 // 300  # 1228
    assert get(f"/api/stream/{VID}", f"bytes={wall - 100}-").status_code == 206
    r = get(f"/api/stream/{VID}", f"bytes={wall + 100}-")
    assert r.status_code == 403
    assert r.get_json()["block"] == "EXPIRED" and "fake wall" in r.get_json()["error"]
    r = get(f"/api/stream/{VID}?fresh=1", f"bytes={wall + 100}-")
    assert r.status_code == 206 and r.data == DATA[wall + 100:]


def test_fake_wall_off_by_default(yt, cdn):
    assert get(f"/api/stream/{VID}", "bytes=5000-").status_code == 206


# ---------- format choice and previews ----------

def test_choose_rejects_dolby_and_surround():
    ok = {"url": "u-aac", "vcodec": "none", "acodec": "mp4a.40.2", "ext": "m4a", "audio_channels": 2, "abr": 128}
    opus = {"url": "u-opus", "vcodec": "none", "acodec": "opus", "ext": "webm", "audio_channels": 2, "abr": 160}
    for bad in (info("u-ec3", acodec="ec-3", formats=[opus, ok]), info("u-51", channels=6, formats=[opus, ok]),
                info("u-ac4", acodec="ac-4", formats=[opus, ok])):
        assert youtube._choose(bad)["url"] == "u-aac"
    plain = info("u-aac")
    assert youtube._choose(plain) is plain
    with pytest.raises(errors.StreamError):
        youtube._choose(info("u-ec3", acodec="ec-3", formats=[{**ok, "acodec": "ac-3"}]))


def _fmt(fid, ext, vcodec, acodec, abr=None, channels=2):
    return {"format_id": fid, "url": gv(itag=fid), "ext": ext, "vcodec": vcodec, "acodec": acodec, "abr": abr,
            "tbr": abr or 500, "audio_channels": channels, "protocol": "https"}


MUXED_18 = _fmt("18", "mp4", "avc1.42001E", "mp4a.40.2", channels=2)


def selected(formats):
    """yt-dlp's real format selection with Reef's format string, offline (no extractor runs)."""
    import yt_dlp
    raw = {"id": VID, "title": "t", "duration": 300, "formats": formats, "extractor": "youtube",
           "extractor_key": "Youtube", "webpage_url": f"https://www.youtube.com/watch?v={VID}"}
    with yt_dlp.YoutubeDL({**youtube.ydl_options(), "logger": None}) as ydl:
        return ydl.process_ie_result(raw, download=False)


def test_only_muxed_format_18_still_resolves(yt, cdn):
    yt["answer"] = lambda exclude: selected([MUXED_18])
    entry = streamer.resolve(VID)
    assert entry["itag"] == "18" and entry["ext"] == "mp4" and entry["mime"] == "audio/mp4"
    r = get(f"/api/stream/{VID}", "bytes=0-1")
    assert r.status_code == 206 and r.headers["Content-Type"] == "audio/mp4"


def test_audio_only_m4a_beats_muxed_and_opus():
    formats = [MUXED_18, _fmt("251", "webm", "none", "opus", 160), _fmt("140", "m4a", "none", "mp4a.40.2", 129)]
    assert selected(formats)["format_id"] == "140"
    assert selected([MUXED_18, _fmt("251", "webm", "none", "opus", 160)])["format_id"] == "251"


def test_dolby_audio_only_pick_falls_back_to_stereo():
    dolby = _fmt("328", "m4a", "none", "ec-3", 384, channels=6)
    opus = _fmt("251", "webm", "none", "opus", 160)
    picked = selected([dolby, MUXED_18, opus])
    assert picked["format_id"] == "328"  # yt-dlp's bestaudio[ext=m4a] takes the Dolby track...
    assert youtube._choose(picked)["format_id"] == "251"  # ...Reef swaps it for stereo audio-only
    assert youtube._choose(selected([dolby, MUXED_18]))["format_id"] == "18"  # or the muxed AAC mp4


def test_preview_gates_its_client_and_tries_the_next(yt):
    yt["answer"] = lambda exclude: info(gv("WEB", dur=300.0) if "web_music" in exclude else gv("WEB_REMIX", dur=30.0))
    entry = streamer.resolve(VID)
    assert entry["client"] == "web" and yt["calls"] == [(), ("web_music",)]
    assert youtube.gates.excluded(VID) == ["web_music"]


@pytest.mark.parametrize("own,duration,preview", [(30, 300, True), (90, 300, True), (100, 300, False),
                                                  (60, 80, False), (85, 95, True), (90, 95, False)])
def test_preview_rule(own, duration, preview):
    assert youtube._preview({"url": gv(dur=own)}, duration) is preview
    assert youtube._preview({"url": "https://x/no-dur"}, duration) is False


def test_only_previews_is_members_or_paid(yt):
    yt["answer"] = lambda exclude: info(gv("WEB_REMIX" if not exclude else "WEB", dur=30.0))
    with pytest.raises(errors.StreamError) as e:
        streamer.resolve(VID)
    assert e.value.block == "MEMBERS_OR_PAID"


# ---------- typed failures, bot wall, GONE ----------

class DownloadError(Exception):
    pass


def test_bot_wall_resets_once_then_reports(yt, monkeypatch):
    resets = []
    monkeypatch.setattr(youtube, "reset", lambda: resets.append(1) or {})
    yt["answer"] = lambda exclude: DownloadError("ERROR: [youtube] x: Sign in to confirm you're not a bot.")
    r = get(f"/api/stream/{VID}", "bytes=0-1")
    assert r.status_code == 502
    body = r.get_json()
    assert body["block"] == "BOT_WALL" and "mobile data" in body["hint"] and "not a bot" in body["error"]
    assert resets == [1] and len(yt["calls"]) == 2
    get(f"/api/stream/{VID}", "bytes=0-1")
    assert resets == [1]  # no second automatic reset within 10 minutes


def test_bot_wall_cleared_by_reset(yt, cdn, monkeypatch):
    monkeypatch.setattr(youtube, "reset", lambda: {})
    answers = iter([DownloadError("Sign in to confirm you’re not a bot"), info(gv())])
    yt["answer"] = lambda exclude: next(answers)
    assert get(f"/api/stream/{VID}", "bytes=0-1").status_code == 206


def test_gone_only_after_every_client(yt):
    youtube.gates.strike(VID, "visionos", "denied")
    yt["answer"] = lambda exclude: DownloadError("ERROR: [youtube] x: Video unavailable. This video has been removed")
    r = get(f"/api/stream/{VID}", "bytes=0-1")
    assert r.status_code == 502 and r.get_json()["block"] == "GONE"
    assert yt["calls"] == [("visionos",), ()]  # the gated client was asked too before calling it gone


def test_timeout_is_network_never_gone(yt):
    yt["answer"] = lambda exclude: requests.ReadTimeout("Read timed out; video unavailable?")
    r = get(f"/api/stream/{VID}", "bytes=0-1")
    assert r.status_code == 502 and r.get_json()["block"] == "NETWORK"


def test_region_and_age_blocks(yt):
    yt["answer"] = lambda exclude: DownloadError(
        "Video unavailable. The uploader has not made this video available in your country")
    assert get(f"/api/stream/{VID}").get_json()["block"] == "REGION"
    yt["answer"] = lambda exclude: DownloadError("Sign in to confirm your age. This video may be inappropriate")
    assert get(f"/api/stream/{VID}").get_json()["block"] == "LOGIN_REQUIRED"


# ---------- reset ----------

def test_reset_clears_gates_cache_and_rebuilds_instances(monkeypatch):
    monkeypatch.setattr(youtube, "_clear_ydl_cache", lambda: 7)
    shared, alt, flat, srch = FakeYDL({}), FakeYDL({}), FakeYDL({}), FakeYDL({})
    monkeypatch.setattr(youtube, "_ydl", shared)
    monkeypatch.setattr(youtube, "_alt", {("web",): alt})
    monkeypatch.setattr(youtube, "_flat", flat)
    monkeypatch.setattr(search, "_ydl", srch)
    youtube.gates.strike(VID, "web", "denied")
    youtube.gates.strike(OTHER, "web", "denied")
    streamer._cache[VID] = {"url": "u", "expires": time.time() + 999, "gen": 1}
    r = app.app.test_client().post("/api/yt/reset")
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True
    assert body["cleared"] == {"instances": 4, "cache_files": 7, "global_gates": 1, "video_gates": 2,
                               "resolve_cache": 1}
    assert youtube._ydl is None and youtube._alt == {} and youtube._flat is None and search._ydl is None
    assert all(y.closed for y in (shared, alt, flat, srch))
    assert youtube.gates.excluded(VID) == [] and streamer._cache == {}


def test_clear_ydl_cache_removes_the_dir(monkeypatch, tmp_path):
    root = tmp_path / "cache" / "yt-dlp"
    (root / "youtube-sigfuncs").mkdir(parents=True)
    (root / "youtube-sigfuncs" / "a.json").write_text("{}")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    assert youtube._clear_ydl_cache() == 1 and not root.exists()


def test_health_reports_gates_cache_and_cooling(yt, cdn, monkeypatch):
    get(f"/api/stream/{VID}", "bytes=0-1")
    youtube.gates.strike(OTHER, "tv", "missing")
    h = app.app.test_client().get("/api/health").get_json()
    assert h["yt_gates"] == {"global": {}, "per_video": 1}
    assert h["stream_cache"]["entries"] == 1 and h["stream_cache"]["misses"] >= 1
    recent = h["stream_cache"]["recent"][0]
    assert recent["id"] == VID and recent["client"] == "visionos" and recent["ttl_s"] > 20000
    assert isinstance(h["failover_cooling"], dict)


def test_download_job_error_carries_block_and_hint(monkeypatch):
    import downloader

    def boom(job):
        raise DownloadError("ERROR: [youtube] x: Private video. Sign in if you've been granted access")

    monkeypatch.setattr(downloader, "_run_ytdlp", boom)
    job = downloader._new_job(VID, "t", "c")
    downloader._run(job)
    assert job["status"] == "error" and job["block"] == "GONE" and job["hint"] == errors.hint("GONE")


# ---------- mid-stream resume ----------

@pytest.fixture
def fixed(monkeypatch):
    """The proxy over one fixed entry (any source) and a CDN keyed by URL."""
    def run(data, url="https://cdn.example/t", mime="audio/mp4"):
        c = CDN({url: data}, mime=mime)
        entry = {"url": url, "headers": {}, "mime": mime, "ext": "m4a", "gen": 1, "client": None}
        monkeypatch.setattr(streamer, "_resolve", lambda tid, foreground=True: (entry, "hit"))
        monkeypatch.setattr(streamer, "_open", c.open)
        return c
    return run


def test_resume_after_drop_is_contiguous(fixed, capsys):
    c = fixed(DATA)
    c.drops = [1000, 2500]
    r = get(f"/api/stream/{VID}", "bytes=100-")
    assert r.status_code == 206 and r.data == DATA[100:]
    assert [rng for _, rng in c.calls] == [f"bytes=100-{100 + streamer._FIRST_SPAN - 1}", "bytes=1100-6143",
                                          "bytes=3600-6143"]
    assert capsys.readouterr().out.count("stream-resume") == 2


@pytest.mark.parametrize("exc", [requests.exceptions.ChunkedEncodingError, requests.ConnectionError,
                                 requests.ReadTimeout])
def test_resume_on_any_drop_without_range(fixed, exc, monkeypatch):
    c = fixed(DATA, mime="audio/flac")
    c.drops = [777]
    up_exc = exc

    orig = c.open

    def open_(entry, rng=None):
        up, ms = orig(entry, rng)
        up.exc = up_exc
        return up, ms

    monkeypatch.setattr(streamer, "_open", open_)
    r = app.app.test_client().get("/api/stream/mc:12345")
    assert r.status_code == 200 and r.data == DATA
    assert c.calls[1][1] == f"bytes=777-{len(DATA) - 1}"


def test_resume_after_patched_head(fixed):
    from test_streamer import durations, mp4
    data = mp4() + b"\x09" * 3000
    c = fixed(data)
    c.drops = [60, 400]  # inside moov, then inside the media
    r = get(f"/api/stream/{VID}")
    assert r.status_code == 200 and len(r.data) == len(data)
    assert r.data == streamer.fix_fragmented_durations(data) and durations(r.data) == [0, 0, 0]
    assert r.data[len(mp4()):] == data[len(mp4()):]


def test_resume_gives_up_after_five(fixed, capsys):
    c = fixed(DATA)
    c.drops = [500]
    c.reopen_status = 503
    r = get(f"/api/stream/{VID}", "bytes=0-")
    assert r.data == DATA[:500]
    assert len(c.calls) == 6
    out = capsys.readouterr().out
    assert out.count("stream-resume id=") == 6 and "gave up after 500 bytes" in out


def test_resume_refreshes_a_dead_url_for_the_same_file(yt, cdn, monkeypatch):
    yt["answer"] = lambda exclude: info(gv("WEB" if "visionos" in exclude else "VISIONOS", clen=len(DATA)))
    orig = cdn.open

    def open_(entry, rng=None):
        up, ms = orig(entry, rng)
        if rng == f"bytes=0-{len(DATA) - 1}":
            up.drop = 2000
            cdn.dead.add("VISIONOS")  # dies while the body is streaming
        return up, ms

    monkeypatch.setattr(streamer, "_open", open_)
    r = get(f"/api/stream/{VID}", "bytes=0-")
    assert r.data == DATA
    assert ("WEB", f"bytes=2000-{len(DATA) - 1}") in cdn.calls
