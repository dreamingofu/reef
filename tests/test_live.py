"""Live checks against real YouTube: `python -m pytest -m live` (on the phone). Deselected by default and skipped on
GitHub Actions, whose IPs YouTube walls. Catches yt-dlp / player-client breakage the offline fakes can't."""
import os

import pytest
import requests

import streamer

VIDEO = "dQw4w9WgXcQ"  # long-lived, unrestricted, audio-only formats available

pytestmark = [pytest.mark.live,
              pytest.mark.skipif(bool(os.environ.get("GITHUB_ACTIONS")), reason="YouTube walls CI IPs")]


def test_resolve_and_fetch_two_bytes():
    entry = streamer.resolve(VIDEO)
    assert entry["url"].startswith("https://") and (entry["mime"] or "").startswith("audio/")
    r = requests.get(entry["url"], headers={**entry["headers"], "Range": "bytes=0-1"}, timeout=20)
    assert r.status_code == 206, f"client={entry.get('client')} status={r.status_code}"
    assert len(r.content) == 2
