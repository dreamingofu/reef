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
os.environ["REEF_BROWSE_FILE"] = str(TMP / "browse.json")  # defaults to the repo dir otherwise
# Empty credentials: navidrome.configured() is False, so nothing talks to a real server.
os.environ["REEF_NAVIDROME_USER"] = ""
os.environ["REEF_NAVIDROME_PASS"] = ""
os.environ["REEF_PIPED_INSTANCES"] = ""
os.environ["REEF_INVIDIOUS_INSTANCES"] = ""

sys.path.insert(0, str(ROOT))


import pytest  # noqa: E402  (after the environment above is set)
import requests  # noqa: E402


class NoNetwork:
    """Stands in for the requests sessions of scrobble / updater / health: nothing in the suite reaches
    ListenBrainz, GitHub, PyPI, a lyrics source or a source by accident (a settings PATCH with a token validates it, for one)."""
    headers = {}

    def request(self, *a, **k):
        raise requests.ConnectionError("network is off in tests")

    get = post = head = request


@pytest.fixture(autouse=True)
def _offline_sessions(monkeypatch):
    import health
    import lyrics
    import lyrics_providers
    import scrobble
    import updater
    import ytmusic
    for mod in (scrobble, updater, health, lyrics, lyrics_providers):
        monkeypatch.setattr(mod, "_session", NoNetwork())
    monkeypatch.setattr(ytmusic, "lyrics", NoNetwork().request)  # the lyrics chain's YouTube Music source
