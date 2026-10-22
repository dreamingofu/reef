"""Music tags from YouTube-ish metadata: what Navidrome shows, without touching the filename.

Pure functions, no Reef imports, so lyrics.py can use them without pulling in the downloader.
"""
import re

# A trailing (...) / [...] group made only of these words is video noise, not part of the song title.
_NOISE_WORDS = {"official", "oficial", "music", "video", "audio", "lyric", "lyrics", "visualizer", "visualiser",
                "hd", "hq", "4k", "mv"}
_TRAILING_GROUP = re.compile(r"\s*(\(([^()]*)\)|\[([^\[\]]*)\])\s*$")
_SEP = re.compile(r"\s+[-–]\s+")


def _is_noise(group):
    words = re.findall(r"[\w']+", group.lower())
    if "4k" in words:  # "4K Remaster" is about the video; a bare "(Remastered 2009)" is about the recording
        words = [w for w in words if w not in ("remaster", "remastered")]
    return bool(words) and all(w in _NOISE_WORDS for w in words)


def _strip_noise(title):
    while True:
        m = _TRAILING_GROUP.search(title)
        if not m or not _is_noise(m.group(2) if m.group(2) is not None else m.group(3)) or not title[:m.start()].strip():
            return title.strip()
        title = title[:m.start()]


def clean_artist(name):
    """An artist name without YouTube's " - Topic" / "VEVO" channel suffixes."""
    name = re.sub(r"\s+-\s+Topic$", "", str(name or "").strip(), flags=re.I)
    return re.sub(r"\s*VEVO$", "", name).strip()


def _names(value):
    return [n for n in (clean_artist(v) for v in (value if isinstance(value, list) else [value])) if n]


def _year(info):
    for v in (info.get("release_year"), (info.get("release_date") or "")[:4], (info.get("upload_date") or "")[:4]):
        if v and re.fullmatch(r"\d{4}", str(v)):
            return str(v)
    return None


def music_tags(info, alias=None):
    """`meta_*` overrides for FFmpegMetadataPP (it prefers them over the plain fields). Never touches
    info["title"], which names the file. `alias(name, channel_id=None)` gives an artist's display name
    (aliases.py); the uploading channel's id goes with a lone artist."""
    artists = _names(info.get("artists")) or _names(info.get("artist")) or _names(info.get("creators")) \
        or _names(info.get("creator"))
    known = bool(artists)
    title = str(info.get("track") or info.get("title") or "").strip()
    if not info.get("track"):
        parts = _SEP.split(title, maxsplit=1)
        if len(parts) == 2 and known:
            # "Rick Astley - Never Gonna..." when yt-dlp already knows the artist is Rick Astley.
            left, first = parts[0].lower(), artists[0].lower()
            if left == ", ".join(artists).lower() or re.match(re.escape(first) + r"(\W|$)", left):
                title = parts[1]
        elif not known and len(_SEP.findall(title)) == 1:
            artists, title = [parts[0].strip()], parts[1]
    if not artists:
        artists = _names(info.get("uploader")) or _names(info.get("channel"))
    tags = {"meta_title": _strip_noise(title) or title}
    if artists:
        album_artist = (_names(info.get("album_artists")) or _names(info.get("album_artist")) or artists)[0]
        if alias:
            channel_id = info.get("channel_id") if len(artists) == 1 else None
            album_artist = alias(album_artist, channel_id if album_artist == artists[0] else None)
            artists = [alias(a, channel_id) for a in artists]
        tags["meta_artist"] = ", ".join(artists)
        tags["meta_album_artist"] = album_artist
    if info.get("album"):
        tags["meta_album"] = str(info["album"])
    if info.get("track_number"):
        tags["meta_track"] = str(info["track_number"])
    tags["meta_date"] = _year(info)
    return {k: v for k, v in tags.items() if v}


_HINT_TAGS = (("album", "meta_album"), ("album_artist", "meta_album_artist"), ("track_number", "meta_track"),
              ("year", "meta_date"))


def with_hints(tags, hints):
    """`music_tags` output with album-page hints laid over it. yt-dlp's per-video album/track/date are often
    missing or name a single, so an album kept from its page would scatter across Navidrome without these."""
    hints = hints or {}
    out = dict(tags)
    if hints.get("album") and hints["album"] != out.get("meta_album") and not hints.get("track_number"):
        out.pop("meta_track", None)  # a track number from another release of the song would be wrong here
    for key, meta in _HINT_TAGS:
        if hints.get(key) not in (None, ""):
            out[meta] = str(hints[key])
    return out


def guess(title, channel):
    """(clean_title, artist) for a track that was never kept, when all the client knows is the YouTube
    title and channel name. Same rules as a keep, so lyrics found now match the tags written later."""
    tags = music_tags({"title": title or "", "uploader": channel or ""})
    return tags.get("meta_title", ""), tags.get("meta_artist", "")
