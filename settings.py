"""Runtime settings (`settings.json`): overrides, set from the UI, of a whitelist of keys safe to change while
Reef runs. reef.env / the environment stays the default; a key only present in settings.json wins, and
resetting it (PATCH with null) goes back to the env value. Code that honours a setting calls `get(key)` at the
moment it needs it, so a change applies without a restart.

Secrets (`secret: True`) are write-only: `public()` reports only whether they're set. Masked text (`masked: True`, an
anonymous id) is reported as set + a preview; server-side code reads it in full.
Instance lists (`urls`) are sent as one comma-separated string (or a list); blank goes back to the reef.env list.
"""
import re
from urllib.parse import urlsplit

import config
import store

_file = store.JsonFile(lambda: config.SETTINGS_FILE, dict)

# key -> spec. `default` is a function so it reads config at call time (env value; tests patch config).
SCHEMA = {
    "default_source": {"type": "choice", "choices": ["yt", "mc", "nd"], "default": lambda: config.DEFAULT_SOURCE,
                       "label": "Default search source", "help": "Where search looks first: YouTube, Monochrome or your library."},
    "ytmusic": {"type": "bool", "default": lambda: config.YTMUSIC, "label": "YouTube Music",
                "help": "Search, suggestions, radio and browse through YouTube Music. Off = plain YouTube search."},
    "prefer_library": {"type": "bool", "default": lambda: True, "label": "Play kept songs from your library",
                       "help": "Stream kept songs from Navidrome instead of YouTube: faster, and works when YouTube blocks."},
    "autoplay_radio": {"type": "bool", "default": lambda: True, "label": "Autoplay",
                       "help": "Keep playing similar songs when the queue runs out (default for new devices)."},
    "sponsorblock_keep": {"type": "bool", "default": lambda: config.SPONSORBLOCK, "label": "Cut non-music parts on keep",
                          "help": "Remove SponsorBlock segments (intros, talking, sponsors) from kept YouTube songs."},
    "replaygain": {"type": "bool", "default": lambda: config.REPLAYGAIN, "label": "Volume levelling tags",
                   "help": "Measure kept songs and write ReplayGain tags so they play at an even volume."},
    "lrc_sidecar": {"type": "bool", "default": lambda: config.LRC_SIDECAR, "label": "Lyrics .lrc files",
                    "help": "Also save synced lyrics as a .lrc file next to each kept song."},
    "keep_lossless": {"type": "bool", "default": lambda: config.KEEP_LOSSLESS, "label": "Keep lossless when possible",
                      "help": "Before keeping a YouTube song, look for the same recording on Monochrome and keep it "
                              "as FLAC; otherwise keep from YouTube as usual. Monochrome is a third-party Tidal "
                              "proxy: legally grey and only as reliable as it is."},
    "sponsorblock_play": {"type": "bool", "default": lambda: True, "label": "Skip non-music parts while playing",
                          "help": "Jump over intros and talking that SponsorBlock users marked as non-music in "
                                  "YouTube songs (each part once; seek back into it to hear it)."},
    "replace_dead_tracks": {"type": "bool", "default": lambda: True, "label": "Replace removed songs",
                            "help": "When a song in the queue was removed or is blocked, play the same song from "
                                    "another upload instead of skipping it (only the queue changes)."},
    # Library tracks read Navidrome's tags first whatever this says. SimpMusic is off by default: its API answers
    # 403 "only available to SimpMusic clients" to everyone else (checked 2026-10-22).
    "lyrics_providers": {"type": "list", "choices": ["lrclib", "simpmusic", "youtube", "betterlyrics"],
                         "default": lambda: ["lrclib", "youtube", "betterlyrics"], "label": "Lyrics sources",
                         "help": "Where the lyrics view looks, in order (LRCLIB, SimpMusic, YouTube Music, "
                                 "BetterLyrics for word timing). Kept songs are tagged with LRCLIB lyrics only."},
    "yt_personalize": {"type": "bool", "default": lambda: config.YT_PERSONALIZE, "label": "Personalise YouTube Music",
                       "help": "Reports what you play to YouTube Music under an anonymous visitor id so Quick "
                               "picks/home learn your taste; no Google account. Off by default."},
    # The anonymous id the reports and YouTube Music requests carry while personalisation is on. Made on first use;
    # paste one to move the taste profile between installs, blank = start over with a new id. Shown masked.
    "yt_visitor_id": {"type": "text", "masked": True, "pattern": r"Cg[A-Za-z0-9_%=+/-]{10,1000}", "default": lambda: "",
                      "label": "YouTube Music visitor id",
                      "help": "The anonymous id personalisation uses. Paste one to keep a taste profile; clear it to "
                              "start over."},
    "itunes_suggestions": {"type": "bool", "default": lambda: True, "label": "iTunes suggestions as a fallback",
                           "help": "When YouTube Music's search suggestions are off or failing, ask Apple's iTunes "
                                   "Search API instead (official, no account; it sees what you type)."},
    "charts_country": {"type": "text", "pattern": r"[A-Z]{2}", "upper": True, "default": lambda: config.CHARTS_COUNTRY,
                       "label": "Charts country",
                       "help": "Whose YouTube Music charts Home and Charts show: ZZ = global, else a country code."},
    "listenbrainz_token": {"type": "secret", "default": lambda: "", "label": "ListenBrainz token",
                           "help": "User token from listenbrainz.org/settings, for scrobbling. Never shown again once saved."},
    "scrobble_enabled": {"type": "bool", "default": lambda: True, "label": "Scrobble to ListenBrainz",
                         "help": "Send every counted listen to ListenBrainz (needs the token above)."},
    "skipped_version": {"type": "text", "pattern": r"[0-9a-f]{7,40}", "default": lambda: "",
                        "label": "Skipped Reef update", "help": "The Reef commit \"Skip this version\" hid."},
    # Effective at once for Monochrome and an already-enabled Piped/Invidious; turning a source on or off
    # (an empty env list and a list set here, or the reverse) takes a restart, since sources register at start.
    "tracks_api": {"type": "urls", "default": lambda: list(config.TRACKS_API), "label": "Monochrome instances",
                   "help": "Comma-separated API URLs; blank = the reef.env list."},
    "piped_instances": {"type": "urls", "default": lambda: list(config.PIPED_INSTANCES), "label": "Piped instances",
                        "help": "Comma-separated API URLs, YouTube fallbacks; blank = the reef.env list."},
    "invidious_instances": {"type": "urls", "default": lambda: list(config.INVIDIOUS_INSTANCES),
                            "label": "Invidious instances",
                            "help": "Comma-separated API URLs, YouTube fallbacks; blank = the reef.env list."},
}
SECRET_MAX = 200
URLS_MAX = 10
URL_MAX = 300


def _url(raw):
    u = raw.strip().rstrip("/")
    try:
        parts = urlsplit(u)
        _ = parts.port  # raises ValueError for a bad port
    except ValueError:
        raise ValueError(f"not a URL: {u[:60]}") from None
    if parts.scheme not in ("http", "https") or not parts.hostname or len(u) > URL_MAX or re.search(r"\s", u):
        raise ValueError(f"not an http(s) URL: {u[:60]}")
    return u


def _valid(key, value):
    """The value to store, or raises ValueError."""
    spec = SCHEMA[key]
    kind = spec["type"]
    if kind == "bool":
        if not isinstance(value, bool):
            raise ValueError(f"{key} must be true or false")
        return value
    if kind == "choice":
        if value not in spec["choices"]:
            raise ValueError(f"{key} must be one of {', '.join(spec['choices'])}")
        return value
    if kind == "list":
        if not isinstance(value, list) or any(v not in spec["choices"] for v in value) or len(set(value)) != len(value):
            raise ValueError(f"{key} must be a list of distinct values from {', '.join(spec['choices'])}")
        return list(value)
    if kind == "secret":
        if not isinstance(value, str) or len(value) > SECRET_MAX or any(c.isspace() for c in value.strip()):
            raise ValueError(f"{key} must be a token without spaces")
        return value.strip()
    if kind == "text":
        if isinstance(value, str) and spec.get("upper"):
            value = value.upper()
        if not isinstance(value, str) or not re.fullmatch(spec["pattern"], value.strip()):
            raise ValueError(f"{key} has the wrong format")
        return value.strip()
    if kind == "urls":
        items = value.split(",") if isinstance(value, str) else value
        if not isinstance(items, list) or not all(isinstance(v, str) for v in items):
            raise ValueError(f"{key} must be comma-separated URLs")
        out = []
        for v in (v for v in items if v.strip()):
            u = _url(v)
            if u not in out:
                out.append(u)
        if not out or len(out) > URLS_MAX:
            raise ValueError(f"{key} takes 1 to {URLS_MAX} URLs")
        return out
    raise ValueError(f"{key} can't be set")


def _overrides():
    with _file.lock:
        data = _file.load()
        return {k: v for k, v in data.items() if k in SCHEMA}


def get(key):
    """The effective value: the settings.json override when it's valid, else the env default."""
    spec = SCHEMA[key]
    over = _overrides()
    if key in over:
        try:
            return _valid(key, over[key])
        except ValueError:
            pass  # a hand-edited file: ignore the bad value
    return spec["default"]()


def mask(value):
    """A recognisable but unusable preview of an id: its first and last 4 characters."""
    value = str(value or "")
    return f"{value[:4]}…{value[-4:]}" if len(value) > 12 else "…" if value else ""


def public():
    """{key: value} for GET; secrets are {"set": bool}, masked values {"set": bool, "masked": "Cgs3…AQE"}."""
    out = {}
    for key, spec in SCHEMA.items():
        value = get(key)
        if spec["type"] == "secret":
            out[key] = {"set": bool(value)}
        elif spec.get("masked"):
            out[key] = {"set": bool(value), "masked": mask(value)}
        else:
            out[key] = value
    return out


def schema():
    """[{key, type, default, label, help, choices?, overridden}] in display order; secrets have no default."""
    over = _overrides()
    rows = []
    for key, spec in SCHEMA.items():
        hidden = spec["type"] == "secret" or spec.get("masked")
        row = {"key": key, "type": spec["type"], "label": spec["label"], "help": spec["help"],
               "default": None if hidden else spec["default"](), "overridden": key in over}
        if spec.get("masked"):
            row["masked"] = True
        if "choices" in spec:
            row["choices"] = list(spec["choices"])
        rows.append(row)
    return rows


def update(changes):
    """PATCH: validate every key first (nothing is written on an error), then store. None resets a key to its
    env default ("" too, for a secret, text or URL list: it clears the token / goes back to the env list)."""
    if not isinstance(changes, dict) or not changes:
        raise ValueError("send a JSON object of settings")
    clean = {}
    for key, value in changes.items():
        if key not in SCHEMA:
            raise ValueError(f"unknown setting: {key}")
        blank = isinstance(value, str) and not value.strip() or value == []
        if value is None or (SCHEMA[key]["type"] in ("secret", "text", "urls") and blank):
            clean[key] = None
        else:
            clean[key] = _valid(key, value)
    with _file.lock:
        data = dict(_file.load())
        for key, value in clean.items():
            if value is None:
                data.pop(key, None)
            else:
                data[key] = value
        _file.save(data)
