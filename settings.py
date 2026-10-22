"""Runtime settings (`settings.json`): overrides, set from the UI, of a whitelist of keys safe to change while
Reef runs. reef.env / the environment stays the default; a key only present in settings.json wins, and
resetting it (PATCH with null) goes back to the env value. Code that honours a setting calls `get(key)` at the
moment it needs it, so a change applies without a restart.

Secrets (`secret: True`) are write-only: `public()` reports only whether they're set.
"""
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
    "lyrics_providers": {"type": "list", "choices": ["navidrome", "lrclib"], "default": lambda: ["navidrome", "lrclib"],
                         "label": "Lyrics sources", "help": "Where lyrics come from, in order. (Not used yet.)"},
    "listenbrainz_token": {"type": "secret", "default": lambda: "", "label": "ListenBrainz token",
                           "help": "User token from listenbrainz.org/settings, for scrobbling. Never shown again once saved."},
}
SECRET_MAX = 200


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


def public():
    """{key: value} for GET; secrets are {"set": bool}."""
    out = {}
    for key, spec in SCHEMA.items():
        out[key] = {"set": bool(get(key))} if spec["type"] == "secret" else get(key)
    return out


def schema():
    """[{key, type, default, label, help, choices?, overridden}] in display order; secrets have no default."""
    over = _overrides()
    rows = []
    for key, spec in SCHEMA.items():
        row = {"key": key, "type": spec["type"], "label": spec["label"], "help": spec["help"],
               "default": None if spec["type"] == "secret" else spec["default"](), "overridden": key in over}
        if "choices" in spec:
            row["choices"] = list(spec["choices"])
        rows.append(row)
    return rows


def update(changes):
    """PATCH: validate every key first (nothing is written on an error), then store. None resets a key to its
    env default ("" too, for a secret: it clears the token)."""
    if not isinstance(changes, dict) or not changes:
        raise ValueError("send a JSON object of settings")
    clean = {}
    for key, value in changes.items():
        if key not in SCHEMA:
            raise ValueError(f"unknown setting: {key}")
        if value is None or (SCHEMA[key]["type"] == "secret" and value == ""):
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
