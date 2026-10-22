"""Moods & genres, album / artist / playlist pages, home tiles, and album keeps with tag hints.
Offline: the YTMusic client is a fake and nothing is downloaded."""
import json
import queue
import shutil
import subprocess

import pytest
import yt_dlp

import app
import blocklist
import config
import downloader
import history
import streamer
import tiles
import ytmusic
from tagging import with_hints

SQ = "https://lh3.googleusercontent.com/abc_DEF-1"
WIDE = "https://lh3.googleusercontent.com/wide_1"
DAFT = "UCRr1xG_2WIDs18a6cIiCxeA"
DAFT_CHANNEL = "UC_kRDKYrUlrbtrSiyu5Tflg"
ROMAN = "UCaaaaaaaaaaaaaaaaaaaaaa"
OTHER = "UCbbbbbbbbbbbbbbbbbbbbbb"
ALBUM = "MPREb_7ltM34kr0mH"
OLAK = "OLAK5uy_mz6eafmqdRHSaR4IwG0ll6J6rgv0_ZpGw"
CHILL, POP, AFRICAN, DIWALI = "ggMPOchill0000", "ggMPOpop000000", "ggMPOafrican00", "ggMPOdiwali000"


def thumbs(base=SQ, w=226, h=226):
    return [{"url": f"{base}=w60-h60-l90-rj", "width": 60}, {"url": f"{base}=w{w}-h{h}-l90-rj", "width": w}]


def song(vid, title="One More Time", artists=((("Daft Punk", DAFT),)), **kw):
    return {"videoId": vid, "title": title, "artists": [{"name": n, "id": i} for n, i in artists],
            "album": {"name": "Discovery", "id": ALBUM}, "duration_seconds": 320, "thumbnails": thumbs(),
            "isExplicit": False, **kw}


class FakeYT:
    def __init__(self):
        self.calls = []
        self.fail = None
        self.audio = []  # the album's OLAK5uy_ audio playlist: tracks, or an exception to raise
        self.album_thumbs = thumbs(SQ, 544, 544)

    def _rec(self, name, *a, **kw):
        self.calls.append((name, a, kw))
        if self.fail:
            raise self.fail

    def get_mood_categories(self):
        self._rec("moods")
        return {"Moods & moments": [{"title": "Chill", "params": CHILL}, {"title": "Diwali", "params": DIWALI},
                                    {"title": None, "params": "x"}],
                "Genres": [{"title": "African", "params": AFRICAN}, {"title": "Pop", "params": POP}],
                "Broken": "not a list"}

    def get_mood_playlists(self, params):
        self._rec("mood", params)
        return [{"title": f"{params} list", "playlistId": "RDCLAK5uy_nBE4bLuBHUXWZrF59ZrkPEToKt8M_I3Vc",
                 "thumbnails": thumbs(f"{SQ}-{params}", 544, 544), "description": "Playlist • YouTube Music"},
                {"title": "no id", "playlistId": None}]

    def get_album_browse_id(self, list_id):
        self._rec("album_browse_id", list_id)
        return ALBUM

    def get_album(self, browse_id):
        self._rec("album", browse_id)
        return {"title": "Discovery", "type": "Album", "thumbnails": self.album_thumbs, "year": "2001",
                "artists": [{"name": "Daft Punk", "id": DAFT}], "trackCount": 14, "duration_seconds": 3676,
                "audioPlaylistId": OLAK,
                "tracks": [song("FGBhQbmPwH8", album="Discovery", thumbnails=None, trackNumber=1),
                           song("L93-7vRfxNs", "Aerodynamic", album="Discovery", thumbnails=None, trackNumber=2),
                           song("FGBhQbmPwH8", album="Discovery", thumbnails=None),  # duplicate row
                           song("aaaaaaaaaaa", "Feat", artists=(("Daft Punk", DAFT), ("Romanthony", ROMAN)),
                                album="Discovery", thumbnails=None)]}

    def get_artist(self, channel_id):
        self._rec("artist", channel_id)
        return {"name": "Daft Punk", "channelId": DAFT_CHANNEL, "description": "word " * 200,
                "thumbnails": [{"url": f"{WIDE}=w1080-h450-p-l90-rj", "width": 1080}],
                "songs": {"browseId": "VLOLAK5uy_lNVBcjNtiCwq-n95uoxJ-Gd4vsfMkyZNs",
                          "results": [song(f"song{i:07d}") for i in range(12)]},
                "albums": {"results": [{"title": "Discovery", "browseId": ALBUM, "year": "2001", "thumbnails": thumbs()},
                                       {"title": "no id", "browseId": None}]},
                "singles": {"results": [{"title": "Get Lucky", "browseId": "MPREb_yw4jv9byKCr", "type": "Single",
                                         "thumbnails": thumbs()},
                                        {"title": "EP", "browseId": "MPREb_ep0000000", "type": "EP"}]},
                "related": {"results": [{"title": "Romanthony", "browseId": ROMAN, "thumbnails": thumbs()},
                                        {"title": "Justice", "browseId": OTHER, "thumbnails": thumbs()},
                                        {"title": "a playlist", "browseId": "VLPLxxxxxxxxxxxx"}]}}

    def get_playlist(self, list_id, limit=100):
        self._rec("playlist", list_id, limit=limit)
        if list_id == OLAK:
            if isinstance(self.audio, Exception):
                raise self.audio
            return {"title": "Discovery", "author": {"name": "Daft Punk", "id": DAFT}, "tracks": self.audio}
        return {"title": "Coffee Shop Blend", "author": {"name": "YouTube Music", "id": None},
                "thumbnails": thumbs(SQ, 544, 544),
                "tracks": [song("ccccccccccc"), song("ddddddddddd", "Feat", artists=(("Romanthony", ROMAN),))]}

    def get_watch_playlist(self, videoId=None, playlistId=None, limit=25, radio=False):
        self._rec("watch", videoId=videoId, playlistId=playlistId, limit=limit, radio=radio)
        return {"tracks": [song("eeeeeeeeeee")]}


@pytest.fixture
def fake(monkeypatch, tmp_path):
    f = FakeYT()
    monkeypatch.setattr(config, "YTMUSIC", True)
    monkeypatch.setattr(ytmusic, "_yt", f)
    monkeypatch.setattr(config, "BROWSE_FILE", tmp_path / "browse.json")
    monkeypatch.setattr(config, "BLOCKLIST_FILE", tmp_path / "blocklist.json")
    monkeypatch.setattr(ytmusic, "_browse", None)
    caches = (ytmusic._mood_cache, ytmusic._album_cache, ytmusic._artist_cache, ytmusic._playlist_cache)
    for cache in caches:
        cache.clear()
    yield f
    for cache in caches:
        cache.clear()
    with ytmusic._art_lock:  # module-global: other test files expect it clean
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


def names(fake):
    return [c[0] for c in fake.calls]


# ---------- ytmusic: moods ----------

def test_moods_flattened_in_order_with_stable_colors(fake):
    m = ytmusic.moods()
    assert [(x["section"], x["title"]) for x in m] == [
        ("Moods & moments", "Chill"), ("Moods & moments", "Diwali"), ("Genres", "African"), ("Genres", "Pop")]
    assert all(x["color"] in ytmusic.PALETTE and x["art"] is None for x in m)
    assert m[0]["color"] == ytmusic._color("Chill") == ytmusic.moods()[0]["color"]
    assert names(fake) == ["moods"]  # second call served from the cache


def test_moods_persist_across_restart_and_expire(fake):
    ytmusic.moods()
    saved = json.loads(config.BROWSE_FILE.read_text(encoding="utf-8"))
    assert [x["title"] for x in saved["moods"]["items"]] == ["Chill", "Diwali", "African", "Pop"]
    ytmusic._browse = None  # "restart"
    fake.fail = RuntimeError("offline")
    assert len(ytmusic.moods()) == 4 and names(fake) == ["moods"]
    ytmusic._browse["moods"]["at"] -= ytmusic.MOODS_TTL + 1
    with pytest.raises(RuntimeError):
        ytmusic.moods()
    assert len(ytmusic.cached_moods()) == 4  # stale but still served by /api/moods


def test_mood_playlists_shape_cache_and_art(fake):
    ytmusic.moods()
    out = ytmusic.mood_playlists(CHILL)
    assert out == [{"id": "RDCLAK5uy_nBE4bLuBHUXWZrF59ZrkPEToKt8M_I3Vc", "title": f"{CHILL} list",
                    "subtitle": "Playlist • YouTube Music", "thumb": f"{SQ}-{CHILL}=w544-h544-l90-rj"}]
    out[0]["title"] = "mutated"
    assert ytmusic.mood_playlists(CHILL)[0]["title"] == f"{CHILL} list"
    assert names(fake).count("mood") == 1
    chill = next(m for m in ytmusic.moods() if m["params"] == CHILL)
    assert chill["art"] == f"{SQ}-{CHILL}=w544-h544-l90-rj"
    assert ytmusic.mood_title(CHILL) == "Chill" and ytmusic.mood_title("nope") is None
    ytmusic._browse = None
    assert json.loads(config.BROWSE_FILE.read_text())["art"][CHILL]["url"].endswith("=w544-h544-l90-rj")


def test_refresh_mood_art_fills_missing_first_ones_first(fake):
    assert ytmusic.refresh_mood_art(first=[POP], pause=0) == 4
    assert [c[1][0] for c in fake.calls if c[0] == "mood"] == [POP, CHILL, DIWALI, AFRICAN]
    assert all(m["art"] for m in ytmusic.moods())
    fake.calls.clear()
    assert ytmusic.refresh_mood_art(pause=0) == 0 and "mood" not in names(fake)  # all fresh


# ---------- ytmusic: pages ----------

def test_album_page(fake):
    a = ytmusic.album(ALBUM)
    assert {k: a[k] for k in ("id", "title", "artist", "artists", "year", "thumb", "track_count", "duration",
                              "audio_playlist_id")} == {
        "id": ALBUM, "title": "Discovery", "artist": "Daft Punk", "artists": [{"name": "Daft Punk", "id": DAFT}],
        "year": "2001", "thumb": SQ + "=w544-h544-l90-rj", "track_count": 14, "duration": 3676,
        "audio_playlist_id": OLAK}
    assert [(t["id"], t["track_number"]) for t in a["tracks"]] == [("FGBhQbmPwH8", 1), ("L93-7vRfxNs", 2),
                                                                   ("aaaaaaaaaaa", 4)]  # position when missing
    t = a["tracks"][0]
    assert t["album"] == "Discovery" and t["album_id"] == ALBUM and t["thumb"] == SQ + "=w544-h544-l90-rj"
    assert ytmusic.square_art("FGBhQbmPwH8", 300) == SQ + "=w300-h300-l90-rj"
    a["tracks"].clear()
    assert len(ytmusic.album(ALBUM)["tracks"]) == 3 and names(fake) == ["album", "playlist"]  # cached, deep-copied


def test_album_from_audio_playlist_id(fake):
    assert ytmusic.album(OLAK)["id"] == ALBUM
    assert names(fake) == ["album_browse_id", "album", "playlist"]


def atv(vid, title, **kw):
    """An audio-only (ATV) row of the album's OLAK5uy_ playlist."""
    return song(vid, title, album=None, thumbnails=thumbs(SQ + "-atv", 544, 544), videoType="MUSIC_VIDEO_TYPE_ATV",
                **kw)


def test_album_swaps_video_ids_for_audio_ids_by_position(fake):
    fake.audio = [atv("ATV00000001", "One More Time (2001 remaster)"), atv("ATV00000002", "Aerodynamic"),
                  atv("ATV00000003", "Feat", duration_seconds=199)]
    fake.album_thumbs = None  # no album art: the audio rows' square art is better than 16:9 video stills
    a = ytmusic.album(ALBUM)
    assert [(t["id"], t["track_number"], t["title"]) for t in a["tracks"]] == [
        ("ATV00000001", 1, "One More Time"), ("ATV00000002", 2, "Aerodynamic"), ("ATV00000003", 4, "Feat")]
    feat = a["tracks"][2]
    assert feat["album"] == "Discovery" and feat["album_id"] == ALBUM and len(feat["artists"]) == 2
    assert feat["duration"] == 320  # the album row had one; only a missing duration is filled
    assert feat["thumb"] == SQ + "-atv=w544-h544-l90-rj"
    assert ytmusic.square_art("ATV00000001", 300) == SQ + "-atv=w300-h300-l90-rj"
    assert fake.calls[-1][:2] == ("playlist", (OLAK,))


def test_album_audio_ids_by_title_when_counts_differ(fake):
    fake.audio = [atv("ATV0000000x", "Unrelated"), atv("ATV00000002", "AERODYNAMIC!")]  # 2 rows vs the album's 3
    ids = [t["id"] for t in ytmusic.album(ALBUM)["tracks"]]
    assert ids == ["FGBhQbmPwH8", "ATV00000002", "aaaaaaaaaaa"]
    assert ytmusic.album(ALBUM)["tracks"][0]["thumb"] == SQ + "=w544-h544-l90-rj"  # album art kept


def test_album_reordered_audio_playlist_follows_titles(fake):
    """Equal counts but another order: positions would give every row the wrong song, so titles decide."""
    fake.audio = [atv("ATV0000feat", "Feat"), atv("ATV00000001", "One More Time (Remastered)"),
                  atv("ATV00000002", "Aerodynamic")]
    assert [t["id"] for t in ytmusic.album(ALBUM)["tracks"]] == ["ATV00000001", "ATV00000002", "ATV0000feat"]


def test_album_partial_title_mismatch_keeps_unmatched_ids(fake):
    fake.audio = [atv("ATV00000001", "One More Time"), atv("ATV0000000x", "Something Else"),
                  atv("ATV0000feat", "Feat")]
    assert [t["id"] for t in ytmusic.album(ALBUM)["tracks"]] == ["ATV00000001", "L93-7vRfxNs", "ATV0000feat"]


def test_album_keeps_video_ids_when_audio_playlist_fails(fake, capsys):
    fake.audio = RuntimeError("playlist down")
    a = ytmusic.album(ALBUM)
    assert a["title"] == "Discovery" and [t["id"] for t in a["tracks"]] == ["FGBhQbmPwH8", "L93-7vRfxNs",
                                                                             "aaaaaaaaaaa"]
    assert "keeping video ids" in capsys.readouterr().out


def test_artist_page(fake):
    a = ytmusic.artist(DAFT)
    assert a["id"] == DAFT and a["channel_id"] == DAFT_CHANNEL and a["name"] == "Daft Punk"
    assert a["thumb"] == WIDE + "=w544-h544-p-l90-rj"  # the wide banner, center-cropped square
    assert len(a["description"]) <= ytmusic.DESCRIPTION_MAX + 1 and a["description"].endswith("…")
    assert len(a["songs"]) == 10 and a["songs"][0]["artists"][0]["id"] == DAFT
    assert a["songs_playlist_id"] == "OLAK5uy_lNVBcjNtiCwq-n95uoxJ-Gd4vsfMkyZNs"
    assert a["albums"] == [{"id": ALBUM, "title": "Discovery", "year": "2001", "thumb": SQ + "=w544-h544-l90-rj",
                            "type": "Album"}]
    assert [(s["title"], s["type"]) for s in a["singles"]] == [("Get Lucky", "Single"), ("EP", "EP")]
    assert a["singles"][1]["thumb"] is None
    assert [r["id"] for r in a["related"]] == [ROMAN, OTHER]
    assert a["related"][0]["thumb"] == SQ + "=w544-h544-p-l90-rj"


def test_playlist_page_curated_and_mix(fake):
    p = ytmusic.playlist("VLRDCLAK5uy_nBE4bLuBHUXWZrF59ZrkPEToKt8M_I3Vc")
    assert fake.calls[-1][:2] == ("playlist", ("RDCLAK5uy_nBE4bLuBHUXWZrF59ZrkPEToKt8M_I3Vc",))
    assert {k: p[k] for k in ("id", "title", "author", "thumb")} == {
        "id": "RDCLAK5uy_nBE4bLuBHUXWZrF59ZrkPEToKt8M_I3Vc", "title": "Coffee Shop Blend",
        "author": "YouTube Music", "thumb": SQ + "=w544-h544-l90-rj"}
    assert [t["id"] for t in p["tracks"]] == ["ccccccccccc", "ddddddddddd"]
    mix = ytmusic.playlist("RDAMVMdQw4w9WgXcQ")
    assert fake.calls[-1][0] == "watch" and mix["title"] is None and mix["tracks"][0]["id"] == "eeeeeeeeeee"


# ---------- routes ----------

def test_api_moods_sections(client, fake):
    body = client.get("/api/moods").get_json()
    assert [s["title"] for s in body["sections"]] == ["Moods & moments", "Genres"]
    assert body["sections"][1]["items"][1] == {"title": "Pop", "params": POP, "color": ytmusic._color("Pop"),
                                               "art": None}
    assert "stale" not in body


def test_api_moods_stale_fallback_and_502(client, fake):
    fake.fail = RuntimeError("down")
    r = client.get("/api/moods")
    assert r.status_code == 502 and "down" in r.get_json()["error"]
    fake.fail = None
    ytmusic.moods()
    ytmusic._browse["moods"]["at"] = 0
    fake.fail = RuntimeError("down")
    body = client.get("/api/moods").get_json()
    assert body["stale"] is True and len(body["sections"]) == 2


def test_api_mood(client, fake):
    ytmusic.moods()
    body = client.get("/api/mood", query_string={"params": CHILL}).get_json()
    assert body["title"] == "Chill" and body["playlists"][0]["id"].startswith("RDCLAK")
    assert client.get("/api/mood", query_string={"params": "<script>"}).status_code == 400
    assert client.get("/api/mood").status_code == 400
    fake.fail = RuntimeError("down")
    assert client.get("/api/mood", query_string={"params": POP}).status_code == 502


@pytest.mark.parametrize("path", ["/api/album/MPRE", "/api/album/PLabcdefghijkl12", "/api/album/OLAK5uy_",
                                  "/api/artist/UCshort", "/api/artist/" + DAFT + "x", "/api/artist/XY" + DAFT[2:],
                                  "/api/playlist/short", "/api/playlist/PL<b>abcdefghij"])
def test_page_routes_reject_bad_ids(client, fake, path):
    assert client.get(path).status_code == 404 and fake.calls == []


def test_api_album_marks_kept_and_prefetches(client, fake):
    history.add({"id": "L93-7vRfxNs", "title": "Aerodynamic"})
    body = client.get(f"/api/album/{ALBUM}").get_json()
    assert body["title"] == "Discovery" and body["blocked"] is False
    assert [t["kept"] for t in body["tracks"]] == [False, True, False]
    assert client.prefetched == ["FGBhQbmPwH8"]
    assert client.get(f"/api/album/{OLAK}").status_code == 200


def test_api_pages_502_on_upstream_error_or_off(client, fake, monkeypatch):
    fake.fail = RuntimeError("upstream")
    for path in (f"/api/album/{ALBUM}", f"/api/artist/{DAFT}", "/api/playlist/PLabcdefghijkl12"):
        r = client.get(path)
        assert r.status_code == 502 and "upstream" in r.get_json()["error"]
    monkeypatch.setattr(config, "YTMUSIC", False)
    assert client.get(f"/api/album/{ALBUM}").status_code == 502


def test_api_album_blocklist(client, fake):
    blocklist.add(ROMAN, "Romanthony")
    body = client.get(f"/api/album/{ALBUM}").get_json()
    assert [t["id"] for t in body["tracks"]] == ["FGBhQbmPwH8", "L93-7vRfxNs"] and body["blocked"] is False
    blocklist.add(None, "daft punk")  # the album's own artist: explicit navigation, so it still opens, flagged
    body = client.get(f"/api/album/{ALBUM}").get_json()
    assert body["blocked"] is True and len(body["tracks"]) == 3


def test_api_artist(client, fake):
    blocklist.add(OTHER, "Justice")
    body = client.get(f"/api/artist/{DAFT}").get_json()
    assert body["blocked"] is False and len(body["songs"]) == 10 and "kept" in body["songs"][0]
    assert [r["name"] for r in body["related"]] == ["Romanthony"]
    assert client.prefetched == [body["songs"][0]["id"]]
    blocklist.add(DAFT_CHANNEL, "Daft Punk")
    body = client.get(f"/api/artist/{DAFT}").get_json()
    assert body["blocked"] is True and len(body["songs"]) == 10


def test_api_playlist_filters_blocked(client, fake):
    body = client.get("/api/playlist/RDCLAK5uy_nBE4bLuBHUXWZrF59ZrkPEToKt8M_I3Vc").get_json()
    assert body["title"] == "Coffee Shop Blend" and len(body["tracks"]) == 2
    blocklist.add(ROMAN, "Romanthony")
    ytmusic._playlist_cache.clear()
    body = client.get("/api/playlist/RDCLAK5uy_nBE4bLuBHUXWZrF59ZrkPEToKt8M_I3Vc").get_json()
    assert [t["id"] for t in body["tracks"]] == ["ccccccccccc"]


def test_link_album_still_works(client, fake):
    body = client.get("/api/link", query_string={"url": f"https://music.youtube.com/browse/{ALBUM}"}).get_json()
    assert body["kind"] == "playlist" and body["title"] == "Discovery" and len(body["tracks"]) == 3


# ---------- home tiles ----------

def test_tiles_from_moods_with_preference(fake, monkeypatch):
    assert tiles.load()[0]["query"] == "top hits this week"  # moods never fetched: search tiles
    ytmusic.moods()
    ytmusic.mood_playlists(CHILL)
    t = tiles.load()
    assert [x["label"] for x in t] == ["Pop", "Chill", "Diwali", "African"]
    assert t[1] == {"label": "Chill", "color": ytmusic._color("Chill"), "query": "Chill", "ids": [],
                    "params": CHILL, "art": f"{SQ}-{CHILL}=w544-h544-l90-rj"}
    assert app.app.test_client().get("/api/tiles").get_json() == t
    assert b'"params": "ggMPOchill0000"' in app.app.test_client().get("/").data.replace(b'":"', b'": "')
    monkeypatch.setattr(config, "YTMUSIC", False)
    fallback = tiles.load()
    assert len(fallback) == len(tiles.TILES) and fallback[0]["params"] is None and fallback[0]["art"] is None


def test_tiles_refresh_uses_moods_then_falls_back(fake, monkeypatch):
    searched = []
    monkeypatch.setattr(tiles, "_refresh_searches", lambda: searched.append(1))
    monkeypatch.setattr(ytmusic, "refresh_mood_art", lambda first=(), pause=0.5: len(first))
    tiles.refresh()
    assert searched == [] and "moods" in names(fake)
    ytmusic._browse["moods"]["at"] = 0
    fake.fail = RuntimeError("down")
    tiles.refresh()
    assert searched == [1]


# ---------- album keep: tag hints ----------

def test_with_hints_override_and_fill():
    tags = {"meta_title": "One More Time", "meta_artist": "Daft Punk", "meta_album": "One More Time (Single)",
            "meta_track": "1", "meta_date": "2000"}
    out = with_hints(tags, {"album": "Discovery", "album_artist": "Daft Punk", "track_number": 1, "year": "2001"})
    assert out == {"meta_title": "One More Time", "meta_artist": "Daft Punk", "meta_album": "Discovery",
                   "meta_album_artist": "Daft Punk", "meta_track": "1", "meta_date": "2001"}
    # Another album without a track number: the single's track number would be wrong, so it goes.
    assert "meta_track" not in with_hints(tags, {"album": "Discovery"})
    assert with_hints(tags, {}) == tags and with_hints(tags, None) == tags


def test_tags_pp_applies_hints_for_ffmpeg_metadata():
    from yt_dlp.postprocessor.ffmpeg import FFmpegMetadataPP
    ydl = yt_dlp.YoutubeDL(downloader._options({}))
    pp = downloader._MusicTagsPP(ydl, {"album": "Discovery", "album_artist": "Daft Punk", "track_number": 2,
                                       "year": "2001"})
    _, info = pp.run({"title": "Daft Punk - Aerodynamic (Official Audio)", "upload_date": "20090101"})
    args = list(FFmpegMetadataPP(ydl)._get_metadata_opts(info))
    for want in ("title=Aerodynamic", "album=Discovery", "album_artist=Daft Punk", "track=2", "date=2001"):
        assert ("-metadata", want) in args
    hinted = [p for p, _ in downloader._custom_pps(ydl, {"album": "X"}) if isinstance(p, downloader._MusicTagsPP)]
    assert hinted[0].hints == {"album": "X"}


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
def test_generic_tag_writes_album_fields(tmp_path):
    from mutagen import File as MutagenFile
    path = tmp_path / "x.flac"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "anullsrc=r=8000:cl=mono", "-t", "0.2",
                    str(path)], check=True)
    downloader._tag(path, "Aerodynamic", "Daft Punk", "Discovery", None, None, album_artist="Daft Punk",
                    track=2, year="2001")
    f = MutagenFile(path)
    assert (f["album"], f["albumartist"], f["tracknumber"], f["date"]) == (
        ["Discovery"], ["Daft Punk"], ["2"], ["2001"])


@pytest.fixture
def keeps(monkeypatch):
    monkeypatch.setattr(downloader, "_queue", queue.Queue())  # no worker: queued jobs just sit there
    monkeypatch.setattr(downloader, "_jobs", {})
    config.HISTORY_FILE.write_text("[]", encoding="utf-8")
    yield app.app.test_client()
    config.HISTORY_FILE.write_text("[]", encoding="utf-8")


def _post(c, path, body):
    return c.post(path, data=json.dumps(body), content_type="application/json")


def test_download_stores_hints(keeps):
    r = _post(keeps, "/api/download", {"id": "FGBhQbmPwH8", "title": "One More Time", "album": " Discovery ",
                                       "album_artist": "Daft Punk", "track_number": "1", "year": 2001})
    assert r.status_code == 202
    assert r.get_json()["hints"] == {"album": "Discovery", "album_artist": "Daft Punk", "track_number": 1,
                                     "year": "2001"}
    assert _post(keeps, "/api/download", {"id": "L93-7vRfxNs"}).get_json()["hints"] == {}


@pytest.mark.parametrize("bad", [{"album": 5}, {"album": "x" * 201}, {"track_number": 0}, {"track_number": 1000},
                                 {"track_number": True}, {"track_number": "1a"}, {"track_number": 1.5},
                                 {"year": "01"}, {"year": "２００１"}, {"year": True}, {"album_artist": ["x"]}])
def test_download_rejects_bad_hints(keeps, bad):
    r = _post(keeps, "/api/download", {"id": "FGBhQbmPwH8", **bad})
    assert r.status_code == 400 and r.get_json()["error"] == "invalid album hints"
    assert downloader._queue.qsize() == 0


def test_download_batch(keeps, tmp_path):
    f = tmp_path / "kept.opus"
    f.write_bytes(b"x")
    history.add({"id": "aaaaaaaaaaa", "title": "Kept", "file": str(f)})
    tracks = [
        {"id": "FGBhQbmPwH8", "title": "One More Time", "channel": "Daft Punk", "album": "Discovery",
         "album_artist": "Daft Punk", "track_number": 1, "year": "2001"},
        {"id": "L93-7vRfxNs", "title": "Aerodynamic", "album": "Discovery", "track_number": 2},
        {"id": "FGBhQbmPwH8"},                          # same track twice: already keeping
        {"id": "aaaaaaaaaaa"},                          # in the library
        {"id": "nd:123"},                               # library source: can't keep
        {"id": "bad id"}, {"id": "bbbbbbbbbbb", "track_number": -1}, "not a dict",
    ]
    r = _post(keeps, "/api/download/batch", {"tracks": tracks})
    assert r.status_code == 202
    body = r.get_json()
    assert [(j["video_id"], j["hints"].get("track_number")) for j in body["queued"]] == [
        ("FGBhQbmPwH8", 1), ("L93-7vRfxNs", 2)]
    assert body["queued"][0]["hints"]["album_artist"] == "Daft Punk"
    assert body["skipped"] == [{"id": "FGBhQbmPwH8", "reason": "keeping"}, {"id": "aaaaaaaaaaa", "reason": "kept"},
                               {"id": "nd:123", "reason": "invalid"}, {"id": "bad id", "reason": "invalid"},
                               {"id": "bbbbbbbbbbb", "reason": "invalid"}, {"id": None, "reason": "invalid"}]
    assert downloader._queue.qsize() == 2


@pytest.mark.parametrize("body", [{}, {"tracks": []}, {"tracks": "x"}, {"tracks": [{"id": "FGBhQbmPwH8"}] * 101}])
def test_download_batch_limits(keeps, body):
    assert _post(keeps, "/api/download/batch", body).status_code == 400
    assert downloader._queue.qsize() == 0


def _two_row(title, browse_id=None):
    run = {"text": title}
    if browse_id:
        run["navigationEndpoint"] = {"browseEndpoint": {"browseId": browse_id}}
    return {"musicTwoRowItemRenderer": {
        "title": {"runs": [run]}, "subtitle": {"runs": [{"text": "Playlist • YouTube Music"}]},
        "thumbnailRenderer": {"musicThumbnailRenderer": {"thumbnail": {"thumbnails": thumbs(SQ, 544, 544)}}}}}


def test_mood_playlists_skip_songs_mixed_into_carousels(fake, monkeypatch):
    """ytmusicapi 1.12.3's get_mood_playlists raises on a carousel that mixes a song in; ours skips it."""
    carousel = {"musicCarouselShelfRenderer": {"contents": [
        _two_row("Beautiful Things"),  # a song: no browse endpoint
        _two_row("Bubble Pop", "VLRDCLAK5uy_lGEOjy5U8xV41C8_LyqNnAZKOH6sGyutI")]}}
    response = {"contents": {"singleColumnBrowseResultsRenderer": {"tabs": [{"tabRenderer": {"content": {
        "sectionListRenderer": {"contents": [carousel, {"somethingElse": {}}]}}}}]}}}
    monkeypatch.setattr(fake, "_send_request", lambda endpoint, body: fake._rec("raw", endpoint, body) or response,
                        raising=False)
    out = ytmusic.mood_playlists(POP)
    assert [(p["id"], p["title"]) for p in out] == [("RDCLAK5uy_lGEOjy5U8xV41C8_LyqNnAZKOH6sGyutI", "Bubble Pop")]
    assert names(fake) == ["raw"] and fake.calls[0][1][1]["params"] == POP
    # Nothing parsed (or the library's internals moved): the library's own call is the fallback.
    ytmusic._mood_cache.clear()
    monkeypatch.setattr(fake, "_send_request", lambda endpoint, body: {}, raising=False)
    assert len(ytmusic.mood_playlists(POP)) == 1 and names(fake)[-1] == "mood"
