"""The tiny-art memory cache (app._tiny_bytes): bounded by entry count and by retained payload bytes."""
import threading

import pytest

import app

KIB = 1024


class Resp:
    def __init__(self, data, status=200, mime="image/jpeg"):
        self.status_code, self.headers, self.data = status, {"Content-Type": mime}, data

    def iter_content(self, n):
        for i in range(0, len(self.data), n):
            yield self.data[i:i + n]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class Session:
    """Answers each URL from `payloads` (bytes, a Resp, an exception, or a callable taking the URL)."""

    def __init__(self, payloads):
        self.payloads, self.calls = payloads, []

    def get(self, url, **kw):
        self.calls.append(url)
        got = self.payloads[url]
        if callable(got):
            got = got(url)
        if isinstance(got, Exception):
            raise got
        return got if isinstance(got, Resp) else Resp(got)


@pytest.fixture
def cache(monkeypatch):
    app._tiny_art.clear()
    yield
    app._tiny_art.clear()


def use(monkeypatch, payloads):
    sess = Session(payloads)
    monkeypatch.setattr(app, "_art_session", sess)
    return sess


def kept():
    return {url: len(data) for url, (data, _) in app._tiny_art.items()}


def test_default_budget_keeps_at_most_32_full_size_images(cache, monkeypatch):
    big = {f"https://x/{n}": bytes([n]) * app.ART_TINY_BYTES for n in range(33)}
    sess = use(monkeypatch, big)
    for url, data in big.items():
        assert app._tiny_bytes(url) == (data, "image/jpeg")
    assert len(app._tiny_art) <= 32 and sum(kept().values()) <= 16 * 1024 * 1024
    assert "https://x/0" not in app._tiny_art and "https://x/32" in app._tiny_art
    assert len(sess.calls) == 33


def test_byte_and_count_bounds_evict_least_recently_used(cache, monkeypatch):
    monkeypatch.setattr(app, "ART_TINY_KEEP", 4)
    monkeypatch.setattr(app, "ART_TINY_BUDGET", 10)
    sizes = {"a": 4, "b": 4, "c": 2, "d": 3, "e": 1, "f": 1}
    sess = use(monkeypatch, {k: k.encode() * n for k, n in sizes.items()})

    def get(k):
        assert app._tiny_bytes(k) == (k.encode() * sizes[k], "image/jpeg")

    for k in "abc":
        get(k)
    assert kept() == {"a": 4, "b": 4, "c": 2}
    get("a")  # a hit: no fetch, now most recent
    get("d")  # 13 bytes: the byte budget drops b (least recent), not a
    assert list(app._tiny_art) == ["c", "a", "d"]
    get("e")  # 10 bytes, 4 entries: both limits met exactly
    assert list(app._tiny_art) == ["c", "a", "d", "e"]
    get("c")  # a hit
    get("f")  # 5 entries: the count limit drops a
    assert list(app._tiny_art) == ["d", "e", "c", "f"]
    assert sess.calls == list("abcdef")


def test_an_image_bigger_than_the_budget_is_served_not_kept(cache, monkeypatch):
    monkeypatch.setattr(app, "ART_TINY_BUDGET", 3)
    use(monkeypatch, {"big": b"x" * 4})
    assert app._tiny_bytes("big") == (b"x" * 4, "image/jpeg")
    assert sum(kept().values()) <= 3


@pytest.mark.parametrize("bad", [Resp(b"x", status=404), Resp(b"<html>", mime="text/html"),
                                 Resp(b"x" * (app.ART_TINY_BYTES + 1)), OSError("network down")])
def test_failed_fetch_leaves_the_cache_as_it_was(cache, monkeypatch, bad):
    sess = use(monkeypatch, {"https://x/ok": b"ok", "https://x/old": b"old", "https://x/bad": bad})
    app._tiny_bytes("https://x/old")
    app._tiny_bytes("https://x/ok")
    before = list(app._tiny_art.items())
    assert app._tiny_bytes("https://x/bad") is None
    assert list(app._tiny_art.items()) == before
    assert app._tiny_bytes("https://x/bad") is None  # a failure isn't cached either
    assert sess.calls.count("https://x/bad") == 2


def _race(monkeypatch, fills, prefill=()):
    """Fill every URL in `prefill`, then run one thread per (url, size) in `fills`; every thread misses the cache
    and waits at a barrier inside the fetch, so all fills land together. Returns [(served, got)] per thread."""
    for url, size in prefill:
        use(monkeypatch, {url: b"p" * size})
        assert app._tiny_bytes(url)
    barrier = threading.Barrier(len(fills), timeout=5)
    served, lock = {}, threading.Lock()

    def answer(url):
        barrier.wait()
        size = sizes[threading.get_ident()]
        data = bytes([size % 251]) * size
        with lock:
            served[threading.get_ident()] = data
        return Resp(data)

    sizes, results = {}, []

    def run(url, size):
        sizes[threading.get_ident()] = size
        got = app._tiny_bytes(url)
        with lock:
            results.append((served[threading.get_ident()], got))

    use(monkeypatch, {url: answer for url, _ in fills})
    threads = [threading.Thread(target=run, args=f) for f in fills]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
        assert not t.is_alive()
    assert len(results) == len(fills)
    return results


@pytest.mark.parametrize("budget", ["default", "small"])
def test_concurrent_fills_keep_both_limits(cache, monkeypatch, budget):
    if budget == "default":
        # 31 full-size images already kept (15.5 MiB), then 4 fills of one URL with different lengths and 4 new URLs
        prefill = [(f"https://x/pre{n}", app.ART_TINY_BYTES) for n in range(31)]
        sizes = [app.ART_TINY_BYTES, 100 * KIB, 300 * KIB, 1 * KIB]
        limit_bytes, limit_count = 16 * 1024 * 1024, 400
    else:
        monkeypatch.setattr(app, "ART_TINY_KEEP", 3)
        monkeypatch.setattr(app, "ART_TINY_BUDGET", 40)
        prefill = [("https://x/pre", 30)]
        sizes = [20, 14, 18, 15]  # any 3 entries exceed 40 bytes
        limit_bytes, limit_count = 40, 3
    fills = [("https://x/same", s) for s in sizes] + [(f"https://x/new{n}", s) for n, s in enumerate(sizes)]
    results = _race(monkeypatch, fills, prefill)
    for served, got in results:
        assert got == (served, "image/jpeg")  # each caller gets the bytes its own fetch read
    assert len(app._tiny_art) <= limit_count
    assert sum(kept().values()) <= limit_bytes
    if "https://x/same" in app._tiny_art:
        assert app._tiny_art["https://x/same"] in [got for _, got in results]
    # still consistent afterwards: a later fill evicts by the actual retained bytes
    use(monkeypatch, {"https://x/after": b"a"})
    assert app._tiny_bytes("https://x/after") == (b"a", "image/jpeg")
    assert len(app._tiny_art) <= limit_count and sum(kept().values()) <= limit_bytes
    assert "https://x/after" in app._tiny_art
