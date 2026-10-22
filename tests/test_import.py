"""Playlist import (production plan P2 → IL 4.4): CSV column detection (Exportify / Spotify-style, plain, TuneMyMusic,
Google Takeout playlists by row shape and its library CSV), placeholders in playlists, the word-subset matcher, the
background resolve pass (throttle, misses, 429 pause and resume, Takeout titles) and the routes. Offline."""
import io
import json
import threading
import time

import pytest

import app
import config
import history
import home
import importer
import matching
import playlists
import ytmusic

EXPORTIFY = (
    '"Track URI","Track Name","Artist URI(s)","Artist Name(s)","Album URI","Album Name","Album Artist URI(s)",'
    '"Album Artist Name(s)","Album Release Date","Album Image URL","Disc Number","Track Number","Track Duration (ms)",'
    '"Track Preview URL","Explicit","Popularity","ISRC","Added By","Added At"\n'
    '"spotify:track:1","Despacito","spotify:artist:1, spotify:artist:2","Luis Fonsi, Daddy Yankee","spotify:album:1",'
    '"VIDA","spotify:artist:1","Luis Fonsi","2019-02-01","","1","3","229360","","false","80","USUM71607007","",""\n'
    '"spotify:track:2","Bohemian Rhapsody - Remastered 2011","spotify:artist:3","Queen","spotify:album:2",'
    '"A Night At The Opera","spotify:artist:3","Queen","1975-11-21","","1","11","354320","","false","85",'
    '"GBUM71029604","",""\n'
)
TAKEOUT_PLAYLIST = (
    "Playlist ID,Add to Top,Playlist Title (Original),Playlist Visibility\n"
    "PLxyz,False,My Mix,Private\n"
    "\n"
    "Video ID,Playlist Video Creation Timestamp\n"
    "dQw4w9WgXcQ,2024-01-02T03:04:05+00:00\n"
    "kJQP7kiw5Fk,2024-01-02T03:05:05+00:00\n"
)
TAKEOUT_LIBRARY = (
    "Video ID,Song Title,Album Title,Artist Name 1,Artist Name 2\n"
    "kJQP7kiw5Fk,Despacito,VIDA,Luis Fonsi,Daddy Yankee\n"
)
DESPACITO = {"id": "kJQP7kiw5Fk", "title": "Despacito", "channel": "Luis Fonsi, Daddy Yankee",
             "artists": [{"name": "Luis Fonsi", "id": None}, {"name": "Daddy Yankee", "id": None}],
             "duration": 229, "source": "yt"}
QUEEN = {"id": "fJ9rUzIMcZQ", "title": "Bohemian Rhapsody", "channel": "Queen",
         "artists": [{"name": "Queen", "id": None}], "duration": 355, "source": "yt"}


class Search:
    def __init__(self, answers=None, errors=()):
        self.answers, self.errors, self.asked = answers or {}, list(errors), []

    def __call__(self, q, limit=10):
        self.asked.append(q)
        if self.errors:
            raise self.errors.pop(0)
        for key, value in self.answers.items():
            if key.lower() in q.lower():
                return [dict(t) for t in value]
        return []


@pytest.fixture
def env(monkeypatch, tmp_path):
    for name in ("PLAYLISTS", "MATCHES", "SETTINGS"):
        monkeypatch.setattr(config, f"{name}_FILE", tmp_path / f"{name.lower()}.json")
    monkeypatch.setattr(config, "YTMUSIC", True)
    monkeypatch.setattr(ytmusic, "YTMusic", object)
    monkeypatch.setattr(importer, "_sleep", lambda s: None)
    monkeypatch.setattr(importer, "throttle", importer.Throttle(0))
    search = Search({"Despacito": [DESPACITO], "Bohemian": [QUEEN]})
    monkeypatch.setattr(ytmusic, "search", search)
    monkeypatch.setattr(ytmusic, "song", lambda vid: None)
    history.HISTORY_FILE.unlink(missing_ok=True)
    yield search
    for job in list(importer._jobs.values()):
        job["stop"].set()
    importer._jobs.clear()


@pytest.fixture
def client():
    return app.app.test_client()


def wait_done(pid, timeout=5):
    end = time.time() + timeout
    while time.time() < end:
        s = importer.status(pid)
        if not s["running"]:
            return s
        time.sleep(0.01)
    raise AssertionError("the resolve pass didn't finish")


# ---------- parsing ----------

def test_exportify_columns_ms_durations_isrc_and_artists():
    p = importer.parse(EXPORTIFY.encode("utf-8-sig"), "Road trip.csv")
    assert p["kind"] == "csv" and p["name"] == "Road trip" and p["skipped"] == 0
    a, b = p["rows"]
    assert a == {"video_id": "", "title": "Despacito", "artist": "Luis Fonsi, Daddy Yankee", "album": "VIDA",
                 "duration": 229, "isrc": "USUM71607007"}
    assert b["title"] == "Bohemian Rhapsody - Remastered 2011" and b["duration"] == 354 and b["artist"] == "Queen"


def test_plain_and_semicolon_csvs_case_insensitive_headers():
    p = importer.parse("SONG,ARTIST,Length\nHello,Adele,4:55\n,Nobody,1:00\nHelp!,The Beatles,138\n")
    assert [(r["title"], r["artist"], r["duration"]) for r in p["rows"]] == [("Hello", "Adele", 295),
                                                                          ("Help!", "The Beatles", 138)]
    assert p["skipped"] == 1  # a row without a title
    p = importer.parse("title;artists;album\nAround the World;Daft Punk;Homework\n")
    assert p["rows"][0]["album"] == "Homework" and p["rows"][0]["artist"] == "Daft Punk"
    p = importer.parse("Track name,Artist name,Album,Playlist name,Type,ISRC\nOne,Metallica,…And Justice,Metal,Playlist,\n")
    assert p["name"] == "Metal" and p["rows"][0]["title"] == "One"  # TuneMyMusic: the name from its column
    p = importer.parse("track,creator\nX,A; B\n")
    assert p["rows"][0]["artist"] == "A, B"  # older Exportify separated artists with ";"


def test_takeout_playlist_by_row_shape_and_name_from_file():
    p = importer.parse(TAKEOUT_PLAYLIST, "My Mix-videos.csv")
    assert p["kind"] == "takeout" and p["name"] == "My Mix"
    assert [r["video_id"] for r in p["rows"]] == ["dQw4w9WgXcQ", "kJQP7kiw5Fk"] and p["rows"][0]["title"] == ""
    bare = importer.parse("dQw4w9WgXcQ,2024-01-02T03:04:05+00:00\nkJQP7kiw5Fk,2024-01-02 03:05:05\n", "x-videos.csv")
    assert bare["kind"] == "takeout" and len(bare["rows"]) == 2  # no header at all: the rows' shape says so
    lib = importer.parse(TAKEOUT_LIBRARY, "music library songs.csv")
    assert lib["rows"][0] == {"video_id": "kJQP7kiw5Fk", "title": "Despacito", "artist": "Luis Fonsi, Daddy Yankee",
                              "album": "VIDA", "duration": None, "isrc": ""}


@pytest.mark.parametrize("text", ["", "   \n", "foo,bar\n1,2\n", "Track Name\n\n"])
def test_unimportable_files(text):
    with pytest.raises(importer.ImportFailed):
        importer.parse(text)


def test_duration_forms():
    assert importer.duration("3:45") == 225 and importer.duration("1:02:03") == 3723
    assert importer.duration("229360") == 229 and importer.duration("229", in_ms=True) is None
    assert importer.duration("229.4") == 229 and importer.duration("abc") is None and importer.duration("") is None


# ---------- placeholders in playlists ----------

def test_import_creates_placeholders_and_real_tracks(env):
    p = importer.create(importer.parse(EXPORTIFY + '"spotify:track:3","","","","","","","","","","","","","",'
                                       '"","","","",""\n', "Trip.csv"))
    a, b = p["tracks"]
    assert a["q"] is True and a["id"].startswith("pq:") and a["artist"] == "Luis Fonsi, Daddy Yankee"
    assert a["key"] == matching.key_for("Despacito", "Luis Fonsi, Daddy Yankee") and a["isrc"] == "USUM71607007"
    assert p["import"] == {"kind": "csv", "rows": 2, "skipped": 1, "total": 2, "at": p["import"]["at"]}
    assert p["cover_id"] is None  # a placeholder has no art
    t = importer.create(importer.parse(TAKEOUT_PLAYLIST, "My Mix-videos.csv"))
    assert [x["id"] for x in t["tracks"]] == ["dQw4w9WgXcQ", "kJQP7kiw5Fk"] and t["name"] == "My Mix"
    assert t["cover_id"] == "dQw4w9WgXcQ" and t["import"]["total"] == 2  # untitled rows get looked up
    assert playlists.summary(playlists.get(p["id"]))["unmatched"] == 2
    assert "unmatched" not in playlists.summary(playlists.get(t["id"]))
    text, kept, skipped = playlists.m3u(playlists.get(p["id"]))
    assert kept == 0 and skipped == 2 and "# not kept: pq:" in text


def test_clean_entry_validates_placeholders():
    good = {"id": "pq:0123456789ab", "q": True, "title": " T ", "artist": "A", "duration": "200", "key": "q:a|t",
            "isrc": "USUM71607007", "miss": True, "miss_at": 5, "junk": 1}
    assert playlists.clean_entry(good) == {"id": "pq:0123456789ab", "q": True, "title": "T", "artist": "A",
                                           "channel": "A", "duration": 200, "isrc": "USUM71607007", "key": "q:a|t",
                                           "miss": True, "miss_at": 5}
    for bad in ({**good, "id": "pq:XYZ"}, {**good, "id": "dQw4w9WgXcQ"}, {**good, "title": ""}, {**good, "q": 1}):
        assert playlists.clean_entry(bad) is None or playlists.clean_entry(bad).get("q") is None
    assert playlists.clean_entry({"id": "dQw4w9WgXcQ", "title": "x"})["source"] == "yt"


# ---------- word-subset matching ----------

def test_loose_best_word_subsets():
    q = {"title": "Kiss from a Rose", "artist": "Seal", "duration": 288}
    film = {"id": "aaaaaaaaaaa", "title": "Kiss from a Rose (From Batman Forever)", "channel": "Seal", "duration": 289}
    assert matching.best(q, [film]) is None  # the extra words sink it under the strict title floor
    assert importer.loose_best(q, [film])[0] is film
    assert importer.loose_best(q, [{**film, "channel": "Someone Else"}]) is None
    assert importer.loose_best(q, [{**film, "duration": 330}]) is None
    assert importer.loose_best(q, [{**film, "title": "Kiss from a Rose (Live)"}]) is None  # another version
    assert importer.loose_best({**q, "title": "Canción"}, [{**film, "title": "Cancion (Ao Vivo Remix Edit)",
                                                           "duration": 288}]) is None
    accents = {"title": "Corazón Partío", "artist": "Alejandro Sanz", "duration": None}
    got = importer.loose_best(accents, [{"id": "bbbbbbbbbbb", "title": "Corazon Partio (Remasterizado)",
                                         "channel": "Alejandro Sanz"}])
    assert got and got[0]["id"] == "bbbbbbbbbbb"


# ---------- background resolve ----------

def test_resolve_pass_matches_lists_misses_and_remembers(env, client):
    csv_text = EXPORTIFY + '"spotify:track:9","Made Up Song Xyzzy","","Nobody Known","","","","","","","","",' \
                           '"200000","","","","","",""\n'
    r = client.post("/api/import", json={"text": csv_text, "filename": "Trip.csv"})
    assert r.status_code == 201
    body = r.get_json()
    assert body["kind"] == "csv" and body["rows"] == 3 and body["to_match"] == 3
    pid = body["playlist"]["id"]
    s = wait_done(pid)
    assert s["total"] == 3 and s["resolved"] == 2 and s["pending"] == 0 and not s["error"]
    assert [m["title"] for m in s["missing"]] == ["Made Up Song Xyzzy"]
    p = playlists.get(pid)
    assert [t["id"] for t in p["tracks"][:2]] == ["kJQP7kiw5Fk", "fJ9rUzIMcZQ"] and p["tracks"][2]["miss"] is True
    assert p["cover_id"] == "kJQP7kiw5Fk"
    assert matching.cached(matching.key_for("Despacito", "Luis Fonsi, Daddy Yankee"))["id"] == "kJQP7kiw5Fk"
    assert client.get(f"/api/import/{pid}/status").get_json()["resolved"] == 2
    # Asking again leaves found songs alone; retry_missing asks for the misses once more.
    asked = len(env.asked)
    r = client.post(f"/api/import/{pid}/resolve", json={"retry_missing": True})
    assert r.status_code == 202
    s = wait_done(pid)
    assert len(s["missing"]) == 1 and len(env.asked) == asked + 2  # title + artist, then the title alone


def test_resolve_uses_matches_cache_and_manual_fixes_first(env):
    matching.remember(matching.key_for("Despacito", "Luis Fonsi, Daddy Yankee"), {**DESPACITO, "id": "zzzzzzzzzzz"},
                      manual=True)
    p = importer.create(importer.parse(EXPORTIFY))
    importer.resolve(p["id"], app._import_find, app._import_lookup)
    wait_done(p["id"])
    assert playlists.get(p["id"])["tracks"][0]["id"] == "zzzzzzzzzzz"
    assert not any("Despacito" in q for q in env.asked)  # no request for a known row


def test_resolve_pauses_on_429_and_resumes(env, monkeypatch):
    monkeypatch.setattr(importer, "RATE_WAIT", 0.05)
    env.errors = [Exception("Server returned HTTP 429: Too Many Requests")]
    p = importer.create(importer.parse(EXPORTIFY))
    seen = []
    orig = importer._wait

    def spy(job, seconds):
        seen.append((job["rate_limited"], round(job["retry_at"] - time.time())))
        return orig(job, seconds)
    monkeypatch.setattr(importer, "_wait", spy)
    importer.resolve(p["id"], app._import_find, app._import_lookup, home.rate_limited, lambda e: None)
    s = wait_done(p["id"])
    assert seen == [(True, 0)] and s["resolved"] == 2 and not s["rate_limited"] and not s["error"]


def test_resolve_stops_after_repeated_429s(env, monkeypatch):
    monkeypatch.setattr(importer, "RATE_WAIT", 0.01)
    env.errors = [Exception("HTTP 429")] * importer.MAX_429
    p = importer.create(importer.parse(EXPORTIFY))
    importer.resolve(p["id"], app._import_find, app._import_lookup, home.rate_limited, lambda e: None)
    s = wait_done(p["id"])
    assert "rate limiting" in s["error"] and s["pending"] == 2 and s["resolved"] == 0


def test_resolve_throttles_requests(env, monkeypatch):
    slept = []
    monkeypatch.setattr(importer, "_sleep", slept.append)
    monkeypatch.setattr(importer, "throttle", importer.Throttle(0.3))
    p = importer.create(importer.parse(EXPORTIFY))
    importer.resolve(p["id"], app._import_find, app._import_lookup)
    wait_done(p["id"])
    assert len(env.asked) == 2 and len(slept) == 1 and 0.2 < slept[0] <= 0.3  # the 2nd request waited its turn


def test_takeout_rows_get_titles(env, monkeypatch):
    monkeypatch.setattr(ytmusic, "song", lambda vid: {**DESPACITO, "id": vid} if vid == "kJQP7kiw5Fk" else None)
    p = importer.create(importer.parse(TAKEOUT_PLAYLIST, "My Mix-videos.csv"))
    importer.resolve(p["id"], app._import_find, app._import_lookup)
    s = wait_done(p["id"])
    a, b = playlists.get(p["id"])["tracks"]
    assert a["title"] == "dQw4w9WgXcQ"  # unknown to YouTube Music: shown by its id, still playable
    assert b["title"] == "Despacito" and b["artists"][0]["name"] == "Luis Fonsi"
    assert s["total"] == 2 and s["resolved"] == 2 and s["missing"] == []


def test_entries_moved_or_removed_meanwhile_stay_consistent(env):
    p = importer.create(importer.parse(EXPORTIFY))
    first = p["tracks"][0]["id"]
    playlists.move(p["id"], 0, 1)
    playlists.replace_entries(p["id"], {first: {**DESPACITO}, "pq:000000000000": {**QUEEN}})
    assert [t["id"] for t in playlists.get(p["id"])["tracks"]][1] == "kJQP7kiw5Fk"


def test_materialize_on_get_after_the_player_matched(env, client):
    p = importer.create(importer.parse(EXPORTIFY))
    key = p["tracks"][1]["key"]
    r = client.post("/api/match", json={"title": "Bohemian Rhapsody - Remastered 2011", "artist": "Queen",
                                        "duration": 354, "key": key})
    assert r.status_code == 200 and r.get_json()["track"]["id"] == "fJ9rUzIMcZQ"
    got = client.get(f"/api/playlists/{p['id']}").get_json()
    assert got["tracks"][1]["id"] == "fJ9rUzIMcZQ" and got["tracks"][0]["q"] is True
    assert importer.status(p["id"])["resolved"] == 1


def test_summary_counts_to_match_and_not_found_apart(env):
    p = importer.create(importer.parse(EXPORTIFY))
    a, b = p["tracks"]
    playlists.replace_entries(p["id"], {a["id"]: {**a, "miss": True}})
    row = next(x for x in playlists.all_playlists() if x["id"] == p["id"])
    assert row["unmatched"] == 1 and row["not_found"] == 1
    playlists.replace_entries(p["id"], {b["id"]: {**b, "miss": True}})
    row = next(x for x in playlists.all_playlists() if x["id"] == p["id"])
    assert "unmatched" not in row and row["not_found"] == 2


# ---------- routes ----------

def test_import_route_multipart_and_errors(env, client, monkeypatch):
    data = {"file": (io.BytesIO(TAKEOUT_PLAYLIST.encode()), "Gym-videos.csv"), "name": "", "resolve": "0"}
    r = client.post("/api/import", data=data, content_type="multipart/form-data")
    body = r.get_json()
    assert r.status_code == 201 and body["playlist"]["name"] == "Gym" and body["kind"] == "takeout"
    assert body["status"]["running"] is False and body["status"]["pending"] == 2
    data = {"file": (io.BytesIO(b"Title,Artist\nHello,Adele\n"), "x.csv"), "name": "Mine", "resolve": "0"}
    assert client.post("/api/import", data=data, content_type="multipart/form-data").get_json()["playlist"]["name"] == "Mine"
    assert client.post("/api/import", json={"text": "nothing,useful\n1,2\n"}).status_code == 400
    assert client.post("/api/import", json={"nope": 1}).status_code == 400
    monkeypatch.setattr(importer, "MAX_BYTES", 100)
    r = client.post("/api/import", json={"text": "Title,Artist\n" + "Hello,Adele\n" * 20})
    assert r.status_code == 413
    assert client.get("/api/import/00000000/status").status_code == 404
    assert client.get("/api/import/nothex!!/status").status_code == 404
    assert client.post("/api/import/00000000/resolve").status_code == 404


def test_resolve_route_needs_youtube_music(env, client, monkeypatch):
    p = importer.create(importer.parse(EXPORTIFY))
    monkeypatch.setattr(config, "YTMUSIC", False)
    assert client.post(f"/api/import/{p['id']}/resolve").status_code == 502
    r = client.post("/api/import", json={"text": EXPORTIFY})
    assert r.status_code == 201 and r.get_json()["status"]["running"] is False  # imported, matched later


def test_delete_cancels_a_running_pass(env, client, monkeypatch):
    gate = threading.Event()

    def slow(text, limit=10):
        gate.wait(2)
        return []
    monkeypatch.setattr(ytmusic, "search", slow)
    p = importer.create(importer.parse(EXPORTIFY))
    importer.resolve(p["id"], app._import_find, app._import_lookup)
    assert client.delete(f"/api/playlists/{p['id']}").status_code == 204
    gate.set()
    s = importer._jobs[p["id"]]
    end = time.time() + 3
    while s["running"] and time.time() < end:
        time.sleep(0.01)
    assert not s["running"]


def test_playlists_route_accepts_placeholders(env, client):
    ph = {"id": "pq:abcdef123456", "q": True, "title": "Hello", "artist": "Adele", "duration": 295}
    r = client.post("/api/playlists", json={"name": "P", "tracks": [ph, {"id": "dQw4w9WgXcQ", "title": "x"}]})
    body = r.get_json()
    assert r.status_code == 201 and body["tracks"][0]["q"] is True and body["cover_id"] == "dQw4w9WgXcQ"
    assert json.loads(config.PLAYLISTS_FILE.read_text())[0]["tracks"][0]["id"] == "pq:abcdef123456"


def test_status_counts_rows_the_player_matched(env, client):
    p = importer.create(importer.parse(EXPORTIFY))
    client.post("/api/match", json={"title": "Despacito", "artist": "Luis Fonsi, Daddy Yankee", "duration": 229,
                                    "key": p["tracks"][0]["key"]})
    s = client.get(f"/api/import/{p['id']}/status").get_json()
    assert s["resolved"] == 1 and s["pending"] == 1 and playlists.get(p["id"])["tracks"][0]["id"] == "kJQP7kiw5Fk"


def big_csv(target):
    """A fully quoted Exportify-style CSV of about `target` bytes (JSON-escaping it adds ~25 %)."""
    head = EXPORTIFY.splitlines(keepends=True)[0]
    rows, size, i = [head], len(head), 0
    while size < target:
        row = (f'"spotify:track:{i}","Song number {i} — ünïcödé","spotify:artist:{i}","Artist {i % 97}","spotify:album:1",'
               f'"Album {i % 31}","","Artist {i % 97}","2020-01-01","","1","{i % 20 + 1}","{180000 + i % 60000}","",'
               f'"false","50","","",""\n')
        rows.append(row)
        size += len(row.encode())
        i += 1
    return "".join(rows)


def test_a_4_5_mb_csv_is_accepted_as_file_and_as_json(env, client):
    text = big_csv(int(4.5 * 1024 * 1024))
    assert 4.5 * 1024 * 1024 <= len(text.encode()) < importer.MAX_BYTES
    r = client.post("/api/import", data={"file": (io.BytesIO(text.encode()), "Big.csv"), "resolve": "0"},
                    content_type="multipart/form-data")
    body = r.get_json()
    assert r.status_code == 201 and body["rows"] == playlists.TRACKS_MAX and body["skipped"] > 0
    payload = json.dumps({"text": text, "filename": "Big.csv", "resolve": False})  # \\uXXXX + \\" escaping
    assert len(payload.encode()) > importer.MAX_BYTES  # bigger than 5 MB on the wire, still a 4.5 MB CSV
    r = client.post("/api/import", data=payload, content_type="application/json")
    assert r.status_code == 201 and r.get_json()["playlist"]["name"] == "Big"


def test_csv_over_5_mb_is_refused_both_ways(env, client):
    text = big_csv(importer.MAX_BYTES + 1024)
    r = client.post("/api/import", data={"file": (io.BytesIO(text.encode()), "Big.csv")},
                    content_type="multipart/form-data")
    assert r.status_code == 413
    assert client.post("/api/import", json={"text": text}).status_code == 413
    huge = b'{"text": "' + b"x" * (importer.REQUEST_MAX + 1) + b'"}'
    assert client.post("/api/import", data=huge, content_type="application/json").status_code == 413
