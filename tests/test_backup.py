"""Backup and restore: content and the settings allow-list (no secrets), write-validate-rename, replace and merge
round trips (merge idempotent), refusals (newer schema, wrong format, bad sections), the pre-restore snapshot,
the size limit, daily snapshots and the routes."""
import io
import json

import pytest

import app
import backup
import blocklist
import config
import history
import likes
import listens
import lyrics
import playlists
import recommend
import settings

YT, YT2, YT3 = "dQw4w9WgXcQ", "FGBhQbmPwH8", "aaaaaaaaaaa"
TOKEN = "lb-secret-token-1234"
T0 = 1_760_000_000


@pytest.fixture(autouse=True)
def files(monkeypatch, tmp_path):
    monkeypatch.setattr(history, "HISTORY_FILE", tmp_path / "history.json")
    for name in ("LISTENS", "LIKES", "PLAYLISTS", "SETTINGS", "DISLIKES", "BLOCKLIST", "LYRICS", "ALIASES", "MATCHES"):
        monkeypatch.setattr(config, f"{name}_FILE", tmp_path / f"{name.lower()}.json")
    monkeypatch.setattr(config, "BACKUP_DIR", tmp_path / "backups")
    monkeypatch.setattr(lyrics, "_cache", None)
    return tmp_path


@pytest.fixture
def client():
    return app.app.test_client()


def track(tid, title="Song"):
    return {"id": tid, "title": title, "channel": "Artist", "duration": 200, "source": "yt"}


def seed(tmp_path):
    kept = tmp_path / "kept.opus"
    kept.write_bytes(b"x")
    history._file.save([{**track(YT), "file": str(kept), "kept_at": T0, "nd_id": "nd1"},
                        {**track(YT2), "file": "/elsewhere/gone.opus", "kept_at": T0 - 10}])
    listens._file.save([{**track(YT), "played_s": 200, "started_at": T0, "ended_at": T0 + 200, "counted": True,
                         "skipped": False, "scrobbled": True}])
    likes._file.save([{**track(YT2), "album": "", "liked_at": T0}])
    playlists._file.save([{"id": "0123abcd", "name": "Mix", "description": "", "tracks": [track(YT), track(YT2)],
                           "pinned": False, "created_at": T0, "updated_at": T0, "cover_id": YT}])
    recommend._dislikes.save([{**track(YT3), "at": T0, "until": T0 + 14 * 86400, "artist_key": "artist", "artist_id": None}])
    blocklist.add(None, "Hidden Band")
    config.LYRICS_FILE.write_text(json.dumps({YT: {"synced": "[00:01.00]a\n[00:02.00]b\n[00:03.00]c", "plain": None,
                                                   "instrumental": False, "at": T0},
                                              YT2: {"miss": True, "at": T0}}))
    settings.update({"replaygain": False, "listenbrainz_token": TOKEN, "tracks_api": "https://key.example/?k=secret",
                     "skipped_version": "abcdef1"})


def test_backup_content_and_allow_list(files):
    seed(files)
    doc = backup.build(now=T0)
    assert doc["format"] == "reef-backup" and doc["schemaVersion"] == 1 and doc["createdAt"].endswith("Z")
    assert set(doc["sections"]) == {"history", "lyrics", "listens", "likes", "playlists", "dislikes", "blocklist", "settings",
                                    "aliases", "matches"}
    assert doc["sections"]["settings"] == {"replaygain": False}  # never the token, URLs or per-device state
    raw = json.dumps(doc)
    assert TOKEN not in raw and "key.example" not in raw and "abcdef1" not in raw
    assert list(doc["sections"]["lyrics"]) == [YT]  # misses aren't worth carrying
    assert doc["sections"]["listens"][0]["scrobbled"] is True


def test_download_route(client, files):
    seed(files)
    r = client.get("/api/backup")
    assert r.status_code == 200
    assert r.headers["Content-Disposition"].startswith('attachment; filename="reef-backup-')
    doc = json.loads(r.data)
    assert doc["format"] == "reef-backup" and TOKEN not in r.get_data(as_text=True)
    assert [p.name for p in config.BACKUP_DIR.iterdir()] == []  # the temp file is gone


def test_write_validates_before_the_rename(files):
    bad = {"format": "reef-backup", "schemaVersion": 1, "sections": {"likes": {"not": "a list"}}}
    with pytest.raises(backup.BackupError):
        backup.write(config.BACKUP_DIR / "x.json", doc=bad)
    assert not (config.BACKUP_DIR / "x.json").exists() and not (config.BACKUP_DIR / "x.json.tmp").exists()


def test_refusals(client):
    for doc, msg in (({"format": "other", "schemaVersion": 1, "sections": {}}, "not a Reef backup"),
                     ({"format": "reef-backup", "schemaVersion": 2, "sections": {}}, "newer Reef"),
                     ({"format": "reef-backup", "schemaVersion": "1", "sections": {}}, "schemaVersion"),
                     ({"format": "reef-backup", "schemaVersion": 1}, "no sections"),
                     ({"format": "reef-backup", "schemaVersion": 1, "sections": {"history": "x"}}, "history must be"),
                     ({"format": "reef-backup", "schemaVersion": 1, "sections": {}}, "none of Reef's")):
        r = client.post("/api/restore", json=doc)
        assert r.status_code == 400 and msg in r.get_json()["error"], doc
    assert client.post("/api/restore", data=b"{nope", content_type="application/json").status_code == 400
    assert not config.BACKUP_DIR.exists() or not list(config.BACKUP_DIR.iterdir())  # refused before any snapshot


def test_size_limit(client, monkeypatch):
    monkeypatch.setattr(backup, "MAX_BYTES", 1000)
    big = json.dumps({"format": "reef-backup", "schemaVersion": 1, "sections": {"likes": [track(YT)] * 50}})
    r = client.post("/api/restore", data=big, content_type="application/json")
    assert r.status_code == 413
    r = client.post("/api/restore", data={"file": (io.BytesIO(big.encode()), "b.json"), "mode": "merge"},
                    content_type="multipart/form-data")
    assert r.status_code == 413


def test_replace_round_trip_and_pre_restore_snapshot(client, files):
    seed(files)
    doc = backup.build(now=T0)
    # Change everything, then restore the backup over it.
    history._file.save([])
    listens._file.save([])
    likes._file.save([{**track(YT3), "liked_at": T0 + 5}])
    playlists._file.save([])
    recommend._dislikes.save([])
    blocklist.remove(name="Hidden Band")
    config.LYRICS_FILE.write_text("{}")
    settings.update({"replaygain": True, "lrc_sidecar": True})
    r = client.post("/api/restore?mode=replace", json=doc)
    assert r.status_code == 200
    out = r.get_json()
    assert out["mode"] == "replace" and out["snapshot"].startswith("pre-restore-")
    assert out["sections"]["likes"] == {"before": 1, "in_backup": 1, "skipped": 0, "after": 1}
    assert [e["id"] for e in likes.entries()] == [YT2]
    assert [e["id"] for e in listens.entries()] == [YT] and listens.entries()[0]["scrobbled"] is True
    assert playlists.get("0123abcd")["name"] == "Mix"
    assert recommend.dislikes(now=T0)[0]["id"] == YT3
    assert [e["name"] for e in blocklist.entries()] == ["Hidden Band"]
    assert lyrics.for_track(YT, "Song", "Artist")["synced"].startswith("[00:01.00]")  # cache re-read, no lookup
    assert settings.get("replaygain") is False and settings.get("lrc_sidecar") == config.LRC_SIDECAR  # replaced
    assert settings.get("listenbrainz_token") == TOKEN  # untouched by a restore
    entries = {e["id"]: e for e in history.list_recent(None)}
    assert entries[YT]["file"].endswith("kept.opus") and "file" not in entries[YT2]  # gone on this device
    snap = json.loads((config.BACKUP_DIR / out["snapshot"]).read_text())
    assert [e["id"] for e in snap["sections"]["likes"]] == [YT3]  # what was there before the restore


def test_merge_is_a_union_newer_wins_and_idempotent(client, files):
    seed(files)
    doc = backup.build(now=T0)
    likes._file.save([{**track(YT2, "Newer title"), "liked_at": T0 + 100}, {**track(YT3), "liked_at": T0 + 50}])
    listens._file.save([{**track(YT2), "played_s": 50, "started_at": T0 + 500, "ended_at": T0 + 550, "counted": True,
                         "skipped": False}])
    playlists._file.save([{"id": "0123abcd", "name": "Renamed", "description": "", "tracks": [], "pinned": True,
                           "created_at": T0, "updated_at": T0 + 1, "cover_id": None}])
    settings.update({"replaygain": True})
    r = client.post("/api/restore", data={"file": (io.BytesIO(json.dumps(doc).encode()), "b.json"), "mode": "merge"},
                    content_type="multipart/form-data")
    assert r.status_code == 200 and r.get_json()["mode"] == "merge"
    got = {e["id"]: e for e in likes.entries()}
    assert set(got) == {YT2, YT3} and got[YT2]["title"] == "Newer title"  # the local copy is newer
    assert [e["id"] for e in likes.entries()] == [YT2, YT3]
    assert [(e["id"], e["started_at"]) for e in listens.entries()] == [(YT, T0), (YT2, T0 + 500)]
    assert playlists.get("0123abcd")["name"] == "Renamed"
    assert settings.get("replaygain") is True  # merge keeps what's set here
    state = lambda: [json.loads(p.read_text()) for p in sorted(files.glob("*.json"))]  # noqa: E731
    before = state()
    r2 = backup.restore(doc, "merge")
    assert state() == before  # merging the same file again changes nothing
    assert all(c["after"] == c["before"] for c in r2["sections"].values())


def test_snapshots_in_the_same_second_dont_overwrite(files):
    a = backup.snapshot("pre-restore", now=T0)
    likes._file.save([{**track(YT), "liked_at": T0}])
    b = backup.snapshot("pre-restore", now=T0)
    assert a != b and b.endswith("-2.json")
    assert [s["name"] for s in backup.snapshots()] == [b, a]
    assert json.loads((config.BACKUP_DIR / a).read_text())["sections"]["likes"] == []


def test_bad_items_are_skipped_and_counted(client):
    doc = {"format": "reef-backup", "schemaVersion": 1, "sections": {
        "likes": [track(YT), {"id": "not an id"}, "junk", track("mc:123")],
        "settings": {"replaygain": "maybe", "listenbrainz_token": TOKEN, "ytmusic": False},
        "blocklist": [{"name": "A"}, {"name": ""}, {"id": "x"}]}}
    out = client.post("/api/restore", json=doc).get_json()
    assert out["sections"]["likes"]["skipped"] == 2 and [e["id"] for e in likes.entries()] == [YT, "mc:123"]
    assert out["sections"]["settings"]["skipped"] == 2 and settings.get("ytmusic") is False
    assert settings.get("listenbrainz_token") == ""
    assert out["sections"]["blocklist"] == {"before": 0, "in_backup": 1, "skipped": 2, "after": 1}


def test_daily_snapshots_keep_seven(files, monkeypatch):
    seed(files)
    names = []
    for day in range(9):
        name = backup.tick(now=T0 + day * 86400)
        assert name and name.startswith("auto-")
        names.append(name)
        assert backup.tick(now=T0 + day * 86400 + 3600) is None  # once a day
    kept = [s["name"] for s in backup.snapshots() if s["kind"] == "auto"]
    assert kept == names[::-1][:7]
    st = backup.status()
    assert st["last_auto_at"] == T0 + 8 * 86400 and len(st["snapshots"]) == 7
    doc = json.loads((config.BACKUP_DIR / kept[0]).read_text())
    backup.validate(doc)
    assert TOKEN not in json.dumps(doc)


def test_round13_settings_are_backed_up_and_restored(files, client):
    """keep_lossless, sponsorblock_play and replace_dead_tracks are preferences: they travel with a backup."""
    assert {"keep_lossless", "sponsorblock_play", "replace_dead_tracks"} <= set(backup.SETTINGS_KEYS)
    assert set(backup.SETTINGS_KEYS) <= set(settings.SCHEMA)
    settings.update({"keep_lossless": True, "sponsorblock_play": False, "replace_dead_tracks": False})
    doc = backup.build(now=T0)
    assert doc["sections"]["settings"] == {"keep_lossless": True, "sponsorblock_play": False,
                                           "replace_dead_tracks": False}
    settings.update({"keep_lossless": None, "sponsorblock_play": None, "replace_dead_tracks": None})
    assert client.post("/api/restore?mode=replace", json=doc).status_code == 200
    assert (settings.get("keep_lossless"), settings.get("sponsorblock_play"), settings.get("replace_dead_tracks")) == \
        (True, False, False)


# ---------- hostile and malformed backups (audit F1) ----------

SYNC = "[00:01.00]a\n[00:12.00]b\n[00:23.00]c"
BIG = 10 ** 400  # an int no float can hold
INF, NAN = float("inf"), float("nan")


def strict(text):
    """json.loads as a browser's JSON.parse does it: NaN / Infinity are syntax errors."""
    def refuse(token):
        raise ValueError(token)
    return json.loads(text, parse_constant=refuse)


def doc_of(**sections):
    return {"format": "reef-backup", "schemaVersion": 1, "sections": sections}


def post(client, doc, mode="replace"):
    # json.dumps writes inf / nan as Infinity / NaN and BIG as its 401 digits, like a hand-edited file would
    return client.post(f"/api/restore?mode={mode}", data=json.dumps(doc), content_type="application/json")


def test_poisoned_fields_are_dropped_and_every_listing_still_reads(client, files, monkeypatch):
    doc = doc_of(
        playlists=[{"id": "0123abcd", "name": "  P  ", "tracks": [track(YT), {"id": YT2, "duration": BIG}],
                    "updated_at": "oops", "created_at": INF, "pinned": "yes", "cover_id": "../x",
                    "exported": {"path": "../../etc/x.m3u"}, "import": {"kind": "csv", "total": "x", "rows": 3},
                    "nested": {"x": [1]}},
                   {"id": "0123abcd", "name": "duplicate id"}, {"id": "4567cdef", "name": "  "}],
        likes=[{**track(YT), "liked_at": "x", "title": {"x": 1}, "duration": BIG},
               {**track(YT2), "liked_at": NAN, "extra": INF}],
        listens=[{**track(YT), "started_at": INF, "played_s": 100, "counted": True},
                 {**track(YT2), "started_at": T0, "ended_at": "x", "played_s": 100, "counted": "yes"},
                 {**track(YT3), "started_at": T0 + 10, "played_s": BIG, "counted": True},
                 {**track(YT3), "started_at": BIG, "played_s": 50, "counted": True}],
        history=[{**track(YT), "kept_at": "x", "nd_cover": 5, "nd_id": {"a": 1}, "gain_db": NAN, "thumb": "javascript:x",
                  "upgraded_from": "nope", "lyrics": "maybe"}],
        dislikes=[{**track(YT3), "at": "x"}, {**track(YT2), "at": T0, "until": INF, "blocked": "junk"}],
        lyrics={YT: {"v": 2, "provider": "lrclib", "synced": SYNC, "plain": None, "instrumental": False, "at": INF,
                     "offset_ms": INF, "words": [[[INF, None, "a"]], [[2000, None, "b"]], [[3000, 1, "c"]]],
                     "lrclib": {"synced": 5, "plain": None, "instrumental": "no"}},
                YT2: {"v": 2, "provider": lyrics.PINNED, "pinned": True, "lrclib_id": BIG, "synced": SYNC, "at": T0},
                YT3: {"v": 2, "offset_ms": BIG, "at": T0}},
        matches={"q:a|b": {"id": YT, "manual": True, "at": INF, "score": NAN, "track": track(YT)}})
    r = post(client, doc)
    assert r.status_code == 200
    s = r.get_json()["sections"]
    assert s["playlists"] == {"before": 0, "in_backup": 1, "skipped": 2, "after": 1}
    assert s["listens"]["skipped"] == 3 and s["dislikes"]["skipped"] == 1 and s["likes"]["skipped"] == 0
    assert s["lyrics"]["skipped"] == 1  # YT3: an offset that isn't a number leaves nothing to keep

    p = playlists.get("0123abcd")
    assert p["name"] == "P" and p["pinned"] is False and p["cover_id"] == YT and [t["id"] for t in p["tracks"]] == [YT, YT2]
    assert p["tracks"][1]["duration"] is None and p["import"] == {"kind": "csv", "rows": 3}
    assert not {"updated_at", "created_at", "exported", "nested"} & set(p)
    lk = {e["id"]: e for e in likes.entries()}
    assert lk[YT]["title"] == "" and lk[YT]["duration"] is None and "liked_at" not in lk[YT] and "extra" not in lk[YT2]
    ls = listens.entries()
    assert [(e["id"], e["started_at"]) for e in ls] == [(YT2, T0)] and "ended_at" not in ls[0] and "counted" not in ls[0]
    h = history.get(YT)
    assert not {"kept_at", "nd_cover", "nd_id", "gain_db", "thumb", "upgraded_from", "lyrics"} & set(h)
    assert recommend._dislikes.load()[0]["id"] == YT2 and not {"until", "blocked"} & set(recommend._dislikes.load()[0])
    e = lyrics._entries()
    assert e[YT]["at"] == 0 and "offset_ms" not in e[YT] and "words" not in e[YT]
    assert e[YT]["lrclib"] == {"synced": None, "plain": None, "instrumental": False}
    assert "pinned" not in e[YT2]
    m = __import__("matching")._file.load()["q:a|b"]
    assert m["at"] == 0 and m["score"] is None

    # Nothing non-finite reached a file, and every listing reads (and parses as strict JSON) again.
    for f in files.glob("*.json"):
        strict(f.read_text())
    for url in ("/api/playlists", "/api/playlists/0123abcd", "/api/likes", "/api/listens", "/api/history",
                "/api/dislikes", "/api/stats?period=all", "/api/recap?period=year", "/api/recap?period=month&offset=-1"):
        got = client.get(url)
        assert got.status_code == 200, url
        strict(got.get_data(as_text=True))
    assert lyrics.for_display(YT, "Song", "Artist")["offset_ms"] == 0 and lyrics.offset(YT3) == 0
    import navidrome
    monkeypatch.setattr(navidrome, "configured", lambda: True)
    monkeypatch.setattr(navidrome, "find_song", lambda *a: None)
    assert client.get(f"/api/art/{YT}?size=300").status_code == 302  # no junk cover id to choke on


def test_valid_metadata_and_unknown_plain_fields_survive(files):
    kept = files / "kept.flac"
    kept.write_bytes(b"x")
    hist = {**track("mc:9"), "source": "mc", "file": str(kept), "kept_at": T0, "nd_id": "nd-1_a", "nd_cover": "al-2.x",
            "thumb": "https://i.ytimg.com/vi/x/mqdefault.jpg", "art": "", "upgraded_from": YT, "lyrics": None,
            "gain_db": -3.25, "album": "LP", "newer_field": "kept", "nested_field": {"x": 1}}
    like = {**track(YT), "album": "", "liked_at": T0, "artists": [{"name": "A", "id": "UCaaaaaaaaaaaaaaaaaaaaaa"}]}
    pl = {"id": "0123abcd", "name": "Mix", "description": "d", "tracks": [track(YT)], "pinned": True, "created_at": T0,
          "updated_at": T0 + 1, "cover_id": YT, "exported": {"path": "Reef/Playlists/Mix.m3u", "at": T0},
          "import": {"kind": "takeout", "rows": 1, "skipped": 0, "total": 0, "at": T0}}
    listen = {**track(YT), "played_s": 12.5, "started_at": T0, "ended_at": T0 + 13, "counted": False, "skipped": True,
              "scrobbled": True, "via": "nd"}
    dis = {**track(YT3), "at": T0, "until": T0 + 9, "artist_key": "artist", "artist_id": None,
           "blocked": {"id": None, "name": "Artist"}}
    clean = backup.validate(doc_of(history=[hist], likes=[like], playlists=[pl], listens=[listen], dislikes=[dis]))
    assert clean["history"] == ([{k: v for k, v in hist.items() if k != "nested_field"}], 0)
    assert clean["likes"] == ([like], 0) and clean["playlists"] == ([pl], 0)
    assert clean["listens"] == ([listen], 0) and clean["dislikes"] == ([dis], 0)


def test_a_restore_that_cant_apply_changes_nothing(client, files, monkeypatch):
    seed(files)
    before = {f.name: f.read_text() for f in files.glob("*.json")}
    real = backup._apply

    def broken(name, mode, cur, new):
        if name == "likes":
            raise TypeError("boom")
        return real(name, mode, cur, new)

    monkeypatch.setattr(backup, "_apply", broken)
    r = post(client, doc_of(listens=[{**track(YT3), "started_at": T0 + 5, "played_s": 60, "counted": True}],
                            likes=[track(YT3)]), mode="merge")
    assert r.status_code == 400 and "likes" in r.get_json()["error"] and "nothing was changed" in r.get_json()["error"]
    assert {f.name: f.read_text() for f in files.glob("*.json")} == before and not backup.snapshots()


def test_merge_with_old_bad_data_here_and_bad_fields_there_applies_every_section(client, files):
    """The audit's case: a merge that used to write listens, then fail on likes with a 500."""
    likes._file.save([{**track(YT2), "liked_at": "x"}, {**track(YT3), "liked_at": T0}])  # junk from before the fix
    history._file.save([{**track(YT2), "kept_at": "x"}])
    r = post(client, doc_of(listens=[{**track(YT), "started_at": T0, "played_s": 100, "counted": True}],
                            likes=[{**track(YT), "liked_at": "x"}], history=[{**track(YT), "kept_at": T0}]), mode="merge")
    assert r.status_code == 200
    assert [e["id"] for e in listens.entries()] == [YT]
    assert {e["id"] for e in likes.entries()} == {YT, YT2, YT3}
    assert [e["id"] for e in history.list_recent(None)] == [YT, YT2]


def test_a_write_that_fails_midway_names_the_snapshot(client, files, monkeypatch):
    seed(files)
    def disk_full(change):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(backup.STORES["likes"], "transact", disk_full)
    r = post(client, doc_of(history=[{**track(YT3), "kept_at": T0}], likes=[track(YT3)]), mode="merge")
    assert r.status_code == 400
    err = r.get_json()["error"]
    snap = backup.snapshots()[0]["name"]
    assert "stopped at likes" in err and "No space left" in err and "after restoring history" in err
    assert f"backups/{snap}" in err and snap.startswith("pre-restore-")
    assert {e["id"] for e in history.list_recent(None)} == {YT, YT2, YT3}  # history was written before likes failed
    assert [e["id"] for e in json.loads((config.BACKUP_DIR / snap).read_text())["sections"]["history"]] == [YT, YT2]
