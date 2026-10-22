"""Running Reef as a service: who may connect (loopback / tailnet / password), log redaction, the rotating log
file + in-memory tail, and /api/diagnostics."""
import json
import logging
import sys
import threading

import pytest

import requests

import app
import config
import log
import navidrome
import streamer
from sources import failover

SIGNED = ("https://rr1---sn-x.googlevideo.com/videoplayback?expire=1767225600&ei=abc&itag=140"
          "&sig=AOq0QJ8wRAIgSECRETSIG&lsig=LSIGSECRET&pot=POTOKEN123&n=NCHALLENGE&c=WEB")


@pytest.fixture
def client():
    return app.app.test_client()


def from_ip(client, addr, path="/api/sources", **kw):
    return client.get(path, environ_base={"REMOTE_ADDR": addr}, **kw)


# ---------- access ----------

@pytest.mark.parametrize("addr", ["127.0.0.1", "127.8.9.10", "::1", "100.64.0.1", "100.78.71.110", "100.127.255.254",
                                  "fd7a:115c:a1e0::1", "fd7a:115c:a1e0:ab12::42", "::ffff:100.78.71.110"])
def test_loopback_and_tailnet_need_no_password(client, monkeypatch, addr):
    monkeypatch.setattr(config, "PASSWORD", "")
    assert from_ip(client, addr).status_code == 200
    monkeypatch.setattr(config, "PASSWORD", "hunter2")
    assert from_ip(client, addr).status_code == 200  # a password only guards other networks


@pytest.mark.parametrize("addr", ["192.168.1.20", "10.0.0.5", "100.63.255.255", "100.128.0.1", "8.8.8.8",
                                  "fd7a:115c:a1e1::1", "2001:db8::1", "::ffff:192.168.1.20", "", "garbage"])
def test_other_networks_get_403_without_a_password(client, monkeypatch, addr):
    monkeypatch.setattr(config, "PASSWORD", "")
    r = from_ip(client, addr)
    assert r.status_code == 403
    assert r.mimetype == "text/plain" and "only open on Tailscale" in r.get_data(as_text=True)
    assert "WWW-Authenticate" not in r.headers


def test_forwarded_for_is_not_trusted(client, monkeypatch):
    monkeypatch.setattr(config, "PASSWORD", "")
    r = from_ip(client, "192.168.1.20", headers={"X-Forwarded-For": "127.0.0.1", "X-Real-IP": "100.64.0.1"})
    assert r.status_code == 403


def test_other_networks_need_the_password_when_set(client, monkeypatch):
    monkeypatch.setattr(config, "PASSWORD", "hunter2")
    r = from_ip(client, "192.168.1.20")
    assert r.status_code == 401 and r.headers["WWW-Authenticate"] == 'Basic realm="reef"'
    assert from_ip(client, "192.168.1.20", auth=("x", "wrong")).status_code == 401
    assert from_ip(client, "192.168.1.20", auth=("anyone", "hunter2")).status_code == 200


def test_non_ascii_password_compares_without_crashing(client, monkeypatch):
    monkeypatch.setattr(config, "PASSWORD", "pässwörd")
    assert from_ip(client, "192.168.1.20", auth=("x", "pässwörd")).status_code == 200
    assert from_ip(client, "192.168.1.20", auth=("x", "passwörd")).status_code == 401


def test_open_restores_the_old_behaviour(client, monkeypatch):
    monkeypatch.setattr(config, "OPEN", True)
    monkeypatch.setattr(config, "PASSWORD", "")
    assert from_ip(client, "192.168.1.20").status_code == 200
    monkeypatch.setattr(config, "PASSWORD", "hunter2")  # as before: a set password applies to every network
    assert from_ip(client, "100.78.71.110").status_code == 401
    assert from_ip(client, "127.0.0.1", auth=("x", "hunter2")).status_code == 200


def test_trusted_helper():
    assert app.trusted("100.64.0.0") and app.trusted("fe80::1%en0") is False and app.trusted(None) is False


# ---------- redaction ----------

def test_redact_masks_signed_params_and_keeps_the_rest():
    out = log.redact(f"stream error={SIGNED!r}")
    for secret in ("AOq0QJ8wRAIgSECRETSIG", "LSIGSECRET", "POTOKEN123", "NCHALLENGE"):
        assert secret not in out
    assert "expire=1767225600" in out and "itag=140" in out and "&c=WEB" in out
    assert "&sig=len=21,sha1=" in out and "&n=len=10,sha1=" in out
    assert log.redact(SIGNED) == log.redact(SIGNED)  # same URL, same mask: two log lines can be compared


def test_redact_subsonic_path_params_and_headers():
    nd = "http://127.0.0.1:4533/rest/ping.view?u=marianne&t=0123456789abcdef&s=a1b2c3&v=1.16.1"
    out = log.redact(nd)
    assert "0123456789abcdef" not in out and "a1b2c3" not in out and "u=marianne" in out
    assert "SIGPATH" not in log.redact("https://r.googlevideo.com/videoplayback/id/x/sig/SIGPATH/key/yt8")
    hdrs = log.redact("{'Cookie': 'SID=abc; HSID=def', 'Authorization': 'Basic dXNlcjpwYXNz', 'Range': 'bytes=0-'}")
    assert "SID=abc" not in hdrs and "dXNlcjpwYXNz" not in hdrs and "'Range': 'bytes=0-'" in hdrs
    assert log.redact("tiles refreshed n=12 s=3") == "tiles refreshed n=12 s=3"  # k=v log fields aren't URL params


def test_redact_obj_walks_structures():
    out = log.redact_obj({"a": [SIGNED, 1, None], "b": {"c": SIGNED}})
    assert "POTOKEN123" not in json.dumps(out) and out["a"][1] == 1


CRED_URL = "https://demo:synthetic-pass@example.invalid/api?api_key=synthetic-token"
CRED_SECRETS = ("synthetic-pass", "synthetic-token", "demo:")


def test_redact_masks_url_userinfo_and_common_secret_params():
    out = log.redact(f"failover cool host={CRED_URL} s=180")
    assert not any(s in out for s in CRED_SECRETS), out
    assert out.startswith("failover cool host=https://len=19,sha1=") and "@example.invalid/api?api_key=len=15," in out
    assert out.endswith(" s=180")
    for name in ("api_key", "key", "token", "access_token", "password", "auth", "API_KEY", "Token"):
        assert "hunter2" not in log.redact(f"http://h/x?a=1&{name}=hunter2&region=eu"), name
    kept = log.redact("http://h/x?keyword=a&monkey=b&author=c&tokens=d&region=eu")
    assert kept == "http://h/x?keyword=a&monkey=b&author=c&tokens=d&region=eu"  # only whole parameter names
    assert log.redact("ftp://user@host/x") == f"ftp://{log.mask('user')}@host/x"
    assert log.redact("http://u:p@ss@host/x").endswith("@host/x") and "p@ss" not in log.redact("http://u:p@ss@host/x")
    assert log.redact("mail me at a@b.example or see http://host/x?u=1") == "mail me at a@b.example or see http://host/x?u=1"


def test_redact_is_idempotent():
    """A message redacted twice (a redacted error, then the log formatter) keeps the first mask, so lines still compare."""
    for text in (SIGNED, CRED_URL, "Authorization: Bearer abc.def", "http://h/rest/x?u=a&t=0123456789&s=abcdef"):
        once = log.redact(text)
        assert log.redact(once) == once


def test_redact_obj_masks_keys_too():
    out = log.redact_obj({CRED_URL: 120, "plain": {CRED_URL: [CRED_URL]}, 7: "x"})
    raw = json.dumps(out)
    assert not any(s in raw for s in CRED_SECRETS), raw
    assert list(out["plain"].values())[0][0] == log.redact(CRED_URL) and out[7] == "x"


class FailingSession:
    """failover's session: every request fails like a real one (requests' text carries the query, not userinfo)."""
    headers = {}

    def request(self, method, url, timeout=None, **kw):
        raise requests.ConnectionError("HTTPSConnectionPool(host='example.invalid', port=443): Max retries exceeded "
                                       "with url: /api?api_key=synthetic-token/x (Caused by NameResolutionError)")


@pytest.fixture
def failing_failover(monkeypatch):
    monkeypatch.setattr(failover, "_session", FailingSession())
    monkeypatch.setattr(failover, "_cooling", {})


def test_credentialed_instance_never_shows_after_a_failover_failure(client, failing_failover, logging_setup):
    with pytest.raises(requests.ConnectionError) as caught:
        failover.request([CRED_URL], "/x")
    # The raw identity is untouched inside failover: cooling is keyed by the configured URL and still skips it.
    assert list(failover.cooling()) == [CRED_URL] and failover._until(CRED_URL) is not None
    assert "synthetic-token" in str(caught.value)  # the raw error stays raw for callers (they classify it)
    for h in logging.getLogger().handlers:
        h.flush()
    text = logging_setup.read_text()
    assert "failover cool host=" in text and not any(s in text for s in CRED_SECRETS), text
    assert not any(s in "\n".join(log.recent()) for s in CRED_SECRETS)
    for path in ("/api/diagnostics", "/api/health"):
        raw = client.get(path).get_data(as_text=True)
        assert not any(s in raw for s in CRED_SECRETS), (path, raw[:400])
        cooling = json.loads(raw)["failover_cooling"]
        assert list(cooling) == [log.redact(CRED_URL)] and list(cooling.values())[0] > 0


def test_navidrome_ping_error_has_no_subsonic_token(monkeypatch):
    class Refused:
        def get(self, url, params=None, timeout=None):
            q = "&".join(f"{k}={v}" for k, v in params.items())
            raise requests.ConnectionError(f"HTTPConnectionPool(host='127.0.0.1', port=9): Max retries exceeded with "
                                           f"url: /rest/ping?{q} (Caused by NewConnectionError('refused'))")

    monkeypatch.setattr(navidrome, "NAVIDROME_USER", "demo")
    monkeypatch.setattr(navidrome, "NAVIDROME_PASS", "synthetic-pass")
    monkeypatch.setattr(navidrome, "_session", Refused())
    seen = []
    real = navidrome._auth_params
    monkeypatch.setattr(navidrome, "_auth_params", lambda: seen.append(real()) or seen[-1])
    out = navidrome.ping()
    assert out["ok"] is False and "Max retries exceeded" in out["error"] and "u=demo" in out["error"]
    assert seen[0]["t"] not in out["error"] and seen[0]["s"] not in out["error"]


def test_health_is_redacted(client, monkeypatch):
    monkeypatch.setattr(navidrome, "ping", lambda: {"ok": False, "error": f"refused {CRED_URL}"})
    monkeypatch.setattr(streamer, "last_timing", {"error": CRED_URL})
    raw = client.get("/api/health").get_data(as_text=True)
    assert not any(s in raw for s in CRED_SECRETS), raw


# ---------- log file + tail ----------

@pytest.fixture
def logging_setup(tmp_path):
    root = logging.getLogger()
    saved = (list(root.handlers), root.level, sys.excepthook, threading.excepthook)
    log._ready = False
    log._recent.clear()
    log.setup(log_dir=tmp_path / "logs", stderr=False)
    added = [h for h in root.handlers if h not in saved[0]]  # not pytest's own capture handlers
    yield tmp_path / "logs" / "reef.log"
    for h in added:
        root.removeHandler(h)
        h.close()
    root.setLevel(saved[1])
    sys.excepthook, threading.excepthook = saved[2], saved[3]
    log._ready = False
    log._recent.clear()


def test_log_file_and_tail_are_redacted(logging_setup):
    logging.getLogger("streamer").warning(f"stream id=FGBhQbmPwH8 error={SIGNED!r}")
    logging.getLogger("tiles").info("tiles refreshed n=4 ms=12")
    for h in logging.getLogger().handlers:
        h.flush()
    text = logging_setup.read_text()
    assert "stream id=FGBhQbmPwH8" in text and "tiles refreshed n=4 ms=12" in text
    assert "AOq0QJ8wRAIgSECRETSIG" not in text and "sig=len=" in text
    tail = log.recent()
    assert tail[-1].endswith("I tiles refreshed n=4 ms=12") and " W stream id=" in tail[-2]
    assert "POTOKEN123" not in "\n".join(tail)


def test_setup_is_idempotent(logging_setup):
    n = len(logging.getLogger().handlers)
    log.setup(log_dir=logging_setup.parent, stderr=False)
    assert len(logging.getLogger().handlers) == n


def test_tail_is_bounded(logging_setup):
    lg = logging.getLogger("reef")
    for i in range(log.RECENT_MAX + 50):
        lg.info(f"line {i}")
    tail = log.recent()
    assert len(tail) == log.RECENT_MAX and tail[-1].endswith(f"line {log.RECENT_MAX + 49}")


def test_thread_crash_is_logged(logging_setup):
    def boom():
        raise RuntimeError("worker died")
    t = threading.Thread(target=boom, name="reef-test-crash")
    t.start()
    t.join()
    assert any("uncaught exception thread=reef-test-crash" in line for line in log.recent())


# ---------- diagnostics ----------

def test_diagnostics(client, monkeypatch, logging_setup):
    monkeypatch.setattr(config, "PASSWORD", "hunter2")
    monkeypatch.setattr(config, "NAVIDROME_PASS", "ndsecret")
    monkeypatch.setattr(streamer, "last_timing", {"id": "FGBhQbmPwH8", "error": repr(SIGNED[:150])})
    logging.getLogger("streamer").info(f"stream id=FGBhQbmPwH8 url={SIGNED}")
    r = client.get("/api/diagnostics")
    assert r.status_code == 200
    body = r.get_json()
    assert body["version"]["ytdlp"] and body["version"]["python"]
    assert body["uptime_s"] >= 0
    assert body["config"]["password_set"] is True and body["config"]["server"] in ("waitress", "flask-dev")
    assert {"entries", "hits", "waits", "misses"} <= set(body["stream_cache"])
    assert "global" in body["yt_gates"] and isinstance(body["failover_cooling"], dict)
    assert body["last_stream"]["id"] == "FGBhQbmPwH8"
    assert any("stream id=FGBhQbmPwH8" in line for line in body["log"])
    raw = r.get_data(as_text=True)
    for secret in ("hunter2", "ndsecret", "AOq0QJ8wRAIgSECRETSIG", "POTOKEN123"):
        assert secret not in raw


def test_diagnostics_hides_credentials_in_configured_urls(client, monkeypatch):
    url = "https://user:secret@example.test/api?key=secret&region=eu"
    monkeypatch.setattr(config, "TRACKS_API", [url])
    monkeypatch.setattr(config, "PIPED_INSTANCES", [url])
    monkeypatch.setattr(config, "INVIDIOUS_INSTANCES", ["http://[fd7a:115c:a1e0::1]:3000/?token=secret"])
    monkeypatch.setattr(config, "NAVIDROME_URL", "http://admin:secret@127.0.0.1:4533")
    raw = client.get("/api/diagnostics").get_data(as_text=True)
    assert "secret" not in raw and "user" not in raw and "admin" not in raw
    cfg = json.loads(raw)["config"]
    assert cfg["tracks_api"][0].startswith("https://example.test/api?key=len=6,sha1=")
    assert "&region=len=2,sha1=" in cfg["tracks_api"][0]
    assert cfg["invidious"][0].startswith("http://[fd7a:115c:a1e0::1]:3000/?token=len=6")
    assert cfg["navidrome"]["url"] == "http://127.0.0.1:4533"


def test_diagnostics_is_behind_the_same_gate(client, monkeypatch):
    monkeypatch.setattr(config, "PASSWORD", "")
    assert from_ip(client, "192.168.1.20", "/api/diagnostics").status_code == 403


def test_error_responses_mask_credentials_before_cutting(client, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(app.ytmusic, "enabled", lambda: True)
    # 150 chars of text first: the old [:160] cut kept "https://demo:synt" without the "@" that marks userinfo.
    boom = RuntimeError("z" * 150 + f" {CRED_URL}")

    def fail(*a, **k):
        raise boom

    monkeypatch.setattr(app.ytmusic, "search_kind", fail)
    r = client.get("/api/search?q=x&filter=videos")
    assert r.status_code == 502 and not any(s in r.get_data(as_text=True) for s in ("synth", "demo"))
    monkeypatch.setattr(app, "_find_song", fail)
    monkeypatch.setattr(app.sources, "get", lambda key: None)  # no Monochrome: YouTube Music is the only source
    dead = {"id": "dQw4w9WgXcQ", "title": "Song", "channel": "Artist", "source": "yt"}
    r = client.post("/api/replace", json={"track": dead})
    assert r.status_code == 502 and r.get_json()["error"].startswith("YouTube Music: zzz")
    assert not any(s in r.get_data(as_text=True) for s in ("synth", "demo"))
    r = client.patch("/api/settings", json={"tracks_api": ["ftp://demo:synthetic-pass@example.invalid/x"]})
    assert r.status_code == 400 and "not an http(s) URL" in r.get_json()["error"]
    assert "synthetic-pass" not in r.get_data(as_text=True)


def test_failover_log_masks_userinfo_longer_than_the_cut(failing_failover, logging_setup, monkeypatch):
    long_url = "https://demo:" + "synthetic-pass" * 6 + "@example.invalid/api"

    class Session(FailingSession):
        def request(self, method, url, timeout=None, **kw):
            raise requests.ConnectionError("x" * 30 + f" proxy refused {long_url}")

    monkeypatch.setattr(failover, "_session", Session())
    with pytest.raises(requests.ConnectionError):
        failover.request(["https://a.example"], "/x")
    for h in logging.getLogger().handlers:
        h.flush()
    text = logging_setup.read_text()
    assert "failover cool host=https://a.example" in text and "synthetic" not in text and "demo:" not in text
    assert list(failover.cooling()) == ["https://a.example"]


def test_playlist_export_failure_is_redacted(client, monkeypatch):
    def fail(pid):
        raise OSError(f"can't write to {CRED_URL}")

    monkeypatch.setattr(app, "_pid_or_404", lambda pid: pid)
    monkeypatch.setattr(app.playlists, "export", fail)
    r = client.post("/api/playlists/0123abcd/export")
    assert r.status_code == 500 and r.get_json()["error"].startswith("couldn't write the playlist file: can't write")
    assert not any(s in r.get_data(as_text=True) for s in CRED_SECRETS)
