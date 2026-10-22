"""Update check (GitHub + PyPI, cache, ETag, behind / outdated) and the in-app update's guards (confirm, one at a
time, Termux only, Tailscale/loopback only), with fake HTTP and a fake spawn: nothing runs or reaches the network."""
import subprocess
import sys
import time

import pytest

import app
import config
import settings
import updater

MINE = "a" * 40
LATEST = "b" * 40
T0 = 1_760_000_000


class Resp:
    def __init__(self, status=200, body=None, etag=None):
        self.status_code, self._body = status, body
        self.headers = {"ETag": etag} if etag else {}

    def json(self):
        return self._body


class FakeHub:
    """GitHub commits/compare + PyPI. Honours If-None-Match with a 304, like both services."""
    headers = {}

    def __init__(self):
        self.calls = []
        self.sha, self.ytdlp, self.compare = LATEST, "2099.1.2", {"status": "ahead", "ahead_by": 3}

    def get(self, url, headers=None, timeout=None):
        headers = headers or {}
        self.calls.append((url, headers.get("If-None-Match")))
        if "/commits/main" in url:
            etag = f'"gh-{self.sha}"'
            if headers.get("If-None-Match") == etag:
                return Resp(304)
            return Resp(200, {"sha": self.sha, "commit": {"message": "Fix things\n\nlong body",
                                                          "committer": {"date": "2026-10-22T10:00:00Z"}}}, etag)
        if "/compare/" in url:
            return Resp(200, self.compare)
        if "pypi.org" in url:
            etag = f'"py-{self.ytdlp}"'
            if headers.get("If-None-Match") == etag:
                return Resp(304)
            return Resp(200, {"info": {"version": self.ytdlp}}, etag)
        raise AssertionError(url)

    def urls(self, part):
        return [c for c in self.calls if part in c[0]]


@pytest.fixture(autouse=True)
def files(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "UPDATE_FILE", tmp_path / "update.json")
    monkeypatch.setattr(config, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(config, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(updater, "_local", {"sha": MINE, "short": MINE[:7], "date": T0})
    monkeypatch.setattr(updater, "ytdlp_installed", lambda: "2026.08.19")
    monkeypatch.setattr(updater, "_proc", None)


@pytest.fixture
def hub(monkeypatch):
    fake = FakeHub()
    monkeypatch.setattr(updater, "_session", fake)
    return fake


@pytest.fixture
def client():
    return app.app.test_client()


# ---------- check ----------

def test_version_body(client, hub):
    v = client.get("/api/version").get_json()
    assert v["reef"] == {"sha": MINE, "short": MINE[:7], "date": T0}
    assert v["latest_reef"] == {"sha": LATEST, "short": LATEST[:7], "date": "2026-10-22T10:00:00Z", "message": "Fix things"}
    assert (v["behind"], v["behind_count"]) == (True, 3)
    assert (v["ytdlp"], v["latest_ytdlp"], v["ytdlp_outdated"]) == ("2026.08.19", "2099.1.2", True)
    assert v["checked_at"] and v["errors"] == [] and v["skipped"] is False


def test_check_is_cached_for_6_hours_and_uses_etags(hub):
    updater.check(now=T0)
    n = len(hub.calls)
    updater.check(now=T0 + 3600)
    assert len(hub.calls) == n  # nothing due
    updater.check(now=T0 + 6 * 3600 + 1)
    gh = hub.urls("/commits/main")
    assert len(gh) == 2 and gh[1][1] == f'"gh-{LATEST}"'  # asked with If-None-Match, answered 304
    assert hub.urls("pypi")[1][1] == '"py-2099.1.2"'
    assert len(hub.urls("/compare/")) == 1  # same pair: compare isn't asked again
    v = updater.body()
    assert v["latest_reef"]["sha"] == LATEST and v["behind_count"] == 3
    updater.check(force=True, now=T0 + 6 * 3600 + 5)  # "Check now": asks again (still conditional)
    assert len(hub.urls("/commits/main")) == 3


def test_failures_are_retried_after_30_minutes(monkeypatch, hub):
    class Down:
        headers = {}

        def get(self, *a, **k):
            import requests
            raise requests.ConnectionError("no route")

    monkeypatch.setattr(updater, "_session", Down())
    v = updater.check(now=T0)
    assert v["latest_reef"] is None and v["behind"] is None and len(v["errors"]) == 2
    monkeypatch.setattr(updater, "_session", hub)
    updater.check(now=T0 + 600)
    assert hub.calls == []
    v = updater.check(now=T0 + 31 * 60)
    assert v["behind"] is True and v["errors"] == []


def test_up_to_date_and_ahead(monkeypatch, hub):
    hub.sha = MINE
    v = updater.check(now=T0)
    assert (v["behind"], v["behind_count"]) == (False, 0) and not hub.urls("/compare/")
    hub.sha, hub.compare = LATEST, {"status": "behind", "behind_by": 2}  # this checkout has unpushed commits
    v = updater.check(force=True, now=T0 + 1)
    assert v["behind"] is False


def test_unknown_local_commit_is_unknown(hub):
    hub.compare = None  # GitHub 404s a commit it doesn't have: our fake returns no dict
    v = updater.check(now=T0)
    assert v["behind"] is None and v["behind_count"] is None


def test_ytdlp_versions_compare_numerically(monkeypatch, hub):
    hub.ytdlp = "2026.8.19"  # PyPI's normalised form of the installed 2026.08.19
    assert updater.check(now=T0)["ytdlp_outdated"] is False
    assert updater.version_key("2026.10.2") > updater.version_key("2026.09.30")
    assert updater.version_key("2026.10.2.1") > updater.version_key("2026.10.2")


def test_skip_this_version(client, hub):
    updater.check(now=T0)
    assert client.patch("/api/settings", json={"skipped_version": LATEST}).status_code == 200
    v = client.get("/api/version").get_json()
    assert v["skipped_version"] == LATEST and v["skipped"] is True and v["behind"] is True
    assert client.patch("/api/settings", json={"skipped_version": "not a sha"}).status_code == 400
    client.patch("/api/settings", json={"skipped_version": None})
    assert client.get("/api/version").get_json()["skipped"] is False


# ---------- update guards ----------

@pytest.fixture
def spawned(monkeypatch):
    calls = []

    def fake(cmd, path):
        calls.append(cmd)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("2026-10-22 10:00:00 reef update\n")
        return 4242

    monkeypatch.setattr(updater, "_spawn", fake)
    monkeypatch.setattr(updater, "_alive", lambda pid: pid == 4242)  # running until a test says otherwise
    return calls


@pytest.fixture
def termux(monkeypatch):
    monkeypatch.setenv("PREFIX", "/data/data/com.termux/files/usr")


def test_refused_off_termux(client, spawned, monkeypatch):
    monkeypatch.delenv("PREFIX", raising=False)
    monkeypatch.delenv("TERMUX_VERSION", raising=False)
    monkeypatch.setattr(config, "ALLOW_SELF_UPDATE", False)
    for what in ("reef", "ytdlp", "both"):
        r = client.post("/api/update", json={"what": what, "confirm": True})
        assert r.status_code == 403 and "Termux" in r.get_json()["error"]
    assert spawned == []
    assert client.get("/api/version").get_json()["self_update"]["allowed"] is False


def test_needs_confirm_and_a_known_target(client, spawned, termux):
    assert client.post("/api/update", json={"what": "reef"}).status_code == 400
    assert client.post("/api/update", json={"what": "reef", "confirm": "yes"}).status_code == 400
    assert client.post("/api/update", json={"what": "everything", "confirm": True}).status_code == 400
    assert client.post("/api/update", data="x").status_code == 400
    assert spawned == []


def test_only_from_loopback_or_tailscale(client, spawned, termux, monkeypatch):
    monkeypatch.setattr(config, "PASSWORD", "pw")
    import base64
    auth = {"Authorization": "Basic " + base64.b64encode(b"x:pw").decode()}
    r = client.post("/api/update", json={"what": "reef", "confirm": True}, headers=auth,
                    environ_base={"REMOTE_ADDR": "192.168.1.20"})
    assert r.status_code == 403 and spawned == []
    r = client.post("/api/update", json={"what": "reef", "confirm": True}, environ_base={"REMOTE_ADDR": "100.78.71.110"})
    assert r.status_code == 202


def test_one_update_at_a_time_and_status(client, spawned, termux, monkeypatch):
    r = client.post("/api/update", json={"what": "reef", "confirm": True})
    assert r.status_code == 202
    body = r.get_json()
    assert body["running"] is True and body["what"] == "reef"
    assert spawned == [["bash", str(config.BASE_DIR / "termux" / "update.sh")]]
    assert client.post("/api/update", json={"what": "ytdlp", "confirm": True}).status_code == 409
    log = updater.log_path()
    log.write_text("pulling\nGET https://x.googlevideo.com/videoplayback?a=1&sig=AOq0QJ8wRAIgSECRET\nUPDATE_OK abc1234\n")
    monkeypatch.setattr(updater, "_alive", lambda pid: False)
    s = client.get("/api/update/status").get_json()
    assert s["running"] is False and s["result"] == "ok" and s["finished_at"]
    assert "AOq0QJ8wRAIgSECRET" not in "".join(s["log_tail"]) and s["log_tail"][-1] == "UPDATE_OK abc1234"
    r = client.post("/api/update", json={"what": "ytdlp", "confirm": True})
    assert r.status_code == 202 and spawned[-1] == [sys.executable, str(config.BASE_DIR / "updater.py"), "ytdlp"]


def test_failed_or_vanished_run_is_reported_failed(client, spawned, termux, monkeypatch):
    client.post("/api/update", json={"what": "both", "confirm": True})
    monkeypatch.setattr(updater, "_alive", lambda pid: False)
    s = updater.status()
    assert s["running"] is False and s["result"] == "failed"
    # A pid alive after RUN_MAX_S is a reused pid (reboot), not our update.
    client.post("/api/update", json={"what": "reef", "confirm": True})
    monkeypatch.setattr(updater, "_alive", lambda pid: True)
    assert updater.status(now=time.time() + updater.RUN_MAX_S + 1)["running"] is False


def test_allow_self_update_env(monkeypatch, spawned, client):
    monkeypatch.delenv("PREFIX", raising=False)
    monkeypatch.delenv("TERMUX_VERSION", raising=False)
    monkeypatch.setattr(config, "ALLOW_SELF_UPDATE", True)
    assert client.post("/api/update", json={"what": "reef", "confirm": True}).status_code == 202


def test_spawn_detaches_and_logs(monkeypatch, tmp_path):
    seen = {}

    class P:
        pid = 77

        def poll(self):
            return None

    def popen(cmd, **kw):
        seen.update(kw, cmd=cmd)
        return P()

    monkeypatch.setattr(subprocess, "Popen", popen)
    path = tmp_path / "logs" / "update.log"
    assert updater._spawn(["true"], path) == 77
    assert seen["start_new_session"] is True and seen["stdin"] is subprocess.DEVNULL
    assert seen["env"]["REEF_UPDATE_PIP"] == f"{sys.executable} -m pip" and seen["env"]["REEF_DIR"] == str(config.BASE_DIR)
    assert path.exists() and updater._alive(77) is True


def test_settings_still_validate_the_new_keys():
    with pytest.raises(ValueError):
        settings.update({"tracks_api": "ftp://x"})
    settings.update({"tracks_api": " https://a.example/ , https://b.example,https://a.example "})
    assert settings.get("tracks_api") == ["https://a.example", "https://b.example"]
    settings.update({"tracks_api": ""})
    assert settings.get("tracks_api") == list(config.TRACKS_API)
