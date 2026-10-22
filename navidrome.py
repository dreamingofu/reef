import hashlib
import os
import secrets

import requests
from requests.adapters import HTTPAdapter

import errors
from config import NAVIDROME_PASS, NAVIDROME_URL, NAVIDROME_USER


_session = requests.Session()
_session.mount("http://", HTTPAdapter(pool_connections=2, pool_maxsize=8))
_session.mount("https://", HTTPAdapter(pool_connections=2, pool_maxsize=8))


def configured():
    return bool(NAVIDROME_USER and NAVIDROME_PASS)


def _auth_params():
    salt = secrets.token_hex(6)
    token = hashlib.md5((NAVIDROME_PASS + salt).encode()).hexdigest()
    return {"u": NAVIDROME_USER, "t": token, "s": salt, "v": "1.16.1", "c": "reef", "f": "json"}


def _call(endpoint, **params):
    r = _session.get(f"{NAVIDROME_URL}/rest/{endpoint}", params={**_auth_params(), **params}, timeout=10)
    r.raise_for_status()
    body = r.json()["subsonic-response"]
    if body.get("status") != "ok":
        raise RuntimeError(body.get("error", {}).get("message", "Navidrome returned an error"))
    return body


def ping():
    if not configured():
        return {"ok": False, "error": "REEF_NAVIDROME_USER/PASS not set"}
    try:
        body = _call("ping")
        return {"ok": True, "version": body.get("serverVersion", "")}
    except Exception as e:
        return {"ok": False, "error": errors.message(e)}  # requests' text carries the URL with the token + salt


def rescan():
    if not configured():
        return False
    try:
        _call("startScan")
        return True
    except Exception:
        return False


def newest_albums(size=20):
    if not configured():
        return []
    body = _call("getAlbumList2", type="newest", size=size)
    albums = (body.get("albumList2") or {}).get("album") or []
    return [
        {
            "id": a.get("id"),
            "name": a.get("name", ""),
            "artist": a.get("artist", ""),
            "cover": a.get("coverArt"),
            "year": a.get("year"),
            "songCount": a.get("songCount"),
        }
        for a in albums
    ]


def find_song(title, file_basename):
    """The library song for a file Reef kept, or None. Raises on Navidrome errors.

    Matches on the song's `path` ending with the file's basename. Navidrome reports a synthetic
    `Artist/Album/NN - Title.ext` path unless "Report Real Path" is on, so fall back to a song whose
    title and suffix match exactly, but only when that match is unique.
    """
    if not configured() or not title or not file_basename:
        return None
    body = _call("search3", query=title, songCount=10, albumCount=0, artistCount=0)
    songs = (body.get("searchResult3") or {}).get("song") or []
    for s in songs:
        path = (s.get("path") or "").replace("\\", "/")
        if path == file_basename or path.endswith("/" + file_basename):
            return s
    suffix = os.path.splitext(file_basename)[1].lstrip(".").lower()
    same = [s for s in songs
            if (s.get("title") or "").casefold() == title.casefold() and (s.get("suffix") or "").lower() == suffix]
    return same[0] if len(same) == 1 else None


def cover_art(cover_id, size=300):
    r = _session.get(
        f"{NAVIDROME_URL}/rest/getCoverArt",
        params={**_auth_params(), "id": cover_id, "size": size},
        timeout=15,
    )
    r.raise_for_status()
    return r.content, r.headers.get("Content-Type", "image/jpeg")


def _lrc_stamp(ms):
    ms = max(0, int(ms))
    return f"[{ms // 60000:02d}:{ms // 1000 % 60:02d}.{ms % 1000 // 10:02d}]"


def lyrics(song_id):
    """{"synced", "plain", "instrumental": False} from OpenSubsonic getLyricsBySongId, or None when the song
    has none or the server predates the extension. Synced lines come back as LRC text, so the client
    parses one format whether lyrics came from here or LRCLIB."""
    if not configured():
        return None
    try:
        body = _call("getLyricsBySongId", id=song_id)
        entries = [e for e in (body.get("lyricsList") or {}).get("structuredLyrics") or []
                   if isinstance(e, dict) and isinstance(e.get("line"), list) and e["line"]]
        synced = next((e for e in entries if e.get("synced")), None)
        if synced:
            # OpenSubsonic: a positive offset makes lyrics appear sooner, same as LRC's [offset:].
            offset = float(synced.get("offset") or 0)
            text = "\n".join(_lrc_stamp(float(ln["start"]) - offset) + str(ln.get("value") or "").strip()
                             for ln in synced["line"] if isinstance(ln, dict) and ln.get("start") is not None)
            return {"synced": text, "plain": None, "instrumental": False} if text else None
        if entries:
            text = "\n".join(str(ln.get("value") or "") for ln in entries[0]["line"] if isinstance(ln, dict)).strip()
            return {"synced": None, "plain": text, "instrumental": False} if text else None
    except Exception:
        return None
    return None
