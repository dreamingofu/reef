"""Registry of search/stream sources. Track ids are "<key>:<native id>"; bare ids are YouTube."""
import re

import config

from . import invidious, monochrome, navidrome_src, piped, youtube

_PREFIX = re.compile(r"^([a-z]{2}):(.+)$")

# Piped / Invidious back up yt-dlp inside the `yt` source; registered only when configured, so off costs nothing.
for _cfg, _mod in ((config.PIPED_INSTANCES, piped), (config.INVIDIOUS_INSTANCES, invidious)):
    if _cfg:
        youtube.search_fallbacks.append(_mod.search)
        youtube.resolve_fallbacks.append(_mod.resolve)

# Every known source, in chip order; SOURCES keeps the enabled ones.
ALL = [youtube.SOURCE, monochrome.SOURCE, navidrome_src.SOURCE]
SOURCES = {s.key: s for s in ALL if s.enabled()}


def get(key):
    return SOURCES.get(key)


def parse(track_id):
    """(source key, native id). No prefix (e.g. a bare 11-char YouTube id) means ("yt", id)."""
    m = _PREFIX.match(track_id or "")
    return (m.group(1), m.group(2)) if m else ("yt", track_id)


def track_id(key, native_id):
    """The canonical id: bare for YouTube, prefixed otherwise."""
    return native_id if key == "yt" else f"{key}:{native_id}"


def default_key():
    return config.DEFAULT_SOURCE if config.DEFAULT_SOURCE in SOURCES else "yt"


def enabled():
    d = default_key()
    return [{**s.describe(), "default": s.key == d} for s in SOURCES.values()]
