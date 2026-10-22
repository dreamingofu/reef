"""termux/update.sh against a throwaway git "origin" and "phone" checkout: the boot script is a fake that
marks Reef healthy (or doesn't), pip is `true`/`false`, so nothing real is installed or restarted."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "termux" / "update.sh"
GOOD_BOOT = '#!/bin/bash\necho "$(git rev-parse --short HEAD)" > "$REEF_TEST_MARK"\n'
BAD_BOOT = "#!/bin/bash\nexit 0  # never comes up\n"

pytestmark = pytest.mark.skipif(not (shutil.which("bash") and shutil.which("git")), reason="needs bash and git")


def git(cwd, *args):
    out = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "init.defaultBranch=main",
                          "-c", "commit.gpgsign=false", *args],
                         cwd=cwd, capture_output=True, text=True, check=True)
    return out.stdout.strip()


def commit(repo, boot, msg):
    (repo / "termux").mkdir(exist_ok=True)
    shutil.copy(SCRIPT, repo / "termux" / "update.sh")
    (repo / "termux" / "reef-boot.sh").write_text(boot)
    (repo / "requirements.txt").write_text(f"# {msg}\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", msg)
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repos(tmp_path):
    origin, phone = tmp_path / "origin", tmp_path / "phone"
    origin.mkdir()
    git(origin, "init", "-q")
    v1 = commit(origin, GOOD_BOOT, "v1")
    git(tmp_path, "clone", "-q", str(origin), str(phone))
    return tmp_path, origin, phone, v1


def run(tmp, phone, *args, pip="true", wait=2):
    mark = tmp / "healthy"
    mark.unlink(missing_ok=True)
    env = {k: v for k, v in os.environ.items() if not k.startswith("REEF_")}
    env.update(REEF_UPDATE_PIP=pip, REEF_UPDATE_BOOT_DIR=str(tmp / "boot"), REEF_UPDATE_WAIT=str(wait),
               REEF_TEST_MARK=str(mark), REEF_UPDATE_HEALTH_CMD=f"test -s {mark}",
               GIT_CONFIG_GLOBAL=os.devnull)
    p = subprocess.run(["bash", str(phone / "termux" / "update.sh"), *args], cwd=tmp, env=env,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=60)
    return p.returncode, p.stdout


def test_syntax():
    assert subprocess.run(["bash", "-n", str(SCRIPT)]).returncode == 0


def test_update_ok(repos):
    tmp, origin, phone, v1 = repos
    v2 = commit(origin, GOOD_BOOT, "v2")
    code, out = run(tmp, phone)
    assert code == 0, out
    assert out.strip().splitlines()[-1] == f"UPDATE_OK {v2[:7]}"
    assert git(phone, "rev-parse", "HEAD") == v2
    boot = tmp / "boot" / "reef.sh"
    assert boot.read_text() == GOOD_BOOT and os.access(boot, os.X_OK)
    assert (tmp / "healthy").read_text().strip() == v2[:7]  # restarted through the copied boot script


def test_unhealthy_update_rolls_back(repos):
    tmp, origin, phone, v1 = repos
    commit(origin, BAD_BOOT, "v2 broken")
    code, out = run(tmp, phone)
    assert code == 1, out
    last = out.strip().splitlines()[-1]
    assert last.startswith("UPDATE_FAILED") and f"rolled back to {v1[:7]}, which is running" in last
    assert git(phone, "rev-parse", "HEAD") == v1
    assert (tmp / "boot" / "reef.sh").read_text() == GOOD_BOOT


def test_pip_failure_rolls_back(repos):
    tmp, origin, phone, v1 = repos
    commit(origin, GOOD_BOOT, "v2")
    code, out = run(tmp, phone, pip="false")
    assert code == 1 and "pip install failed" in out and git(phone, "rev-parse", "HEAD") == v1


def test_pull_failure_changes_nothing(repos):
    tmp, origin, phone, v1 = repos
    commit(origin, GOOD_BOOT, "v2")
    (phone / "local.txt").write_text("diverged")
    git(phone, "add", "local.txt")
    git(phone, "commit", "-qm", "local")
    local = git(phone, "rev-parse", "HEAD")
    code, out = run(tmp, phone)
    assert code == 1 and out.strip().splitlines()[-1].startswith("UPDATE_FAILED git pull")
    assert git(phone, "rev-parse", "HEAD") == local and not (tmp / "healthy").exists()  # not restarted


def test_dry_run_and_log(repos):
    tmp, origin, phone, v1 = repos
    commit(origin, GOOD_BOOT, "v2")
    code, out = run(tmp, phone, "--dry-run")
    assert code == 0 and out.strip().splitlines()[-1] == "DRY_RUN" and git(phone, "rev-parse", "HEAD") == v1
    log = tmp / "logs" / "update.log"
    code, out = run(tmp, phone, "--log", str(log))
    assert code == 0 and "UPDATE_OK" in log.read_text()
