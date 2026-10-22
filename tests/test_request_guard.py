"""Request hygiene on the API: the cross-site guard for unsafe methods (Origin / Sec-Fetch-Site, direct access and
Tailscale Serve), JSON bodies that aren't objects, and durations too large for a float. All offline, scratch stores."""
import io
import json

import pytest

import app
import config
import downloader
import history
import likes
import lyrics
import playlists

YT = "dQw4w9WgXcQ"
TAILNET = "100.78.71.110:5274"
SERVE = "reef.tail1234.ts.net"


@pytest.fixture(autouse=True)
def files(monkeypatch, tmp_path):
    monkeypatch.setattr(history, "HISTORY_FILE", tmp_path / "history.json")
    for name in ("LISTENS", "LIKES", "PLAYLISTS", "SETTINGS", "DISLIKES", "BLOCKLIST", "LYRICS", "ALIASES", "MATCHES"):
        monkeypatch.setattr(config, f"{name}_FILE", tmp_path / f"{name.lower()}.json")
    monkeypatch.setattr(config, "BACKUP_DIR", tmp_path / "backups")
    monkeypatch.setattr(config, "OPEN", False)
    monkeypatch.setattr(config, "PASSWORD", "")
    monkeypatch.setattr(lyrics, "_cache", None)
    return tmp_path


@pytest.fixture
def client():
    return app.app.test_client()


def prefetch(client, host=TAILNET, scheme="http", peer="100.64.0.5", **headers):
    """An unsafe request with a harmless effect (prefetch of nothing), from `peer` to `scheme://host`."""
    return client.post("/api/prefetch", json={"ids": []}, base_url=f"{scheme}://{host}",
                       environ_base={"REMOTE_ADDR": peer}, headers=headers)


# ---------- the cross-site guard ----------

def test_cross_site_text_plain_restore_is_refused_and_changes_nothing(client):
    likes.add({"id": YT, "title": "Song", "channel": "Artist", "source": "yt"})
    doc = json.loads(client.get("/api/backup").get_data())
    doc["likes"] = []
    r = client.post("/api/restore?mode=replace", data=json.dumps(doc), content_type="text/plain",
                    base_url=f"http://{TAILNET}", environ_base={"REMOTE_ADDR": "100.64.0.5"},
                    headers={"Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403 and "cross-site" in r.get_json()["error"]
    assert [e["id"] for e in likes.entries()] == [YT]


@pytest.mark.parametrize("headers", [
    {"Origin": "https://evil.example"},
    {"Sec-Fetch-Site": "cross-site"},
    {"Sec-Fetch-Site": "same-site", "Origin": "http://100.78.71.110:8080"},  # another service on the same address
    {"Sec-Fetch-Site": "same-origin", "Origin": "https://evil.example"},  # metadata that disagree: refuse
    {"Sec-Fetch-Site": "cross-site", "Origin": f"http://{TAILNET}"},
    {"Sec-Fetch-Site": "bogus", "Origin": f"http://{TAILNET}"},
    {"Origin": "null"},
    {"Origin": ""},
    {"Origin": "http://"},
    {"Origin": "not a url"},
    {"Origin": "javascript:alert(1)"},
    {"Origin": f"ftp://{TAILNET}"},
    {"Origin": f"http://{TAILNET}/path"},
    {"Origin": f"http://user@{TAILNET}"},
    {"Origin": "http://100.78.71.110:99999"},
    {"Origin": "http://100.78.71.110"},  # port 80, not 5274
    {"Origin": f"https://{TAILNET}"},  # scheme differs from the direct http connection
    {"Origin": f"http://{TAILNET}, http://{TAILNET}"},
])
def test_unsafe_cross_origin_requests_are_refused(client, headers):
    r = prefetch(client, **headers)
    assert r.status_code == 403, headers


@pytest.mark.parametrize("method,path", [("PUT", f"/api/likes/{YT}"), ("PATCH", "/api/settings"),
                                         ("DELETE", f"/api/likes/{YT}"), ("POST", "/api/yt/reset")])
def test_every_unsafe_method_is_guarded(client, method, path):
    r = client.open(path, method=method, data="{}", content_type="text/plain", base_url=f"http://{TAILNET}",
                    environ_base={"REMOTE_ADDR": "100.64.0.5"}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403


def test_safe_methods_are_not_guarded(client):
    r = client.get("/api/sources", base_url=f"http://{TAILNET}", environ_base={"REMOTE_ADDR": "100.64.0.5"},
                   headers={"Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 200


@pytest.mark.parametrize("peer", ["127.0.0.1", "100.64.0.5", "::1"])
def test_requests_without_origin_or_fetch_metadata_pass(client, peer):
    """curl, scripts, termux/update.sh and the tests themselves."""
    assert prefetch(client, peer=peer).status_code == 202
    assert prefetch(client, peer=peer, **{"Sec-Fetch-Site": "none"}).status_code == 202


@pytest.mark.parametrize("host,scheme,headers", [
    (TAILNET, "http", {"Origin": f"http://{TAILNET}", "Sec-Fetch-Site": "same-origin"}),
    (TAILNET, "http", {"Origin": f"http://{TAILNET}"}),
    ("localhost:5274", "http", {"Origin": "http://LOCALHOST:5274"}),
    ("[fd7a:115c:a1e0::1]:5274", "http", {"Origin": "http://[FD7A:115C:A1E0:0:0:0:0:1]:5274"}),
    ("[::1]:5274", "http", {"Origin": "http://[::1]:5274", "Sec-Fetch-Site": "same-origin"}),
    ("100.78.71.110:80", "http", {"Origin": "http://100.78.71.110"}),  # default ports compare equal
    ("100.78.71.110", "http", {"Origin": "http://100.78.71.110:80"}),
    (TAILNET, "https", {"Origin": f"https://{TAILNET}", "Sec-Fetch-Site": "same-origin"}),  # direct TLS
])
def test_direct_same_origin_requests_pass(client, host, scheme, headers):
    assert prefetch(client, host=host, scheme=scheme, **headers).status_code == 202


@pytest.mark.parametrize("host", [SERVE, f"{SERVE}:443"])
def test_tailscale_serve_https_passes(client, host):
    """Serve keeps the Host header, sets X-Forwarded-Proto: https and connects from loopback."""
    r = prefetch(client, host=host, peer="127.0.0.1", **{"Origin": f"https://{SERVE}", "Sec-Fetch-Site": "same-origin",
                                                        "X-Forwarded-Proto": "https"})
    assert r.status_code == 202


@pytest.mark.parametrize("peer,headers", [
    # A tailnet peer can send any header: forwarded scheme / host / client are never believed from it.
    ("100.64.0.5", {"Origin": f"https://{SERVE}", "X-Forwarded-Proto": "https"}),
    ("100.64.0.5", {"Origin": "https://evil.example", "X-Forwarded-Host": "evil.example", "X-Forwarded-Proto": "https"}),
    ("100.64.0.5", {"Origin": f"https://{SERVE}", "X-Forwarded-Proto": "https", "X-Forwarded-For": "127.0.0.1"}),
    ("127.0.0.1", {"Origin": "https://evil.example", "X-Forwarded-Host": "evil.example", "X-Forwarded-Proto": "https"}),
    # No scheme fallback: Serve's https doesn't also admit http, and a missing/odd X-Forwarded-Proto isn't https.
    ("127.0.0.1", {"Origin": f"http://{SERVE}", "X-Forwarded-Proto": "https"}),
    ("127.0.0.1", {"Origin": f"https://{SERVE}"}),
    ("127.0.0.1", {"Origin": f"https://{SERVE}", "X-Forwarded-Proto": "http"}),
    ("127.0.0.1", {"Origin": f"https://{SERVE}", "X-Forwarded-Proto": "gopher"}),
])
def test_forwarded_headers_cant_make_a_cross_origin_request_pass(client, peer, headers):
    assert prefetch(client, host=SERVE, peer=peer, **headers).status_code == 403


def test_forwarded_for_does_not_change_who_needs_a_password(client, monkeypatch):
    monkeypatch.setattr(config, "PASSWORD", "hunter2")
    r = client.post("/api/prefetch", json={"ids": []}, base_url=f"http://{TAILNET}",
                    environ_base={"REMOTE_ADDR": "192.168.1.20"},
                    headers={"X-Forwarded-For": "127.0.0.1", "Origin": f"http://{TAILNET}"})
    assert r.status_code == 401


def test_same_origin_beacon_listen_and_upload_restore_still_work(client):
    """sendBeacon posts text/plain JSON with the page's Origin; the restore sheet uploads a multipart file."""
    beacon = {"id": YT, "title": "Song", "channel": "Artist", "source": "yt", "played_s": 120, "duration": 200,
              "started_at": 1_760_000_000, "ended_at": 1_760_000_120}
    same = {"Origin": f"http://{TAILNET}", "Sec-Fetch-Site": "same-origin"}
    r = client.post("/api/listens", data=json.dumps(beacon), content_type="text/plain;charset=UTF-8",
                    base_url=f"http://{TAILNET}", environ_base={"REMOTE_ADDR": "100.64.0.5"}, headers=same)
    assert r.status_code in (200, 201), r.get_data(as_text=True)
    doc = client.get("/api/backup").get_data()
    r = client.post("/api/restore", data={"file": (io.BytesIO(doc), "reef.json"), "mode": "merge"},
                    base_url=f"http://{TAILNET}", environ_base={"REMOTE_ADDR": "100.64.0.5"}, headers=same)
    assert r.status_code == 200, r.get_data(as_text=True)


# ---------- JSON bodies that aren't objects ----------

NOT_OBJECTS = ["[1]", "[]", "1", '"text"', "true", "null", "{", ""]


def _routes(pid):
    return [("POST", "/api/blocklist"), ("POST", "/api/prefetch"), ("POST", "/api/download"),
            ("POST", "/api/download/batch"), ("POST", "/api/playlists"), ("POST", f"/api/playlists/{pid}/tracks"),
            ("POST", f"/api/playlists/{pid}/move"), ("PATCH", f"/api/playlists/{pid}"), ("PUT", f"/api/likes/{YT}"),
            ("POST", f"/api/dislikes/{YT}"), ("POST", "/api/match"), ("POST", "/api/match/candidates"),
            ("PUT", "/api/match/some%20key"), ("POST", "/api/replace"), ("PUT", "/api/aliases"),
            ("PATCH", "/api/settings"), ("POST", "/api/update"), ("POST", "/api/import"),
            ("PUT", f"/api/lyrics/{YT}/offset"), ("PUT", f"/api/lyrics/{YT}/pin"), ("POST", "/api/listens"),
            ("POST", "/api/personal/play"), ("POST", "/api/personal/progress"), ("POST", "/api/restore")]


@pytest.mark.parametrize("raw", NOT_OBJECTS)
def test_bodies_that_arent_objects_never_500(client, monkeypatch, raw):
    monkeypatch.setattr(app.updater, "_spawn", lambda *a: pytest.fail("an update must not start"))
    pid = playlists.create("Mix", [], "")["id"]
    for method, path in _routes(pid):
        for ctype in ("application/json", "text/plain"):
            r = client.open(path, method=method, data=raw, content_type=ctype)
            assert r.status_code < 500, (method, path, raw, ctype, r.status_code, r.get_data(as_text=True)[:200])


@pytest.mark.parametrize("raw", ["[1]", "1", '"x"', "true", "null", "{", ""])
@pytest.mark.parametrize("path", ["/api/blocklist", "/api/prefetch", "/api/download", "/api/download/batch",
                                  "/api/playlists"])
def test_required_bodies_answer_400(client, path, raw):
    r = client.post(path, data=raw, content_type="application/json")
    assert r.status_code == 400 and r.get_json()["error"]


@pytest.mark.parametrize("raw", ["[1]", "1", "null", "{"])
def test_playlist_track_and_move_bodies_answer_400(client, raw):
    pid = playlists.create("Mix", [], "")["id"]
    for path in (f"/api/playlists/{pid}/tracks", f"/api/playlists/{pid}/move"):
        r = client.post(path, data=raw, content_type="application/json")
        assert r.status_code == 400, (path, raw)


def test_valid_object_bodies_still_work(client):
    assert client.post("/api/blocklist", json={"name": "Band"}).status_code == 201
    assert client.post("/api/prefetch", json={"ids": []}).status_code == 202
    p = client.post("/api/playlists", json={"name": "Mix"}).get_json()
    track = {"id": YT, "title": "Song", "channel": "Artist", "source": "yt"}
    r = client.post(f"/api/playlists/{p['id']}/tracks", json={"tracks": [track, {**track, "id": "FGBhQbmPwH8"}]})
    assert r.status_code == 200 and r.get_json()["added"] == 2
    r = client.post(f"/api/playlists/{p['id']}/move", json={"from": 0, "to": 1})
    assert r.status_code == 200 and r.get_json()["tracks"][0]["id"] == "FGBhQbmPwH8"


def test_optional_bodies_stay_optional(client, monkeypatch):
    """Routes whose body is optional still answer without one (or with a non-object) exactly as before."""
    assert client.post("/api/yt/reset").status_code == 200
    calls = []
    monkeypatch.setattr(app, "_import_resolve", lambda pid, retry_missing: calls.append(retry_missing) or {})
    monkeypatch.setattr(app.ytmusic, "enabled", lambda: True)
    pid = playlists.create("Mix", [], "")["id"]
    for raw in ("", "[1]", '{"retry_missing": true}'):
        r = client.post(f"/api/import/{pid}/resolve", data=raw, content_type="application/json")
        assert r.status_code == 202
    assert calls == [False, False, True]


# ---------- durations too large for a float ----------

HUGE = 10 ** 400


def test_seconds_treats_huge_ints_as_no_duration():
    assert app._seconds(HUGE) is None and app._seconds(-HUGE) is None
    assert app._seconds("1" * 400) is None  # a string: float() gives inf, rejected as before
    assert app._seconds(200) == 200.0 and app._seconds("200.5") == 200.5


def _huge_json(obj):
    """JSON with HUGE where the placeholder string was (json.dumps would write it out anyway; this is explicit)."""
    return json.dumps(obj).replace('"HUGE"', str(HUGE))


def test_huge_durations_on_match_routes_are_no_duration(client, monkeypatch):
    asked = []
    monkeypatch.setattr(app, "_find_song", lambda query, *a, **k: asked.append(query["duration"]))
    monkeypatch.setattr(app.ytmusic, "enabled", lambda: True)
    monkeypatch.setattr(app.ytmusic, "search", lambda q, limit=10: [])
    body = _huge_json({"title": "Song", "artist": "Artist", "duration": "HUGE"})
    r = client.post("/api/match", data=body, content_type="application/json")
    assert r.status_code == 404 and asked == [None]
    r = client.post("/api/match/candidates", data=body, content_type="application/json")
    assert r.status_code == 200 and r.get_json()["candidates"] == []


def test_huge_durations_on_keeps_are_no_duration(client, monkeypatch):
    seen = []

    def enqueue(tid, title, channel, hints, duration=None):
        seen.append(duration)
        return {"id": f"job-{len(seen)}", "track_id": tid}, True

    monkeypatch.setattr(downloader, "enqueue_unique", enqueue)
    r = client.post("/api/download", data=_huge_json({"id": YT, "title": "Song", "duration": "HUGE"}),
                    content_type="application/json")
    assert r.status_code == 202
    r = client.post("/api/download/batch", data=_huge_json({"tracks": [{"id": "FGBhQbmPwH8", "duration": "HUGE"}]}),
                    content_type="application/json")
    assert r.status_code == 202 and seen == [None, None]
