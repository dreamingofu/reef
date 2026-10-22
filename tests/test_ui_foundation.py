"""UI remake step 1 server bits: test audio mode (REEF_TEST_AUDIO) and its loopback guard, same-origin tiny art for
the cover palette, and typed error payloads on upstream failures. Offline."""
import os

import pytest

import app
import config
import sources
import streamer
import testaudio
import ytmusic

VID = "dQw4w9WgXcQ"
OTHER = "kJQP7kiw5Fk"


@pytest.fixture
def client():
    return app.app.test_client()


@pytest.fixture
def audio_dir(tmp_path):
    for name, size in (("a.m4a", 3000), ("b.m4a", 4000), ("c.mp3", 5000)):
        (tmp_path / name).write_bytes(bytes(range(256)) * (size // 256) + b"x" * (size % 256))
    (tmp_path / "notes.txt").write_text("not audio")
    return tmp_path


@pytest.fixture(autouse=True)
def _reset_testaudio():
    testaudio.setup(path="")
    yield
    testaudio.setup(path="")


# ---------- the guard ----------

@pytest.mark.parametrize("host,ok", [("127.0.0.1", True), ("::1", True), ("[::1]", True), ("localhost", True),
                                     ("127.0.0.2", True), ("0.0.0.0", False), ("100.78.71.110", False),
                                     ("192.168.1.10", False), ("", False), ("::", False)])
def test_allowed_only_on_loopback(host, ok):
    assert testaudio.allowed(host=host, i_know=False) is ok


def test_i_know_overrides_the_bind_address():
    assert testaudio.allowed(host="0.0.0.0", i_know=True)


def test_refused_on_a_public_bind_even_with_files(audio_dir):
    assert testaudio.setup(path=str(audio_dir), host="0.0.0.0", i_know=False) == []
    assert not testaudio.enabled() and not testaudio.covers("yt")
    st = testaudio.status()
    assert st["on"] is False and "not loopback" in st["reason"]


def test_refused_without_audio_files(tmp_path):
    (tmp_path / "x.txt").write_text("x")
    assert testaudio.setup(path=str(tmp_path), host="127.0.0.1", i_know=False) == []
    assert "no audio files" in testaudio.status()["reason"]
    assert testaudio.setup(path=str(tmp_path / "missing"), host="127.0.0.1", i_know=False) == []


def test_default_environment_is_off():
    os.environ.pop("REEF_TEST_AUDIO", None)
    assert testaudio.setup() == [] and testaudio.status() == {"on": False, "files": 0, "reason": None}


def test_env_driven_setup_uses_config_host(monkeypatch, audio_dir):
    monkeypatch.setenv("REEF_TEST_AUDIO", str(audio_dir))
    monkeypatch.delenv("REEF_TEST_AUDIO_I_KNOW", raising=False)
    monkeypatch.setattr(config, "HOST", "0.0.0.0")
    assert testaudio.setup() == []
    monkeypatch.setattr(config, "HOST", "127.0.0.1")
    assert [p.name for p in testaudio.setup()] == ["a.m4a", "b.m4a", "c.mp3"]
    monkeypatch.setattr(config, "HOST", "0.0.0.0")
    monkeypatch.setenv("REEF_TEST_AUDIO_I_KNOW", "1")
    assert len(testaudio.setup()) == 3


def test_announce_is_loud(audio_dir, caplog):
    testaudio.setup(path=str(audio_dir), host="127.0.0.1", i_know=False)
    with caplog.at_level("WARNING"):
        testaudio.announce()
    assert "TEST AUDIO MODE" in caplog.text


def test_announce_says_why_it_is_off(audio_dir, caplog):
    testaudio.setup(path=str(audio_dir), host="0.0.0.0", i_know=False)
    with caplog.at_level("ERROR"):
        testaudio.announce()
    assert "test audio mode is off" in caplog.text


# ---------- the stream ----------

def test_pick_is_stable_per_id(audio_dir):
    testaudio.setup(path=str(audio_dir), host="127.0.0.1", i_know=False)
    first = testaudio.pick(VID)
    assert all(testaudio.pick(VID) == first for _ in range(5))
    picks = {testaudio.pick(f"id{n:09d}") for n in range(60)}
    assert len(picks) == 3  # spread over the directory


def test_single_file_path(audio_dir):
    testaudio.setup(path=str(audio_dir / "b.m4a"), host="127.0.0.1", i_know=False)
    assert testaudio.pick(VID).name == "b.m4a" and testaudio.pick(OTHER).name == "b.m4a"


def test_stream_serves_local_file_with_ranges(client, audio_dir, monkeypatch):
    testaudio.setup(path=str(audio_dir), host="127.0.0.1", i_know=False)
    monkeypatch.setattr(streamer, "proxy", lambda *a, **k: pytest.fail("YouTube must not be resolved"))
    path = testaudio.pick(VID)
    data = path.read_bytes()
    r = client.get(f"/api/stream/{VID}")
    assert r.status_code == 200 and r.data == data
    assert r.headers["Accept-Ranges"] == "bytes"
    assert r.mimetype == testaudio.mimetype(path)
    r = client.get(f"/api/stream/{VID}?fresh=abc", headers={"Range": "bytes=0-1"})
    assert r.status_code == 206 and r.data == data[:2]
    assert r.headers["Content-Range"] == f"bytes 0-1/{len(data)}"
    r = client.get(f"/api/stream/{VID}", headers={"Range": "bytes=100-"})
    assert r.status_code == 206 and r.data == data[100:]


def test_other_sources_still_proxy(client, audio_dir, monkeypatch):
    testaudio.setup(path=str(audio_dir), host="127.0.0.1", i_know=False)
    seen = []
    monkeypatch.setattr(streamer, "proxy", lambda tid, fresh=None: seen.append(tid) or ("proxied", 200))
    monkeypatch.setattr(sources, "get", lambda key: type("S", (), {"key": key, "can_keep": True})())
    client.get("/api/stream/mc:12345")
    assert seen == ["mc:12345"]


def test_stream_off_by_default_goes_to_the_proxy(client, monkeypatch):
    seen = []
    monkeypatch.setattr(streamer, "proxy", lambda tid, fresh=None: seen.append(tid) or ("proxied", 200))
    assert client.get(f"/api/stream/{VID}").data == b"proxied"
    assert seen == [VID]


def test_prefetch_skips_youtube_in_test_mode(audio_dir, monkeypatch):
    testaudio.setup(path=str(audio_dir), host="127.0.0.1", i_know=False)
    submitted = []
    monkeypatch.setattr(streamer._pool, "submit", lambda fn, tid: submitted.append(tid))
    monkeypatch.setattr(streamer, "_fresh", lambda tid: False)
    assert streamer.prefetch([VID, OTHER, "mc:999"]) == 1
    assert submitted == ["mc:999"]
    streamer._queued.discard("mc:999")


def test_health_and_diagnostics_report_the_mode(client, audio_dir):
    testaudio.setup(path=str(audio_dir), host="127.0.0.1", i_know=False)
    assert client.get("/api/diagnostics").get_json()["config"]["test_audio"]["on"] is True


# ---------- tiny art, same origin ----------

class FakeResp:
    def __init__(self, status=200, mime="image/jpeg", data=b"\xff\xd8jpegbytes"):
        self.status_code, self.headers, self.data = status, {"Content-Type": mime}, data

    def iter_content(self, n):
        for i in range(0, len(self.data), n):
            yield self.data[i:i + n]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeSession:
    def __init__(self, resp):
        self.resp, self.calls = resp, []

    def get(self, url, **kw):
        self.calls.append(url)
        if isinstance(self.resp, Exception):
            raise self.resp
        return self.resp


@pytest.fixture
def tiny(monkeypatch):
    app._tiny_art.clear()
    yield
    app._tiny_art.clear()


def test_art_size_clamp_allows_64(client, monkeypatch):
    sizes = []
    monkeypatch.setattr(sources.youtube.SOURCE, "art", lambda native, size: sizes.append(size) or None)
    for asked in (16, 64, 96, 5000):
        client.get(f"/api/art/{VID}?size={asked}")
    assert sizes == [64, 64, 96, 1000]


def test_tiny_art_is_proxied_and_cached(client, tiny, monkeypatch):
    sess = FakeSession(FakeResp())
    monkeypatch.setattr(app, "_art_session", sess)
    monkeypatch.setattr(ytmusic, "square_art", lambda vid, size: f"https://lh3.googleusercontent.com/x=w{size}-h{size}")
    for _ in range(3):
        r = client.get(f"/api/art/{VID}?size=64")
        assert r.status_code == 200 and r.mimetype == "image/jpeg" and r.data == b"\xff\xd8jpegbytes"
        assert "max-age" in r.headers["Cache-Control"]
    assert sess.calls == ["https://lh3.googleusercontent.com/x=w64-h64"]


def test_bigger_art_still_redirects(client, tiny, monkeypatch):
    sess = FakeSession(FakeResp())
    monkeypatch.setattr(app, "_art_session", sess)
    monkeypatch.setattr(ytmusic, "square_art", lambda vid, size: None)
    r = client.get(f"/api/art/{VID}?size=200")
    assert r.status_code == 302 and r.headers["Location"].endswith("/mqdefault.jpg")
    assert sess.calls == []


@pytest.mark.parametrize("resp", [FakeResp(status=404), FakeResp(mime="text/html"),
                                  FakeResp(data=b"x" * (app.ART_TINY_BYTES + 10)), OSError("network down")])
def test_tiny_art_falls_back_to_the_redirect(client, tiny, monkeypatch, resp):
    monkeypatch.setattr(app, "_art_session", FakeSession(resp))
    monkeypatch.setattr(ytmusic, "square_art", lambda vid, size: None)
    r = client.get(f"/api/art/{VID}?size=96")
    assert r.status_code == 302 and "i.ytimg.com" in r.headers["Location"]
    assert not app._tiny_art


def test_tiny_art_memory_is_bounded(tiny, monkeypatch):
    monkeypatch.setattr(app, "_art_session", FakeSession(FakeResp()))
    monkeypatch.setattr(app, "ART_TINY_KEEP", 3)
    for n in range(6):
        assert app._tiny_bytes(f"https://x/{n}")
    assert list(app._tiny_art) == ["https://x/3", "https://x/4", "https://x/5"]


# ---------- typed errors on upstream failures ----------

def test_search_failure_carries_block_and_hint(client, monkeypatch):
    def boom(q, limit):
        raise RuntimeError("Sign in to confirm you're not a bot")
    monkeypatch.setattr(sources.youtube.SOURCE, "search", boom)
    r = client.get("/api/search?q=daft&src=yt")
    body = r.get_json()
    assert r.status_code == 502 and body["block"] == "BOT_WALL" and "bot check" in body["hint"]
    assert "not a bot" in body["error"]
