"""For you (recommend.py: seeds, Quick picks scoring, affinity, dislikes, shelves) and Home from cache (home.py:
TTLs, 429 backoff, the listens trigger, one build at a time, the cold-start budget) plus their routes.
Offline: ytmusic.radio is a fake, listens are synthetic."""
import threading
import time
from types import SimpleNamespace

import pytest

import app
import blocklist
import config
import history
import home
import likes
import listens
import recommend
import ytmusic

DAY = 86400
NOW = 1_800_000_000.0
real_retry_after = ytmusic.retry_after


def vid(n):
    """A valid 11-char video id per n."""
    return f"v{n:010d}"


def track(n, artist="Art", aid=None, title=None):
    return {"id": vid(n), "title": title or f"Song {n}", "channel": artist, "duration": 200, "source": "yt",
            "artists": [{"name": artist, "id": aid}]}


def listen(n, at, artist="Art", counted=True, skipped=False, aid=None):
    return {**track(n, artist, aid), "played_s": 200 if counted else 5, "started_at": int(at),
            "ended_at": int(at) + 200, "counted": counted, "skipped": skipped and not counted}


@pytest.fixture(autouse=True)
def files(monkeypatch, tmp_path):
    for name in ("LISTENS_FILE", "LIKES_FILE", "DISLIKES_FILE", "HOME_FILE", "BLOCKLIST_FILE"):
        monkeypatch.setattr(config, name, tmp_path / f"{name.lower()}.json")
    monkeypatch.setattr(history, "HISTORY_FILE", tmp_path / "history.json")
    monkeypatch.setattr(ytmusic, "enabled", lambda: True)
    monkeypatch.setattr(ytmusic, "retry_after", lambda max_age=300: None)
    monkeypatch.setattr(home.tiles, "load", lambda: [{"label": "Chill", "color": "#000", "query": "Chill",
                                                       "ids": [], "params": None, "art": None}])
    monkeypatch.setattr(home, "_retry_at", 0.0)
    yield
    ev = home._running
    if ev is not None:
        ev.wait(5)  # don't leave a build writing into the next test's files


def put_listens(items):
    listens._file.save(sorted(items, key=lambda e: e["started_at"]))


@pytest.fixture
def client():
    return app.app.test_client()


# ---------- seeds ----------

def test_seeds_one_per_artist_newest_first_max_five():
    items = [listen(i, NOW - 100 * (20 - i), artist=f"A{i % 7}") for i in range(20)]
    seeds = recommend.seeds(items)
    assert len(seeds) == recommend.SEEDS
    assert [s["id"] for s in seeds] == [vid(19), vid(18), vid(17), vid(16), vid(15)]
    assert len({s["artists"][0]["name"] for s in seeds}) == 5


def test_seeds_skip_uncounted_disliked_blocked_and_fall_back_to_keeps():
    items = [listen(1, NOW - 50, "A"), listen(2, NOW - 40, "A"), listen(3, NOW - 30, "B", counted=False),
             listen(4, NOW - 20, "C"), {**listen(5, NOW - 10, "D"), "id": "mc:123"}]
    blocked = blocklist.Matcher([{"name": "C", "id": None}])
    kept = [{"id": vid(9), "title": "Kept", "channel": "E", "duration": 100, "file": "x.opus"}]
    seeds = recommend.seeds(items, kept, blocked, disliked={vid(2)})
    assert [s["id"] for s in seeds] == [vid(1), vid(9)]


# ---------- Quick picks ----------

def radio_of(lanes):
    def fetch(seed_id):
        out = lanes[seed_id]
        if isinstance(out, Exception):
            raise out
        return out
    return fetch


def test_score_sums_lanes_and_weights_by_seed_rank():
    s0, s1 = track(100, "S0"), track(101, "S1")
    shared = track(1, "X")
    lanes = {s0["id"]: [s0, shared, track(2, "Y")], s1["id"]: [s1, track(3, "Z"), shared]}
    picks, _, errors = recommend.quick_picks([s0, s1], radio_of(lanes))
    assert not errors
    # shared: lane 0 place 0 -> 1*1, lane 1 place 1 -> (1-1/8)*(1-1/2); Y: (1-1/8)*1; Z: 1*(1/2)
    scores = {vid(1): 1 + 7 / 8 / 2, vid(2): 7 / 8, vid(3): 0.5}
    assert set(t["id"] for t in picks) == set(scores)
    assert picks[0]["id"] == vid(1)  # lane 0 first in the round-robin


def test_drops_excluded_blocked_and_counts_places_among_eligible():
    seed = track(100, "S")
    radio = [seed] + [track(i, f"A{i}") for i in range(1, 12)]
    blocked = blocklist.Matcher([{"name": "A2", "id": None}])
    picks, _, _ = recommend.quick_picks([seed], radio_of({seed["id"]: radio}), exclude={vid(1), vid(3)},
                                        blocked=blocked)
    ids = [t["id"] for t in picks]
    assert vid(1) not in ids and vid(3) not in ids and vid(2) not in ids and seed["id"] not in ids
    # 8 eligible places score: 4,5,...,11
    assert ids == [vid(i) for i in range(4, 12)]


def test_at_most_three_per_artist_and_fifteen_total():
    seeds = [track(100 + s, f"S{s}") for s in range(5)]
    lanes = {s["id"]: [track(1000 * (k + 1) + i, "Same" if i < 5 else f"L{k}-{i}") for i in range(8)]
             for k, s in enumerate(seeds)}
    picks, _, _ = recommend.quick_picks(seeds, radio_of(lanes))
    assert len(picks) == recommend.PICKS
    assert sum(1 for t in picks if t["channel"] == "Same") == recommend.PER_ARTIST


def test_round_robin_interleaves_lanes():
    seeds = [track(100, "S0"), track(101, "S1")]
    lanes = {seeds[0]["id"]: [track(i, f"A{i}") for i in range(1, 5)],
             seeds[1]["id"]: [track(i, f"B{i}") for i in range(11, 15)]}
    picks, _, _ = recommend.quick_picks(seeds, radio_of(lanes))
    assert [t["id"] for t in picks[:4]] == [vid(1), vid(11), vid(2), vid(12)]


def test_same_song_under_two_ids_counts_once():
    seed = track(100, "S")
    radio = [track(1, "X", title="Song!"), track(2, "X", title="song"), track(3, "Y")]
    picks, _, _ = recommend.quick_picks([seed], radio_of({seed["id"]: radio}))
    assert [t["id"] for t in picks] == [vid(1), vid(3)]


def test_failed_lane_is_skipped_and_429_stops():
    seeds = [track(100, "S0"), track(101, "S1"), track(102, "S2")]
    calls = []

    def fetch(sid):
        calls.append(sid)
        if sid == seeds[0]["id"]:
            raise RuntimeError("timeout")
        if sid == seeds[1]["id"]:
            raise RuntimeError("Server returned HTTP 429: Too Many Requests.")
        return [track(5, "Z")]
    picks, lanes, errors = recommend.quick_picks(seeds, fetch, stop=home.rate_limited)
    assert len(errors) == 2 and calls == [seeds[0]["id"], seeds[1]["id"]] and picks == []


def test_affinity_boost_reorders():
    seed = track(100, "S")
    radio = [seed, track(1, "Cold"), track(2, "Warm")]
    aff = {blocklist.key("Warm"): 0.9}
    picks, _, _ = recommend.quick_picks([seed], radio_of({seed["id"]: radio}), aff=aff)
    assert [t["id"] for t in picks] == [vid(2), vid(1)]


# ---------- affinity ----------

def test_affinity_ema_decay_and_skips():
    a = recommend.affinity([listen(1, NOW, "A")], NOW)
    assert a[blocklist.key("A")] == pytest.approx(0.15)
    a = recommend.affinity([listen(1, NOW, "A")], NOW + DAY)
    assert a[blocklist.key("A")] == pytest.approx(0.15 * 0.85)
    two = recommend.affinity([listen(1, NOW - DAY, "A"), listen(2, NOW, "A")], NOW)
    assert two[blocklist.key("A")] == pytest.approx(0.15 * 0.85 + 0.15 * (1 - 0.15 * 0.85))
    s = recommend.affinity([listen(1, NOW, "B", counted=False, skipped=True)], NOW)
    assert s[blocklist.key("B")] == pytest.approx(-0.05)
    assert recommend.affinity([listen(1, NOW, "C", counted=False)], NOW) == {}  # short, not a skip: nothing


# ---------- dislikes ----------

def test_dislike_cooldown_and_artist_escalation():
    t1, t2 = track(1, "Bad", aid="UC" + "b" * 22), track(2, "Bad")
    e, created, blocked = recommend.dislike(t1, NOW)
    assert created and blocked is None and e["until"] == NOW + 14 * DAY
    assert recommend.disliked_ids(NOW + 13 * DAY) == {vid(1)}
    assert recommend.disliked_ids(NOW + 15 * DAY) == set()
    _, _, blocked = recommend.dislike(t1, NOW + DAY)  # the same track again: no escalation
    assert blocked is None and blocklist.entries() == []
    _, _, blocked = recommend.dislike(t2, NOW + 2 * DAY)
    assert blocked["name"] == "Bad"
    assert [(b["id"], b["name"]) for b in blocklist.entries()] == [("UC" + "b" * 22, "Bad")]
    assert recommend.undislike(vid(2)) and blocklist.entries() == []  # undo lifts the block it caused
    assert not recommend.undislike(vid(2))


def test_dislikes_older_than_thirty_days_dont_escalate():
    recommend.dislike(track(1, "Meh"), NOW)
    _, _, blocked = recommend.dislike(track(2, "Meh"), NOW + 31 * DAY)
    assert blocked is None and blocklist.entries() == []
    assert [e["id"] for e in recommend.dislikes(NOW + 31 * DAY)] == [vid(2)]  # the old one was pruned


# ---------- shelves from the log ----------

def test_on_repeat_threshold_and_window():
    items = [listen(1, NOW - DAY), listen(1, NOW - 2 * DAY), listen(1, NOW - 3 * DAY),
             listen(2, NOW - DAY), listen(2, NOW - 2 * DAY),
             listen(3, NOW - DAY),
             listen(4, NOW - 40 * DAY), listen(4, NOW - 41 * DAY),
             listen(5, NOW - DAY, counted=False), listen(5, NOW - 2 * DAY, counted=False)]
    out = recommend.on_repeat(sorted(items, key=lambda e: e["started_at"]), NOW)
    assert [(t["id"], t["plays"]) for t in out] == [(vid(1), 3), (vid(2), 2)]


def test_jump_back_in_window():
    items = [listen(1, NOW - 60 * DAY), listen(1, NOW - 61 * DAY),       # in: played a lot 30-180 days ago
             listen(2, NOW - 60 * DAY), listen(2, NOW - 61 * DAY), listen(2, NOW - 3 * DAY, counted=False),  # heard lately
             listen(3, NOW - 200 * DAY), listen(3, NOW - 201 * DAY),     # too long ago
             listen(4, NOW - 60 * DAY),                                   # only once
             listen(5, NOW - 20 * DAY), listen(5, NOW - 21 * DAY)]       # too recent for the window
    out = recommend.jump_back_in(sorted(items, key=lambda e: e["started_at"]), NOW)
    assert [t["id"] for t in out] == [vid(1)]


def test_top_artists_with_ids_and_cover():
    aid = "UC" + "a" * 22
    items = [listen(1, NOW - DAY, "A", aid=aid), listen(1, NOW - 2 * DAY, "A"), listen(2, NOW - 3 * DAY, "A"),
             listen(3, NOW - DAY, "B")]
    out = recommend.top_artists(items, NOW)
    assert out[0] == {"name": "A", "id": aid, "count": 3, "cover_id": vid(1)}
    assert out[1]["name"] == "B" and out[1]["id"] is None


# ---------- home build ----------

def seed_listens(n_artists=3):
    put_listens([listen(i, NOW - DAY + i * 600, f"Art{i}") for i in range(n_artists)])


def fake_radio(monkeypatch, fail=None):
    calls = []

    def radio(seed_id, limit=50):
        calls.append(seed_id)
        if fail:
            raise fail
        n = int(seed_id[1:])
        return [track(n), *(track(500 + n * 10 + i, f"R{n}-{i % 2}") for i in range(6))]
    monkeypatch.setattr(ytmusic, "radio", radio)
    return calls


def test_build_complete_has_shelves_and_six_hour_ttl(monkeypatch):
    seed_listens()
    fake_radio(monkeypatch)
    likes.add({**track(77, "Liked"), "album": ""})
    doc = home.build(NOW)
    assert doc["source"] == "fresh" and doc["reason"] is None and doc["next_at"] == NOW + 6 * 3600
    ids = [s["id"] for s in doc["shelves"]]
    assert ids == ["quick", "mixed", "top_artists"]
    served = home._present(doc, "cache", False, NOW)["shelves"]
    assert [s["id"] for s in served] == ["quick", "mixed", "liked", "top_artists", "moods"]  # liked: read fresh
    assert served[2]["items"][0]["id"] == vid(77)
    assert len(doc["shelves"][0]["items"]) == recommend.PICKS  # 18 candidates
    mixed = doc["shelves"][1]["items"]
    assert [m["seed"]["id"] for m in mixed] == [vid(2), vid(1), vid(0)] and mixed[0]["with"] == ["R2-0", "R2-1"]


def test_build_fallback_ttl_and_429_backoff(monkeypatch):
    seed_listens()
    fake_radio(monkeypatch, fail=RuntimeError("read timeout"))
    doc = home.build(NOW)
    assert doc["source"] == "fallback" and doc["reason"] == "radio_failed" and doc["next_at"] == NOW + 1800
    calls = fake_radio(monkeypatch, fail=RuntimeError("Server returned HTTP 429: Too Many Requests."))
    doc = home.build(NOW)
    assert doc["reason"] == "rate_limited" and doc["next_at"] == NOW + 60 and len(calls) == 1
    monkeypatch.setattr(ytmusic, "retry_after", lambda max_age=300: 300)
    assert home.build(NOW)["next_at"] == NOW + 300
    err = RuntimeError("HTTP 429")
    err.response = SimpleNamespace(status_code=429, headers={"Retry-After": "120"})
    fake_radio(monkeypatch, fail=err)
    assert home.build(NOW)["next_at"] == NOW + 120


def test_ytmusic_remembers_retry_after(monkeypatch):
    monkeypatch.setattr(ytmusic, "_last_429", None)
    assert real_retry_after() is None
    ytmusic._note_429(SimpleNamespace(status_code=429, headers={"Retry-After": "42"}))
    assert real_retry_after() == 42
    ytmusic._note_429(SimpleNamespace(status_code=200, headers={}))  # other answers don't clear it
    assert real_retry_after() == 42
    ytmusic._note_429(SimpleNamespace(status_code=429, headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}))
    assert real_retry_after() is None


def test_failed_refresh_keeps_previous_quick_picks(monkeypatch):
    seed_listens()
    fake_radio(monkeypatch)
    first = home.refresh(NOW)
    fake_radio(monkeypatch, fail=RuntimeError("down"))
    second = home.refresh(NOW + 7 * 3600)
    assert second["source"] == "fallback" and second["carried"] == ["quick"]
    assert second["shelves"][0] == first["shelves"][0]


# ---------- due / serve ----------

def test_due_timings(monkeypatch):
    seed_listens()
    fake_radio(monkeypatch)
    doc = home.refresh(NOW)
    assert not home.due(doc, NOW + 6 * 3600 - 1) and home.due(doc, NOW + 6 * 3600)
    fake_radio(monkeypatch, fail=RuntimeError("down"))
    doc = home.refresh(NOW)
    assert not home.due(doc, NOW + 1799) and home.due(doc, NOW + 1800)


def test_new_listens_trigger_but_not_during_429_backoff(monkeypatch):
    seed_listens()
    fake_radio(monkeypatch)
    doc = home.refresh(NOW)
    items = listens.entries() + [listen(100 + i, NOW + 10 + i, "New") for i in range(4)]
    put_listens(items)
    assert not home.due(doc, NOW + 60)
    put_listens(items + [listen(200, NOW + 100, "New")])
    assert home.due(doc, NOW + 120)
    assert not home.due({**doc, "reason": "rate_limited", "next_at": NOW + 300}, NOW + 120)


def test_first_listen_triggers_when_there_were_no_seeds(monkeypatch):
    fake_radio(monkeypatch)
    doc = home.refresh(NOW)
    assert doc["seeds"] == 0 and doc["source"] == "fresh"
    put_listens([listen(1, NOW + 5)])
    assert home.due(doc, NOW + 10)


def test_serve_from_cache_then_stale_kicks_one_refresh(monkeypatch):
    seed_listens()
    fake_radio(monkeypatch)
    home.refresh(NOW)
    started = []
    monkeypatch.setattr(home, "refresh_async", lambda: started.append(1))
    body = home.serve(NOW + 60)
    assert body["source"] == "cache" and body["stale"] is False and started == []
    assert [s["id"] for s in body["shelves"]][-1] == "moods"
    body = home.serve(NOW + 6 * 3600 + 1)
    assert body["stale"] is True and started == [1] and body["shelves"]


def test_one_build_at_a_time(monkeypatch):
    gate, calls = threading.Event(), []

    def slow(now=None, remote=True):
        calls.append(remote)
        gate.wait(5)
        return {"generated_at": NOW, "source": "fresh", "reason": None, "next_at": NOW + 10, "ytm": True,
                "seeds": 0, "errors": [], "shelves": []}
    monkeypatch.setattr(home, "build", slow)
    a, b = home.refresh_async(), home.refresh_async()
    assert a is b and home.refreshing()
    gate.set()
    assert a.wait(5) and calls == [True] and not home.refreshing()


def test_cold_start_budget_returns_local_shelves(monkeypatch):
    seed_listens()
    calls = fake_radio(monkeypatch)
    gate = threading.Event()
    real = home.build

    def slow(now=None, remote=True):
        if remote:
            gate.wait(5)
        return real(now, remote)
    monkeypatch.setattr(home, "build", slow)
    t0 = time.monotonic()
    body = home.serve(NOW, budget=0.2)
    assert time.monotonic() - t0 < 2
    assert body["stale"] is True and body["source"] == "fallback" and body["reason"] == "local_only"
    assert "quick" not in [s["id"] for s in body["shelves"]] and "mixed" in [s["id"] for s in body["shelves"]]
    assert calls == []
    gate.set()
    home._running.wait(5)
    body = home.serve()
    assert body["source"] == "cache" and body["shelves"][0]["id"] == "quick"


def test_cold_start_within_budget_is_fresh(monkeypatch):
    seed_listens()
    fake_radio(monkeypatch)
    body = home.serve(NOW)
    assert body["source"] == "fresh" and body["stale"] is False and body["shelves"][0]["title"] == "Quick picks"


# ---------- routes ----------

def test_api_home_marks_and_filters(monkeypatch, client):
    seed_listens()
    fake_radio(monkeypatch)
    home.refresh(time.time())
    quick = client.get("/api/home").get_json()["shelves"][0]
    assert quick["id"] == "quick" and quick["kind"] == "tracks"
    first = quick["items"][0]
    assert first["kept"] is False and first["liked"] is False
    r = client.post(f"/api/dislikes/{first['id']}", json=first)
    assert r.status_code == 201 and r.get_json()["blocked"] is None
    quick2 = client.get("/api/home").get_json()["shelves"][0]
    assert first["id"] not in [t["id"] for t in quick2["items"]]
    same = next(t for t in quick2["items"] if t["channel"] == first["channel"])
    r = client.post(f"/api/dislikes/{same['id']}", json=same)
    assert r.get_json()["blocked"]["name"] == first["channel"]
    shelves = client.get("/api/home").get_json()["shelves"]
    assert all(t["channel"] != first["channel"] for s in shelves if s["kind"] == "tracks" for t in s["items"])
    assert client.delete(f"/api/dislikes/{same['id']}").status_code == 204
    assert blocklist.entries() == []
    assert client.delete(f"/api/dislikes/{same['id']}").status_code == 404
    assert [e["id"] for e in client.get("/api/dislikes").get_json()] == [first["id"]]


def test_api_dislike_validates(client):
    assert client.post("/api/dislikes/nope", json={}).status_code == 404
    assert client.post(f"/api/dislikes/{vid(1)}", json={"id": vid(2)}).status_code == 400
    assert client.post(f"/api/dislikes/{vid(1)}", json={"title": "x"}).status_code == 201


def test_listen_post_triggers_rebuild_only_once_built(monkeypatch, client):
    started = []
    monkeypatch.setattr(home, "refresh_async", lambda: started.append(1))
    body = {**track(1), "played_s": 200, "started_at": time.time() - 210}
    client.post("/api/listens", json=body)
    assert started == []  # no home.json yet: the refresher's job
    fake_radio(monkeypatch)
    home._file.save({"generated_at": time.time() - 100, "next_at": time.time() + 3600, "ytm": True, "seeds": 0,
                     "source": "fresh", "reason": None, "shelves": []})
    client.post("/api/listens", json={**body, "id": vid(2), "started_at": time.time() - 50})
    assert started == [1]


def test_a_play_started_before_the_build_but_reported_after_is_new():
    put_listens([listen(1, NOW - 100)])  # ended at NOW + 100
    assert home.new_listens(NOW) == 1
    put_listens([listen(1, NOW - 300)])  # ended before the build
    assert home.new_listens(NOW) == 0


def test_undislike_keeps_a_block_the_user_made(client):
    aid = "UC" + "m" * 22
    assert client.post("/api/blocklist", json={"name": "Mine", "id": aid}).status_code == 201
    t1, t2 = track(1, "Mine", aid=aid), track(2, "Mine", aid=aid)
    assert client.post(f"/api/dislikes/{t1['id']}", json=t1).get_json()["blocked"] is None
    r = client.post(f"/api/dislikes/{t2['id']}", json=t2).get_json()
    assert r["blocked"] is None and "blocked" not in r["entry"]  # already hidden: not this mark's doing
    assert client.delete(f"/api/dislikes/{t2['id']}").status_code == 204
    assert [(b["id"], b["name"]) for b in blocklist.entries()] == [(aid, "Mine")]
