"""Is this the same song? Scores a candidate track against a query (title, artist, duration) for everything that
has to find one song in another catalogue: /api/match (placeholder queue entries, later imports), dead-track
replacement and the lossless upgrade on keep (production plan P1.6; numbers from Meld's SpotifyMapper).

- Normalise for comparison only: lowercase, no accents, no "(feat. X)" / "ft. X" / "[...]" / "(... remaster ...)" /
  "(... remix ...)" / " - Remastered 2011", no video noise ("(Official Video)"), no punctuation. Artist: the first
  one only, without YouTube's " - Topic" / "VEVO", compared without spaces ("RickAstleyVEVO" = "Rick Astley").
- Raw score = 0.45 title + 0.35 artist (Dice bigram similarity) + 0.20 duration (1.0 within 2 s, 0.8 within 5 s,
  0.5 within 10 s, 0.2 within 30 s, else 0; 0.5 when either side doesn't know it). An unknown artist counts 0.5 too.
- Version words (live, acoustic, remix, sped up, ...) must agree: each one only one side has costs 0.15 (at most
  0.30). That only ranks candidates; the accept / early-stop cutoffs read the raw score, so a live take still
  matches when it's all there is.
- A match also needs a title that looks the same: Dice >= 0.6 (0.75 with an unknown artist, 0.9 when the artists
  differ), so a similar artist name can't carry an unrelated title over the raw floor.

`matches.json` (config.MATCHES_FILE) remembers what an external key (a playlist import's row, a chart entry, or
"q:<artist>|<title>") resolved to: {id, score, manual, at, track}. A manual fix ("Wrong song? Paste link") is never
overwritten by the automatic matcher.
"""
import re
import time
import unicodedata

import config
import store
import tagging

W_TITLE, W_ARTIST, W_DURATION = 0.45, 0.35, 0.20
ACCEPT = 0.35          # raw score a candidate needs at all
STOP = 0.95            # a candidate ranked this high ends the scan (search order breaks the remaining ties)
VERSION_PENALTY, VERSION_CAP = 0.15, 0.30
UNKNOWN = 0.5          # duration / artist similarity when one side doesn't know it
# The raw floor alone lets a similar artist name and an unknown length carry an unrelated title (a made-up title
# scored 0.41 live). A match must also look like the same title (Dice of the normalised titles):
# - >= 0.6 when both artists are known and alike: not that artist's other song ("Мечтать" vs "Мечтой", 0.55 live);
# - >= 0.75 when either artist is unknown: the title is all there is ("Around the Sun" vs "Around the World", 0.67);
# - >= 0.9 when the artists differ: "Hello" by Adele is not "Help!" by the Beatles (0.57).
TITLE_MIN, TITLE_MIN_NO_ARTIST, TITLE_MIN_OTHER_ARTIST = 0.6, 0.75, 0.9
ARTIST_SAME = 0.5      # artist Dice from which two artists count as alike for the title floor
_DURATION_STEPS = ((2, 1.0), (5, 0.8), (10, 0.5), (30, 0.2))

# Version markers: a remix is not the song, a lyric video usually is (but ranks below the plain upload).
VERSION_WORDS = {
    "live": r"live", "acoustic": r"acoustic", "remix": r"remix(?:ed)?", "sped up": r"sped[\s-]*up",
    "nightcore": r"nightcore", "slowed": r"slowed", "instrumental": r"instrumental", "karaoke": r"karaoke",
    "cover": r"cover", "demo": r"demo", "8d": r"8d", "music video": r"music\s+video", "lyric video": r"lyrics?\s+video",
}
_VERSION_RX = {k: re.compile(r"(?<![\w])" + rx + r"(?![\w])", re.I) for k, rx in VERSION_WORDS.items()}

_FEAT_GROUP = re.compile(r"[(\[]\s*(?:feat\.?|ft\.?|featuring|with)\s[^)\]]*[)\]]", re.I)
_FEAT_TAIL = re.compile(r"\s+(?:feat\.?|ft\.?|featuring)\s.*$", re.I)
_SQUARE = re.compile(r"\[[^\[\]]*\]")
# Remaster / remix notes and version groups ("(Live at the BBC)", " - Acoustic") leave the compared title: the
# version words already rank them (VERSION_PENALTY); as title text they'd also sink a live take under the title floor.
_REWORKS = r"\b(?:remaster(?:ed)?|remix(?:ed)?|" + "|".join(rx for rx in VERSION_WORDS.values()) + r")\b"
_REWORK_GROUP = re.compile(r"\([^()]*" + _REWORKS + r"[^()]*\)", re.I)
_REWORK_TAIL = re.compile(r"\s+[-–]\s+[^-–]*" + _REWORKS + r"[^-–]*$", re.I)
_ARTIST_SPLIT = re.compile(r"\s*(?:,|&|;|/|\s+x\s+|\s+(?:feat\.?|ft\.?|featuring|with|and|vs\.?)\s+)\s*", re.I)
_SEP = re.compile(r"\s+[-–]\s+")
_REWORK_WORD = re.compile(r"\b(?:remaster(?:ed)?|edit|version|mix)\b", re.I)


# ---------- normalising ----------

def _fold(text):
    """Lowercase, accents off (for mostly-Latin text), punctuation to spaces, spaces collapsed."""
    text = str(text or "")
    if not text.isascii() and _latin(text):
        text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^\w\s]|_", " ", text.lower())
    return " ".join(text.split())


def _latin(text):
    """True when dropping accents keeps the text readable (it's mostly Latin): "Andrés" -> "andres", but a
    Japanese or Cyrillic title must keep its letters, or every such title would compare equal (empty)."""
    letters = [c for c in str(text) if c.isalpha()]
    return bool(letters) and sum(c.isascii() or "LATIN" in unicodedata.name(c, "") for c in letters) >= len(letters) * 0.8


def norm_title(title):
    """The title as compared: no featured artists, brackets, remaster/remix notes, video noise or punctuation."""
    t = tagging._strip_noise(str(title or ""))  # "(Official Video)", "[HD]": the same song as the audio upload
    t = _FEAT_GROUP.sub(" ", t)
    t = _SQUARE.sub(" ", t)
    t = _REWORK_GROUP.sub(" ", t)
    t = _REWORK_TAIL.sub("", t)
    t = _FEAT_TAIL.sub("", t)
    return _fold(t) or _fold(title)


def first_artist(artist):
    """The first of "A, B & C feat. D" without YouTube's " - Topic" / "VEVO" suffixes."""
    name = tagging.clean_artist(_ARTIST_SPLIT.split(str(artist or "").strip(), maxsplit=1)[0])
    return name or tagging.clean_artist(artist)


def norm_artist(artist):
    return _fold(first_artist(artist))


def versions(*texts):
    """The version words found in any of `texts` (raw, before normalising)."""
    joined = " ".join(str(t or "") for t in texts)
    return {k for k, rx in _VERSION_RX.items() if rx.search(joined)}


# ---------- similarity ----------

def _bigrams(s):
    s = s.replace(" ", "")
    return [s[i:i + 2] for i in range(len(s) - 1)]


def dice(a, b):
    """Sørensen-Dice coefficient over character bigrams (spaces ignored), 0..1."""
    a, b = (a or "").replace(" ", ""), (b or "").replace(" ", "")
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    x, y = _bigrams(a), _bigrams(b)
    if not x or not y:
        return 0.0
    pool = {}
    for g in x:
        pool[g] = pool.get(g, 0) + 1
    shared = 0
    for g in y:
        if pool.get(g):
            pool[g] -= 1
            shared += 1
    return 2.0 * shared / (len(x) + len(y))


def _seconds(value):
    s = store.seconds(value)
    return s if s else None


def duration_score(a, b):
    a, b = _seconds(a), _seconds(b)
    if a is None or b is None:
        return UNKNOWN
    gap = abs(a - b)
    for limit, value in _DURATION_STEPS:
        if gap <= limit:
            return value
    return 0.0


# ---------- tracks ----------

def fields(track):
    """{title, artist, duration, album} of a query or candidate dict. The artist is the first of `artists`
    ([{name}] or [str]) when there is a list, else `artist`, else `channel`."""
    t = track if isinstance(track, dict) else {}
    artist = ""
    names = t.get("artists")
    if isinstance(names, list):
        for a in names:
            name = a.get("name") if isinstance(a, dict) else a
            if isinstance(name, str) and name.strip():
                artist = name.strip()
                break
    if not artist:
        artist = str(t.get("artist") or t.get("channel") or "").strip()
    return {"title": str(t.get("title") or "").strip(), "artist": artist,
            "duration": _seconds(t.get("duration")), "album": str(t.get("album") or "").strip()}


def query_of(track):
    """The query for finding `track` elsewhere. A YouTube video without an artist list is "Artist - Song
    (Official Video)" from some channel: split like a keep tags it, unless the dash only separates a version
    ("Song - Remix") or the left side is the channel itself."""
    q = fields(track)
    t = track if isinstance(track, dict) else {}
    tid = store.track_id(t.get("id"))
    if t.get("artists") or not tid or store.source_of(tid) != "yt" or not q["title"]:
        return q
    parts = _SEP.split(q["title"])
    if len(parts) == 2 and not versions(parts[1]) and not _REWORK_WORD.search(parts[1]):
        left, right = parts[0].strip(), parts[1].strip()
        same = norm_artist(left).replace(" ", "") == norm_artist(q["artist"]).replace(" ", "")
        # "Daft Punk - Around the World" from "Some Reuploader": the title names the artist, not the channel.
        q["artist"], q["title"] = (q["artist"] if same else left), right
    q["artist"] = tagging.clean_artist(q["artist"])
    q["title"] = tagging._strip_noise(q["title"]) or q["title"]
    return q


def search_text(query):
    """What to type into a search box for `query`: the title without featured artists or bracketed notes,
    then the first artist."""
    q = fields(query)
    title = _FEAT_TAIL.sub("", _SQUARE.sub(" ", _FEAT_GROUP.sub(" ", q["title"])))
    return " ".join(" ".join((title, first_artist(q["artist"]))).split())


def _compare(query, candidate):
    """(raw, ranked, title similarity, artist similarity or None when either side has no artist)."""
    q, c = fields(query), fields(candidate)
    title = dice(norm_title(q["title"]), norm_title(c["title"]))
    qa, ca = norm_artist(q["artist"]), norm_artist(c["artist"])
    artist = dice(qa, ca) if qa and ca else None
    raw = W_TITLE * title + W_ARTIST * (UNKNOWN if artist is None else artist) \
        + W_DURATION * duration_score(q["duration"], c["duration"])
    differ = versions(q["title"]) ^ versions(c["title"])
    penalty = min(VERSION_CAP, VERSION_PENALTY * len(differ))
    return round(raw, 4), round(raw - penalty, 4), title, artist


def title_floor(artist):
    """The title similarity a match needs, given the artist similarity (None = unknown)."""
    if artist is None:
        return TITLE_MIN_NO_ARTIST
    return TITLE_MIN if artist >= ARTIST_SAME else TITLE_MIN_OTHER_ARTIST


def score(query, candidate):
    """(raw, ranked): raw in 0..1 is what the cutoffs read; ranked = raw minus the version-word penalty."""
    return _compare(query, candidate)[:2]


def plausible(query, candidate):
    """Whether `candidate` can be the same song at all: its title is close enough (see TITLE_MIN)."""
    _, _, title, artist = _compare(query, candidate)
    return title >= title_floor(artist)


def best(query, candidates, accept=ACCEPT, stop=STOP, exclude=(), same_artist=False):
    """(candidate, raw score) of the best-ranked candidate whose raw score reaches `accept` and whose title clears
    the title floor, else None. `same_artist` also rejects a known artist unlike the query's (a dead-track
    replacement plays instead of what the user picked: same title by someone else is a cover or another song).
    Candidates come in search order; one ranked at `stop` or better ends the scan. Ids in `exclude` (e.g. the
    dead track being replaced) never match."""
    skip = set(exclude or ())
    top = None  # (ranked, raw, candidate)
    for c in candidates or []:
        if not isinstance(c, dict) or c.get("id") in skip:
            continue
        raw, ranked, title, artist = _compare(query, c)
        if raw < accept or title < title_floor(artist) or (same_artist and artist is not None and artist < ARTIST_SAME):
            continue
        if top is None or ranked > top[0]:
            top = (ranked, raw, c)
        if ranked >= stop:
            break
    return (top[2], top[1]) if top else None


# ---------- matches.json: external key -> {id, score, manual, at, track} ----------

KEY_MAX = 200
AUTO_TTL = 30 * 86400  # an automatic match is re-checked after this (YouTube ids die); a manual one never
CACHE_MAX = 5000

_file = store.JsonFile(lambda: config.MATCHES_FILE, dict)


def valid_key(key):
    return isinstance(key, str) and 0 < len(key) <= KEY_MAX and key.isprintable() and "/" not in key


def key_for(title, artist):
    """The key of a query without an external one: same song, same key, whatever its punctuation."""
    return ("q:" + norm_artist(artist) + "|" + norm_title(title))[:KEY_MAX]


def cached(key, now=None):
    """The entry for `key` (a copy), or None when there is none or an automatic one is too old."""
    with _file.lock:
        e = _file.load().get(key)
        if not isinstance(e, dict) or not store.track_id(e.get("id")):
            return None
        if not e.get("manual") and (now or time.time()) - (e.get("at") or 0) > AUTO_TTL:
            return None
        return dict(e)


def remember(key, track, score=None, manual=False, now=None):
    """Store what `key` matched. An automatic result never replaces a manual fix: returns the entry kept."""
    clean = store.clean_track(track)
    if not valid_key(key) or not clean:
        return None
    with _file.lock:
        data = dict(_file.load())
        old = data.get(key)
        if not manual and isinstance(old, dict) and old.get("manual"):
            return dict(old)
        data[key] = {"id": clean["id"], "score": score, "manual": bool(manual), "at": now or time.time(),
                     "track": clean}
        if len(data) > CACHE_MAX:  # oldest automatic matches go first; manual fixes stay
            def at(k):
                return data[k].get("at") or 0 if isinstance(data[k], dict) else 0
            autos = sorted((k for k, v in data.items() if not (isinstance(v, dict) and v.get("manual"))), key=at)
            for k in autos[:len(data) - CACHE_MAX]:
                del data[k]
        _file.save(data)
        return dict(data[key])
