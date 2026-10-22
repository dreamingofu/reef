"""Typed playback failures: one `block` per kind of failure, so the UI can say what to do about it.

`classify(message)` reads yt-dlp / requests error text; `classify_exc(e)` also looks at the exception
type (a timeout is NETWORK whatever its text says). `GONE` is only final when every client said so
(sources/youtube.py decides that and raises a StreamError with the block set).
"""
import re
import socket

import requests

import log

BLOCKS = ("BOT_WALL", "NETWORK", "LOGIN_REQUIRED", "REGION", "RATE_LIMITED", "MEMBERS_OR_PAID", "GONE", "UNKNOWN")
# Not produced by classify(): the proxy's "the stream URL/file changed under a playing element" signal.
EXPIRED = "EXPIRED"

# First match wins, so the specific ones come before "unavailable" (REGION's message also says
# "Video unavailable") and before NETWORK (a 429 page can mention a timeout).
_PATTERNS = [
    ("LOGIN_REQUIRED", r"sign in to confirm your age|age.?restricted|login required|inappropriate for some users"),
    ("BOT_WALL", r"not a bot|confirm you.?re"),
    ("MEMBERS_OR_PAID", r"members.?only|join this channel|requires payment|premium members|only a preview"),
    ("REGION", r"in your (?:country|region)|geo.?restrict"),
    ("RATE_LIMITED", r"try again later|\b429\b|too many requests|rate.?limit"),
    ("NETWORK", r"timed out|time ?out|connection (?:refused|reset|aborted)|remote ?disconnected|remote end closed"
                r"|name or service not known|nodename nor servname|name resolution|getaddrinfo"
                r"|network is unreachable|no route to host|\bssl\b|certificate verify|eof occurred"
                r"|max retries exceeded|failed to establish a new connection|broken pipe|unable to connect"),
    ("GONE", r"private|removed|unavailable|video is not available|does not exist|no longer available"
             r"|terminated|deleted"),
]
_COMPILED = [(block, re.compile(rx, re.I)) for block, rx in _PATTERNS]

HINTS = {
    "BOT_WALL": "YouTube is asking for a bot check — try mobile data or turn off VPN",
    "NETWORK": "Network problem — check your connection",
    "LOGIN_REQUIRED": "YouTube requires sign-in for this one",
    "REGION": "Not available in your region",
    "RATE_LIMITED": "YouTube is rate-limiting, try again in a minute",
    "MEMBERS_OR_PAID": "Members-only or paid content",
    "GONE": "This video was removed or is private",
    "UNKNOWN": "Couldn't play this one",
    EXPIRED: "The stream link expired — reloading",
}

_NETWORK_TYPES = (requests.ConnectionError, requests.Timeout, TimeoutError, socket.timeout, ConnectionError)


class StreamError(Exception):
    """A failure whose block is already known (e.g. GONE after every client said so)."""

    def __init__(self, block, message):
        super().__init__(message)
        self.block = block


def classify(message):
    text = str(message or "")
    for block, rx in _COMPILED:
        if rx.search(text):
            return block
    return "UNKNOWN"


def classify_exc(e):
    if isinstance(e, StreamError):
        return e.block
    if isinstance(e, _NETWORK_TYPES):
        return "NETWORK"
    return classify(e)


def hint(block):
    return HINTS.get(block, HINTS["UNKNOWN"])


def message(err, limit=None):
    """An exception's (or a message's) text fit to show or log: secrets masked first, then cut to `limit`, so the
    cut can't keep part of a password or token that the full text would have masked."""
    return log.redact(str(err))[:limit]


def payload(err, block=None):
    """{error, block, hint} for an exception or a message; `block` overrides the classification, which reads the
    raw text (the redacted one could hide a word it matches)."""
    block = block or (classify_exc(err) if isinstance(err, BaseException) else classify(err))
    return {"error": message(err, 300), "block": block, "hint": hint(block)}
