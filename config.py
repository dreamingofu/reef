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
AUDIO_FORMAT = os.environ.get("REEF_AUDIO_FORMAT", "opus")
SEARCH_LIMIT = int(os.environ.get("REEF_SEARCH_LIMIT", "10"))
NAVIDROME_URL = os.environ.get("REEF_NAVIDROME_URL", "http://127.0.0.1:4533").rstrip("/")
NAVIDROME_USER = os.environ.get("REEF_NAVIDROME_USER", "")
NAVIDROME_PASS = os.environ.get("REEF_NAVIDROME_PASS", "")
PASSWORD = os.environ.get("REEF_PASSWORD", "")
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


def yt_extractor_args(clients=None):
    """yt-dlp `extractor_args` for YouTube. `clients=None` means YT_CLIENT; [] means yt-dlp's default."""
    clients = YT_CLIENT if clients is None else clients
    args = {"skip": ["hls", "dash", "translated_subs"]}
    if clients:
        args["player_client"] = list(clients)
    return {"youtube": args}
