import json
import threading
import time

from config import HISTORY_FILE

_MAX = 500
_lock = threading.Lock()


def _read():
    try:
        return json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def add(entry):
    with _lock:
        items = [i for i in _read() if i.get("id") != entry["id"]]
        items.insert(0, {**entry, "kept_at": time.time()})
        HISTORY_FILE.write_text(json.dumps(items[:_MAX], ensure_ascii=False), encoding="utf-8")


def list_recent(limit=50):
    with _lock:
        return _read()[:limit]


def ids():
    with _lock:
        return {i.get("id") for i in _read()}
