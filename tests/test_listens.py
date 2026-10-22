"""Listen log: the counting rule, the store (upserts, cap, atomic writes, threads), stats / recap aggregates
and the /api/listens, /api/stats, /api/recap routes."""
import json
import threading
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

import app
import config
import listens

UTC = timezone.utc
NY = ZoneInfo("America/New_York")
YT, YT2, YT3 = "dQw4w9WgXcQ", "FGBhQbmPwH8", "aaaaaaaaaaa"


def ts(*args, tz=UTC):
    return datetime(*args, tzinfo=tz).timestamp()


def listen(tid=YT, at=0.0, played=200.0, duration=200, **kw):
    """A stored-shape entry (what make_entry produces), for the pure aggregate tests."""
    counted = listens.counts(played, duration)
    e = {"id": tid, "title": kw.pop("title", f"t-{tid}"), "channel": kw.pop("channel", "Chan"), "duration": duration,
         "source": "yt", "played_s": played, "started_at": int(at), "ended_at": int(at + played),
         "counted": counted, "skipped": kw.pop("skipped", False) and not counted}
    e.update(kw)
    return e


@pytest.fixture(autouse=True)
def lfile(monkeypatch, tmp_path):
    path = tmp_path / "listens.json"
    monkeypatch.setattr(config, "LISTENS_FILE", path)
    return path


@pytest.fixture
def client():
    return app.app.test_client()


# ---------- counting rule and entries ----------

@pytest.mark.parametrize("played,duration,want", [
    (30, None, True), (29.9, None, False), (29.9, 3600, False), (100, 200, True), (25, 60, False), (25, 50, True),
    (12, 20, True), (5, 0, False), (0, 0, False),
])
def test_counting_rule(played, duration, want):
    assert listens.counts(played, duration) is want


def test_make_entry_cleans_and_decides():
    now = ts(2026, 10, 22, 12)
    e = listens.make_entry({"id": YT, "title": " Song ", "channel": "Chan", "duration": "212.4", "played_s": 40,
                            "started_at": now - 50, "skipped": True, "artists": [{"name": "A", "id": "UCx"}, "junk"],
                            "album": "Al", "album_id": "MPREb_x", "source": "mc", "extra": "dropped", "via": "nd"}, now)
    assert e == {"id": YT, "title": "Song", "channel": "Chan", "duration": 212, "source": "yt", "album": "Al",
                 "album_id": "MPREb_x", "artists": [{"name": "A", "id": "UCx"}], "played_s": 40.0,
                 "started_at": int(now - 50), "ended_at": int(now), "counted": True, "skipped": False, "via": "nd"}
    short = listens.make_entry({"id": "mc:123", "played_s": 4.26, "duration": 200, "started_at": now - 5,
                                "skipped": True}, now)
    assert (short["counted"], short["skipped"], short["played_s"], short["source"]) == (False, True, 4.3, "mc")


@pytest.mark.parametrize("body,msg", [
    (None, "JSON object"), ({"id": "nope", "played_s": 3}, "track id"), ({"id": "xx:1", "played_s": 3}, "track id"),
    ({"id": YT}, "played_s"), ({"id": YT, "played_s": "abc"}, "played_s"), ({"id": YT, "played_s": -1}, "played_s"),
    ({"id": YT, "played_s": float("nan")}, "played_s"), ({"id": YT, "played_s": True}, "played_s"),
])
def test_make_entry_rejects(body, msg):
    with pytest.raises(ValueError, match=msg):
        listens.make_entry(body, 1.8e9)


def test_implausible_start_is_replaced():
    now = 1.8e9
    for bad in (0, "x", now + 3600, 12345):
        assert listens.make_entry({"id": YT, "played_s": 60, "started_at": bad}, now)["started_at"] == int(now - 60)


# ---------- store ----------

def test_record_upserts_the_same_play(lfile):
    now = 1.8e9
    body = {"id": YT, "played_s": 10, "duration": 200, "started_at": now - 100, "skipped": False}
    e, created = listens.record(body, now)
    assert created and not e["counted"]
    e, created = listens.record({**body, "played_s": 95, "skipped": True}, now + 90)  # hidden, then skipped later
    assert not created and e["counted"] and e["played_s"] == 95 and e["skipped"] is False
    e, created = listens.record({**body, "played_s": 20}, now + 100)  # an older, shorter report: ignored
    assert not created and e["played_s"] == 95
    assert len(json.loads(lfile.read_text())) == 1


def test_entries_stay_sorted_and_capped(monkeypatch, lfile):
    monkeypatch.setattr(listens, "MAX", 5)
    base = 1.8e9
    for i in (3, 1, 2, 7, 6, 5, 4):
        listens.record({"id": YT, "played_s": 60, "started_at": base + i * 1000}, base + 10 ** 5)
    starts = [e["started_at"] for e in listens.entries()]
    assert starts == [int(base + i * 1000) for i in (3, 4, 5, 6, 7)]  # oldest dropped
    assert not (lfile.parent / "listens.json.tmp").exists()


def test_store_survives_a_broken_file_and_sees_external_changes(lfile):
    lfile.write_text("{not json")
    assert listens.entries() == []
    listens.record({"id": YT, "played_s": 60, "started_at": 1.8e9}, 1.8e9 + 60)
    assert len(listens.entries()) == 1
    lfile.write_text(json.dumps([listen(YT2, 1.7e9), listen(YT3, 1.71e9)]))  # e.g. restored from a backup
    assert [e["id"] for e in listens.entries()] == [YT2, YT3]


def test_concurrent_records_all_land(lfile):
    base = 1.8e9

    def worker(n):
        for i in range(25):
            listens.record({"id": YT, "played_s": 31, "started_at": base + n * 1000 + i}, base + 10 ** 5)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(listens.entries()) == 200
    assert len(json.loads(lfile.read_text())) == 200


def test_recent_pages_and_filters():
    base = 1.8e9
    for i in range(6):
        listens.record({"id": YT, "played_s": 60 if i % 2 else 5, "duration": 300, "started_at": base + i * 10},
                       base + 1000)
    assert [e["started_at"] for e in listens.recent(3)] == [int(base + 50), int(base + 40), int(base + 30)]
    assert [e["started_at"] for e in listens.recent(10, before=base + 30)] == [int(base + 20), int(base + 10), int(base)]
    assert all(e["counted"] for e in listens.recent(10, counted=True)) and len(listens.recent(10, counted=False)) == 3


# ---------- aggregates ----------

def test_aggregate_tops_and_minutes():
    t0 = ts(2026, 10, 20, 12)
    items = [
        listen(YT, t0, 200, channel="Daft Punk - Topic"),
        listen(YT, t0 + 300, 200, channel="Daft Punk - Topic"),
        listen(YT2, t0 + 600, 100, artists=[{"name": "Daft Punk", "id": "UCdp"}, {"name": "Pharrell", "id": None}],
               album="Random Access Memories", album_id="MPREb_ram"),
        listen(YT3, t0 + 900, 6, skipped=True, channel="Someone"),
        listen(YT3, t0 + 950, 6, skipped=True, channel="Someone"),
    ]
    out = listens.aggregate(items)
    assert out["listens"] == 3 and out["skips"] == 2 and out["minutes"] == round(512 / 60, 1)
    assert [(t["id"], t["count"]) for t in out["top_tracks"]] == [(YT, 2), (YT2, 1)]
    assert out["top_tracks"][0]["minutes"] == round(400 / 60, 1)
    artists = {a["name"]: a for a in out["top_artists"]}
    assert artists["Daft Punk"]["count"] == 3 and artists["Daft Punk"]["id"] == "UCdp"  # Topic channel merged
    assert artists["Pharrell"]["count"] == 1 and "Someone" not in artists  # skips don't make a top artist
    assert out["top_albums"] == [{"name": "Random Access Memories", "artist": "Daft Punk", "album_id": "MPREb_ram",
                                  "cover_id": YT2, "count": 1, "minutes": round(100 / 60, 1)}]
    assert out["most_skipped"] == [{"id": YT3, "title": f"t-{YT3}", "channel": "Someone", "count": 2}]
    assert out["first_listen_at"] == int(t0) and out["tracks_count"] == 2 and out["artists_count"] == 2


def test_by_hour_and_weekday_use_the_given_zone():
    at = ts(2026, 10, 20, 3, 30)  # Tuesday 03:30 UTC = Monday 23:30 in New York (EDT)
    items = [listen(YT, at, 100)]
    utc, ny = listens.aggregate(items, tz=UTC), listens.aggregate(items, tz=NY)
    assert utc["by_hour"][3] == 1 and utc["by_weekday"][1] == 1
    assert ny["by_hour"][23] == 1 and ny["by_weekday"][0] == 1
    assert sum(ny["by_hour"]) == 1 and len(ny["by_hour"]) == 24 and len(ny["by_weekday"]) == 7


def test_stats_periods_are_rolling_windows():
    now = ts(2026, 10, 22, 12)
    items = [listen(YT, now - d * 86400, 100) for d in (1, 6, 8, 29, 31, 364, 366)]
    got = {p: listens.stats(items, p, now=now, tz=UTC)["listens"] for p in listens.PERIODS}
    assert got == {"week": 2, "month": 4, "year": 6, "all": 7}
    s = listens.stats(items, "week", now=now, tz=UTC)
    assert s["period"] == "week" and s["to"] == int(now) and s["from"] == int(now - 7 * 86400)
    assert listens.stats(items, "all", now=now, tz=UTC)["from"] is None


def test_streaks():
    now = ts(2026, 10, 22, 20)
    day = 86400
    items = [listen(YT, now - d * day, 100) for d in (0, 1, 2, 4, 5, 6, 7)]
    assert listens.streak(items, now, UTC) == 3
    assert listens.aggregate(items, tz=UTC)["longest_streak_days"] == 4
    assert listens.streak([e for e in items if e["started_at"] < now - day / 2], now, UTC) == 2  # nothing yet today
    assert listens.streak([listen(YT, now - 3 * day, 100)], now, UTC) == 0
    assert listens.streak([listen(YT, now, 5, skipped=True)], now, UTC) == 0  # a skip isn't a listen
    # 23:30 UTC on the 21st is already the 22nd in Tokyo
    late = ts(2026, 10, 21, 23, 30)
    assert listens.streak([listen(YT, late, 100)], ts(2026, 10, 22, 12), ZoneInfo("Asia/Tokyo")) == 1


@pytest.mark.parametrize("now,period,offset,label,start,end", [
    (ts(2026, 10, 22), "month", 0, "September 2026", ts(2026, 9, 1), ts(2026, 10, 1)),
    (ts(2026, 10, 22), "month", 1, "August 2026", ts(2026, 8, 1), ts(2026, 9, 1)),
    (ts(2026, 10, 22), "month", -1, "October 2026", ts(2026, 10, 1), ts(2026, 11, 1)),
    (ts(2026, 1, 15), "month", 0, "December 2025", ts(2025, 12, 1), ts(2026, 1, 1)),
    (ts(2026, 12, 15), "month", -1, "December 2026", ts(2026, 12, 1), ts(2027, 1, 1)),
    (ts(2026, 10, 22), "year", 0, "2025", ts(2025, 1, 1), ts(2026, 1, 1)),
    (ts(2026, 10, 22), "year", -1, "2026", ts(2026, 1, 1), ts(2027, 1, 1)),
])
def test_recap_bounds(now, period, offset, label, start, end):
    assert listens.recap_bounds(period, offset, now, UTC) == (start, end, label)


def test_recap_bounds_are_local_midnights():
    start, end, _ = listens.recap_bounds("month", 0, ts(2026, 10, 22), NY)
    assert start == ts(2026, 9, 1, tz=NY) and end == ts(2026, 10, 1, tz=NY) and start - ts(2026, 9, 1) == 4 * 3600


def test_recap_new_artists_and_sessions():
    now = ts(2026, 10, 22)
    sep = ts(2026, 9, 10, 20)
    items = [
        listen(YT, ts(2026, 8, 5), 100, channel="Old Friend"),  # before September
        listen(YT, sep, 200, channel="Old Friend"),
        listen(YT2, sep + 210, 180, channel="New One"),
        listen(YT2, sep + 400, 180, channel="New One"),
        listen(YT3, sep + 3 * 3600, 150, channel="Later"),  # > 30 min gap: a new session
        listen(YT3, ts(2026, 10, 2), 150, channel="October"),  # after September
    ]
    r = listens.recap(items, "month", 0, now=now, tz=UTC)
    assert r["period_label"] == "September 2026" and r["partial"] is False and r["listens"] == 4
    assert r["new_artists_count"] == 2 and [a["name"] for a in r["new_artists"]] == ["New One", "Later"]
    assert r["top_tracks"][0]["id"] == YT2 and r["top_track_play_time"] == 6.0
    assert r["longest_session_min"] == round(560 / 60, 1)
    assert listens.recap(items, "month", -1, now=now, tz=UTC)["partial"] is True


def test_recap_new_artists_count_all_artists_not_just_the_top():
    now = ts(2026, 10, 22)
    sep = ts(2026, 9, 10, 12)
    items = [listen(YT, ts(2026, 8, 1), 100, channel="Old")]
    items += [listen(YT, sep + i * 300, 200, channel="Old") for i in range(5)]  # the top artist, not new
    items += [listen(YT2, sep + 3600 + i * 300, 200, channel=f"New {i}") for i in range(3)]
    r = listens.recap(items, "month", 0, now=now, tz=UTC, limit=1)
    assert [a["name"] for a in r["top_artists"]] == ["Old"] and len(r["top_tracks"]) == 1
    assert r["new_artists_count"] == 3 and len(r["new_artists"]) == 3


def test_sessions_split_on_long_gaps():
    t = 1.8e9
    items = [listen(YT, t, 100), listen(YT, t + 120, 100), listen(YT, t + 120 + 100 + 31 * 60, 50)]
    assert listens.sessions(items) == [200, 50]
    assert listens.sessions([]) == []


# ---------- routes ----------

def test_post_listen_accepts_a_beacon(client, lfile):
    body = json.dumps({"id": YT, "title": "T", "channel": "C", "duration": 200, "played_s": 35,
                       "started_at": int(time.time()) - 60, "skipped": True, "source": "yt"})
    # A beacon may arrive as text/plain: the body is parsed regardless of the Content-Type.
    r = client.post("/api/listens", data=body, content_type="text/plain;charset=UTF-8")
    assert r.status_code == 201 and r.get_json()["entry"]["counted"] is True
    r = client.post("/api/listens", data=body, content_type="application/json")
    assert r.status_code == 200 and r.get_json()["created"] is False
    assert client.post("/api/listens", data="nope", content_type="application/json").status_code == 400
    assert client.post("/api/listens", json={"id": "bad id", "played_s": 3}).status_code == 400
    huge = f'{{"id": "{YT}", "played_s": 1{"0" * 400}}}'  # an int no float can hold: a 400, not a 500
    assert client.post("/api/listens", data=huge, content_type="application/json").status_code == 400
    got = client.get("/api/listens?limit=5").get_json()
    assert len(got) == 1 and got[0]["id"] == YT


def test_listens_route_paging_and_clear(client):
    base = int(time.time()) - 10000
    for i in range(4):
        client.post("/api/listens", json={"id": YT, "played_s": 40 if i < 2 else 3, "started_at": base + i * 100})
    assert [e["started_at"] for e in client.get("/api/listens?limit=2").get_json()] == [base + 300, base + 200]
    assert len(client.get(f"/api/listens?before={base + 150}").get_json()) == 2
    assert len(client.get("/api/listens?counted=1").get_json()) == 2
    assert client.delete("/api/listens").status_code == 204
    assert client.get("/api/listens").get_json() == []


def test_stats_and_recap_routes(client, monkeypatch):
    now = 1.8e9
    monkeypatch.setattr(listens.time, "time", lambda: now)
    client.post("/api/listens", json={"id": YT, "title": "T", "channel": "C", "played_s": 120, "duration": 200,
                                      "started_at": now - 3600})
    s = client.get("/api/stats?period=week&tz=America/New_York").get_json()
    keys = {"listens", "minutes", "top_tracks", "top_artists", "top_albums", "by_hour", "by_weekday", "streak_days",
            "first_listen_at", "period", "from", "to", "skips", "longest_streak_days", "most_skipped"}
    assert keys <= set(s) and s["listens"] == 1 and s["top_tracks"][0]["title"] == "T"
    local_hour = datetime.fromtimestamp(now - 3600, NY).hour
    assert s["by_hour"][local_hour] == 1
    s2 = client.get("/api/stats?period=week&tz=Not/AZone&utc_offset=540").get_json()  # falls back to the offset
    assert s2["by_hour"][datetime.fromtimestamp(now - 3600, ZoneInfo("Asia/Tokyo")).hour] == 1
    assert client.get("/api/stats?period=decade").status_code == 400
    r = client.get("/api/recap?period=month&offset=-1&tz=UTC").get_json()
    assert r["partial"] is True and r["listens"] == 1 and {"period_label", "new_artists_count", "top_track_play_time",
                                                            "longest_session_min"} <= set(r)
    assert client.get("/api/recap?period=week").status_code == 400
