"""The deploy scripts (deploy/*.sh), run by bash in scratch directories with stub uv, sudo,
systemctl, curl, sleep and gh on PATH, and the smoke-check points against the app."""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from broombuster.cities import REGIONS

_DEPLOY = Path(__file__).parent.parent / "deploy"
_PAGE = "<title>BroomBuster</title>"
needs_bash = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("git") is None, reason="bash or git missing"
)

# The checked-out revision's `mode` file decides how the stubs answer; `title` is the page title.
_STUBS = {
    "uv": '[ "$(cat mode 2>/dev/null)" != uvfail ]',
    "sudo": 'echo "$* $(cat mode)" >> "$LOG"',
    "sleep": ":",
    "curl": """for a; do url=$a; done
case "$(cat mode):$url" in
  down:*/health) exit 22 ;;
  *:*/health) echo '{"status":"ok"}' ;;
  nostreet:*/api/check) echo '{"covered":false}' ;;
  *:*/api/check) echo '{"covered":true}' ;;
  *) echo "<title>$(cat title 2>/dev/null || echo BroomBuster)</title>" ;;
esac""",
    "gh": """while [ $# -gt 0 ]; do [ "$1" = --commit ] && sha=$2; shift; done
line="$(grep "^$sha " "$RUNS")" && echo "${line#* }" || echo "null null"
""",
}
# install.sh: sudo runs the command with /etc and /var/lib moved under $ROOT (chmod: no-op).
_INSTALL_STUBS = {
    "uv": ":",
    "visudo": ":",
    "sleep": ":",
    "systemctl": 'echo "$*" >> "$LOG"; [ "$1" != is-active ]',
    "sudo": """[ "$1" = chmod ] && exit 0
args=()
for a; do case $a in /etc/*|/var/lib/*) a=$ROOT$a ;; esac; args+=("$a"); done
exec "${args[@]}"
""",
}


def _stubs(tmp: Path, stubs: dict) -> dict:
    """Stub commands in tmp/bin; the environment that puts them first on PATH."""
    bin_dir = tmp / "bin"
    bin_dir.mkdir()
    for name, body in stubs.items():
        (bin_dir / name).write_text(f"#!/usr/bin/env bash\n{body}\n")
        (bin_dir / name).chmod(0o755)
    return {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "HOME": str(tmp),
        "LOG": str(tmp / "log"),
        "RUNS": str(tmp / "runs"),
        "ROOT": str(tmp / "root"),
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        **{f"GIT_{who}_NAME": "t" for who in ("AUTHOR", "COMMITTER")},
        **{f"GIT_{who}_EMAIL": "t@t" for who in ("AUTHOR", "COMMITTER")},
    }


def _git(cwd: Path, env: dict, *args: str) -> str:
    out = subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


def _repo(tmp: Path, update_sh: str, modes: list[str], files=None) -> tuple[Path, dict]:
    """A host checkout at the first of `modes`' commits, origin/main at the last; `files`
    gives a commit (by mode) extra files, None to delete one."""
    env = _stubs(tmp, _STUBS)
    (tmp / "runs").write_text("")
    _git(tmp, env, "init", "--quiet", "--bare", "-b", "main", "origin.git")
    pi = tmp / "pi"
    _git(tmp, env, "init", "--quiet", "-b", "main", str(pi))
    _git(pi, env, "remote", "add", "origin", str(tmp / "origin.git"))
    (pi / "deploy").mkdir()
    shutil.copy(_DEPLOY / "auto-update.sh", pi / "deploy")
    shutil.copy(_DEPLOY / "smoke.sh", pi / "deploy")
    (pi / "deploy" / "update.sh").write_text(update_sh)
    (pi / "deploy" / "update.sh").chmod(0o755)
    for mode in modes:
        (pi / "mode").write_text(mode)
        for path, text in (files or {}).get(mode, {}).items():
            if text is None:
                (pi / path).unlink()
            else:
                (pi / path).write_text(text)
        _git(pi, env, "add", "-A")
        _git(pi, env, "commit", "--quiet", "-m", mode)
    _git(pi, env, "push", "--quiet", "origin", "main")
    _git(pi, env, "reset", "--quiet", "--hard", f"HEAD~{len(modes) - 1}")
    _git(pi, env, "fetch", "--quiet", "origin")
    return pi, env


def _run(cwd: Path, env: dict, script: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", f"deploy/{script}"], cwd=cwd, env=env, capture_output=True, text=True, timeout=60
    )


def _log(env: dict) -> list[str]:
    log = Path(env["LOG"])
    return log.read_text().splitlines() if log.exists() else []


def _update(tmp, modes, files=None):
    pi, env = _repo(tmp, (_DEPLOY / "update.sh").read_text(), modes, files)
    good, target = (_git(pi, env, "rev-parse", r) for r in ("HEAD", "origin/main"))
    r = _run(pi, env, "update.sh")
    bad = pi / ".deploy-bad"
    head = _git(pi, env, "rev-parse", "HEAD")
    return r, head, good, target, bad.read_text().strip() if bad.exists() else None, _log(env)


# ── update.sh ────────────────────────────────────────────────────────────────


@needs_bash
def test_update_deploys_a_revision_that_passes_every_check(tmp_path):
    r, head, _, target, bad, log = _update(tmp_path, ["good", "next"])
    assert r.returncode == 0, r.stderr
    assert (head, bad, log) == (target, None, ["systemctl restart broombuster next"])


@needs_bash
def test_update_runs_the_target_revisions_checks(tmp_path):
    renamed = (_DEPLOY / "smoke.sh").read_text().replace(_PAGE, "<title>Renamed</title>")
    files = {"next": {"deploy/smoke.sh": renamed, "title": "Renamed"}}
    r, head, _, target, bad, _ = _update(tmp_path, ["good", "next"], files)
    assert r.returncode == 0, r.stderr
    assert (head, bad) == (target, None)


@needs_bash
def test_update_without_smoke_script_checks_health_only(tmp_path):
    r, head, _, target, bad, _ = _update(
        tmp_path, ["good", "nostreet"], {"good": {"deploy/smoke.sh": None}}
    )
    assert r.returncode == 0, r.stderr
    assert (head, bad) == (target, None)


@needs_bash
@pytest.mark.parametrize("mode", ["down", "nostreet"])
def test_update_rolls_back_and_marks_a_failed_deploy(tmp_path, mode):
    r, head, good, target, bad, log = _update(tmp_path, ["good", mode])
    assert r.returncode == 1
    assert (head, bad) == (good, target)
    assert log == [f"systemctl restart broombuster {mode}", "systemctl restart broombuster good"]


@needs_bash
def test_update_failed_install_restores_without_restart_or_mark(tmp_path):
    r, head, good, _, bad, log = _update(tmp_path, ["good", "uvfail"])
    assert r.returncode == 1
    assert (head, bad, log) == (good, None, [])  # retried on the next run


# ── auto-update.sh ───────────────────────────────────────────────────────────


@needs_bash
@pytest.mark.parametrize(
    ("runs", "bad", "verify", "rc", "deployed"),
    [
        ({"C": "completed success"}, None, False, 0, "C"),
        ({"B": "completed success"}, None, False, 0, "B"),  # C has no CI run ([skip ci])
        ({"C": "completed cancelled", "B": "completed success"}, None, False, 0, "B"),
        ({"C": "completed failure", "B": "completed success"}, None, False, 1, None),
        ({"C": "in_progress null"}, None, False, 0, None),
        ({}, None, False, 0, None),
        ({"C": "completed success"}, "C", False, 0, None),  # C failed its deploy
        ({"C": "completed success"}, None, True, 1, None),  # unsigned
    ],
)
def test_auto_update_deploys_the_newest_green_commit(tmp_path, runs, bad, verify, rc, deployed):
    pi, env = _repo(tmp_path, 'echo "deploy $1" >> "$LOG"\n', ["A", "B", "C"])
    sha = {c: _git(pi, env, "rev-parse", f"origin/main~{2 - i}") for i, c in enumerate("ABC")}
    Path(env["RUNS"]).write_text("".join(f"{sha[c]} {s}\n" for c, s in runs.items()))
    if bad:
        (pi / ".deploy-bad").write_text(sha[bad] + "\n")
    if verify:
        _git(pi, env, "config", "broombuster.verifyCommits", "true")
    r = _run(pi, env, "auto-update.sh")
    assert r.returncode == rc, r.stderr
    assert _log(env) == ([f"deploy {sha[deployed]}"] if deployed else [])


# ── install.sh ───────────────────────────────────────────────────────────────


def _host(tmp: Path, env_file: str = "", db: str | None = None, state_db: str | None = None):
    """A repo copy with deploy/, .env and optionally data/users.sqlite and a state-dir database."""
    env = _stubs(tmp, _INSTALL_STUBS)
    repo = tmp / "repo"
    shutil.copytree(_DEPLOY, repo / "deploy")
    (repo / "data").mkdir()
    (repo / ".env.example").write_text("")
    (repo / ".env").write_text(env_file)
    for d in ("etc/systemd/system", "etc/sudoers.d"):
        (tmp / "root" / d).mkdir(parents=True)
    if db is not None:
        (repo / "data" / "users.sqlite").write_text(db)
    if state_db is not None:
        (tmp / "root/var/lib/broombuster").mkdir(parents=True)
        (tmp / "root/var/lib/broombuster/users.sqlite").write_text(state_db)
    return repo, env


@needs_bash
def test_install_moves_the_database_before_the_new_units_and_reruns(tmp_path):
    repo, env = _host(tmp_path, db="accounts")
    for _ in range(2):  # idempotent
        r = _run(repo, env, "install.sh")
        assert r.returncode == 0, r.stderr
    assert (tmp_path / "root/var/lib/broombuster/users.sqlite").read_text() == "accounts"
    assert (repo / "data/users.sqlite.bak").read_text() == "accounts"
    assert not (repo / "data/users.sqlite").exists()
    unit = (tmp_path / "root/etc/systemd/system/broombuster-update.service").read_text()
    assert f"-{tmp_path}/.cache/uv" in unit and "__" not in unit
    log = _log(env)
    assert log[0] == "stop broombuster-update.timer"
    assert log.index("stop broombuster") < log.index("daemon-reload")
    assert log[-1] == "enable --now broombuster-update.timer"


@needs_bash
def test_install_refuses_two_databases(tmp_path):
    repo, env = _host(tmp_path, db="old", state_db="new")
    r = _run(repo, env, "install.sh")
    assert r.returncode == 1
    assert "data/users.sqlite" in r.stderr and "/var/lib/broombuster/users.sqlite" in r.stderr
    assert (repo / "data/users.sqlite").read_text() == "old"
    assert "daemon-reload" not in _log(env)


@needs_bash
@pytest.mark.parametrize(
    ("line", "rc"),
    [
        ("BB_DB=/home/u/users.sqlite\n", 1),
        ("BB_DB=\n", 1),
        ('BB_DB="/var/lib/broombuster/a.db"\n', 0),
    ],
)
def test_install_accepts_bb_db_only_in_the_state_directory(tmp_path, line, rc):
    repo, env = _host(tmp_path, env_file=line, db="accounts")
    r = _run(repo, env, "install.sh")
    assert r.returncode == rc, r.stderr
    moved = tmp_path / "root/var/lib/broombuster/a.db"
    assert moved.exists() == (rc == 0)


# ── smoke.sh points ──────────────────────────────────────────────────────────


def test_smoke_checks_find_a_street_in_every_region(app_client):
    script = (_DEPLOY / "smoke.sh").read_text()
    points = re.findall(r"^street (\w+) (\S+) (\S+)$", script, re.MULTILINE)
    assert {p[0] for p in points} == set(REGIONS)
    for region, lat, lon in points:
        body = {"region": region, "lat": float(lat), "lon": float(lon)}
        assert '"covered":true' in app_client.post("/api/check", json=body).text, region
    assert _PAGE in script and _PAGE in app_client.get("/").text
