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
AUDIO_FORMAT = os.environ.get("REEF_AUDIO_FORMAT", "opus")
SEARCH_LIMIT = int(os.environ.get("REEF_SEARCH_LIMIT", "10"))
NAVIDROME_URL = os.environ.get("REEF_NAVIDROME_URL", "http://127.0.0.1:4533").rstrip("/")
NAVIDROME_USER = os.environ.get("REEF_NAVIDROME_USER", "")
NAVIDROME_PASS = os.environ.get("REEF_NAVIDROME_PASS", "")
PASSWORD = os.environ.get("REEF_PASSWORD", "")
