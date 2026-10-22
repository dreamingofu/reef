"""Point Reef at a throwaway dir before any Reef module is imported.

config.py reads the environment at import time and fills gaps from reef.env with setdefault,
so assigning here (not setdefault) wins over a developer's reef.env.
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="reef-test-"))

os.environ["REEF_HISTORY_FILE"] = str(TMP / "history.json")
os.environ["REEF_MUSIC_DIR"] = str(TMP / "music")
os.environ["REEF_TMP_DIR"] = str(TMP / "tmp")
# Empty credentials: navidrome.configured() is False, so nothing talks to a real server.
os.environ["REEF_NAVIDROME_USER"] = ""
os.environ["REEF_NAVIDROME_PASS"] = ""
os.environ["REEF_PIPED_INSTANCES"] = ""
os.environ["REEF_INVIDIOUS_INSTANCES"] = ""

sys.path.insert(0, str(ROOT))
