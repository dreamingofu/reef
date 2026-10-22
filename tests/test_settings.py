"""Runtime settings: env defaults vs settings.json overrides, validation, write-only secrets, and the
consumers that read through them (keeps, lyrics sidecar, YouTube Music, default source)."""
import json

import pytest
import yt_dlp

import app
import config
import downloader
import settings
import sources
import ytmusic

TOKEN = "lb-secret-token-1234"


@pytest.fixture(autouse=True)
def sfile(monkeypatch, tmp_path):
    path = tmp_path / "settings.json"
    monkeypatch.setattr(config, "SETTINGS_FILE", path)
    return path


@pytest.fixture
def client():
    return app.app.test_client()


def test_get_shape(client):
    body = client.get("/api/settings").get_json()
    assert set(body) == {"values", "schema"}
    keys = [r["key"] for r in body["schema"]]
    assert set(body["values"]) == set(keys) == set(settings.SCHEMA)
    for row in body["schema"]:
        assert {"key", "type", "default", "label", "help", "overridden"} <= set(row)
        assert row["type"] in ("bool", "choice", "list", "secret")
    by_key = {r["key"]: r for r in body["schema"]}
    assert by_key["default_source"]["choices"] == ["yt", "mc", "nd"]
    assert body["values"]["prefer_library"] is True and body["values"]["listenbrainz_token"] == {"set": False}


def test_env_is_the_default_and_the_file_overrides(monkeypatch, sfile):
    monkeypatch.setattr(config, "SPONSORBLOCK", True)
    assert settings.get("sponsorblock_keep") is True
    settings.update({"sponsorblock_keep": False})
    assert settings.get("sponsorblock_keep") is False
    assert json.loads(sfile.read_text()) == {"sponsorblock_keep": False}
    monkeypatch.setattr(config, "SPONSORBLOCK", False)
    assert settings.get("sponsorblock_keep") is False
    settings.update({"sponsorblock_keep": None})  # reset: back to the env value
    monkeypatch.setattr(config, "SPONSORBLOCK", True)
    assert settings.get("sponsorblock_keep") is True and json.loads(sfile.read_text()) == {}


def test_patch_validates_everything_before_writing(client, sfile):
    for bad in ({"replaygain": "yes"}, {"default_source": "spotify"}, {"nope": True},
                {"lyrics_providers": ["lrclib", "lrclib"]}, {"lyrics_providers": ["genius"]},
                {"listenbrainz_token": "has space"}, {"listenbrainz_token": 5}, {"ytmusic": 1},
                {"replaygain": False, "default_source": "zz"}, [], "x"):
        r = client.patch("/api/settings", json=bad)
        assert r.status_code == 400 and r.get_json()["error"], bad
    assert not sfile.exists()
    r = client.patch("/api/settings", json={"replaygain": False, "default_source": "mc",
                                            "lyrics_providers": ["lrclib"]})
    assert r.status_code == 200
    v = r.get_json()["values"]
    assert (v["replaygain"], v["default_source"], v["lyrics_providers"]) == (False, "mc", ["lrclib"])
    over = {row["key"] for row in r.get_json()["schema"] if row["overridden"]}
    assert over == {"replaygain", "default_source", "lyrics_providers"}


def test_secret_is_write_only(client, sfile, monkeypatch):
    r = client.patch("/api/settings", json={"listenbrainz_token": f"  {TOKEN} "})
    assert r.status_code == 200 and TOKEN not in r.get_data(as_text=True)
    assert r.get_json()["values"]["listenbrainz_token"] == {"set": True}
    assert settings.get("listenbrainz_token") == TOKEN  # server-side code can read it
    row = next(x for x in client.get("/api/settings").get_json()["schema"] if x["key"] == "listenbrainz_token")
    assert row["default"] is None
    monkeypatch.setattr(app.log, "recent", lambda: [])
    assert TOKEN not in client.get("/api/diagnostics").get_data(as_text=True)
    client.patch("/api/settings", json={"listenbrainz_token": ""})  # "" clears it
    assert client.get("/api/settings").get_json()["values"]["listenbrainz_token"] == {"set": False}


def test_bad_values_in_the_file_fall_back_to_the_default(sfile, monkeypatch):
    monkeypatch.setattr(config, "REPLAYGAIN", True)
    sfile.write_text(json.dumps({"replaygain": "maybe", "unknown": 1}))
    assert settings.get("replaygain") is True
    sfile.write_text("{broken")
    assert settings.get("replaygain") is True


# ---------- consumers ----------

def test_keeps_follow_the_settings(monkeypatch):
    monkeypatch.setattr(config, "SPONSORBLOCK", False)
    settings.update({"sponsorblock_keep": True})
    keys = [p["key"] for p in downloader._options({})["postprocessors"]]
    assert "ModifyChapters" in keys
    ydl = yt_dlp.YoutubeDL({"quiet": True})
    assert "after_filter" in [w for _, w in downloader._custom_pps(ydl)]


def test_replaygain_setting_skips_loudness(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "REPLAYGAIN", True)
    settings.update({"replaygain": False})
    monkeypatch.setattr(downloader.loudness, "measure", lambda p: pytest.fail("measured with replaygain off"))
    monkeypatch.setattr(downloader.lyrics, "for_track", lambda *a, **k: None)
    monkeypatch.setattr(downloader.history, "update", lambda *a, **k: True)
    downloader._post_steps({"file": str(tmp_path / "x.opus"), "video_id": "dQw4w9WgXcQ"},
                           {"title": "t", "artist": "a", "duration": 1})


def test_ytmusic_and_default_source_follow_the_settings(monkeypatch):
    monkeypatch.setattr(config, "YTMUSIC", True)
    monkeypatch.setattr(ytmusic, "YTMusic", object)
    assert ytmusic.enabled()
    settings.update({"ytmusic": False})
    assert not ytmusic.enabled()
    monkeypatch.setattr(config, "DEFAULT_SOURCE", "yt")
    monkeypatch.setitem(sources.SOURCES, "mc", sources.monochrome.SOURCE)
    settings.update({"default_source": "mc"})
    assert sources.default_key() == "mc"
    settings.update({"default_source": "nd"})  # not enabled here (no Navidrome credentials): YouTube
    assert "nd" not in sources.SOURCES and sources.default_key() == "yt"


def test_diagnostics_reports_effective_values(client, monkeypatch):
    monkeypatch.setattr(app.log, "recent", lambda: [])
    settings.update({"lrc_sidecar": True, "prefer_library": False})
    cfg = client.get("/api/diagnostics").get_json()["config"]
    assert cfg["lrc_sidecar"] is True and cfg["prefer_library"] is False
    assert cfg["settings_overridden"] == ["lrc_sidecar", "prefer_library"]
