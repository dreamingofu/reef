"""YouTube Music charts (charts.py, `GET /api/charts`, Home's "Charts" shelf): sections and ranks from the charts page
and its playlists, the songs chart looked up by title, the 6-hour cache, partial and failed fetches, the route's
validation / marking / filtering, and the charts page parser picking the artists carousel. Offline: fakes only."""
import pytest

import app
import blocklist
import charts
import config
import history
import home
import settings
import streamer
import ytmusic

ART = "UC" + "a" * 22
ART2 = "UC" + "b" * 22
VIDEOS, TRENDING, SONGS_US = "PL4fGSI1pDJnVIDEOSxx", "OLAK5uy_trendingxxxx", "PL4fGSI1pDJnSONGSUSx"


def tr(n, artist="Chart Artist", aid=None):
    return {"id": f"c{n:010d}", "title": f"Hit {n}", "channel": artist, "artists": [{"name": artist, "id": aid}],
            "album": None, "album_id": None, "duration": 180, "thumb": None, "explicit": False, "source": "yt"}


class FakeYTM:
    def __init__(self):
        self.calls, self.fail = [], set()

    def charts(self, country):
        self.calls.append(("charts", country))
        if "charts" in self.fail:
            raise RuntimeError("HTTP 503")
        name = {"ZZ": "Global", "US": "United States"}.get(country, country)
        return {"country": country, "name": name, "countries": ["DE", "US", "ZZ"],
                "playlists": [{"id": "PL4fGSI1pDJnDAILYxxx", "title": f"Daily Top Music Videos - {name}", "group": "Video charts"},
                              {"id": VIDEOS, "title": f"Top 100 Music Videos {name}", "group": "Video charts"},
                              {"id": TRENDING, "title": f"Trending 20 {name}", "group": "Video charts"}],
                "artists": [{"id": ART, "name": "Chart Artist", "rank": 1, "trend": "up", "subscribers": "1M",
                             "thumb": None},
                            {"id": ART2, "name": "Other", "rank": 2, "trend": None, "subscribers": None, "thumb": None}]}

    def playlist(self, list_id, limit=100):
        self.calls.append(("playlist", list_id, limit))
        if list_id in self.fail:
            raise RuntimeError("timeout")
        base = {VIDEOS: 100, TRENDING: 200}.get(list_id, 0)
        return {"id": list_id, "tracks": [tr(base + i, "Chart Artist" if i == 0 else f"A{i}", ART if i == 0 else None)
                                          for i in range(12)]}

    def chart_playlist(self, title):
        self.calls.append(("search", title))
        return SONGS_US if title == "Top 100 Songs United States" else None


@pytest.fixture
def fake(monkeypatch, tmp_path):
    f = FakeYTM()
    monkeypatch.setattr(ytmusic, "charts", f.charts)
    monkeypatch.setattr(ytmusic, "playlist", f.playlist)
    monkeypatch.setattr(ytmusic, "chart_playlist", f.chart_playlist)
    monkeypatch.setattr(ytmusic, "enabled", lambda: True)
    monkeypatch.setattr(ytmusic, "retry_after", lambda max_age=300: None)
    monkeypatch.setattr(charts, "_cache", {})
    monkeypatch.setattr(charts, "_lists", {})
    monkeypatch.setattr(charts, "_failed", {})
    monkeypatch.setattr(config, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(config, "BLOCKLIST_FILE", tmp_path / "blocklist.json")
    monkeypatch.setattr(config, "ALIASES_FILE", tmp_path / "aliases.json")
    monkeypatch.setattr(streamer, "prefetch", lambda ids: len(ids))
    return f


@pytest.fixture
def client(fake):
    return app.app.test_client()


def test_global_sections_ranks_and_cache(fake):
    doc = charts.get("ZZ")
    assert doc["country"] == "ZZ" and doc["name"] == "Global" and doc["countries"] == ["DE", "US", "ZZ"]
    assert [(s["kind"], s["title"]) for s in doc["sections"]] == [
        ("tracks", "Top 100 Songs Global"), ("videos", "Top 100 Music Videos Global"),
        ("trending", "Trending 20 Global"), ("artists", "Top artists")]
    songs = doc["sections"][0]
    assert songs["playlist_id"] == ytmusic.SONGS_GLOBAL and [t["rank"] for t in songs["items"][:3]] == [1, 2, 3]
    assert doc["sections"][3]["items"][0] == {"id": ART, "name": "Chart Artist", "rank": 1, "trend": "up",
                                               "subscribers": "1M", "thumb": None}
    assert ("search", "Top 100 Songs Global") not in fake.calls  # the global songs chart is known
    assert ("playlist", TRENDING, charts.TRENDING_MAX) in fake.calls
    n = len(fake.calls)
    doc["sections"][0]["items"].clear()  # callers get copies
    again = charts.get("ZZ")
    assert len(fake.calls) == n and len(again["sections"][0]["items"]) == 12


def test_country_songs_chart_found_by_title_or_left_out(fake):
    us = charts.get("US")
    assert ("search", "Top 100 Songs United States") in fake.calls
    assert us["sections"][0]["kind"] == "tracks" and us["sections"][0]["playlist_id"] == SONGS_US
    de = charts.get("DE")
    assert [s["kind"] for s in de["sections"]] == ["videos", "trending", "artists"]
    n = len(fake.calls)
    charts._cache.clear()
    charts.get("DE")  # the miss is remembered: no second search
    assert ("search", "Top 100 Songs DE") not in fake.calls[n:]


def test_partial_fetch_gets_a_short_ttl_and_failed_refetch_serves_stale(fake):
    fake.fail.add(VIDEOS)
    doc = charts.get("ZZ")
    assert "videos" not in [s["kind"] for s in doc["sections"]]
    assert charts._cache["ZZ"][1] == charts.PARTIAL_TTL
    fake.fail = {"charts"}
    stored, _, _ = charts._cache["ZZ"]
    charts._cache["ZZ"] = (stored - charts.TTL, charts.TTL, charts._cache["ZZ"][2])
    stale = charts.get("ZZ")
    assert stale["stale"] is True and stale["sections"][0]["kind"] == "tracks"
    assert charts.fresh("ZZ")  # not asked again for RETRY_S
    charts._cache.clear()
    with pytest.raises(RuntimeError):
        charts.get("ZZ")
    assert charts.warm() is False  # a failure waits RETRY_S


def test_warm_follows_the_setting_and_home_shelf(fake):
    settings.update({"charts_country": "us"})  # upper-cased
    assert settings.get("charts_country") == "US"
    assert charts.home_shelf() is None  # Home only reads the cache
    assert charts.warm() is True and charts.warm() is False  # fresh now
    shelf = charts.home_shelf()
    assert shelf["country"] == "US" and shelf["section"] == "Top 100 Songs United States"
    assert len(shelf["items"]) == charts.HOME_ITEMS and shelf["items"][0]["rank"] == 1
    assert shelf["playlist_id"] and shelf["playlist_id"] == charts.get("US")["sections"][0]["playlist_id"]  # "See all"
    charts.get("DE")
    assert charts.home_shelf("DE")["section"] == "Top 100 Music Videos DE"  # no songs chart: the videos one


def test_api_charts(client, fake):
    r = client.get("/api/charts")
    body = r.get_json()
    assert r.status_code == 200 and body["country"] == "ZZ"
    assert [s["kind"] for s in body["sections"]] == ["tracks", "videos", "trending", "artists"]
    first = body["sections"][0]["items"][0]
    assert first["kept"] is False and first["liked"] is False and first["rank"] == 1
    assert client.get("/api/charts?country=us").get_json()["country"] == "US"
    for bad in ("USA", "1", "u"):
        assert client.get(f"/api/charts?country={bad}").status_code == 400
    r = client.get("/api/charts?country=FR")  # not in YouTube's list
    assert r.status_code == 400 and "ZZ" in r.get_json()["countries"]


def test_api_charts_filters_blocked_artists_keeps_ranks(client, fake):
    blocklist.add(ART, "Chart Artist")
    body = client.get("/api/charts").get_json()
    songs = body["sections"][0]["items"]
    assert songs[0]["rank"] == 2 and all(t["channel"] != "Chart Artist" for t in songs)
    assert [a["id"] for a in body["sections"][3]["items"]] == [ART2]


def test_api_charts_errors(client, fake, monkeypatch):
    fake.fail.add("charts")
    assert client.get("/api/charts").status_code == 502
    monkeypatch.setattr(ytmusic, "enabled", lambda: False)
    assert client.get("/api/charts").status_code == 502


def test_home_charts_shelf_before_moods_and_filtered(fake, monkeypatch, tmp_path, client):
    for name in ("LISTENS_FILE", "LIKES_FILE", "DISLIKES_FILE", "HOME_FILE"):
        monkeypatch.setattr(config, name, tmp_path / f"{name.lower()}.json")
    monkeypatch.setattr(history, "HISTORY_FILE", tmp_path / "history.json")
    monkeypatch.setattr(home.tiles, "load", lambda: [{"label": "Chill", "color": "#000", "query": "Chill", "ids": [],
                                                       "params": None, "art": None}])
    monkeypatch.setattr(ytmusic, "radio", lambda vid, limit=50: [])
    home.refresh()
    assert [s["id"] for s in client.get("/api/home").get_json()["shelves"]] == ["moods"]
    charts.warm()
    blocklist.add(None, "A1")
    shelves = client.get("/api/home").get_json()["shelves"]
    assert [s["id"] for s in shelves] == ["charts", "moods"]
    c = shelves[0]
    assert c["title"] == "Charts" and c["kind"] == "tracks" and c["country"] == "ZZ"
    assert c["section"] == "Top 100 Songs Global" and "A1" not in [t["channel"] for t in c["items"]]
    assert all("kept" in t for t in c["items"])


# ---------- the charts page parser ----------

def _run(text):
    return {"runs": [{"text": text}]}


def _playlist_item(list_id, title):
    return {"musicTwoRowItemRenderer": {
        "title": {"runs": [{"text": title, "navigationEndpoint": {"browseEndpoint": {"browseId": "VL" + list_id}}}]},
        "thumbnailRenderer": {"musicThumbnailRenderer": {"thumbnail": {"thumbnails": []}}}}}


def _list_item(browse_id, title, rank=None):
    item = {"flexColumns": [{"musicResponsiveListItemFlexColumnRenderer": {"text": _run(title)}}],
            "navigationEndpoint": {"browseEndpoint": {"browseId": browse_id}},
            "thumbnail": {"musicThumbnailRenderer": {"thumbnail": {"thumbnails": []}}}}
    if rank:
        item["customIndexColumn"] = {"musicCustomIndexColumnRenderer": {"text": _run(str(rank)),
                                                                        "icon": {"iconType": "ARROW_DROP_UP"}}}
    return {"musicResponsiveListItemRenderer": item}


def _carousel(title, items):
    return {"musicCarouselShelfRenderer": {
        "header": {"musicCarouselShelfBasicHeaderRenderer": {"title": _run(title)}}, "contents": items}}


def test_tolerant_parser_skips_the_albums_carousel(monkeypatch):
    if ytmusic.YTMusic is None:
        pytest.skip("ytmusicapi missing")
    response = {
        "contents": {"singleColumnBrowseResultsRenderer": {"tabs": [{"tabRenderer": {"content": {"sectionListRenderer": {
            "contents": [
                {"musicShelfRenderer": {"subheaders": [{"musicSideAlignedItemRenderer": {"startItems": [
                    {"musicSortFilterButtonRenderer": {"title": _run("United States")}}]}}]}},
                _carousel("Video charts", [_playlist_item(VIDEOS, "Top 100 Music Videos United States")]),
                _carousel("Top albums", [_list_item("MPREb_abcdefghijk", "An album", 1)]),
                _carousel("Top artists", [_list_item(ART, "Chart Artist", 1), _list_item(ART2, "Other", 2)]),
                _carousel("Weekly top podcast shows", [_list_item("MPSPPLxxxxxxxxxxxx", "A podcast", 1)]),
            ]}}}}]}},
        "frameworkUpdates": {"entityBatchUpdate": {"mutations": [
            {"payload": {"musicFormBooleanChoice": {"opaqueToken": "US"}}},
            {"payload": {"musicFormBooleanChoice": {"opaqueToken": "ZZ"}}}, {"payload": {}}]}}}

    class Client:
        def _send_request(self, endpoint, body):
            assert endpoint == "browse" and body == {"browseId": "FEmusic_charts", "formData": {"selectedValues": ["US"]}}
            return response

    monkeypatch.setattr(ytmusic, "enabled", lambda: True)
    monkeypatch.setattr(ytmusic, "_client", lambda: Client())
    page = ytmusic.charts("US")
    assert page["name"] == "United States" and page["countries"] == ["US", "ZZ"]
    assert page["playlists"] == [{"id": VIDEOS, "title": "Top 100 Music Videos United States", "group": "Video charts"}]
    assert [(a["id"], a["rank"], a["trend"]) for a in page["artists"]] == [(ART, 1, "up"), (ART2, 2, "up")]
