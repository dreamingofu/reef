"""termux/update.sh against a throwaway git "origin" and "phone" checkout: the boot script is a fake that
marks Reef healthy (or doesn't), pip is `true`/`false`, so nothing real is installed or restarted."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from fake_reef import FakeReef

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


def run(tmp, phone, *args, pip="true", wait=2, health_cmd=True, **extra):
    """update.sh with `extra` REEF_* variables; `health_cmd=False` leaves REEF_UPDATE_HEALTH_CMD unset, so the real
    curl checks of /api/health and /api/diagnostics run."""
    mark = tmp / "healthy"
    mark.unlink(missing_ok=True)
    env = {k: v for k, v in os.environ.items() if not k.startswith("REEF_")}
    env.update(REEF_UPDATE_PIP=pip, REEF_UPDATE_BOOT_DIR=str(tmp / "boot"), REEF_UPDATE_WAIT=str(wait),
               REEF_TEST_MARK=str(mark), GIT_CONFIG_GLOBAL=os.devnull, **extra)
    if health_cmd:
        env["REEF_UPDATE_HEALTH_CMD"] = f"test -s {mark}"
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


# ---------- the real health checks (no REEF_UPDATE_HEALTH_CMD) against a fake Reef, with REEF_OPEN=1 + a password ----------

PASSWORD = "p w ü $(touch PWNED) `id` ;|&'\"\\ :x"
needs_curl = pytest.mark.skipif(not (shutil.which("curl") and shutil.which("base64")), reason="needs curl and base64")


@pytest.fixture
def updated(repos):
    """origin has v2 waiting; the fake Reef reports v2's short sha once "restarted"."""
    tmp, origin, phone, v1 = repos
    v2 = commit(origin, GOOD_BOOT, "v2")
    with FakeReef(reef=v2[:7]) as fake:
        yield tmp, phone, v1, v2, fake


def assert_no_secret(tmp, out, fake):
    assert not (tmp / "PWNED").exists() and not (Path.cwd() / "PWNED").exists()  # nothing was evaluated
    for secret in (PASSWORD, "p w ü", "touch PWNED"):
        assert secret not in out
    assert all(h is None or h not in out for h in fake.headers())  # nor the Basic token


def accepted_by_reef(header):
    """The header update.sh sent passes the real app's own check (loopback needs the password under OPEN=1)."""
    import app
    import config
    saved = config.OPEN, config.PASSWORD
    config.OPEN, config.PASSWORD = True, PASSWORD
    try:
        c = app.app.test_client()
        return c.get("/api/health", headers={"Authorization": header},
                     environ_base={"REMOTE_ADDR": "127.0.0.1"}).status_code == 200
    finally:
        config.OPEN, config.PASSWORD = saved


@needs_curl
def test_open_with_password_from_the_environment(updated):
    tmp, phone, v1, v2, fake = updated
    fake.password = PASSWORD
    with FakeReef() as proxy:  # a proxy from the environment never sees the probe (or its password)
        code, out = run(tmp, phone, health_cmd=False, REEF_PORT=str(fake.port), REEF_OPEN="1", REEF_PASSWORD=PASSWORD,
                        http_proxy=f"http://127.0.0.1:{proxy.port}", HTTP_PROXY=f"http://127.0.0.1:{proxy.port}")
        assert not proxy.seen
    assert code == 0 and out.strip().splitlines()[-1] == f"UPDATE_OK {v2[:7]}", out
    assert {p for p, _ in fake.seen} == {"/api/health", "/api/diagnostics"}
    sent = fake.headers() - {None}
    assert len(sent) == 1 and accepted_by_reef(sent.pop())
    assert_no_secret(tmp, out, fake)


@needs_curl
@pytest.mark.parametrize("password", [None, "wrong"])
def test_open_with_a_missing_or_wrong_password_rolls_back(updated, password):
    tmp, phone, v1, v2, fake = updated
    fake.password = PASSWORD
    extra = {"REEF_PASSWORD": password} if password else {}
    code, out = run(tmp, phone, wait=1, health_cmd=False, REEF_PORT=str(fake.port), REEF_OPEN="1", **extra)
    assert code == 1 and "rolled back to" in out.strip().splitlines()[-1], out
    assert git(phone, "rev-parse", "HEAD") == v1 and fake.seen
    assert_no_secret(tmp, out, fake)


@needs_curl
def test_default_mode_sends_no_credentials(updated):
    tmp, phone, v1, v2, fake = updated
    code, out = run(tmp, phone, health_cmd=False, REEF_PORT=str(fake.port))
    assert code == 0 and out.strip().splitlines()[-1] == f"UPDATE_OK {v2[:7]}", out
    # A password without REEF_OPEN=1: loopback never needs it, so it isn't sent anywhere.
    commit(phone.parent / "origin", GOOD_BOOT, "v3")
    fake.reef = git(phone.parent / "origin", "rev-parse", "--short=7", "HEAD")
    code, out = run(tmp, phone, health_cmd=False, REEF_PORT=str(fake.port), REEF_PASSWORD=PASSWORD)
    assert code == 0, out
    assert fake.headers() == {None}


@needs_curl
def test_a_different_commit_is_still_unhealthy(updated):
    tmp, phone, v1, v2, fake = updated
    fake.password, fake.reef = PASSWORD, v1[:7]  # the old process still answering
    code, out = run(tmp, phone, wait=1, health_cmd=False, REEF_PORT=str(fake.port), REEF_OPEN="1",
                    REEF_PASSWORD=PASSWORD)
    assert code == 1 and f"rolled back to {v1[:7]}, which is running" in out.strip().splitlines()[-1], out


def reef_env(phone, text):
    (phone / "reef.env").write_text(text, encoding="utf-8")  # untracked, like the phone's own


@needs_curl
def test_password_open_and_port_from_reef_env_like_config_reads_them(updated):
    """reef.env is data, read like config.py: first line of a key wins, quotes around a value go, nothing runs."""
    tmp, phone, v1, v2, fake = updated
    fake.password = PASSWORD
    reef_env(phone, f"# Reef settings\n  REEF_OPEN = '1'\nREEF_PORT=\"{fake.port}\"\r\n"
                    f'REEF_PASSWORD = "{PASSWORD}"  \nREEF_PASSWORD=second-line-loses\nREEF_PORT=1\n'
                    f"export REEF_PASSWORD=not-a-config-key\n")
    code, out = run(tmp, phone, health_cmd=False)
    assert code == 0 and out.strip().splitlines()[-1] == f"UPDATE_OK {v2[:7]}", out
    sent = fake.headers() - {None}
    assert len(sent) == 1 and accepted_by_reef(sent.pop())
    assert_no_secret(tmp, out, fake)


@needs_curl
def test_the_environment_wins_over_reef_env(updated):
    tmp, phone, v1, v2, fake = updated
    fake.password = PASSWORD
    reef_env(phone, f"REEF_OPEN=1\nREEF_PASSWORD=wrong\nREEF_PORT={fake.port}\n")
    code, out = run(tmp, phone, health_cmd=False, REEF_PASSWORD=PASSWORD)
    assert code == 0, out
    # An empty REEF_PASSWORD in the environment still wins (config's setdefault): no password, so 401s.
    commit(phone.parent / "origin", GOOD_BOOT, "v3")
    code, out = run(tmp, phone, wait=1, health_cmd=False, REEF_PASSWORD="")
    assert code == 1, out


@needs_curl
def test_a_redirect_elsewhere_is_unhealthy_and_gets_no_password(updated):
    tmp, phone, v1, v2, fake = updated
    with FakeReef(reef=v2[:7]) as elsewhere:
        fake.password, fake.redirect = PASSWORD, f"http://127.0.0.1:{elsewhere.port}/api/diagnostics"
        code, out = run(tmp, phone, wait=1, health_cmd=False, REEF_PORT=str(fake.port), REEF_OPEN="1",
                        REEF_PASSWORD=PASSWORD)
        assert code == 1 and "rolled back to" in out, out
        assert not elsewhere.seen
