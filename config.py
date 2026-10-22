import json
import logging
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
ENV_FILE = BASE_DIR / "reef.env"


def _load_env_file(path):
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def _default_music_dir():
    termux = Path.home() / "storage" / "shared" / "Music"
    return termux if termux.exists() else Path.home() / "Music"


_load_env_file(ENV_FILE)

HOST = os.environ.get("REEF_HOST", "0.0.0.0")
PORT = int(os.environ.get("REEF_PORT", "5274"))
MUSIC_DIR = Path(os.environ.get("REEF_MUSIC_DIR") or _default_music_dir())
DOWNLOAD_DIR = MUSIC_DIR / os.environ.get("REEF_SUBFOLDER", "Reef")
TMP_DIR = Path(os.environ.get("REEF_TMP_DIR") or BASE_DIR / "tmp")
HISTORY_FILE = Path(os.environ.get("REEF_HISTORY_FILE") or BASE_DIR / "history.json")
TILES_FILE = BASE_DIR / "tiles.json"
# YouTube Music moods & genres and their artwork, so a restart doesn't refetch them (git-ignored).
BROWSE_FILE = Path(os.environ.get("REEF_BROWSE_FILE") or BASE_DIR / "browse.json")
AUDIO_FORMAT = os.environ.get("REEF_AUDIO_FORMAT", "opus")
SEARCH_LIMIT = int(os.environ.get("REEF_SEARCH_LIMIT", "10"))
NAVIDROME_URL = os.environ.get("REEF_NAVIDROME_URL", "http://127.0.0.1:4533").rstrip("/")
NAVIDROME_USER = os.environ.get("REEF_NAVIDROME_USER", "")
NAVIDROME_PASS = os.environ.get("REEF_NAVIDROME_PASS", "")
# HTTP Basic password for clients outside loopback and the tailnet (100.64.0.0/10, fd7a:115c:a1e0::/48).
# Empty = those clients get a 403; loopback and Tailscale never need it.
PASSWORD = os.environ.get("REEF_PASSWORD", "")
# 1 = the pre-2026-10-22 behaviour: every network is treated alike (REEF_PASSWORD, if set, applies to all).
OPEN = os.environ.get("REEF_OPEN", "0").strip() == "1"
# 1 = Flask's development server instead of waitress (local debugging only).
DEV = os.environ.get("REEF_DEV", "0").strip() == "1"
# Rotated reef.log (1 MB x 5) lives here (default logs/). REEF_LOG_STDERR=0 (set by termux/reef-run.sh) drops the console copy.
LOG_DIR = Path(os.environ["REEF_LOG_DIR"]) if os.environ.get("REEF_LOG_DIR") else BASE_DIR / "logs"
LOG_STDERR = os.environ.get("REEF_LOG_STDERR", "1").strip() != "0"
# Search source used when /api/search has no `src` (yt = YouTube, mc = Monochrome, nd = Navidrome library).
DEFAULT_SOURCE = os.environ.get("REEF_DEFAULT_SOURCE", "yt").strip() or "yt"
# Monochrome tracks API instance(s), comma-separated; empty disables the source.
TRACKS_API = [u.strip().rstrip("/") for u in os.environ.get("REEF_TRACKS_API", "https://tracks.monochrome.st").split(",") if u.strip()]
# Piped / Invidious API instances, comma-separated: fallbacks for YouTube search/stream, tried only
# when yt-dlp fails. Empty (the default) disables them.
PIPED_INSTANCES = [u.strip().rstrip("/") for u in os.environ.get("REEF_PIPED_INSTANCES", "").split(",") if u.strip()]
INVIDIOUS_INSTANCES = [u.strip().rstrip("/") for u in os.environ.get("REEF_INVIDIOUS_INSTANCES", "").split(",") if u.strip()]
# yt-dlp innertube client(s) used to resolve stream URLs, e.g. "ios" or "android_vr,web_safari".
# Empty = yt-dlp's own default. Pick with scripts/bench_resolve.py on the phone.
YT_CLIENT = [c.strip() for c in os.environ.get("REEF_YT_CLIENT", "").split(",") if c.strip()]
# Cut SponsorBlock segments (non-music, sponsor, intro, outro) out of kept YouTube tracks.
SPONSORBLOCK = os.environ.get("REEF_SPONSORBLOCK", "0").strip() == "1"
# LRCLIB lyrics cache. Defaults next to history.json, so a scratch REEF_HISTORY_FILE keeps it out of the repo too.
LYRICS_FILE = Path(os.environ.get("REEF_LYRICS_FILE") or HISTORY_FILE.with_name("lyrics.json"))
# Also write synced lyrics as a `<file>.lrc` sidecar next to each kept track (the tag alone is enough for Navidrome).
LRC_SIDECAR = os.environ.get("REEF_LRC_SIDECAR", "0").strip() == "1"
# Blocked artists, hidden from search, radio, playlists and album pages. Defaults next to history.json.
BLOCKLIST_FILE = Path(os.environ.get("REEF_BLOCKLIST_FILE") or HISTORY_FILE.with_name("blocklist.json"))
# Search, suggestions, radio and link metadata via YouTube Music (ytmusicapi, anonymous).
# 0 = yt-dlp search only (no suggestions or radio).
YTMUSIC = os.environ.get("REEF_YTMUSIC", "1").strip() != "0"
# Measure loudness (ffmpeg ebur128) of each kept track and write ReplayGain / R128 gain tags.
REPLAYGAIN = os.environ.get("REEF_REPLAYGAIN", "1").strip() == "1"


def _ydl_extra(raw):
    try:
        extra = json.loads(raw or "{}")
    except ValueError:
        extra = None
    if not isinstance(extra, dict):
        logging.getLogger(__name__).warning("reef: ignoring REEF_YDL_EXTRA, it isn't a JSON object")
        return {}
    return extra


# Extra yt-dlp options (a JSON object) merged into the keep options, e.g. {"cookiefile": "/path"}.
YDL_EXTRA = _ydl_extra(os.environ.get("REEF_YDL_EXTRA"))


# Test hook: the stream proxy answers 403 for Range starts past ~60 s of audio on a track's first URL,
# like YouTube walling a client mid-song, so the player's recovery can be tried without YouTube.
FAKE_WALL = os.environ.get("REEF_FAKE_WALL", "0").strip() == "1"


def yt_extractor_args(clients=None, exclude=()):
    """yt-dlp `extractor_args` for YouTube. `clients=None` means YT_CLIENT; [] means yt-dlp's default.

    `exclude` drops clients (yt-dlp's `-name` syntax), e.g. ones YouTube is currently denying.
    """
    clients = YT_CLIENT if clients is None else clients
    args = {"skip": ["hls", "dash", "translated_subs"]}
    if clients or exclude:
        args["player_client"] = list(clients or ["default"]) + [f"-{c}" for c in exclude]
    return {"youtube": args}
