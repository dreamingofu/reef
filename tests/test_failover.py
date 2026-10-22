"""sources/failover.py host cooldowns. Fake session; no network."""
import pytest
import requests

from sources import failover

A, B, C = "https://a.example", "https://b.example", "https://c.example"


class Resp:
    def __init__(self, status=200, text='{"ok": true}', ctype="application/json"):
        self.status_code, self.text, self.headers = status, text, {"Content-Type": ctype}
        self.ok = status < 400
        self.closed = False

    def json(self):
        import json
        return json.loads(self.text)

    def raise_for_status(self):
        if not self.ok:
            raise requests.HTTPError(str(self.status_code), response=self)

    def close(self):
        self.closed = True


@pytest.fixture
def net(monkeypatch):
    """hosts[base] = Resp | Exception | callable; records the hosts asked, in order."""
    state = {"hosts": {}, "asked": [], "now": 1000.0}

    def fake_request(method, url, timeout=None, **kw):
        base = next(b for b in state["hosts"] if url.startswith(b))
        state["asked"].append(base)
        out = state["hosts"][base]
        if isinstance(out, Exception):
            raise out
        return out

    monkeypatch.setattr(failover._session, "request", fake_request)
    monkeypatch.setattr(failover, "_clock", lambda: state["now"])
    monkeypatch.setattr(failover.random, "randrange", lambda n: 0)  # always start at the first instance
    failover._cooling.clear()
    yield state
    failover._cooling.clear()


@pytest.mark.parametrize("failure,seconds", [
    (requests.ConnectionError("Failed to resolve 'a.example' (nodename nor servname provided)"), 180),
    (requests.ConnectionError("Connection refused"), 180),
    (requests.ConnectTimeout("timed out"), 180),
    (requests.ReadTimeout("timed out"), 180),
    (Resp(502, "bad gateway", "text/plain"), 180),
    (Resp(403, "forbidden", "text/plain"), 300),
    (Resp(200, "<html>captcha: are you human?</html>", "text/html"), 300),
    (Resp(200, "<!doctype html><title>Instance down</title>", "text/html"), 180),
])
def test_failures_cool_the_host_and_rotate(net, failure, seconds):
    net["hosts"] = {A: failure, B: Resp()}
    assert failover.get_json([A, B], "/x") == {"ok": True}
    assert net["asked"] == [A, B]
    assert failover.cooling() == {A: seconds}


def test_cooling_host_is_skipped_until_it_expires(net):
    net["hosts"] = {A: Resp(503, "", "text/plain"), B: Resp()}
    failover.get_json([A, B], "/x")
    net["hosts"][A] = Resp()
    net["asked"].clear()
    failover.get_json([A, B], "/x")
    assert net["asked"] == [B]
    net["now"] += 181
    net["asked"].clear()
    failover.get_json([A, B], "/x")
    assert net["asked"] == [A] and failover.cooling() == {}


def test_all_cooling_tries_the_one_that_expires_first(net):
    failover.cool(A, 300)
    failover.cool(B, 60)
    failover.cool(C, 180)
    net["hosts"] = {A: Resp(), B: Resp(), C: Resp()}
    r = failover.request([A, B, C], "/x")
    assert r.base == B and net["asked"] == [B]


def test_all_cooling_and_failing_raises_after_one_try(net):
    failover.cool(A, 300)
    failover.cool(B, 60)
    net["hosts"] = {A: Resp(), B: requests.ConnectionError("refused")}
    with pytest.raises(requests.ConnectionError):
        failover.request([A, B], "/x")
    assert net["asked"] == [B]


def test_html_only_counts_when_json_was_expected(net):
    net["hosts"] = {A: Resp(200, "<html>ok</html>", "text/html")}
    assert failover.request([A], "/x").base == A  # e.g. a probe that never parses the body
    assert failover.cooling() == {}


def test_429_rotates_without_cooling(net):
    net["hosts"] = {A: Resp(429, "slow down", "text/plain"), B: Resp()}
    assert failover.get_json([A, B], "/x") == {"ok": True}
    assert failover.cooling() == {}


def test_every_host_failing_raises_last_error(net):
    net["hosts"] = {A: Resp(500, "", "text/plain"), B: Resp(500, "", "text/plain")}
    with pytest.raises(requests.HTTPError):
        failover.get_json([A, B], "/x")
    assert set(failover.cooling()) == {A, B}
