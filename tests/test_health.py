"""Source health panel: aggregation of fake checks (statuses, latency, timeouts, crashes), the plan built from
settings (instance lists, disabled services), the 60 s cache and the route. No network."""
import threading
import time

import pytest
import requests

import app
import config
import health
import settings
import streamer


@pytest.fixture(autouse=True)
def files(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(health, "_cache", {"at": 0.0, "rows": None})


def test_run_aggregates_statuses_in_order():
    release = threading.Event()

    def slow():
        release.wait(5)
        return health.ONLINE, "late"

    def boom():
        raise RuntimeError("bug in a check")

    checks = [("A", "x", lambda: (health.ONLINE, "fine")),
              ("B", "x", lambda: (health.REACHABLE, "HTTP 503")),
              ("C", "x", slow),
              ("D", "x", None),
              ("E", "x", boom),
              ("F", "x", lambda: (health.DISABLED, "off"))]
    t0 = time.monotonic()
    rows = health.run(checks, budget=0.3)
    release.set()
    assert time.monotonic() - t0 < 2  # the slow check doesn't hold the panel
    assert [r["name"] for r in rows] == list("ABCDEF")
    assert [r["status"] for r in rows] == ["ONLINE", "REACHABLE", "OFFLINE", "DISABLED", "REACHABLE", "DISABLED"]
    assert rows[0]["latency_ms"] is not None and rows[2]["detail"] == "no answer within 0.3 s"
    assert rows[3]["latency_ms"] is None and rows[5]["latency_ms"] is None
    assert "bug in a check" in rows[4]["detail"]
    assert set(rows[0]) == {"name", "kind", "status", "latency_ms", "detail"}


class Sess:
    headers = {}

    def __init__(self, answers):
        self.answers, self.urls = answers, []

    def request(self, method, url, **kw):
        self.urls.append((method, url))
        a = self.answers(url)
        if isinstance(a, Exception):
            raise a
        return a

    def get(self, url, **kw):
        return self.request("GET", url, **kw)


class R:
    def __init__(self, status=200, body=None):
        self.status_code, self._body = status, body
        self.ok = status < 400

    def json(self):
        if self._body is None:
            raise ValueError("html")
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_http_statuses(monkeypatch):
    answers = {"https://a/x": R(200, {"ok": 1}), "https://b/x": R(200, None), "https://c/x": R(502),
               "https://d/x": requests.ConnectionError("refused"), "https://e/x": requests.Timeout("slow")}
    monkeypatch.setattr(health, "_session", Sess(lambda u: answers[u]))
    got = {u: health._http(u)[0] for u in answers}
    assert got == {"https://a/x": "ONLINE", "https://b/x": "REACHABLE", "https://c/x": "REACHABLE",
                   "https://d/x": "OFFLINE", "https://e/x": "OFFLINE"}
    assert health._http("https://b/x", want_json=False)[0] == "ONLINE"


def test_plan_follows_settings(monkeypatch):
    monkeypatch.setattr(config, "TRACKS_API", ["https://tracks.example"])
    names = [(n, k, fn is None) for n, k, fn in health.plan()]
    assert ("Monochrome tracks.example", "monochrome", False) in names
    assert ("Piped", "piped", True) in names and ("Invidious", "invidious", True) in names
    settings.update({"piped_instances": "https://p1.example, https://p2.example", "tracks_api": "https://m.example"})
    names = [n for n, _, _ in health.plan()]
    assert "Piped p1.example" in names and "Piped p2.example" in names and "Monochrome m.example" in names
    assert "Monochrome tracks.example" not in names


def test_disabled_services_report_disabled(monkeypatch):
    monkeypatch.setattr(health.ytmusic, "enabled", lambda: False)
    assert health.check_ytmusic()[0] == "DISABLED"
    assert health.check_navidrome()[0] == "DISABLED"  # no credentials in tests
    assert health.check_listenbrainz()[0] == "DISABLED"  # no token


def test_youtube_uses_a_recent_play_or_resolves(monkeypatch):
    monkeypatch.setattr(streamer, "last_timing", {"src": "yt", "status": 206, "at": int(time.time()) - 60, "resolve_ms": 900})
    monkeypatch.setattr(streamer, "resolve", lambda tid: pytest.fail("resolved despite a recent play"))
    status, detail = health.check_youtube()
    assert status == "ONLINE" and "played 1 min ago" in detail
    monkeypatch.setattr(streamer, "last_timing", {})
    monkeypatch.setattr(streamer, "resolve", lambda tid: {"client": "tv"})
    assert health.check_youtube() == ("ONLINE", "resolved via tv")

    def walled(tid):
        raise RuntimeError("Sign in to confirm you're not a bot")

    monkeypatch.setattr(streamer, "resolve", walled)
    assert health.check_youtube()[0] == "REACHABLE"

    def offline(tid):
        raise requests.ConnectionError("Failed to resolve host")

    monkeypatch.setattr(streamer, "resolve", offline)
    assert health.check_youtube()[0] == "OFFLINE"


def test_listenbrainz_check_with_a_token(monkeypatch):
    settings.update({"listenbrainz_token": "tok"})
    monkeypatch.setattr(health, "_session", Sess(lambda u: R(200, {"valid": True, "user_name": "marianne"})))
    assert health.check_listenbrainz() == ("ONLINE", "signed in as marianne")
    monkeypatch.setattr(health, "_session", Sess(lambda u: R(200, {"valid": False})))
    assert health.check_listenbrainz()[0] == "REACHABLE"


def test_route_caches_for_a_minute(monkeypatch):
    calls = []
    clock = [1000.0]
    monkeypatch.setattr(health, "_clock", lambda: clock[0])
    monkeypatch.setattr(health, "plan", lambda: calls.append(1) or [("A", "x", lambda: ("ONLINE", "ok"))])
    client = app.app.test_client()
    first = client.get("/api/health/sources").get_json()
    assert first[0]["status"] == "ONLINE" and first[0]["checked_at"]
    clock[0] += 30
    assert client.get("/api/health/sources").get_json() == first and len(calls) == 1
    assert client.get("/api/health/sources?refresh=1").status_code == 200 and len(calls) == 2
    clock[0] += 61
    client.get("/api/health/sources")
    assert len(calls) == 3


def test_default_plan_runs_offline(monkeypatch):
    """The real plan with the conftest's no-network session: every row has a status, nothing hangs."""
    monkeypatch.setattr(streamer, "resolve", lambda tid: (_ for _ in ()).throw(requests.ConnectionError("offline")))
    monkeypatch.setattr(health.ytmusic, "_call", lambda *a, **k: (_ for _ in ()).throw(requests.ConnectionError("x")))
    rows = health.run(budget=2)
    assert rows and all(r["status"] in ("ONLINE", "REACHABLE", "OFFLINE", "DISABLED") for r in rows)
    by = {r["name"]: r["status"] for r in rows}
    assert by["YouTube"] == "OFFLINE" and by["GitHub"] == "OFFLINE" and by["Navidrome"] == "DISABLED"


LONG_USERINFO = "https://demo:" + "synthetic-pass" * 10 + "@example.invalid/api?api_key=synthetic-token"


def test_crashed_check_text_is_masked_before_any_cut(monkeypatch):
    """A cut URL loses the "@" that marks its userinfo, so masking must come before the run's and the row's cuts."""
    def boom():
        raise RuntimeError(f"bug talking to {LONG_USERINFO}")

    rows = health.run([("A", "x", boom)], budget=1)
    assert rows[0]["detail"].startswith("check failed: bug talking to https://len=")
    assert "synthetic" not in rows[0]["detail"] and "demo:" not in rows[0]["detail"]
    monkeypatch.setattr(health, "plan", lambda: [("A", "x", boom)])
    raw = app.app.test_client().get("/api/health/sources?refresh=1").get_data(as_text=True)
    assert "synthetic" not in raw and "demo:" not in raw


def test_row_detail_is_masked_before_its_cut():
    detail = health._row("A", "x", health.REACHABLE, "x" * 150 + " " + LONG_USERINFO)["detail"]
    assert len(detail) <= 200 and "synthetic" not in detail and "demo:" not in detail
    assert health._row("A", "x", health.ONLINE, "fine")["detail"] == "fine"
