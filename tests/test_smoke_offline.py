"""Offline smoke tests: imports, track ids, history, /api/health. No network."""
import importlib

import pytest


@pytest.mark.parametrize("name", ["app", "downloader", "streamer", "sources", "history", "tiles"])
def test_imports(name):
    importlib.import_module(name)


def test_config_uses_test_dirs():
    import os
    from pathlib import Path

    import config

    assert config.HISTORY_FILE == Path(os.environ["REEF_HISTORY_FILE"])
    assert config.DOWNLOAD_DIR.parent == Path(os.environ["REEF_MUSIC_DIR"])
    assert config.TMP_DIR == Path(os.environ["REEF_TMP_DIR"])
    assert "reef-test-" in str(config.HISTORY_FILE)


def test_track_id_round_trip():
    import sources

    assert sources.parse("dQw4w9WgXcQ") == ("yt", "dQw4w9WgXcQ")
    assert sources.track_id("yt", "dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert sources.parse("mc:123") == ("mc", "123")
    assert sources.track_id("mc", "123") == "mc:123"


def test_history_add_get_update_ids():
    import history

    history.HISTORY_FILE.unlink(missing_ok=True)
    assert history.get("dQw4w9WgXcQ") is None
    history.add({"id": "dQw4w9WgXcQ", "title": "One"})
    history.add({"id": "mc:123", "title": "Two"})
    assert history.ids() == {"dQw4w9WgXcQ", "mc:123"}
    assert history.list_recent()[0]["id"] == "mc:123"  # newest first

    entry = history.get("dQw4w9WgXcQ")
    assert entry["title"] == "One" and "kept_at" in entry
    assert history.update("dQw4w9WgXcQ", nd_cover="al-1") is True
    assert history.get("dQw4w9WgXcQ")["nd_cover"] == "al-1"
    assert history.update("missing", nd_cover="x") is False

    history.add({"id": "dQw4w9WgXcQ", "title": "One again"})  # re-keep replaces, not duplicates
    assert len(history.list_recent()) == 2
    assert history.list_recent()[0]["title"] == "One again"


def test_health_offline(monkeypatch):
    import app
    import navidrome

    def no_network(*a, **kw):
        raise AssertionError("navidrome made a network call while unconfigured")

    monkeypatch.setattr(navidrome._session, "get", no_network)
    assert navidrome.configured() is False

    r = app.app.test_client().get("/api/health")
    assert r.status_code == 200
    body = r.get_json()
    assert body["reef"] == "ok"
    assert body["navidrome"]["ok"] is False
    assert "yt" in body["sources"]
