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


CRED_URL = "https://demo:synthetic-pass@example.invalid/api?api_key=synthetic-token"


def test_payload_masks_credentials_before_cutting_and_classifies_the_raw_text():
    # Long enough that cutting the raw text at 300 would keep "demo:synth..." without the "@" that marks userinfo.
    err = requests.ConnectionError("x" * 280 + f" Max retries exceeded with url: {CRED_URL}")
    p = errors.payload(err)
    assert p["block"] == "NETWORK" and len(p["error"]) <= 300
    assert "synth" not in p["error"] and "demo" not in p["error"]
    nd = requests.ConnectionError("HTTPConnectionPool(host='127.0.0.1', port=9): Max retries exceeded with url: "
                                  "/rest/stream?u=demo&t=0123456789abcdef0123456789abcdef&s=a1b2c3d4e5f6&id=7")
    p = errors.payload(nd)
    assert "0123456789abcdef" not in p["error"] and "a1b2c3d4e5f6" not in p["error"] and "id=7" in p["error"]
    assert errors.payload(f"HTTP Error 429 from {CRED_URL}")["block"] == "RATE_LIMITED"
    assert "synthetic" not in errors.payload(RuntimeError(f"failed: {CRED_URL}"))["error"]


def test_message_is_redacted_then_cut():
    assert errors.message(RuntimeError(CRED_URL)) == errors.message(CRED_URL)
    assert "synthetic" not in errors.message(RuntimeError("y" * 30 + CRED_URL), 40)
    assert errors.message(RuntimeError("plain"), 3) == "pla" and errors.message(RuntimeError("plain")) == "plain"
