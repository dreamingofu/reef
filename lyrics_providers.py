"""Lyrics sources after LRCLIB, for the lyrics view only (production plan P1.3), and the text checks every
source's answer goes through. Kept files are still tagged with LRCLIB lyrics alone (lyrics.embed).

- SimpMusic: `GET https://api-lyrics.simpmusic.org/v1/{videoId}`, keyed by the YouTube id (no title matching).
- YouTube Music's own lyrics (ytmusic.lyrics): plain, sometimes line-timed. Needs a YouTube id too.
- BetterLyrics: `GET https://lyrics-api.boidu.dev/getLyrics?s=&a=&al=&d=` -> {"ttml"}, word-timed when it has them.
  401 with "Uncached ... API key" means "not in its cache": no lyrics, not a block.

A fetcher takes the lookup `q` ({video_id, title, artist, album, duration}) and a timeout and returns
{"synced", "plain", "instrumental", "words"} or None (it has none). It raises Blocked on 401/403/429 (lyrics.py
pauses that source for 10 min) and Unavailable / requests.RequestException on network trouble or a 5xx (a short
pause, and the lookup isn't cached as a miss).

`words` is per synced line, aligned with the lines the client's parseLRC reads from `synced` (every stamped line,
blank ones too, sorted by time): [[{t, d, text}, ...], ...] with t/d in seconds; text carries its trailing space.
"""
import logging
import re
import xml.etree.ElementTree as ET

import requests

import ytmusic

logger = logging.getLogger(__name__)

SIMPMUSIC = "https://api-lyrics.simpmusic.org/v1/"
BETTERLYRICS = "https://lyrics-api.boidu.dev/getLyrics"
_HEADERS = {"User-Agent": "Reef (https://github.com/dreamingofu/reef)"}
_session = requests.Session()  # its own pool: LRCLIB's session carries LRCLIB's retries and tests' fakes

MIN_SPAN_S = 10      # a synced text whose stamps span less than this is not timed to the song (all at 0:00, ...)
LATE_S = 10          # ... nor one whose first line starts this long after the track has ended
DUR_SLACK_S = 10     # BetterLyrics' TTML says how long its recording is: further off than this, it's another one
TTML_MAX = 2_000_000
_MIN_LINES = 3
_EDGE = 3            # credit lines are looked for among the first and last few lines only


class Blocked(Exception):
    """401 / 403 / 429: the source refuses us for now."""

    def __init__(self, source, status):
        super().__init__(f"{source} answered {status}")
        self.status = status


class Unavailable(Exception):
    """Network trouble or a 5xx: try again soon, and don't remember the lookup as a miss."""


# ---------- text checks ----------

_STAMP = re.compile(r"^\[(\d{1,3}):(\d{1,2})(?:[.:](\d{1,3}))?\]\s*")  # the client's parseLRC STAMP
_BAD_STAMP = re.compile(r"\[(\d{1,3}):(\d{2}):(\d{2,3})\]")             # [mm:ss:xx] -> [mm:ss.xx]
_OFFSET = re.compile(r"^\[offset:\s*([+-]?\d+)\s*\]$", re.I)
_LEADING = re.compile(r"^(\s*\[[^\]]*\])+")
_WORD_TAG = re.compile(r"<(\d{1,3}):(\d{1,2})(?:[.:](\d{1,3}))?>")
# "Synced by X", "Lyrics by X", "Written by X", "Lyrics & music by X", "Lyrics from X", "Source: X", Chinese
# "作词 : X" credits. Only near the start or end: "Written by the river" mid-song is a lyric.
_CREDIT = re.compile(
    r"^\s*(?:(?:synced|lyrics?|written|composed|composer|arranged|produced|music|words|lrc|transcribed|timed|"
    r"edited|translated|translation|subtitles?)(?:\s*(?:&|and|/)\s*\w+)?\s+by\b|lyrics?\s+(?:from|provided\s+by)\b|"
    r"source\s*:|作词|作詞|作曲|编曲|編曲)", re.I)


def _stamp_s(m):
    return int(m[1]) * 60 + int(m[2]) + (int(m[3]) / 10 ** len(m[3]) if m[3] else 0)


def _line_text(raw):
    return _WORD_TAG.sub("", _LEADING.sub("", raw)).strip()


def _credit_lines(texts):
    """Indexes of credit lines among the first and last _EDGE non-empty entries of `texts`."""
    filled = [i for i, t in enumerate(texts) if t.strip()]
    edge = filled[:_EDGE] + filled[-_EDGE:]
    return {i for i in edge if _CREDIT.match(texts[i])}


def clean(text):
    """`text` with [mm:ss:xx] stamps fixed and credit lines dropped; other lines are kept verbatim."""
    if not isinstance(text, str):
        return text
    lines = [_BAD_STAMP.sub(r"[\1:\2.\3]", ln) for ln in text.splitlines()]
    drop = _credit_lines([_line_text(ln) for ln in lines])
    return "\n".join(ln for i, ln in enumerate(lines) if i not in drop)


def enough(text):
    """The text, or None when it is too thin to be a song (crowd-edited junk like "[00:00.00]probe")."""
    if not isinstance(text, str):
        return None
    return text if sum(1 for ln in text.splitlines() if _line_text(ln)) >= _MIN_LINES else None


def parse_lrc(text):
    """[(t seconds, text, raw rest of the line)] the way the client's parseLRC reads it: every stamp of a line,
    blank lines too, [offset:] applied (positive = sooner), sorted by time (stable)."""
    out, offset = [], 0.0
    for raw in str(text or "").splitlines():
        line = raw.strip()
        off = _OFFSET.match(line)
        if off:
            offset = int(off[1]) / 1000
            continue
        times = []
        while (m := _STAMP.match(line)):
            times.append(_stamp_s(m))
            line = line[m.end():]
        for t in times:
            out.append([t, _WORD_TAG.sub("", line).strip(), line])
    for row in out:
        row[0] = max(0.0, row[0] - offset)
    return [tuple(r) for r in sorted(out, key=lambda r: r[0])]


def synced_ok(text, duration=None):
    """Whether a synced text is timed to this recording: its lyric lines span >= MIN_SPAN_S and the first one
    doesn't start after the track (+ LATE_S)."""
    times = [t for t, line, _ in parse_lrc(text) if line]
    if len(times) < 2 or times[-1] - times[0] < MIN_SPAN_S:
        return False
    return not (duration and times[0] > float(duration) + LATE_S)


def plain_of(text):
    """The words of an LRC text without its stamps (a synced text that failed synced_ok still has the words)."""
    lines = [_line_text(ln) for ln in str(text or "").splitlines() if not _OFFSET.match(ln.strip())]
    out = "\n".join(lines).strip()
    return out or None


def words_from_lrc(text):
    """Word timing from enhanced LRC ("[00:12.00]<00:12.00>Hey <00:12.40>you"), aligned with parse_lrc, or
    None when no line has word tags."""
    rows, any_tags = [], False
    for t, line, rest in parse_lrc(text):
        tags = list(_WORD_TAG.finditer(rest))
        if not tags:
            rows.append([{"t": round(t, 3), "text": line}] if line else [])
            continue
        any_tags, words = True, []
        for k, m in enumerate(tags):
            end = tags[k + 1].start() if k + 1 < len(tags) else len(rest)
            word = rest[m.end():end]
            if not word.strip():
                continue
            nxt = _stamp_s(tags[k + 1]) if k + 1 < len(tags) else None
            w = {"t": round(_stamp_s(m), 3), "text": word.lstrip()}
            if nxt is not None and nxt > _stamp_s(m):
                w["d"] = round(nxt - _stamp_s(m), 3)
            words.append(w)
        if words:
            words[-1]["text"] = words[-1]["text"].rstrip()
        rows.append(words)
    return rows if any_tags else None


def lrc_stamp(t):
    cs = max(0, round(t * 100))
    return f"[{cs // 6000:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}]"


def result(synced=None, plain=None, instrumental=False, words=None, duration=None):
    """The checked answer of any source, or None. Synced text that isn't timed to the song is kept as plain."""
    synced, plain = enough(clean(synced)), enough(clean(plain))
    if synced and not synced_ok(synced, duration):
        plain, synced, words = plain or enough(plain_of(synced)), None, None
    if synced and words is not None and len(words) != len(parse_lrc(synced)):
        words = None  # alignment is the client's contract: no words rather than words on the wrong lines
    if words is not None and not any(len(w) > 1 or (w and "d" in w[0]) for w in words):
        words = None  # line timing only
    if not (synced or plain or instrumental):
        return None
    return {"synced": synced, "plain": plain, "instrumental": bool(instrumental), "words": words}


def _status(r, source):
    """Raise for the statuses that aren't an answer: Blocked on 401/403/429, Unavailable on a 5xx."""
    if r.status_code in (401, 403, 429):
        raise Blocked(source, r.status_code)
    if r.status_code >= 500:
        raise Unavailable(f"{source} answered {r.status_code}")


# ---------- SimpMusic ----------

def simpmusic(q, timeout):
    vid = q.get("video_id")
    if not vid:
        return None
    r = _session.get(SIMPMUSIC + vid, headers=_HEADERS, timeout=timeout)
    if r.status_code == 404:
        return None
    _status(r, "SimpMusic")
    if r.status_code >= 400:
        return None
    try:
        body = r.json()
    except ValueError:
        raise Unavailable("SimpMusic answered with something other than JSON") from None
    items = body.get("data") if isinstance(body, dict) else body
    if isinstance(items, dict):
        items = [items]
    best = None
    for i, it in enumerate(items if isinstance(items, list) else []):
        if not isinstance(it, dict):
            continue
        rich = it.get("richSyncLyrics") if isinstance(it.get("richSyncLyrics"), str) else None
        words = words_from_lrc(rich) if rich else None
        synced = rich if words else it.get("syncedLyrics")
        found = result(synced if isinstance(synced, str) else None,
                       it.get("plainLyric") or it.get("plainLyrics"), False, words, q.get("duration"))
        if not found:
            continue
        try:
            votes = float(it.get("vote") or 0)
        except (TypeError, ValueError):
            votes = 0.0
        rank = (not found["words"], not found["synced"], -votes, i)  # word-timed, synced, most voted, first
        if best is None or rank < best[0]:
            best = (rank, found)
    return best[1] if best else None


# ---------- YouTube Music ----------

_REFUSED = re.compile(r"\b(401|403|429)\b")
_SERVER_ERROR = re.compile(r"\bHTTP 5\d\d\b")


def youtube(q, timeout):
    vid = q.get("video_id")
    if not vid or not ytmusic.enabled():
        return None
    try:
        found = ytmusic.lyrics(vid)
    except Exception as e:
        m = _REFUSED.search(str(e))
        if m:
            raise Blocked("YouTube Music", int(m[1])) from None
        if isinstance(e, requests.RequestException) or _SERVER_ERROR.search(str(e)):
            raise Unavailable(f"YouTube Music lyrics: {str(e)[:160]}") from None
        # "No content returned by the server" (a removed / unknown video) or a page ytmusicapi can't read: this
        # song has no lyrics we can get, which mustn't pause the source for every other song.
        logger.info(f"ytmusic lyrics {vid}: {type(e).__name__}: {str(e)[:160]}")
        return None
    if not found:
        return None
    lines = found.get("lines")
    if lines:
        synced = "\n".join(lrc_stamp(t) + text for t, text in lines)
        return result(synced, found.get("text"), duration=q.get("duration"))
    return result(None, found.get("text"))


# ---------- BetterLyrics (TTML) ----------

_TT = "{http://www.w3.org/ns/ttml}"
_ROLE = "{http://www.w3.org/ns/ttml#metadata}role"
_UNSAFE = re.compile(r"<!\s*(?:DOCTYPE|ENTITY)", re.I)


def ttml_time(value):
    """TTML clock value -> seconds: "18.893", "3:33.107", "1:02:03.5", "12.3s", "500ms"; None when unreadable."""
    v = str(value or "").strip()
    try:
        if v.endswith("ms"):
            return float(v[:-2]) / 1000
        if v.endswith("s"):
            return float(v[:-1])
        parts = [float(p) for p in v.split(":")]
    except ValueError:
        return None
    if not parts or len(parts) > 3 or any(p < 0 for p in parts):
        return None
    secs = 0.0
    for p in parts:
        secs = secs * 60 + p
    return secs


def _safe_xml(text):
    """Parsed XML, refusing any DTD: entities can only be declared in one, so no entity expansion (billion
    laughs) and no external entities reach the parser; an undeclared &entity; is a parse error."""
    if not isinstance(text, str) or len(text) > TTML_MAX or _UNSAFE.search(text):
        raise ValueError("refused TTML (DTD / entity declarations or too large)")
    return ET.fromstring(text)


def _line_words(p):
    """[{t, d, text}] of a <p>'s timed spans (background-vocal containers included, in document order)."""
    words = []

    def walk(el):
        for child in el:
            if child.tag == _TT + "span":
                begin, end = ttml_time(child.get("begin")), ttml_time(child.get("end"))
                if begin is not None:
                    text = "".join(child.itertext())
                    if text.strip():
                        w = {"t": round(begin, 3), "text": text}
                        if end is not None and end > begin:
                            w["d"] = round(end - begin, 3)
                        words.append(w)
                else:
                    walk(child)  # <span ttm:role="x-bg"> wraps timed spans
            tail = child.tail or ""
            if words and tail:
                if tail.strip():
                    words[-1]["text"] += tail  # untimed text between spans stays with the word before it
                elif not words[-1]["text"].endswith(" "):
                    words[-1]["text"] += " "
    walk(p)
    if words:
        words[-1]["text"] = words[-1]["text"].rstrip()
    return words


def parse_ttml(text):
    """(lines [(t, text, words)], body duration or None, plain text or None). Raises ValueError for unsafe or
    broken XML. Untimed TTML (timing="None") gives only the plain text."""
    try:
        root = _safe_xml(text)
    except ET.ParseError as e:
        raise ValueError(f"bad TTML: {e}") from None
    body = root.find(_TT + "body")
    dur = ttml_time(body.get("dur")) if body is not None and body.get("dur") else None
    lines, texts = [], []
    for p in root.iter(_TT + "p"):
        line = " ".join("".join(p.itertext()).split())
        if not line:
            continue
        texts.append(line)
        begin = ttml_time(p.get("begin"))
        if begin is not None:
            words = _line_words(p)
            if words:
                line = "".join(w["text"] for w in words).strip() or line
            lines.append((begin, line, words))
    lines.sort(key=lambda r: r[0])
    return lines, dur, ("\n".join(texts) or None)


def betterlyrics(q, timeout):
    title, artist = q.get("title") or "", q.get("artist") or ""
    if not title:
        return None
    params = {"s": title, "a": artist}
    if q.get("album"):
        params["al"] = q["album"]
    if q.get("duration"):
        params["d"] = round(float(q["duration"]))
    r = _session.get(BETTERLYRICS, params=params, headers=_HEADERS, timeout=timeout)
    if r.status_code == 401 and "uncached" in (r.text or "").lower():
        return None  # not in its cache (looking it up needs an API key we don't have): no lyrics, not a block
    if r.status_code == 404:
        return None
    _status(r, "BetterLyrics")
    if r.status_code >= 400:
        return None
    try:
        ttml = (r.json() or {}).get("ttml")
    except (ValueError, AttributeError):
        raise Unavailable("BetterLyrics answered with something other than JSON") from None
    if not isinstance(ttml, str) or not ttml.strip():
        return None
    try:
        lines, dur, plain = parse_ttml(ttml)
    except ValueError as e:
        logger.warning(f"reef: BetterLyrics TTML skipped: {str(e)[:160]}")
        return None
    duration = q.get("duration")
    if duration and dur and abs(dur - float(duration)) > DUR_SLACK_S:
        return None  # timed to another recording (an album cut vs this music video, say)
    if not lines:
        return result(None, plain)
    keep = [i for i in range(len(lines)) if i not in _credit_lines([ln for _, ln, _ in lines])]
    lines = [lines[i] for i in keep]
    synced = "\n".join(lrc_stamp(t) + ln for t, ln, _ in lines)
    words = [w for _, _, w in lines] if any(w for _, _, w in lines) else None
    return result(synced, None, words=words, duration=duration)
