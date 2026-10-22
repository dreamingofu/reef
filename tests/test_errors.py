"""errors.classify: real yt-dlp / requests messages -> block, and the hints the UI shows."""
import socket

import pytest
import requests

import errors

CASES = [
    ("ERROR: [youtube] abc: Sign in to confirm you're not a bot. Use --cookies-from-browser", "BOT_WALL"),
    ("Sign in to confirm you’re not a bot", "BOT_WALL"),
    ("ERROR: [youtube] abc: Sign in to confirm your age. This video may be inappropriate for some users.",
     "LOGIN_REQUIRED"),
    ("This video is age-restricted", "LOGIN_REQUIRED"),
    ("Login required to view this", "LOGIN_REQUIRED"),
    ("Video unavailable. The uploader has not made this video available in your country", "REGION"),
    ("This video is not available in your region", "REGION"),
    ("HTTP Error 429: Too Many Requests", "RATE_LIMITED"),
    ("This content isn't available, try again later.", "RATE_LIMITED"),
    ("The current session has been rate-limited by YouTube for up to an hour", "RATE_LIMITED"),
    ("Join this channel to get access to members-only content like this video", "MEMBERS_OR_PAID"),
    ("This video requires payment to watch", "MEMBERS_OR_PAID"),
    ("This video is only available to Music Premium members", "MEMBERS_OR_PAID"),
    ("Private video. Sign in if you've been granted access to this video", "GONE"),
    ("Video unavailable. This video has been removed by the uploader", "GONE"),
    ("This video is no longer available because the YouTube account associated with it has been terminated", "GONE"),
    ("Video unavailable", "GONE"),
    ("Unable to download API page: <urlopen error [Errno 8] nodename nor servname provided, or not known>",
     "NETWORK"),
    ("[Errno -3] Temporary failure in name resolution", "NETWORK"),
    ("HTTPSConnectionPool(host='x', port=443): Read timed out. (read timeout=10)", "NETWORK"),
    ("Connection refused", "NETWORK"),
    ("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed", "NETWORK"),
    ("Requested format is not available", "UNKNOWN"),
    ("", "UNKNOWN"),
]


@pytest.mark.parametrize("message,block", CASES)
def test_classify(message, block):
    assert errors.classify(message) == block


@pytest.mark.parametrize("exc", [requests.ReadTimeout("video unavailable"), requests.ConnectionError("removed"),
                                 TimeoutError("private"), socket.timeout("x")])
def test_network_exceptions_are_never_gone(exc):
    assert errors.classify_exc(exc) == "NETWORK"


def test_stream_error_keeps_its_block():
    assert errors.classify_exc(errors.StreamError("GONE", "every client said unplayable")) == "GONE"


def test_hints_and_payload():
    for block in errors.BLOCKS + (errors.EXPIRED,):
        assert errors.hint(block)
    assert "mobile data" in errors.hint("BOT_WALL")
    assert errors.hint("NOPE") == errors.hint("UNKNOWN")
    p = errors.payload(RuntimeError("Video unavailable. This video has been removed by the uploader"))
    assert p == {"error": "Video unavailable. This video has been removed by the uploader", "block": "GONE",
                 "hint": "This video was removed or is private"}
    assert errors.payload("x" * 500, block="REGION")["error"] == "x" * 300
