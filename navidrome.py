import hashlib
import secrets

import requests

from config import NAVIDROME_PASS, NAVIDROME_URL, NAVIDROME_USER


def configured():
    return bool(NAVIDROME_USER and NAVIDROME_PASS)


def _auth_params():
    salt = secrets.token_hex(6)
    token = hashlib.md5((NAVIDROME_PASS + salt).encode()).hexdigest()
    return {"u": NAVIDROME_USER, "t": token, "s": salt, "v": "1.16.1", "c": "reef", "f": "json"}


def _call(endpoint, **params):
    r = requests.get(f"{NAVIDROME_URL}/rest/{endpoint}", params={**_auth_params(), **params}, timeout=10)
    r.raise_for_status()
    body = r.json()["subsonic-response"]
    if body.get("status") != "ok":
        raise RuntimeError(body.get("error", {}).get("message", "Navidrome returned an error"))
    return body


def ping():
    if not configured():
        return {"ok": False, "error": "REEF_NAVIDROME_USER/PASS not set"}
    try:
        body = _call("ping")
        return {"ok": True, "version": body.get("serverVersion", "")}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def rescan():
    if not configured():
        return False
    try:
        _call("startScan")
        return True
    except Exception:
        return False
