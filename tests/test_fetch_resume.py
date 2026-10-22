"""Resumable generic (Monochrome) keeps: `.part` + `Range: bytes=<size>-` after a dropped connection, a restart from 0
when the server answers 200 instead of 206 (or other bytes), re-resolving a URL about to expire, REEF_FAKE_DROP.
A real local HTTP server drops the connection mid-file."""
import hashlib
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import requests

import downloader
import streamer

BODY = os.urandom(3 * 1024 * 1024 + 1234)


class Server:
    """Serves BODY at /file. `drops`: per request, after how many bytes to cut the connection (None = don't);
    `ranges`: honour Range (206) or always answer 200 with the whole file; `fail`: statuses to answer first."""

    def __init__(self, drops=(), ranges=True, fail=(), body=BODY, every=None):
        self.drops, self.ranges, self.fail, self.seen = list(drops), ranges, list(fail), []
        self.body, self.every = body, every  # every: cut every request after that many bytes
        server = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def do_GET(self):
                rng = self.headers.get("Range")
                server.seen.append(rng)
                if server.fail:
                    self.send_response(server.fail.pop(0))
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                start = int(rng.split("=")[1].rstrip("-")) if rng and server.ranges else 0
                body = server.body
                if start >= len(body):
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{len(body)}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                chunk = body[start:]
                self.send_response(206 if start else 200)
                if start:
                    self.send_header("Content-Range", f"bytes {start}-{len(body) - 1}/{len(body)}")
                self.send_header("Content-Length", str(len(chunk)))
                self.end_headers()
                drop = server.drops.pop(0) if server.drops else server.every
                if drop is not None and drop < len(chunk):
                    self.wfile.write(chunk[:drop])
                    self.wfile.flush()
                    self.close_connection = True
                    self.connection.shutdown(2)  # mid-file: the client sees a short body
                    return
                self.wfile.write(chunk)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/file"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def sleeps(monkeypatch):
    slept = []
    monkeypatch.setattr(downloader, "_sleep", slept.append)
    monkeypatch.setattr(streamer, "session", requests.Session())
    monkeypatch.delenv("REEF_FAKE_DROP", raising=False)
    return slept


@pytest.fixture
def serve():
    made = []

    def make(**kw):
        made.append(Server(**kw))
        return made[-1]
    yield make
    for s in made:
        s.close()


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def offset(rng):
    return int(rng.split("=")[1].rstrip("-"))


def test_drop_mid_file_resumes_with_range(serve, sleeps, tmp_path):
    s = serve(drops=[1_000_000, 500_000])
    dest, job = tmp_path / "a.flac", {"percent": 0}
    downloader._fetch(job, {"url": s.url}, dest)
    assert digest(dest) == hashlib.sha256(BODY).hexdigest()
    # The bytes that arrived before each drop were kept (urllib3 hands them over in whole reads, so a little less
    # than the drop point), and each retry asked for the rest.
    assert s.seen[0] is None and len(s.seen) == 3 and sleeps == [2, 2]  # both drops came after progress
    assert 0 < offset(s.seen[1]) <= 1_000_000 < offset(s.seen[2]) <= offset(s.seen[1]) + 500_000
    assert not (tmp_path / "a.flac.part").exists() and job["percent"] == 99


def test_server_ignoring_range_restarts_from_zero(serve, sleeps, tmp_path):
    s = serve(drops=[700_000], ranges=False)
    dest = tmp_path / "a.flac"
    downloader._fetch({"percent": 0}, {"url": s.url}, dest)
    assert dest.read_bytes() == BODY  # the 200 replaced the partial file, nothing appended twice
    assert s.seen[0] is None and 0 < offset(s.seen[1]) <= 700_000 and len(s.seen) == 2


def test_5xx_then_resume_and_give_up_cleans_the_part(serve, sleeps, tmp_path):
    s = serve(drops=[None, 400_000], fail=[503])
    dest = tmp_path / "a.flac"
    downloader._fetch({"percent": 0}, {"url": s.url}, dest)
    assert dest.read_bytes() == BODY and s.seen[:2] == [None, None] and sleeps == [2]
    s2 = serve(drops=[600_000, 0, 0, 0, 0])
    with pytest.raises(requests.exceptions.ChunkedEncodingError):
        downloader._fetch({"percent": 0}, {"url": s2.url}, tmp_path / "b.flac")
    assert not (tmp_path / "b.flac.part").exists() and not (tmp_path / "b.flac").exists()
    # The first drop came after real progress: no retry used. Then three retries without progress (2/4/8 s) and out.
    assert len(s2.seen) == 5 and len(set(s2.seen[1:])) == 1 and offset(s2.seen[1]) > 0  # every retry resumed
    assert sleeps == [2, 2, 2, 4, 8]


BIG = os.urandom(10 * 1024 * 1024)


def test_progress_costs_no_retry_and_resets_the_backoff(serve, sleeps, tmp_path):
    s = serve(body=BIG, every=1024 * 1024)  # cuts every request after 1 MB: ~13 attempts for 10 MB
    dest = tmp_path / "big.flac"
    downloader._fetch({"percent": 0}, {"url": s.url}, dest)
    assert hashlib.sha256(dest.read_bytes()).digest() == hashlib.sha256(BIG).digest()
    assert len(s.seen) > len(downloader._RETRY_SLEEPS) + 1 and set(sleeps) == {2}  # never past the first step
    offsets = [offset(r) for r in s.seen[1:]]
    assert offsets == sorted(offsets) and len(set(offsets)) == len(offsets)


def test_progress_then_stall_gets_fresh_retries(serve, sleeps, tmp_path):
    s = serve(body=BIG, drops=[0, 1024 * 1024, 0, 0, 0])  # no progress, progress, then three without
    dest = tmp_path / "big.flac"
    downloader._fetch({"percent": 0}, {"url": s.url}, dest)
    assert dest.read_bytes() == BIG and sleeps == [2, 2, 2, 4, 8]  # the backoff started over after the progress


def test_attempts_are_capped(serve, sleeps, tmp_path, monkeypatch):
    monkeypatch.setattr(downloader, "FETCH_ATTEMPTS", 5)
    s = serve(body=BIG, every=1024 * 1024)
    with pytest.raises(requests.exceptions.ChunkedEncodingError):
        downloader._fetch({"percent": 0}, {"url": s.url}, tmp_path / "big.flac")
    assert len(s.seen) == 5 and not (tmp_path / "big.flac.part").exists()


def test_fake_drop_env_hook(serve, sleeps, tmp_path, monkeypatch):
    monkeypatch.setenv("REEF_FAKE_DROP", "300000")
    s = serve()
    dest = tmp_path / "a.flac"
    downloader._fetch({"percent": 0}, {"url": s.url}, dest)
    assert dest.read_bytes() == BODY and s.seen[1] == "bytes=300000-" and sleeps == [2]


def test_expiring_url_is_resolved_again_first(serve, sleeps, tmp_path, monkeypatch):
    s = serve()
    now = 1_800_000_000
    monkeypatch.setattr(streamer, "_now", lambda: now)
    fresh = {"url": s.url, "expire": now + 3600, "expires": now + 3540}
    asked = []
    monkeypatch.setattr(streamer, "resolve", lambda tid: asked.append(tid) or fresh)
    dest = tmp_path / "a.flac"
    downloader._fetch({"percent": 0}, {"url": "http://127.0.0.1:9/never", "expire": now + 30, "expires": now - 30},
                      dest, "mc:123")
    assert asked == ["mc:123"] and dest.read_bytes() == BODY
    asked.clear()
    downloader._fetch({"percent": 0}, fresh, tmp_path / "b.flac", "mc:123")
    assert asked == []  # an hour left: used as is
    downloader._fetch({"percent": 0}, {"url": "http://127.0.0.1:9/x", "expire": None, "expires": now - 1},
                      tmp_path / "c.flac", "mc:123")
    assert asked == ["mc:123"]  # no URL expiry: streamer's cache TTL ran out


def test_resume_refused_with_other_bytes_restarts(sleeps, tmp_path, monkeypatch):
    class Resp:
        def __init__(self, status, body, headers):
            self.status_code, self.body, self.headers = status, body, headers

        def raise_for_status(self):
            if self.status_code >= 400:
                raise requests.HTTPError(str(self.status_code), response=self)

        def iter_content(self, n):
            if self.body is None:
                yield b"abc"
                raise requests.exceptions.ChunkedEncodingError("dropped")
            yield self.body

        def close(self):
            pass

    answers = [Resp(200, None, {"Content-Length": "6"}),
               Resp(206, b"zzz", {"Content-Range": "bytes 0-2/6"}),  # not where we asked: start over
               Resp(200, b"abcdef", {"Content-Length": "6"})]
    seen = []

    class Session:
        def get(self, url, headers=None, **k):
            seen.append((headers or {}).get("Range"))
            return answers.pop(0)
    monkeypatch.setattr(streamer, "session", Session())
    dest = tmp_path / "a.bin"
    downloader._fetch({"percent": 0}, {"url": "http://x"}, dest)
    assert dest.read_bytes() == b"abcdef" and seen == [None, "bytes=3-", None]
