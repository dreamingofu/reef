"""Likes, local playlists (+ .m3u and export), and P1.5: lists expose `liked`, `kept` and the Navidrome id of
kept tracks so the player can stream the library copy. Offline: no Navidrome, no YouTube."""
import json
import os

import pytest

import app
import config
import history
import likes
import navidrome
import playlists
import sources
import store
import streamer

A, B, C = "dQw4w9WgXcQ", "FGBhQbmPwH8", "aaaaaaaaaaa"


def track(tid, **kw):
    return {"id": tid, "title": f"Song {tid[:3]}", "channel": "Artist - Topic", "duration": 200, "source": "yt", **kw}


@pytest.fixture(autouse=True)
def files(monkeypatch, tmp_path):
    music = tmp_path / "music"
    monkeypatch.setattr(config, "LIKES_FILE", tmp_path / "likes.json")
    monkeypatch.setattr(config, "PLAYLISTS_FILE", tmp_path / "playlists.json")
    monkeypatch.setattr(config, "MUSIC_DIR", music)
    monkeypatch.setattr(config, "DOWNLOAD_DIR", music / "Reef")
    monkeypatch.setattr(history, "HISTORY_FILE", tmp_path / "history.json")
    monkeypatch.setattr(streamer, "prefetch", lambda ids: len(ids))
    return tmp_path


@pytest.fixture
def client():
    return app.app.test_client()


def keep(tid, name, nd_id=None):
    """A kept track: a file in the music folder plus its history entry."""
    f = config.DOWNLOAD_DIR / name
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(b"x")
    history.add({"id": tid, "title": name, "channel": "Artist", "file": str(f), **({"nd_id": nd_id} if nd_id else {})})
    return f


# ---------- track ids and cleaning ----------

@pytest.mark.parametrize("raw,want", [
    (A, A), ("yt:" + A, A), ("mc:123", "mc:123"), ("nd:ab-c.1", "nd:ab-c.1"),
    ("short", None), ("zz:123", None), ("mc:", None), ("../etc", None), (None, None), (42, None),
])
def test_track_id(raw, want):
    assert store.track_id(raw) == want


def test_clean_track_keeps_known_fields_only():
    t = store.clean_track({"id": A, "title": "x" * 300, "channel": 5, "duration": float("inf"), "kept": True,
                           "artists": [{"name": "N", "id": "../bad"}, {"id": "UCx"}], "explicit": True, "thumb": "u"})
    assert t == {"id": A, "title": "x" * 200, "channel": "", "duration": None, "source": "yt",
                 "artists": [{"name": "N", "id": None}], "explicit": True}


@pytest.mark.parametrize("raw,want", [
    (10 ** 400, None), (-(10 ** 400), None), (float("nan"), None), (float("inf"), None), ("1e400", None),
    (True, None), (None, None), ("x", None), (-1, None), (86400, None), (0, 0.0), ("12.5", 12.5), (86399.5, 86399.5),
])
def test_seconds_rejects_what_isnt_a_finite_length(raw, want):
    assert store.seconds(raw) == want


def test_a_huge_json_integer_is_an_unknown_length_not_a_500(client):
    body = f'{{"id": "{A}", "title": "Big", "duration": 1{"0" * 400}}}'
    r = client.put(f"/api/likes/{A}", data=body, content_type="application/json")
    assert r.status_code == 201 and r.get_json()["entry"]["duration"] is None
    r = client.post("/api/playlists", data=f'{{"name": "P", "tracks": [{body}]}}', content_type="application/json")
    assert r.status_code == 201 and r.get_json()["tracks"][0]["duration"] is None


# ---------- likes ----------

def test_like_unlike(client, files):
    r = client.put(f"/api/likes/{A}", json=track(A, album="Al"))
    assert r.status_code == 201 and r.get_json()["entry"]["liked_at"]
    first = r.get_json()["entry"]["liked_at"]
    assert client.put("/api/likes/mc:123", json={"title": "Lossless"}).status_code == 201
    r = client.put(f"/api/likes/{A}", json=track(A, title="Renamed"))  # idempotent; refreshes the metadata
    assert r.status_code == 200 and r.get_json()["entry"]["liked_at"] == first
    got = client.get("/api/likes").get_json()
    assert [e["id"] for e in got] == ["mc:123", A]  # newest first; re-liking doesn't move it
    assert got[1]["title"] == "Renamed" and got[1]["album"] == "Al" and got[1]["liked"] is True
    assert set(got[1]) >= {"id", "title", "channel", "album", "duration", "liked_at", "kept"}
    assert client.delete(f"/api/likes/{A}").status_code == 204
    assert client.delete(f"/api/likes/{A}").status_code == 404
    assert [e["id"] for e in json.loads((files / "likes.json").read_text())] == ["mc:123"]


def test_like_validation(client):
    assert client.put("/api/likes/notanid").status_code == 404
    assert client.put(f"/api/likes/{A}", json={"id": B}).status_code == 400
    assert client.put(f"/api/likes/{A}").status_code == 201  # no body: just the id


def test_lists_mark_liked_kept_and_nd_id(client, monkeypatch):
    keep(A, "a.opus", nd_id="nd-a")
    keep(B, "b.opus")  # kept, not scanned yet
    likes.add(store.clean_track(track(C)))
    monkeypatch.setattr(sources.youtube.SOURCE, "search", lambda q, limit: [track(A), track(B), track(C)])
    body = client.get("/api/search?q=x&src=yt").get_json()
    assert [(t["kept"], t["liked"], t.get("nd_id")) for t in body] == [(True, False, "nd-a"), (True, False, None),
                                                                       (False, True, None)]
    hist = client.get("/api/history").get_json()
    assert {h["id"]: h.get("nd_id") for h in hist} == {A: "nd-a", B: None}


def test_library_results_stay_kept():
    out = app._mark_kept([{"id": "nd:xyz"}, {"id": A}])
    assert out[0]["kept"] is True and out[1]["kept"] is False and "nd_id" not in out[1]


# ---------- playlists ----------

def make(client, name="Road trip", tracks=()):
    r = client.post("/api/playlists", json={"name": name, "tracks": list(tracks)})
    assert r.status_code == 201, r.get_json()
    return r.get_json()


def test_playlist_crud(client):
    p = make(client, tracks=[track(A), track(B), {"id": "bad"}])
    assert playlists.valid_id(p["id"]) and [t["id"] for t in p["tracks"]] == [A, B] and p["cover_id"] == A
    assert p["pinned"] is False and p["description"] == "" and p["created_at"] == p["updated_at"]
    other = make(client, "Other")
    rows = client.get("/api/playlists").get_json()
    assert [r["id"] for r in rows] == [other["id"], p["id"]]  # most recently changed first
    assert "tracks" not in rows[1] and rows[1]["count"] == 2 and rows[1]["duration"] == 400
    r = client.patch(f"/api/playlists/{p['id']}", json={"pinned": True, "name": " Trip ", "description": "summer"})
    assert r.status_code == 200 and r.get_json()["name"] == "Trip" and r.get_json()["pinned"] is True
    assert [r["id"] for r in client.get("/api/playlists").get_json()][0] == p["id"]  # pinned first
    for bad in ({"name": ""}, {"pinned": "yes"}, {"tracks": []}, {"description": 3}):
        assert client.patch(f"/api/playlists/{p['id']}", json=bad).status_code == 400
    assert client.post("/api/playlists", json={"name": "  "}).status_code == 400
    assert client.post("/api/playlists", json={"name": "x", "tracks": "nope"}).status_code == 400
    got = client.get(f"/api/playlists/{p['id']}").get_json()
    assert got["name"] == "Trip" and len(got["tracks"]) == 2 and "liked" in got["tracks"][0]
    assert client.delete(f"/api/playlists/{p['id']}").status_code == 204
    assert client.get(f"/api/playlists/{p['id']}").status_code == 404
    assert client.get("/api/playlists/NOTANID").status_code == 404


def test_add_remove_move(client):
    p = make(client, tracks=[track(A)])
    pid = p["id"]
    r = client.post(f"/api/playlists/{pid}/tracks", json={"tracks": [track(B), track(C)], "position": 0}).get_json()
    assert [t["id"] for t in r["tracks"]] == [B, C, A] and r["added"] == 2 and r["cover_id"] == B
    r = client.post(f"/api/playlists/{pid}/tracks", json={"tracks": [track(A), track("mc:9"), track("mc:9"),
                                                                      {"id": "x"}], "dedupe": True}).get_json()
    assert [t["id"] for t in r["tracks"]] == [B, C, A, "mc:9"] and (r["added"], r["skipped"]) == (1, 3)
    r = client.post(f"/api/playlists/{pid}/tracks", json={"tracks": [track(A)]}).get_json()  # no dedupe: repeats ok
    assert [t["id"] for t in r["tracks"]].count(A) == 2
    assert client.post(f"/api/playlists/{pid}/tracks", json={"tracks": [track(A)], "position": "1"}).status_code == 400
    r = client.post(f"/api/playlists/{pid}/move", json={"from": 0, "to": 3}).get_json()
    assert [t["id"] for t in r["tracks"]] == [C, A, "mc:9", B, A]
    r = client.post(f"/api/playlists/{pid}/move", json={"from": 4, "to": 0}).get_json()
    assert [t["id"] for t in r["tracks"]] == [A, C, A, "mc:9", B]
    assert client.post(f"/api/playlists/{pid}/move", json={"from": 0, "to": 9}).status_code == 400
    assert client.post(f"/api/playlists/{pid}/move", json={"from": True, "to": 1}).status_code == 400
    assert client.delete(f"/api/playlists/{pid}/tracks/1?id={B}").status_code == 409  # the list changed meanwhile
    r = client.delete(f"/api/playlists/{pid}/tracks/1?id={C}")
    assert r.status_code == 200 and [t["id"] for t in r.get_json()["tracks"]] == [A, A, "mc:9", B]
    assert client.delete(f"/api/playlists/{pid}/tracks/9").status_code == 404
    assert client.post("/api/playlists/abcdef12/move", json={"from": 0, "to": 0}).status_code == 404


def test_m3u(client, files):
    f = keep(A, "Kept Song.opus")
    p = make(client, "Mix", [track(A, artists=[{"name": "Daft Punk", "id": None}]), track(B, duration=None),
                             {"id": "mc:77", "title": "Flac one"}])
    r = client.get(f"/api/playlists/{p['id']}.m3u")
    assert r.status_code == 200 and r.mimetype == "audio/x-mpegurl"
    assert 'filename="Mix.m3u"' in r.headers["Content-Disposition"]
    lines = r.get_data(as_text=True).splitlines()
    assert lines == ["#EXTM3U", "#PLAYLIST:Mix",
                     f"#EXTINF:200,Daft Punk - Song {A[:3]}", "Reef/Kept Song.opus",
                     f"#EXTINF:-1,Artist - Topic - Song {B[:3]}", f"https://www.youtube.com/watch?v={B}",
                     "# not kept: mc:77 Flac one"]
    assert os.path.exists(f)
    assert client.get("/api/playlists/abcdef12.m3u").status_code == 404


def test_export_writes_kept_tracks_and_rescans(client, files, monkeypatch):
    keep(A, "One.opus")
    rescans = []
    monkeypatch.setattr(navidrome, "rescan", lambda: rescans.append(1) or True)
    p = make(client, "../../evil: name/x", [track(A), track(B)])
    r = client.post(f"/api/playlists/{p['id']}/export")
    assert r.status_code == 200, r.get_json()
    out = r.get_json()
    assert out["tracks"] == 1 and out["skipped"] == 1 and out["rescanned"] is True and rescans == [1]
    written = config.MUSIC_DIR / out["path"]
    assert written.parent == config.DOWNLOAD_DIR / "Playlists" and written.suffix == ".m3u"
    assert "/" not in written.name and not written.name.startswith(".")
    # Paths are relative to the playlist file, which is how Navidrome resolves them.
    assert written.read_text().splitlines()[2:] == [f"#EXTINF:200,Artist - Topic - Song {A[:3]}", "../One.opus"]
    assert (written.parent / "../One.opus").resolve() == (config.DOWNLOAD_DIR / "One.opus").resolve()
    # Renamed: the old file goes, the new one is written.
    client.patch(f"/api/playlists/{p['id']}", json={"name": "Better"})
    out2 = client.post(f"/api/playlists/{p['id']}/export").get_json()
    assert out2["path"] == "Reef/Playlists/Better.m3u" and not written.exists()
    assert playlists.get(p["id"])["exported"]["path"] == "Reef/Playlists/Better.m3u"


def test_export_needs_a_kept_track(client):
    p = make(client, "Nothing kept", [track(B)])
    assert client.post(f"/api/playlists/{p['id']}/export").status_code == 409
    assert client.post("/api/playlists/abcdef12/export").status_code == 404


def test_export_name_sanitizing():
    assert playlists.export_name("..") == "Playlist"
    assert "/" not in playlists.export_name("a/b") and "\\" not in playlists.export_name("a\\b")
    assert not playlists.export_name(".hidden").startswith(".")
    assert len(playlists.export_name("x" * 400)) <= 180


# ---------- exports that share a name (audit F9) ----------

def _export(client, pid):
    r = client.post(f"/api/playlists/{pid}/export")
    assert r.status_code == 200, r.get_json()
    return r.get_json()["path"]


@pytest.fixture
def two_kept(monkeypatch):
    monkeypatch.setattr(navidrome, "rescan", lambda: False)
    keep(A, "One.opus")
    keep(B, "Two.opus")


def test_same_name_exports_get_their_own_files(client, two_kept):
    p1, p2 = make(client, "Mix", [track(A)]), make(client, "Mix", [track(B)])
    p3, p4 = make(client, "AC/DC", [track(A)]), make(client, "AC⧸DC", [track(A), track(B)])  # sanitise alike
    assert playlists.export_name("AC/DC") == playlists.export_name("AC⧸DC")
    a, b, c, d = (_export(client, p["id"]) for p in (p1, p2, p3, p4))
    assert (a, b) == ("Reef/Playlists/Mix.m3u", f"Reef/Playlists/Mix [{p2['id']}].m3u")
    assert (c, d) == ("Reef/Playlists/AC⧸DC.m3u", f"Reef/Playlists/AC⧸DC [{p4['id']}].m3u")
    read = lambda rel: (config.MUSIC_DIR / rel).read_text().splitlines()[3:]  # noqa: E731  (the paths)
    assert read(a) == ["../One.opus"] and read(b) == ["../Two.opus"] and read(c) == ["../One.opus"]
    assert read(d)[::2] == ["../One.opus", "../Two.opus"]
    assert _export(client, p1["id"]) == a and read(b) == ["../Two.opus"]  # a re-export keeps its own file


def test_rename_and_reexport_never_deletes_another_playlists_file(client, two_kept):
    p1, p2 = make(client, "Mix", [track(A)]), make(client, "Mix", [track(B)])
    a, b = _export(client, p1["id"]), _export(client, p2["id"])
    client.patch(f"/api/playlists/{p2['id']}", json={"name": "Renamed"})
    assert _export(client, p2["id"]) == "Reef/Playlists/Renamed.m3u"
    assert not (config.MUSIC_DIR / b).exists() and (config.MUSIC_DIR / a).exists()  # only its own old file went
    # History from before the fix: both playlists recorded the same file. Neither re-export may delete it.
    with playlists._file.lock:
        items = playlists._file.load()
        for p in items:
            p["exported"] = {"path": a, "at": 1}
        playlists._file.save(items)
    client.patch(f"/api/playlists/{p1['id']}", json={"name": "Other"})
    assert _export(client, p1["id"]) == "Reef/Playlists/Other.m3u" and (config.MUSIC_DIR / a).exists()
    client.patch(f"/api/playlists/{p1['id']}", json={"name": "Mix"})
    assert _export(client, p1["id"]) == f"Reef/Playlists/Mix [{p1['id']}].m3u"  # p2 still points at Mix.m3u


def test_a_file_reef_didnt_write_is_left_alone(client, two_kept):
    folder = playlists.export_dir()
    folder.mkdir(parents=True)
    (folder / "Solo.m3u").write_text("the user's own list")
    p = make(client, "Solo", [track(A)])
    assert _export(client, p["id"]) == f"Reef/Playlists/Solo [{p['id']}].m3u"
    assert (folder / "Solo.m3u").read_text() == "the user's own list"


@pytest.mark.parametrize("spelling", ["link", "case"])
def test_an_alias_of_another_playlists_export_is_not_ours(client, two_kept, spelling):
    """Another playlist records a link to (or another spelling of) this playlist's file: re-exporting must neither
    write over it nor delete it."""
    p1, p2 = make(client, "Mix", [track(A)]), make(client, "Other", [track(B)])
    mix = config.MUSIC_DIR / _export(client, p1["id"])
    if spelling == "link":
        (mix.parent / "Alias.m3u").symlink_to(mix)
        theirs = "Reef/Playlists/Alias.m3u"
    else:
        theirs = "Reef/Playlists/MIX.m3u"
    with playlists._file.lock:
        items = playlists._file.load()
        next(p for p in items if p["id"] == p2["id"])["exported"] = {"path": theirs, "at": 1}
        playlists._file.save(items)
    before = mix.read_text()
    assert _export(client, p1["id"]) == f"Reef/Playlists/Mix [{p1['id']}].m3u"
    assert mix.read_text() == before  # left as it was: p2's export points at it too
    client.patch(f"/api/playlists/{p1['id']}", json={"name": "Renamed"})
    assert _export(client, p1["id"]) == "Reef/Playlists/Renamed.m3u" and mix.exists()

def test_a_leftover_named_like_the_temp_file_is_never_written_through(client, two_kept):
    """The old export wrote `<name>.m3u.tmp`, then renamed it: a link left under that name sent the playlist text into
    whatever it pointed at (a kept song) and published the link itself as the .m3u."""
    folder = playlists.export_dir()
    folder.mkdir(parents=True)
    song = config.DOWNLOAD_DIR / "One.opus"
    audio = song.read_bytes()
    (folder / "Mix.m3u.tmp").symlink_to(song)
    p = make(client, "Mix", [track(A)])
    assert _export(client, p["id"]) == "Reef/Playlists/Mix.m3u"
    assert song.read_bytes() == audio and not (folder / "Mix.m3u").is_symlink()
    assert (folder / "Mix.m3u").read_text().startswith("#EXTM3U") and (folder / "Mix.m3u.tmp").is_symlink()
    assert sorted(x.name for x in folder.iterdir()) == ["Mix.m3u", "Mix.m3u.tmp"]  # no temp file left

def test_concurrent_same_name_exports_and_a_playlist_deleted_midway(client, two_kept, monkeypatch):
    import threading
    import time
    ps = [make(client, "Mix", [track(A)]) for _ in range(4)]
    real_m3u = playlists.m3u

    def slow(*a, **k):  # widen the window between picking a name and recording it
        time.sleep(0.02)
        return real_m3u(*a, **k)
    monkeypatch.setattr(playlists, "m3u", slow)
    out = []
    threads = [threading.Thread(target=lambda pid=p["id"]: out.append(playlists.export(pid)["path"])) for p in ps]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(set(out)) == 4
    p = make(client, "Gone", [track(A)])
    monkeypatch.setattr(playlists, "set_exported", lambda pid, rel: playlists.delete(pid) and False)
    assert client.post(f"/api/playlists/{p['id']}/export").status_code == 404
    assert not (playlists.export_dir() / "Gone.m3u").exists()  # no orphan file for a deleted playlist


# ---------- history: kept tracks are never dropped ----------

def vid(n):
    return f"k{n:010d}"  # 11 chars: a valid YouTube id


def test_history_keeps_every_kept_track(client, files, monkeypatch):
    n = history._MAX + 100
    entries = [{"id": vid(i), "title": f"t{i}", "file": f"/m/{i}.opus", "kept_at": i,
                **({"nd_id": f"nd-{i}"} if i % 2 else {})} for i in range(n)]
    history.HISTORY_FILE.write_text(json.dumps(entries))
    history.add({"id": vid(n), "title": "newest", "file": "/m/new.opus"})
    assert len(history.ids()) == n + 1 and len(history.list_recent(limit=None)) == n + 1
    assert len(history.list_recent()) == 50 and history.list_recent()[0]["id"] == vid(n)
    oldest, older = vid(n - 1), vid(n - 2)  # the file's tail, past entry 500: nd-599 and no nd_id
    assert n - 1 == 599
    out = app._mark_kept([{"id": oldest}, {"id": older}, {"id": A}])
    assert (out[0]["kept"], out[0]["nd_id"]) == (True, "nd-599") and out[1]["kept"] is True and "nd_id" not in out[1]
    assert out[2]["kept"] is False
    monkeypatch.setattr(sources.youtube.SOURCE, "search", lambda q, limit: [track(oldest)])
    body = client.get("/api/search?q=x&src=yt").get_json()
    assert body[0]["kept"] is True and body[0]["nd_id"] == "nd-599"
    assert history.update(oldest, nd_id="late") and history.get(oldest)["nd_id"] == "late"


def test_history_caps_only_entries_without_a_file(files):
    loose = [{"id": vid(i), "title": "no file"} for i in range(history._MAX + 20)]
    history.HISTORY_FILE.write_text(json.dumps(loose))
    history.add({"id": A, "title": "kept", "file": "/m/a.opus"})
    items = history.list_recent(limit=None)
    assert items[0]["id"] == A and sum(1 for i in items if not i.get("file")) == history._MAX


def test_history_reads_see_outside_changes(files):
    history.add({"id": A, "title": "a", "file": "/m/a.opus"})
    assert history.ids() == {A}
    history.HISTORY_FILE.unlink()
    assert history.ids() == set() and history.get(A) is None
    history.HISTORY_FILE.write_text(json.dumps([{"id": B, "file": "/m/b.opus"}]))
    assert history.ids() == {B}
