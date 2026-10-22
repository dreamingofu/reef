"""Fragmented-MP4 duration fix in the stream proxy. Synthetic boxes only; no network."""
import struct
from pathlib import Path

import pytest

import streamer

DUR = 14176256
SAMPLES = Path("/private/tmp/claude-501/-Users-felipe-orca-workspaces-reef-larvacean/"
               "cfbdd81e-5acf-46ef-a49d-95820f04ea05/scratchpad/dur")


def box(kind, body=b"", large=False):
    if large:
        return struct.pack(">I4sQ", 1, kind, 16 + len(body)) + body
    return struct.pack(">I4s", 8 + len(body), kind) + body


def mvhd(ver, dur=DUR):
    if ver:
        return box(b"mvhd", struct.pack(">B3xQQIQ", 1, 7, 8, 44100, dur) + b"\x01" * 80)
    return box(b"mvhd", struct.pack(">B3xIIII", 0, 7, 8, 44100, dur) + b"\x01" * 80)


def tkhd(ver, dur=DUR):
    if ver:
        return box(b"tkhd", struct.pack(">B3xQQIIQ", 1, 7, 8, 1, 0, dur) + b"\x02" * 60)
    return box(b"tkhd", struct.pack(">B3xIIIII", 0, 7, 8, 1, 0, dur) + b"\x02" * 60)


def mdhd(ver, dur=DUR):
    if ver:
        return box(b"mdhd", struct.pack(">B3xQQIQ", 1, 7, 8, 44100, dur) + b"\x00\x00\x00\x00")
    return box(b"mdhd", struct.pack(">B3xIIII", 0, 7, 8, 44100, dur) + b"\x00\x00\x00\x00")


def mp4(ver=0, fragmented=True, large_moov=False):
    trak = box(b"trak", tkhd(ver) + box(b"mdia", mdhd(ver) + box(b"hdlr", b"\x00" * 25) + box(b"minf", b"\x03" * 12)))
    mvex = box(b"mvex", box(b"trex", b"\x00" * 24)) if fragmented else b""
    moov = box(b"moov", mvhd(ver) + mvex + trak + box(b"udta", b"\x04" * 9), large=large_moov)
    tail = box(b"sidx", b"\x05" * 32) + box(b"moof", b"\x06" * 40) + box(b"mdat", b"\x07" * 100)
    return box(b"ftyp", b"dash\x00\x00\x00\x00iso6mp41") + moov + tail


def durations(buf):
    """Every mvhd/tkhd/mdhd duration found in buf's moov, in file order."""
    found = []

    def walk(start, end):
        for kind, body, stop in streamer._boxes(buf, start, end):
            if kind in streamer._DUR_AT:
                ver = buf[body]
                at = body + streamer._DUR_AT[kind][ver]
                found.append(struct.unpack_from(">Q" if ver else ">I", buf, at)[0])
            elif kind in (b"moov", b"trak", b"mdia"):
                walk(body, stop)

    walk(0, len(buf))
    return found


def diff(a, b):
    return [i for i in range(len(a)) if a[i] != b[i]]


@pytest.mark.parametrize("ver", [0, 1])
@pytest.mark.parametrize("large_moov", [False, True])
def test_fragmented_durations_zeroed(ver, large_moov):
    src = mp4(ver, large_moov=large_moov)
    out = streamer.fix_fragmented_durations(src)
    assert len(out) == len(src)
    assert durations(src) == [DUR] * 3
    assert durations(out) == [0, 0, 0]
    # Only the three duration fields change: DUR is 0x00D85000, two non-zero bytes each.
    assert len(diff(src, out)) == 6


def test_non_fragmented_unchanged():
    src = mp4(fragmented=False)
    assert streamer.fix_fragmented_durations(src) == src


@pytest.mark.parametrize("cut", [30, 100, 200])
def test_truncated_moov_unchanged(cut):
    src = mp4()[:cut]
    assert streamer.fix_fragmented_durations(src) == src


@pytest.mark.parametrize("src", [b"", b"\x00", b"ID3\x04" + b"\xff" * 200, b"\x00\x00\x00\x02moov" + b"\x00" * 40,
                                 b"fLaC" + b"\x00" * 100, struct.pack(">I4sQ", 1, b"moov", 4) + b"\x00" * 40])
def test_garbage_unchanged(src):
    assert streamer.fix_fragmented_durations(src) == src


def test_bad_version_unchanged():
    src = bytearray(mp4())
    src[src.index(b"mvhd") + 4] = 3
    assert streamer.fix_fragmented_durations(bytes(src)) == bytes(src)


@pytest.mark.skipif(not (SAMPLES / "orig.m4a").exists(), reason="scratch YouTube sample not present")
def test_real_youtube_head():
    orig = (SAMPLES / "orig.m4a").read_bytes()[:4096]
    out = streamer.fix_fragmented_durations(orig)
    assert durations(orig) and all(durations(orig))
    assert durations(out) == [0] * len(durations(orig))
    patched = SAMPLES / "patched.m4a"
    if patched.exists():
        ref = patched.read_bytes()[:4096]
        assert all(out[i] == ref[i] for i in diff(orig, ref) + diff(orig, out))


# ---------- through the proxy ----------

class FakeUpstream:
    def __init__(self, body, status=200, headers=None, chunk=7):
        self.body, self.status_code, self.chunk = body, status, chunk
        self.headers = {"Content-Type": "video/mp4", "Content-Length": str(len(body)), **(headers or {})}
        self.closed = False

    def iter_content(self, _size):
        for i in range(0, len(self.body), self.chunk):
            yield self.body[i:i + self.chunk]

    def close(self):
        self.closed = True


@pytest.fixture
def stream(monkeypatch):
    import app

    def run(body, status=200, headers=None, mime="audio/mp4", req_range=None):
        up = FakeUpstream(body, status, headers)
        entry = {"url": "https://example.invalid/a", "headers": {}, "mime": mime, "ext": "m4a"}
        monkeypatch.setattr(streamer, "_resolve", lambda tid, foreground=True: (entry, "hit"))
        monkeypatch.setattr(streamer, "_open", lambda e: (up, 0))
        resp = app.app.test_client().get("/api/stream/FGBhQbmPwH8",
                                         headers={"Range": req_range} if req_range else {})
        return resp, up

    return run


def test_proxy_patches_moov_split_across_chunks(stream):
    src = mp4()
    resp, up = stream(src)
    assert resp.status_code == 200
    assert resp.data != src and len(resp.data) == len(src)
    assert durations(resp.data) == [0, 0, 0]
    assert resp.headers["Content-Length"] == str(len(src))
    assert up.closed


def test_proxy_patches_206_from_zero(stream):
    src = mp4(1)
    resp, _ = stream(src, 206, {"Content-Range": f"bytes 0-{len(src) - 1}/{len(src)}"}, mime=None,
                     req_range="bytes=0-")
    assert resp.status_code == 206
    assert durations(resp.data) == [0, 0, 0]
    assert resp.headers["Content-Range"] == f"bytes 0-{len(src) - 1}/{len(src)}"


def test_proxy_two_byte_probe_passes_through(stream):
    resp, _ = stream(b"\x00\x00", 206, {"Content-Range": "bytes 0-1/5203271"}, req_range="bytes=0-1")
    assert resp.status_code == 206 and resp.data == b"\x00\x00"


def test_proxy_leaves_other_ranges_alone(stream):
    # A body that would be patched if it started at byte 0; a non-zero range start must keep it intact.
    src = mp4()
    resp, _ = stream(src, 206, {"Content-Range": f"bytes 1000-{999 + len(src)}/9999"}, req_range="bytes=1000-")
    assert resp.data == src


def test_proxy_leaves_non_mp4_alone(stream):
    src = mp4()
    resp, _ = stream(src, headers={"Content-Type": "audio/flac"}, mime="audio/flac")
    assert resp.data == src
