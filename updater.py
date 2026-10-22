"""Update check and in-app update (production plan P1.11).

Check: this checkout's commit vs the newest on GitHub `main` (`/repos/<repo>/commits/main`, with If-None-Match so
an unchanged answer is a free 304, and `/compare/<local>...<latest>` once per new pair for "N behind"), and the
running yt-dlp vs PyPI's latest. Every 6 h (30 min after a failure), cached in `update.json`, so restarts don't
spend GitHub's 60-requests-an-hour anonymous limit.

Update: `POST /api/update` starts a detached child (its own session, so the restart it causes doesn't take it
down) and answers at once:
  reef  → `bash termux/update.sh` (pull, pip install, restart through the boot script, wait for the new commit,
          roll back on failure);
  ytdlp → this file run as a script: `pip install -U yt-dlp`, restart, wait for /api/health with the new
          version, else reinstall the previous one and restart again;
  both  → the yt-dlp upgrade, then update.sh (whose restart picks both up).
Output goes to logs/update.log; its last `UPDATE_OK …` / `UPDATE_FAILED …` line is the result. The run's pid and
start time sit in update.json, so the new process can report on an update the old one started. Only on Termux
(or with REEF_ALLOW_SELF_UPDATE=1): a dev checkout is never git-pulled by surprise.
"""
import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
import urllib.request
from importlib import metadata as importlib_metadata
from pathlib import Path

import requests
import yt_dlp

import config
import log
import settings
import store

logger = logging.getLogger(__name__)

GITHUB = "https://api.github.com"
PYPI = "https://pypi.org/pypi/yt-dlp/json"
TIMEOUT = 6
CHECK_EVERY = 6 * 3600
RETRY_FAILED = 30 * 60
RUN_MAX_S = 20 * 60    # an update still "running" after this is treated as dead (a pid reused after a reboot)
TAIL = 20
WHAT = ("reef", "ytdlp", "both")
HEALTH_WAIT_S = 30

_file = store.JsonFile(lambda: config.UPDATE_FILE, dict)
_session = requests.Session()
_session.headers["User-Agent"] = "Reef (https://github.com/dreamingofu/reef)"
_check_lock = threading.Lock()
_run_lock = threading.Lock()
_proc = None    # the child this process started (reaped through poll(); after a restart, kill(0) on the pid)
_local = None


class UpdateError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


# ---------- versions ----------

def _git(*args):
    out = subprocess.run(["git", "-C", str(config.BASE_DIR), *args], capture_output=True, text=True, timeout=5)
    return out.stdout.strip() if out.returncode == 0 else ""


def local():
    """{sha, short, date (epoch)} of the running checkout, read once (an update restarts the process)."""
    global _local
    if _local is None:
        try:
            sha, _, ts = _git("log", "-1", "--format=%H %ct").partition(" ")
        except (OSError, subprocess.SubprocessError):
            sha, ts = "", ""
        _local = {"sha": sha or None, "short": sha[:7] or None, "date": int(ts) if ts.isdigit() else None}
    return dict(_local)


def ytdlp_installed():
    return yt_dlp.version.__version__


def version_key(v):
    """'2025.10.22' / '2025.10.22.1' -> comparable tuple of ints."""
    return tuple(int(x) for x in re.findall(r"\d+", str(v or "")))


def _get(url, prev=None):
    """(status, json|None, etag) with If-None-Match from `prev`; raises requests errors."""
    headers = {"Accept": "application/vnd.github+json"} if url.startswith(GITHUB) else {"Accept": "application/json"}
    if prev and prev.get("etag"):
        headers["If-None-Match"] = prev["etag"]
    r = _session.get(url, headers=headers, timeout=TIMEOUT)
    if r.status_code == 304:
        return 304, None, prev.get("etag")
    body = None
    if r.status_code == 200:
        body = r.json()
    return r.status_code, body, r.headers.get("ETag")


def _github(prev, now):
    try:
        status, body, etag = _get(f"{GITHUB}/repos/{config.UPDATE_REPO}/commits/main", prev)
    except (requests.RequestException, ValueError) as e:
        return {**prev, "checked_at": now, "error": f"GitHub: {log.redact(str(e))[:160]}"}
    if status == 304:
        return {**prev, "checked_at": now, "error": None}
    if status != 200 or not isinstance(body, dict) or not body.get("sha"):
        return {**prev, "checked_at": now, "error": f"GitHub answered {status}"}
    commit = body.get("commit") or {}
    return {"sha": body["sha"], "date": (commit.get("committer") or {}).get("date"),
            "message": str(commit.get("message") or "").split("\n", 1)[0][:120], "etag": etag,
            "checked_at": now, "error": None}


def _pypi(prev, now):
    try:
        status, body, etag = _get(PYPI, prev)
    except (requests.RequestException, ValueError) as e:
        return {**prev, "checked_at": now, "error": f"PyPI: {log.redact(str(e))[:160]}"}
    if status == 304:
        return {**prev, "checked_at": now, "error": None}
    version = ((body or {}).get("info") or {}).get("version") if isinstance(body, dict) else None
    if status != 200 or not version:
        return {**prev, "checked_at": now, "error": f"PyPI answered {status}"}
    return {"version": str(version), "etag": etag, "checked_at": now, "error": None}


def _compare(base, head):
    """(behind: bool|None, count|None) of `base` against `head`; None when GitHub doesn't know `base`."""
    try:
        r = _session.get(f"{GITHUB}/repos/{config.UPDATE_REPO}/compare/{base}...{head}?per_page=1",
                         headers={"Accept": "application/vnd.github+json"}, timeout=TIMEOUT)
        body = r.json() if r.status_code == 200 else None
    except (requests.RequestException, ValueError):
        return None, None
    if not isinstance(body, dict):
        return None, None
    status, ahead = body.get("status"), body.get("ahead_by")
    if status in ("ahead", "diverged"):  # head has commits base lacks: this checkout is behind
        return True, ahead if isinstance(ahead, int) else None
    if status in ("identical", "behind"):  # behind = this checkout is ahead of main (unpushed work)
        return False, 0
    return None, None


def _due(part, now):
    if not part.get("checked_at"):
        return True
    return now - part["checked_at"] >= (RETRY_FAILED if part.get("error") else CHECK_EVERY)


def check(force=False, now=None):
    """Refresh what's due (everything with `force`; ETags still apply) and return the /api/version body.
    The HTTP happens outside the file lock; the result is merged into update.json as it is *then*, so an update
    run recorded meanwhile (start / status write `run` under the lock) is never overwritten by a stale copy."""
    now = time.time() if now is None else now
    with _check_lock:
        data = dict(_file.load())
        gh, py = dict(data.get("github") or {}), dict(data.get("pypi") or {})
        changed = False
        if force or _due(gh, now):
            gh, changed = _github(gh, now), True
        if force or _due(py, now):
            py, changed = _pypi(py, now), True
        mine, latest = local()["sha"], gh.get("sha")
        cmp_ = data.get("compare") or {}
        if mine and latest and mine != latest and (cmp_.get("base"), cmp_.get("head")) != (mine, latest):
            behind, count = _compare(mine, latest)
            if behind is not None:  # an unknown answer is asked again next time
                cmp_, changed = {"base": mine, "head": latest, "behind": behind, "count": count}, True
        if changed:
            with _file.lock:
                data = dict(_file.load())  # only the check's own keys change
                data.update(github=gh, pypi=py, compare=cmp_)
                _file.save(data)
    return body(data)


def body(data=None):
    data = dict(_file.load()) if data is None else data
    gh, py, cmp_ = data.get("github") or {}, data.get("pypi") or {}, data.get("compare") or {}
    mine = local()
    latest = gh.get("sha")
    if not latest or not mine["sha"]:
        behind, count = None, None
    elif latest == mine["sha"]:
        behind, count = False, 0
    elif (cmp_.get("base"), cmp_.get("head")) == (mine["sha"], latest):
        behind, count = cmp_.get("behind"), cmp_.get("count")
    else:
        behind, count = None, None  # GitHub doesn't know this commit (a dev checkout) or compare failed
    installed, newest = ytdlp_installed(), py.get("version")
    skipped = settings.get("skipped_version") or None
    allowed, why = can_self_update()
    checked = [t for t in (gh.get("checked_at"), py.get("checked_at")) if t]
    return {
        "reef": mine,
        "latest_reef": {"sha": latest, "short": latest[:7], "date": gh.get("date"), "message": gh.get("message")}
        if latest else None,
        "behind": behind, "behind_count": count,
        "ytdlp": installed, "latest_ytdlp": newest,
        "ytdlp_outdated": version_key(newest) > version_key(installed) if newest else None,
        "checked_at": min(checked) if checked else None,
        "skipped_version": skipped,
        "skipped": bool(skipped and latest and latest.startswith(skipped)),
        "errors": [e for e in (gh.get("error"), py.get("error")) if e],
        "self_update": {"allowed": allowed, "reason": why},
    }


def tick(now=None):
    """Background check (the app's chores thread, every minute): only does something when a part is due."""
    now = time.time() if now is None else now
    data = _file.load()
    if _due(data.get("github") or {}, now) or _due(data.get("pypi") or {}, now):
        check(now=now)


# ---------- running an update ----------

def is_termux():
    # PREFIX / TERMUX_VERSION come with Termux's shell; Termux:Boot scripts may lack the latter, never the files dir.
    return ("com.termux" in os.environ.get("PREFIX", "") or bool(os.environ.get("TERMUX_VERSION"))
            or os.path.isdir("/data/data/com.termux/files"))


def can_self_update():
    if is_termux() or config.ALLOW_SELF_UPDATE:
        return True, None
    return False, "In-app updates only run on the phone (Termux). Set REEF_ALLOW_SELF_UPDATE=1 to allow them here."


def log_path():
    return Path(config.LOG_DIR or config.BASE_DIR / "logs") / "update.log"


def _command(what):
    if what == "reef":
        return ["bash", str(config.BASE_DIR / "termux" / "update.sh")]
    return [sys.executable, str(Path(__file__).resolve()), what]


def _spawn(cmd, path):
    """Start the update detached from this process (own session: the restart kills this process, not it)."""
    global _proc
    path.parent.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "REEF_DIR": str(config.BASE_DIR), "REEF_UPDATE_PIP": f"{sys.executable} -m pip",
           "PYTHONUNBUFFERED": "1"}
    with open(path, "w", encoding="utf-8") as out:  # a fresh log per run: its tail is this run's progress
        _proc = subprocess.Popen(cmd, cwd=str(config.BASE_DIR), stdin=subprocess.DEVNULL, stdout=out,
                                 stderr=subprocess.STDOUT, start_new_session=True, env=env)
    return _proc.pid


def _alive(pid):
    if _proc is not None and _proc.pid == pid:
        return _proc.poll() is None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except (OSError, TypeError, ValueError):
        return False
    return True


def _tail(path, n=TAIL):
    try:
        lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    return [log.redact(ln)[:300] for ln in lines[-n:]]


# The outcome line: update.sh prints it bare, older yt-dlp children printed it after a "YYYY-MM-DD HH:MM:SS " stamp.
_MARKER = re.compile(r"^(?:\d{4}-\d\d-\d\d \d\d:\d\d:\d\d )?(UPDATE_OK|UPDATE_FAILED)\b")


def _result(path):
    """"ok" / "failed" from the last outcome line of the whole log (not just the tail), else None."""
    try:
        lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    for ln in reversed(lines):
        m = _MARKER.match(ln.strip())
        if m:
            return "ok" if m.group(1) == "UPDATE_OK" else "failed"
    return None


def status(now=None):
    """GET /api/update/status."""
    now = time.time() if now is None else now
    run = dict(_file.load().get("run") or {})
    if not run:
        return {"running": False, "what": None, "started_at": None, "finished_at": None, "result": None,
                "log_tail": []}
    path = run.get("log") or log_path()
    tail = _tail(path)
    running = (not run.get("finished_at") and _alive(run.get("pid"))
               and now - (run.get("started_at") or 0) < RUN_MAX_S)
    if not running:
        # The log decides, also after a restart in the middle of the run (this process never saw the child start):
        # an outcome line = that outcome; a child gone without one = failed. Re-read each time, so a result stored
        # wrongly by an older Reef (it missed stamped markers) corrects itself.
        result = _result(path) or "failed"
        if not run.get("finished_at") or run.get("result") != result:
            with _file.lock:
                data = dict(_file.load())
                if (data.get("run") or {}).get("pid") == run.get("pid"):
                    run.update(finished_at=run.get("finished_at") or now, result=result)
                    data["run"] = run
                    _file.save(data)
    return {"running": running, "what": run.get("what"), "started_at": run.get("started_at"),
            "finished_at": run.get("finished_at"), "result": None if running else run.get("result"),
            "log_tail": tail}


def start(what, confirm, now=None):
    """POST /api/update. Raises UpdateError(http status, message) when refused."""
    now = time.time() if now is None else now
    if what not in WHAT:
        raise UpdateError(400, "what must be reef, ytdlp or both")
    if confirm is not True:
        raise UpdateError(400, "send confirm: true to update")
    allowed, why = can_self_update()
    if not allowed:
        raise UpdateError(403, why)
    with _run_lock:
        if status(now)["running"]:
            raise UpdateError(409, "an update is already running")
        path = log_path()
        pid = _spawn(_command(what), path)
        with _file.lock:
            data = dict(_file.load())
            data["run"] = {"what": what, "pid": pid, "started_at": now, "log": str(path)}
            _file.save(data)
    logger.info(f"update start what={what} pid={pid}")
    return status(now)


# ---------- the yt-dlp child (python updater.py ytdlp|both) ----------

def _say(msg):
    """A progress line with a time stamp; the outcome line (UPDATE_OK / UPDATE_FAILED) bare, like update.sh's."""
    marker = msg.startswith(("UPDATE_OK", "UPDATE_FAILED"))
    print(msg if marker else f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}", flush=True)


def _dist_version():
    try:
        return importlib_metadata.version("yt-dlp")
    except importlib_metadata.PackageNotFoundError:
        return None


def _pip(*args):
    return subprocess.call([sys.executable, "-m", "pip", "install", "-q", *args])


def _restart():
    boot = Path(os.environ.get("REEF_UPDATE_BOOT_DIR") or Path.home() / ".termux" / "boot") / "reef.sh"
    script = boot if boot.exists() else config.BASE_DIR / "termux" / "reef-boot.sh"
    subprocess.call(["bash", str(script)])


def _healthy(version, wait_s=HEALTH_WAIT_S):
    """True once the restarted Reef answers /api/diagnostics with yt-dlp `version`."""
    url = f"http://127.0.0.1:{config.PORT}/api/diagnostics"
    end = time.monotonic() + wait_s
    while time.monotonic() < end:
        try:
            with urllib.request.urlopen(url, timeout=5) as r:
                running = json.loads(r.read().decode()).get("version", {}).get("ytdlp")
                if version_key(running) == version_key(version):  # PyPI's metadata drops leading zeros
                    return True
        except Exception:
            pass
        time.sleep(1)
    return False


def _child(what):
    prev = _dist_version()
    _say(f"yt-dlp update (installed {prev or '?'})")
    if _pip("-U", "yt-dlp") != 0:
        _say("UPDATE_FAILED pip install -U yt-dlp failed (nothing changed)")
        return 1
    new = _dist_version()
    _say(f"yt-dlp {prev or '?'} -> {new or '?'}")
    if what == "ytdlp" and new == prev:
        _say(f"UPDATE_OK yt-dlp {new} (already the newest; no restart needed)")
        return 0
    if what == "both":  # update.sh pulls, installs, restarts and checks; its restart loads the new yt-dlp too
        sys.stdout.flush()
        os.execvp("bash", ["bash", str(config.BASE_DIR / "termux" / "update.sh")])
    _restart()
    if _healthy(new):
        _say(f"UPDATE_OK yt-dlp {new}")
        return 0
    if prev and prev != new:
        _say(f"Reef didn't come back with yt-dlp {new}; reinstalling {prev}")
        _pip(f"yt-dlp=={prev}")
        _restart()
        ok = _healthy(prev)
        _say(f"UPDATE_FAILED yt-dlp {new} didn't start; back on {prev}" + ("" if ok else ", which isn't answering either"))
    else:
        _say(f"UPDATE_FAILED Reef didn't answer within {HEALTH_WAIT_S}s after the restart")
    return 1


if __name__ == "__main__":
    sys.exit(_child(sys.argv[1] if len(sys.argv) > 1 else "ytdlp"))
