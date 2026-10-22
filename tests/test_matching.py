"""matching.py: normalising, scores, version words, best(), the matches.json cache (production plan P1.6)."""
import pytest

import config
import matching

# ---------- normalising ----------


@pytest.mark.parametrize("raw, want", [
    ("Despacito", "despacito"),
    ("Despacito (feat. Daddy Yankee)", "despacito"),
    ("Despacito (ft. Daddy Yankee)", "despacito"),
    ("Despacito feat. Daddy Yankee", "despacito"),
    ("Despacito [Official Video]", "despacito"),
    ("Despacito (Official Music Video)", "despacito"),
    ("Bohemian Rhapsody (Remastered 2011)", "bohemian rhapsody"),
    ("Bohemian Rhapsody - Remastered 2011", "bohemian rhapsody"),
    ("Titanium (David Guetta Remix)", "titanium"),
    ("Don't Stop Me Now", "don t stop me now"),
    ("Beyoncé", "beyonce"),
    ("Мечтать", "мечтать"),  # non-Latin letters stay (they'd all compare equal otherwise)
    ("Song (Live)", "song"),  # version groups go too: the version words rank them instead
    ("Song - Acoustic Version", "song"),
    ("Live and Let Die", "live and let die"),  # a version word in the title itself stays
])
def test_norm_title(raw, want):
    assert matching.norm_title(raw) == want


@pytest.mark.parametrize("raw, want", [
    ("Daft Punk - Topic", "daft punk"),
    ("RickAstleyVEVO", "rickastley"),
    ("Luis Fonsi, Daddy Yankee", "luis fonsi"),
    ("Calvin Harris & Dua Lipa", "calvin harris"),
    ("Calvin Harris feat. Rihanna", "calvin harris"),
    ("Lil Nas X", "lil nas x"),  # " x " separates artists only between words, not a trailing X
    ("", ""),
])
def test_norm_artist(raw, want):
    assert matching.norm_artist(raw) == want


@pytest.mark.parametrize("text, want", [
    ("Song (Live at Wembley)", {"live"}),
    ("Song - Acoustic Version", {"acoustic"}),
    ("Song (Sped Up)", {"sped up"}),
    ("Song (slowed + reverb)", {"slowed"}),
    ("Song (Nightcore)", {"nightcore"}),
    ("Song (Instrumental)", {"instrumental"}),
    ("Song (Karaoke Version)", {"karaoke"}),
    ("Song (Cover)", {"cover"}),
    ("Song (Demo)", {"demo"}),
    ("Song (8D Audio)", {"8d"}),
    ("Song (Official Music Video)", {"music video"}),
    ("Song (Lyric Video)", {"lyric video"}),
    ("Song (Remixed)", {"remix"}),
    ("Alive", set()),  # whole words only
    ("Coverage", set()),
])
def test_versions(text, want):
    assert matching.versions(text) == want


def test_dice():
    assert matching.dice("night", "night") == 1.0
    assert matching.dice("night", "nacht") == 0.25  # ni gh ht vs na ac ch ht: one shared of 8
    assert matching.dice("", "x") == 0.0 and matching.dice("a", "b") == 0.0
    assert matching.dice("rick astley", "rickastley") == 1.0  # spaces don't count


@pytest.mark.parametrize("a, b, want", [
    (200, 200, 1.0), (200, 202, 1.0), (200, 205, 0.8), (200, 190, 0.5), (200, 230, 0.2), (200, 231, 0.0),
    (None, 200, 0.5), (200, 0, 0.5), ("200", 200, 1.0),
])
def test_duration_buckets(a, b, want):
    assert matching.duration_score(a, b) == want


# ---------- scores ----------

Q = {"title": "Despacito", "artist": "Luis Fonsi", "duration": 229}


@pytest.mark.parametrize("cand, raw, ranked", [
    # same song, featured artist and artist list
    ({"title": "Despacito (feat. Daddy Yankee)", "artists": [{"name": "Luis Fonsi"}, {"name": "Daddy Yankee"}],
      "duration": 230}, 1.0, 1.0),
    # Topic channel, video noise, length off by 4 s
    ({"title": "Despacito (Official Video)", "channel": "Luis Fonsi - Topic", "duration": 233}, 0.96, 0.96),
    # a remix: same normalised title, but one version word only it has (ranking only)
    ({"title": "Despacito - Remix", "channel": "Luis Fonsi", "duration": 229}, 1.0, 0.85),
    # unknown length
    ({"title": "Despacito", "channel": "Luis Fonsi"}, 0.9, 0.9),
    # another song entirely
    ({"title": "Gangnam Style", "channel": "PSY", "duration": 219}, 0.1, 0.1),
])
def test_score_table(cand, raw, ranked):
    got = matching.score(Q, cand)
    assert got == pytest.approx((raw, ranked), abs=0.02)


def test_version_penalty_is_capped():
    raw, ranked = matching.score({"title": "Song"}, {"title": "Song (Live Acoustic Karaoke Cover)"})
    assert round(raw - ranked, 4) == matching.VERSION_CAP


def test_unknown_artist_counts_half():
    raw, _ = matching.score({"title": "Song", "duration": 100}, {"title": "Song", "channel": "X", "duration": 100})
    assert raw == pytest.approx(0.45 + 0.35 * 0.5 + 0.2)


def test_best_picks_ranked_but_cuts_on_raw():
    remix = {"id": "aaaaaaaaaaa", "title": "Despacito - Remix", "channel": "Luis Fonsi", "duration": 229}
    live = {"id": "bbbbbbbbbbb", "title": "Despacito (Live)", "channel": "Luis Fonsi", "duration": 260}
    plain = {"id": "ccccccccccc", "title": "Despacito", "channel": "Luis Fonsi", "duration": 231}
    assert matching.best(Q, [remix, live, plain])[0] is plain
    # the remix still matches when it's all there is (the penalty only ranks)
    assert matching.best(Q, [remix]) == (remix, 1.0)
    assert matching.best(Q, [{"id": "d" * 11, "title": "Gangnam Style", "channel": "PSY"}]) is None
    assert matching.best(Q, [plain], exclude={"ccccccccccc"}) is None
    assert matching.best(Q, []) is None and matching.best(Q, None) is None


def test_best_stops_early_at_stop():
    seen = []

    class Spy(dict):
        def get(self, k, d=None):
            if k == "id":
                seen.append(self["id"])
            return super().get(k, d)
    first = Spy(id="aaaaaaaaaaa", title="Despacito", channel="Luis Fonsi", duration=229)
    second = Spy(id="bbbbbbbbbbb", title="Despacito", channel="Luis Fonsi", duration=229)
    assert matching.best(Q, [first, second])[0] is first
    assert seen == ["aaaaaaaaaaa"]  # never looked at the second


def test_accept_threshold():
    weak = {"id": "aaaaaaaaaaa", "title": "Despacitos", "channel": "Someone", "duration": 400}
    raw, _ = matching.score(Q, weak)
    assert matching.best(Q, [weak], accept=raw) is not None
    assert matching.best(Q, [weak], accept=raw + 0.01) is None


@pytest.mark.parametrize("track, title, artist", [
    ({"id": "dQw4w9WgXcQ", "title": "Rick Astley - Never Gonna Give You Up (Official Video)", "channel": "RickAstleyVEVO"},
     "Never Gonna Give You Up", "RickAstley"),
    ({"id": "dQw4w9WgXcQ", "title": "Daft Punk - Around the World", "channel": "Some Reuploader"},
     "Around the World", "Daft Punk"),
    ({"id": "dQw4w9WgXcQ", "title": "Despacito - Remix", "channel": "Luis Fonsi"}, "Despacito - Remix", "Luis Fonsi"),
    ({"id": "dQw4w9WgXcQ", "title": "A - B", "artists": [{"name": "Z"}]}, "A - B", "Z"),  # YT Music: as is
    ({"id": "mc:1", "title": "A - B", "channel": "Z"}, "A - B", "Z"),
    ({"title": "A - B", "artist": "Z"}, "A - B", "Z"),  # a placeholder (no playable id)
])
def test_query_of(track, title, artist):
    q = matching.query_of(track)
    assert (q["title"], q["artist"]) == (title, artist)


def test_search_text_and_key():
    assert matching.search_text({"title": "Despacito (feat. Daddy Yankee) [Video]", "artist": "Luis Fonsi, X"}) == \
        "Despacito Luis Fonsi"
    assert matching.key_for("Despacito (feat. X)", "Luis Fonsi - Topic") == matching.key_for("despacito!", "Luis Fonsi")
    assert matching.valid_key("spotify:track:abc") and not matching.valid_key("a/b") and not matching.valid_key("")
    assert not matching.valid_key("x" * 201) and not matching.valid_key("a\nb") and not matching.valid_key(3)


# ---------- matches.json ----------

@pytest.fixture
def cache(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "MATCHES_FILE", tmp_path / "matches.json")
    return tmp_path / "matches.json"


T1 = {"id": "aaaaaaaaaaa", "title": "One", "channel": "A", "duration": 100, "thumb": "x"}
T2 = {"id": "bbbbbbbbbbb", "title": "Two", "channel": "B", "duration": 100}


def test_cache_roundtrip_and_ttl(cache):
    e = matching.remember("k", T1, 0.9, now=1000)
    assert e["id"] == "aaaaaaaaaaa" and e["score"] == 0.9 and e["manual"] is False
    assert e["track"] == {"id": "aaaaaaaaaaa", "title": "One", "channel": "A", "duration": 100, "source": "yt"}
    assert matching.cached("k", now=1000 + matching.AUTO_TTL)["id"] == "aaaaaaaaaaa"
    assert matching.cached("k", now=1001 + matching.AUTO_TTL) is None  # an automatic match gets re-checked
    assert matching.cached("nope") is None
    assert cache.exists()


def test_manual_fix_is_never_overwritten(cache):
    matching.remember("k", T1, 0.9, now=1000)
    assert matching.remember("k", T2, None, manual=True, now=1001)["id"] == "bbbbbbbbbbb"
    kept = matching.remember("k", T1, 0.99, now=1002)  # the automatic matcher again
    assert kept["id"] == "bbbbbbbbbbb" and kept["manual"] is True
    assert matching.cached("k", now=1002 + 10 * matching.AUTO_TTL)["id"] == "bbbbbbbbbbb"  # never expires
    assert matching.remember("k", T1, None, manual=True)["id"] == "aaaaaaaaaaa"  # a new manual fix replaces it


def test_cache_rejects_bad_input(cache):
    assert matching.remember("k", {"id": "nope"}, 0.5) is None
    assert matching.remember("a/b", T1, 0.5) is None
    assert not cache.exists()


def test_cache_cap_drops_oldest_automatic(cache, monkeypatch):
    monkeypatch.setattr(matching, "CACHE_MAX", 3)
    matching.remember("manual", T2, None, manual=True, now=1)
    for i in range(4):
        matching.remember(f"k{i}", T1, 0.5, now=10 + i)
    data = matching._file.load()
    assert set(data) == {"manual", "k2", "k3"}


# ---------- title floor (no unrelated song over the raw floor) ----------

def _c(title, artist="", duration=None, id_="aaaaaaaaaaa"):
    return {"id": id_, "title": title, "channel": artist, "duration": duration}


@pytest.mark.parametrize("query, cand, matches", [
    # the live case: a made-up title scored 0.41 against an unrelated song on artist-name likeness
    ({"title": "Zzqx nonexistent song", "artist": "Nobody Qq"}, _c("Quiecent", "Nobody._935"), False),
    # the same songs, spelled differently
    ({"title": "Bohemian Rhapsody - Remastered 2011", "artist": "Queen", "duration": 354},
     _c("Bohemian Rhapsody", "Queen", 355), True),
    ({"title": "Despacito (feat. Daddy Yankee)", "artist": "Luis Fonsi"}, _c("Despacito", "Luis Fonsi - Topic"), True),
    ({"title": "Dont Stop Me Now", "artist": "Queen"}, _c("Don't Stop Me Now", "Queen"), True),
    # short titles
    ({"title": "Hello", "artist": "Adele", "duration": 295}, _c("Hello", "Adele", 295), True),
    ({"title": "Help!", "artist": "The Beatles"}, _c("Help! (Remastered 2009)", "The Beatles"), True),
    ({"title": "Hello", "artist": "Adele"}, _c("Help!", "The Beatles"), False),  # titles 0.57 alike, artists not
    ({"title": "Hello", "artist": "Adele"}, _c("Hello (Live at the BBC)", "Adele"), True),  # a version: ranks lower
    ({"title": "Up", "artist": "Cardi B"}, _c("Up", "Cardi B"), True),
    ({"title": "Up", "artist": "Cardi B"}, _c("Upside Down", "Cardi B"), False),
    # non-Latin titles
    ({"title": "Мечтать", "artist": "Земфира"}, _c("Мечтать", "Земфира"), True),
    ({"title": "Мечтать", "artist": "Земфира"}, _c("мечтой", "Земфира"), False),  # her other song (live: 0.55)
    ({"title": "Hello", "artist": "Adele"}, _c("Help", "Adele"), False),  # same artist, another title (0.57)
    ({"title": "夜に駆ける", "artist": "YOASOBI"}, _c("夜に駆ける", "YOASOBI"), True),
    ({"title": "夜に駆ける", "artist": "YOASOBI"}, _c("群青", "YOASOBI"), False),
    ({"title": "愛", "artist": "X"}, _c("愛", "X"), True),
    # unknown artist on one side: the title has to carry it (>= 0.75)
    ({"title": "Around the World"}, _c("Around the World", "Daft Punk", 429), True),
    ({"title": "Around the World"}, _c("Around the Sun", "R.E.M."), False),
    # another artist: only a near-identical title (a cover of the same song still matches)
    ({"title": "Hurt", "artist": "Nine Inch Nails"}, _c("Hurt", "Johnny Cash"), True),
    ({"title": "Blinding Lights", "artist": "The Weeknd"}, _c("Blinding Light", "Someone Else"), True),
    ({"title": "Blinding Lights", "artist": "The Weeknd"}, _c("Binding Lies", "Someone Else"), False),
])
def test_title_floor_table(query, cand, matches):
    assert (matching.best(query, [cand]) is not None) == matches
    assert matching.plausible(query, cand) == matches or matching.score(query, cand)[0] < matching.ACCEPT


def test_title_floor_values():
    assert matching.title_floor(None) == 0.75
    assert matching.title_floor(0.5) == 0.6 and matching.title_floor(1.0) == 0.6
    assert matching.title_floor(0.49) == 0.9


def test_same_artist_mode_for_replacements():
    q = {"title": "Мечтать", "artist": "Земфира"}
    other = _c("Мечтать", "Владимир Клявин")  # live: same title, another artist
    assert matching.best(q, [other]) is not None  # a plain match allows it (covers, transliterated names)
    assert matching.best(q, [other], same_artist=True) is None
    assert matching.best(q, [_c("Мечтать", "Земфира")], same_artist=True) is not None
    assert matching.best({"title": "Мечтать"}, [other], same_artist=True) is not None  # artist unknown: title decides
