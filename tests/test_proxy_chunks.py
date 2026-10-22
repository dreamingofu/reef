"""The proxy fetches every client range upstream as bounded pieces (googlevideo throttles open-ended ranges).

Offline: a fake upstream serves an in-memory file, records each requested Range and can drop, ignore Range,
omit the total, or overshoot. Small piece sizes stand in for the real 2 / 4 MiB.
"""
import random

import pytest
import requests

import app
import config
import errors
import streamer
from test_streamer import durations, mp4

VID = "FGBhQbmPwH8"
FIRST, SPAN = 1000, 3000
DATA = random.Random(7).randbytes(20000)


class Up:
    def __init__(self, body, status, headers, drop=None):
        self.body, self.status_code, self.headers, self.drop = body, status, headers, drop
        self.closed = False

    def iter_content(self, _size):
        stop = len(self.body) if self.drop is None else self.drop
        for i in range(0, stop, 333):
            yield self.body[i:min(i + 333, stop)]
        if self.drop is not None:
            raise requests.exceptions.ChunkedEncodingError("connection broken: IncompleteRead")

    def close(self):
        self.closed = True


class Origin:
    """One file behind a Range-aware server. `drops`: {absolute byte: True} cuts the body that contains it, once.
    `mode`: "ignore" answers 200 with the whole file, "nototal" says `/*`, "overshoot" ignores the range's end."""

    def __init__(self, data, mode=None, mime="audio/webm"):
        self.data, self.mode, self.mime = data, mode, mime
        self.calls, self.ups, self.drops = [], [], set()
        self.open_ended = 0  # what googlevideo would throttle to ~2x realtime

    def open(self, entry, rng=None):
        self.calls.append(rng)
        n = len(self.data)
        if not rng or self.mode == "ignore":
            up = Up(self.data, 200, {"Content-Type": self.mime, "Content-Length": str(n)})
        else:
            a, _, b = rng.split("=")[1].partition("-")
            a = int(a)
            if not b:
                self.open_ended += 1
            b = n - 1 if not b or self.mode == "overshoot" else min(int(b), n - 1)
            if a >= n:
                return Up(b"", 416, {"Content-Range": f"bytes */{n}"}), 0
            total = "*" if self.mode == "nototal" else n
            up = Up(self.data[a:b + 1], 206, {"Content-Type": self.mime, "Content-Length": str(b - a + 1),
                                               "Content-Range": f"bytes {a}-{b}/{total}"})
            cut = min((d for d in self.drops if a <= d <= b), default=None)
            if cut is not None:
                self.drops.discard(cut)
                up.drop = cut - a
        self.ups.append(up)
        return up, 0


@pytest.fixture
def origin(monkeypatch):
    monkeypatch.setattr(streamer, "_FIRST_SPAN", FIRST)
    monkeypatch.setattr(streamer, "_SPAN", SPAN)
    monkeypatch.setattr(streamer, "_sleep", lambda s: None)
    monkeypatch.setattr(config, "FAKE_WALL", False)

    def run(data=DATA, mode=None, mime="audio/webm", **entry):
        o = Origin(data, mode, mime)
        e = {"url": "https://rr1.googlevideo.com/videoplayback", "headers": {}, "mime": mime, "ext": "m4a",
             "gen": 2, "client": "web", **entry}
        o.entry = e
        monkeypatch.setattr(streamer, "_resolve", lambda tid, foreground=True: (e, "hit"))
        monkeypatch.setattr(streamer, "_open", o.open)
        return o

    with streamer._lock:
        streamer._served.clear()
        streamer._fresh_tokens.clear()
    yield run
    with streamer._lock:
        streamer._served.clear()
        streamer._fresh_tokens.clear()


def get(rng=None, path=f"/api/stream/{VID}"):
    return app.app.test_client().get(path, headers={"Range": rng} if rng else {})


def bounds(calls):
    out = []
    for rng in calls:
        a, _, b = rng.split("=")[1].partition("-")
        out.append((int(a), int(b)))
    return out


def assert_pieces(calls, first, last):
    """Every upstream request is bounded, they tile [first, last] in order, the first is FIRST long, then SPAN."""
    got = bounds(calls)
    assert all(rng.rstrip()[-1] != "-" for rng in calls), calls
    assert got[0][0] == first and got[-1][1] == last
    assert all(b[0] == a[1] + 1 for a, b in zip(got, got[1:]))
    assert got[0][1] - got[0][0] + 1 == min(FIRST, last - first + 1)
    assert all(b - a + 1 == SPAN for a, b in got[1:-1])


def test_open_ended_range_is_fetched_in_bounded_pieces(origin, capsys):
    o = origin()
    r = get("bytes=0-")
    assert r.status_code == 206 and r.data == DATA
    assert r.headers["Content-Range"] == f"bytes 0-{len(DATA) - 1}/{len(DATA)}"
    assert r.headers["Content-Length"] == str(len(DATA))
    assert o.open_ended == 0
    assert_pieces(o.calls, 0, len(DATA) - 1)
    assert len(o.calls) == 1 + -(-(len(DATA) - FIRST) // SPAN)  # 1000 + 7 x 3000 >= 20000
    assert all(up.closed for up in o.ups)  # each finished piece goes back to the keep-alive pool
    assert f"bytes={len(DATA)} pieces={len(o.calls)} resumes=0" in capsys.readouterr().out
    assert streamer.last_timing["up"] == f"bytes=0-{FIRST - 1}"


def test_mid_file_open_ended_range(origin):
    o = origin()
    r = get("bytes=12345-")
    assert r.status_code == 206 and r.data == DATA[12345:]
    assert r.headers["Content-Range"] == f"bytes 12345-{len(DATA) - 1}/{len(DATA)}"
    assert r.headers["Content-Length"] == str(len(DATA) - 12345)
    assert_pieces(o.calls, 12345, len(DATA) - 1)


def test_bounded_range_spanning_pieces(origin):
    o = origin()
    r = get("bytes=500-7499")
    assert r.status_code == 206 and r.data == DATA[500:7500]
    assert r.headers["Content-Range"] == f"bytes 500-7499/{len(DATA)}" and r.headers["Content-Length"] == "7000"
    assert o.calls == ["bytes=500-1499", "bytes=1500-4499", "bytes=4500-7499"]


def test_range_past_the_end_is_clamped(origin):
    o = origin()
    r = get(f"bytes=19000-{len(DATA) + 5000}")
    assert r.status_code == 206 and r.data == DATA[19000:]
    assert r.headers["Content-Range"] == f"bytes 19000-{len(DATA) - 1}/{len(DATA)}"
    assert o.calls == ["bytes=19000-19999"]


def test_clen_bounds_the_first_piece(origin):
    o = origin(clen=len(DATA))
    get("bytes=19500-")
    assert o.calls == [f"bytes=19500-{len(DATA) - 1}"]


def test_two_byte_probe_is_one_tiny_request(origin):
    o = origin()
    r = get("bytes=0-1")
    assert r.status_code == 206 and r.data == DATA[:2]
    assert r.headers["Content-Range"] == f"bytes 0-1/{len(DATA)}" and r.headers["Content-Length"] == "2"
    assert o.calls == ["bytes=0-1"]


def test_no_range_is_200_with_the_full_length(origin):
    o = origin()
    r = get()
    assert r.status_code == 200 and r.data == DATA
    assert r.headers["Content-Length"] == str(len(DATA)) and "Content-Range" not in r.headers
    assert_pieces(o.calls, 0, len(DATA) - 1)


def test_moov_patched_across_pieces_rest_intact(origin, monkeypatch):
    head = mp4()
    data = head + DATA[:5000]
    monkeypatch.setattr(streamer, "_FIRST_SPAN", 100)  # moov straddles the first pieces
    o = origin(data, mime="audio/mp4")
    r = get("bytes=0-")
    assert r.status_code == 206 and len(r.data) == len(data)
    assert durations(r.data) == [0, 0, 0] and r.data == streamer.fix_fragmented_durations(data)
    assert r.data[len(head):] == data[len(head):]
    assert bounds(o.calls)[0] == (0, 99)


def test_moov_left_alone_past_byte_zero(origin):
    data = mp4() + DATA[:5000]
    origin(data, mime="audio/mp4")
    assert get("bytes=1-").data == data[1:]


def test_drop_mid_piece_resumes_within_that_piece(origin, capsys):
    o = origin()
    o.drops = {2700, 9000}  # inside the 2nd piece (1000-3999) and the 4th (7000-9999)
    r = get("bytes=0-")
    assert r.status_code == 206 and r.data == DATA
    assert "bytes=2700-3999" in o.calls and "bytes=9000-9999" in o.calls
    assert o.calls.index("bytes=2700-3999") == o.calls.index("bytes=1000-3999") + 1
    assert o.calls[o.calls.index("bytes=2700-3999") + 1] == "bytes=4000-6999"  # then the pieces carry on
    out = capsys.readouterr().out
    assert out.count("stream-resume id=") == 2 and "resumes=2" in out


def test_each_piece_gets_its_own_resume_budget(origin):
    o = origin()
    o.drops = {1500, 1600, 1700, 1800, 1900, 4500, 4600, 4700, 4800, 4900}  # 5 drops in each of two pieces
    assert get("bytes=0-").data == DATA


def test_dead_url_on_a_later_piece_swaps_to_the_same_file(origin, monkeypatch):
    o = origin(itag="140", lmt="1", clen=len(DATA))
    new = {**o.entry, "url": "https://rr2.googlevideo.com/videoplayback", "gen": 3}
    orig = o.open

    def open_(entry, rng=None):
        if entry["url"] == o.entry["url"] and rng.startswith("bytes=4000-"):
            return Up(b"", 403, {}), 0
        return orig(entry, rng)

    monkeypatch.setattr(streamer, "_open", open_)
    monkeypatch.setattr(streamer, "_refresh", lambda tid, entry, verdict: (new, "miss"))
    r = get("bytes=0-")
    assert r.data == DATA
    assert o.calls.count("bytes=4000-6999") == 1  # the 403 isn't recorded by the origin; the retry is


def test_fake_wall_ends_the_body_at_a_piece_past_it(origin, monkeypatch, capsys):
    monkeypatch.setattr(config, "FAKE_WALL", True)
    origin(gen=1, clen=len(DATA), duration=200)  # wall at 20000 * 60 / 200 = 6000
    r = get("bytes=0-")
    # pieces start at 0, 1000, 4000 (before the wall) and 7000 (past it): the body stops at 7000
    assert r.data == DATA[:7000]
    assert "fake wall at byte 7000" in capsys.readouterr().out
    r = get("bytes=7000-")  # the player's next request meets the wall
    assert r.status_code == 403 and r.get_json()["block"] == errors.EXPIRED


def test_fake_wall_spares_later_generations(origin, monkeypatch):
    monkeypatch.setattr(config, "FAKE_WALL", True)
    origin(gen=2, clen=len(DATA), duration=200)
    assert get("bytes=0-").data == DATA


def test_upstream_ignoring_range_passes_through(origin):
    o = origin(mode="ignore")
    r = get("bytes=0-")
    assert r.status_code == 200 and r.data == DATA and r.headers["Content-Length"] == str(len(DATA))
    assert len(o.calls) == 1


def test_upstream_without_total_falls_back_to_the_clients_range(origin):
    o = origin(mode="nototal")
    r = get("bytes=100-")
    assert r.status_code == 206 and r.data == DATA[100:]
    assert o.calls == [f"bytes=100-{100 + FIRST - 1}", "bytes=100-"]


def test_upstream_overshooting_the_piece_is_trimmed(origin):
    o = origin(mode="overshoot")
    r = get("bytes=10-109")
    assert r.status_code == 206 and r.data == DATA[10:110] and r.headers["Content-Length"] == "100"
    assert len(o.calls) == 1


def test_suffix_range_goes_upstream_as_is(origin):
    o = origin()
    get("bytes=-500")
    assert o.calls == ["bytes=-500"]


# ---------- ?fresh= tokens ----------

def test_interleaved_plain_and_fresh_requests_evict_once_per_token(origin, monkeypatch):
    origin()
    evicted = []
    monkeypatch.setattr(streamer, "_invalidate", lambda tid, entry=None: evicted.append(tid) or True)
    for q in ["?fresh=t-1", "", "?fresh=t-1", "", "?fresh=t-1", "?fresh=t-2", "", "?fresh=t-1", "?fresh=t-2", ""]:
        assert get("bytes=0-1", f"/api/stream/{VID}{q}").status_code == 206
    assert evicted == [VID, VID]


def test_fresh_tokens_are_bounded_per_id():
    with streamer._lock:
        streamer._fresh_tokens.clear()
    for i in range(streamer._FRESH_KEEP + 1):
        assert streamer._take_fresh(VID, f"t{i}")
    assert not streamer._take_fresh(VID, f"t{streamer._FRESH_KEEP}")
    assert streamer._take_fresh(VID, "t0")  # the oldest fell out
    assert len(streamer._fresh_tokens[VID]) == streamer._FRESH_KEEP
    assert not streamer._take_fresh(VID, None) and not streamer._take_fresh(VID, "")
    streamer._fresh_tokens.clear()
