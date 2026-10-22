"""Backup sections added after Round 12: aliases, manual match fixes (not the automatic cache), lyrics timing offsets
(offset-only entries too) and pins, playlists with an import's placeholders, dislikes, and the newer settings
(never the visitor id). Validation, replace and merge (idempotent) for each."""
import json

import pytest

import aliases
import app
import backup
import config
import history
import lyrics
import matching
import playlists
import recommend
import settings

YT, YT2, YT3 = "dQw4w9WgXcQ", "FGBhQbmPwH8", "aaaaaaaaaaa"
CH = "UC" + "a" * 22
T0 = 1_760_000_000
PH = {"id": "pq:0123456789ab", "q": True, "title": "Hello", "artist": "Adele", "channel": "Adele", "duration": 295,
      "key": "q:adele|hello"}
SYNC = "[00:01.00]a\n[00:02.00]b\n[00:03.00]c"


@pytest.fixture(autouse=True)
def files(monkeypatch, tmp_path):
    monkeypatch.setattr(history, "HISTORY_FILE", tmp_path / "history.json")
    for name in ("LISTENS", "LIKES", "PLAYLISTS", "SETTINGS", "DISLIKES", "BLOCKLIST", "LYRICS", "ALIASES", "MATCHES"):
        monkeypatch.setattr(config, f"{name}_FILE", tmp_path / f"{name.lower()}.json")
    monkeypatch.setattr(config, "BACKUP_DIR", tmp_path / "backups")
    monkeypatch.setattr(lyrics, "_cache", None)
    return tmp_path


def track(tid, title="Song"):
    return {"id": tid, "title": title, "channel": "Artist", "duration": 200, "source": "yt"}


def seed():
    aliases._file.save([{"id": CH, "match": None, "name": "Бейонсе", "at": T0},
                        {"id": None, "match": "DaftPunkVEVO", "name": "Daft Punk (FR)", "at": T0}])
    matching._file.save({"q:adele|hello": {"id": YT, "score": None, "manual": True, "at": T0, "track": track(YT, "Hello")},
                         "q:x|auto": {"id": YT2, "score": 0.9, "manual": False, "at": T0, "track": track(YT2)}})
    config.LYRICS_FILE.write_text(json.dumps({
        YT: {"v": 2, "provider": "betterlyrics", "synced": SYNC, "plain": None, "instrumental": False,
             "words": [[[1000, 500, "a"]], [[2000, None, "b"]], [[3000, 400, "c"]]],
             "lrclib": {"synced": SYNC, "plain": None, "instrumental": False}, "offset_ms": 1500, "at": T0},
        YT2: {"v": 2, "provider": "lrclib-pinned", "pinned": True, "lrclib_id": 42, "synced": SYNC, "plain": None,
              "instrumental": False, "offset_ms": -500, "at": T0},
        "nd:abc": {"v": 2, "offset_ms": 800, "at": T0},                      # library track: offset only
        YT3: {"v": 2, "miss": True, "sig": "lrclib", "offset_ms": 300, "at": T0},  # a miss carrying an offset
        "mc:9": {"v": 2, "miss": True, "sig": "lrclib", "at": T0},            # plain miss: not worth carrying
    }))
    playlists._file.save([{"id": "0123abcd", "name": "Imported", "description": "", "pinned": False,
                           "tracks": [PH, track(YT)], "created_at": T0, "updated_at": T0, "cover_id": YT,
                           "import": {"kind": "csv", "rows": 2, "skipped": 0, "total": 1, "at": T0}}])
    recommend._dislikes.save([{**track(YT3), "at": T0, "until": T0 + 14 * 86400, "artist_key": "artist",
                               "artist_id": None}])
    settings.update({"yt_personalize": True, "charts_country": "us", "itunes_suggestions": False,
                     "yt_visitor_id": "CgtABCDEFGHIJKL"})


def wipe():
    for f in (config.ALIASES_FILE, config.MATCHES_FILE, config.LYRICS_FILE, config.PLAYLISTS_FILE, config.DISLIKES_FILE,
              config.SETTINGS_FILE):
        f.unlink(missing_ok=True)
    lyrics._cache = None


def test_sections_content():
    seed()
    s = backup.build(now=T0)["sections"]
    assert [a["name"] for a in s["aliases"]] == ["Бейонсе", "Daft Punk (FR)"]
    assert list(s["matches"]) == ["q:adele|hello"]  # the manual fix, not the automatic cache entry
    assert set(s["lyrics"]) == {YT, YT2, "nd:abc", YT3}
    assert s["lyrics"][YT]["offset_ms"] == 1500 and s["lyrics"][YT]["provider"] == "betterlyrics"
    assert s["lyrics"][YT]["words"][0] == [[1000, 500, "a"]] and s["lyrics"][YT]["v"] == 2
    assert s["lyrics"][YT2]["pinned"] is True and s["lyrics"][YT2]["lrclib_id"] == 42
    assert s["lyrics"]["nd:abc"] == {"v": 2, "offset_ms": 800, "at": T0}
    assert s["lyrics"][YT3] == {"v": 2, "offset_ms": 300, "at": T0}
    assert s["playlists"][0]["tracks"][0]["q"] is True and s["playlists"][0]["import"]["total"] == 1
    assert s["dislikes"][0]["id"] == YT3
    assert s["settings"] == {"yt_personalize": True, "charts_country": "US", "itunes_suggestions": False}
    assert "CgtABCDEFGHIJKL" not in json.dumps(backup.build(now=T0))  # never the visitor id


def test_replace_round_trip_restores_everything():
    seed()
    doc = json.loads(json.dumps(backup.build(now=T0)))
    wipe()
    matching._file.save({"q:old|fix": {"id": YT3, "score": None, "manual": True, "at": T0, "track": track(YT3)},
                         "q:keep|auto": {"id": YT3, "score": 0.8, "manual": False, "at": T0, "track": track(YT3)}})
    out = backup.restore(doc, "replace", now=T0 + 1)
    assert out["sections"]["aliases"] == {"before": 0, "in_backup": 2, "skipped": 0, "after": 2}
    assert out["sections"]["matches"] == {"before": 1, "in_backup": 1, "skipped": 0, "after": 1}
    assert aliases.resolver().name("Beyonce", CH) == "Бейонсе" and aliases.resolver().name("Daft Punk") == "Daft Punk (FR)"
    m = matching._file.load()
    assert set(m) == {"q:adele|hello", "q:keep|auto"}  # manual fixes swapped, the automatic cache left alone
    assert matching.cached("q:adele|hello")["manual"] is True
    assert lyrics.offset(YT) == 1500 and lyrics.offset("nd:abc") == 800 and lyrics.offset(YT3) == 300
    assert lyrics.pinned(YT2)["offset_ms"] == -500
    shown = lyrics.for_display(YT, "Song", "Artist")  # a cached v2 hit: no lookup, words intact
    assert shown["provider"] == "betterlyrics" and shown["words"][1][0]["text"] == "b" and shown["offset_ms"] == 1500
    p = playlists.get("0123abcd")
    assert p["tracks"][0] == PH and p["import"]["kind"] == "csv"
    assert recommend._dislikes.load()[0]["id"] == YT3
    assert settings.get("yt_personalize") is True and settings.get("charts_country") == "US"
    assert settings.get("yt_visitor_id") == ""


def test_merge_semantics_and_idempotence():
    seed()
    doc = json.loads(json.dumps(backup.build(now=T0)))
    wipe()
    # Here: a newer alias for the same channel, a lyrics hit without an offset for YT, a lookup hit for YT2 (the
    # backup's pin wins), an offset-only entry newer than the backup's for nd:abc.
    aliases._file.save([{"id": CH, "match": None, "name": "Beyoncé", "at": T0 + 100}])
    config.LYRICS_FILE.write_text(json.dumps({
        YT: {"v": 2, "provider": "lrclib", "synced": SYNC, "plain": None, "instrumental": False, "at": T0 + 50},
        YT2: {"v": 2, "provider": "lrclib", "synced": SYNC, "plain": None, "instrumental": False, "at": T0 + 50},
        "nd:abc": {"v": 2, "offset_ms": 100, "at": T0 + 50},
    }))
    lyrics._cache = None
    matching._file.save({"q:adele|hello": {"id": YT3, "score": 0.7, "manual": False, "at": T0 + 9, "track": track(YT3)}})
    backup.restore(doc, "merge", now=T0 + 1)
    names = {a.get("id") or a.get("match"): a["name"] for a in aliases.entries()}
    assert names == {CH: "Beyoncé", "DaftPunkVEVO": "Daft Punk (FR)"}  # the newer one here stays
    e = lyrics._entries()
    assert e[YT]["provider"] == "lrclib" and e[YT]["offset_ms"] == 1500  # newer text here, the backup's offset kept
    assert e[YT2]["pinned"] is True  # a pin beats a lookup
    assert e["nd:abc"]["offset_ms"] == 100  # the newer offset here stays
    assert matching.cached("q:adele|hello")["id"] == YT  # a manual fix beats an automatic match
    first = json.dumps([aliases._file.load(), lyrics._entries(), matching._file.load(), playlists._file.load()],
                       sort_keys=True)
    lyrics._cache = None
    backup.restore(doc, "merge", now=T0 + 2)
    lyrics._cache = None
    again = json.dumps([aliases._file.load(), lyrics._entries(), matching._file.load(), playlists._file.load()],
                       sort_keys=True)
    assert again == first  # merging the same file twice changes nothing


def test_validation_skips_bad_items():
    doc = {"format": "reef-backup", "schemaVersion": 1, "sections": {
        "aliases": [{"id": CH, "name": "Ok"}, {"id": "bad", "name": "x"}, {"name": "no target"}, "junk",
                    {"match": "Some Band", "name": "  Band  "}],
        "matches": {"q:a|b": {"id": YT, "manual": True, "at": T0, "track": track(YT)},
                    "q:auto": {"id": YT, "manual": False, "at": T0, "track": track(YT)},
                    "q:bad|id": {"id": "nope", "manual": True, "track": {"id": "nope"}},
                    "bad/key": {"id": YT, "manual": True, "track": track(YT)},
                    "q:mismatch": {"id": YT2, "manual": True, "track": track(YT)}},
        "lyrics": {YT: {"v": 2, "offset_ms": 99999, "at": T0}, YT2: {"v": 2, "offset_ms": 200, "at": T0},
                   "bad id": {"synced": SYNC}},
        "playlists": [{"id": "0123abcd", "name": "P", "tracks": [PH, {**PH, "id": "pq:NOPE"}, track(YT)]}]}}
    out = backup.restore(doc, "merge", now=T0)
    assert out["sections"]["aliases"]["skipped"] == 3 and out["sections"]["aliases"]["after"] == 2
    assert out["sections"]["matches"]["skipped"] == 4 and list(matching._file.load()) == ["q:a|b"]
    assert out["sections"]["lyrics"]["skipped"] == 2 and lyrics.offset(YT2) == 200
    assert [t["id"] for t in playlists.get("0123abcd")["tracks"]] == [PH["id"], YT]
    with pytest.raises(backup.BackupError, match="matches must be"):
        backup.validate({"format": "reef-backup", "schemaVersion": 1, "sections": {"matches": []}})
    with pytest.raises(backup.BackupError, match="aliases must be"):
        backup.validate({"format": "reef-backup", "schemaVersion": 1, "sections": {"aliases": {}}})


def test_old_backups_without_new_sections_leave_them_alone():
    seed()
    doc = {"format": "reef-backup", "schemaVersion": 1, "sections": {"likes": []}}
    backup.restore(doc, "replace", now=T0)
    assert len(aliases.entries()) == 2 and "q:x|auto" in matching._file.load()


def test_backup_route_round_trip():
    seed()
    client = app.app.test_client()
    raw = client.get("/api/backup").data
    wipe()
    r = client.post("/api/restore?mode=merge", data=raw, content_type="application/json")
    assert r.status_code == 200 and r.get_json()["sections"]["aliases"]["after"] == 2
    assert lyrics.offset("nd:abc") == 800 and matching.cached("q:adele|hello")["id"] == YT
