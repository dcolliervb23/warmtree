"""Slot leases: release refuses while another live session holds the slot."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.conftest import git as run_git
from warmtree.config import Config
from warmtree.pool import Pool, PoolError, _pid_alive


def dead_pid() -> int:
    """A pid that belonged to a real process which has exited."""
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    return child.pid


@pytest.fixture
def pool(repo: Path) -> Pool:
    pool = Pool(repo, Config(size=1))
    pool.fill()
    return pool


def test_take_records_the_taking_session(pool: Pool):
    slot, _ = pool.take("feature")
    assert slot.lease_pid == os.getppid()
    assert slot.lease_since is not None


def test_release_from_the_same_session_passes_and_clears_the_lease(pool: Pool):
    pool.take("feature")
    slot, _ = pool.release("feature")
    assert slot.lease_pid is None
    assert slot.lease_since is None


def test_release_refuses_a_slot_held_by_another_live_session(pool: Pool):
    slot, _ = pool.take("feature")
    # Our own pid is alive and differs from this process's parent, so it
    # stands in for another session still working in the slot.
    pool._update_slot(slot.name, lease_pid=os.getpid())

    with pytest.raises(PoolError, match="held by process"):
        pool.release("feature")


def test_release_force_overrides_a_live_lease(pool: Pool):
    slot, _ = pool.take("feature")
    pool._update_slot(slot.name, lease_pid=os.getpid())

    released, _ = pool.release("feature", force=True)
    assert released.state == "ready"
    assert released.lease_pid is None


def test_release_ignores_a_stale_lease(pool: Pool):
    slot, _ = pool.take("feature")
    pool._update_slot(slot.name, lease_pid=dead_pid())

    released, _ = pool.release("feature")
    assert released.state == "ready"


def test_adopt_records_the_adopting_session(repo: Path):
    worktree = repo.parent / "hand-made"
    run_git("worktree", "add", "-q", "-b", "feature", str(worktree), cwd=repo)

    slot = Pool(repo, Config(size=0)).adopt(worktree)
    assert slot.lease_pid == os.getppid()


def test_old_state_without_lease_fields_still_loads(pool: Pool):
    pool.take("feature")
    state = pool.state_path.read_text(encoding="utf-8")
    state = state.replace('"lease_pid"', '"_gone_pid"').replace(
        '"lease_since"', '"_gone_since"'
    )
    # Rewrite the file the way an older warmtree would have written it.
    data = json.loads(state)
    for item in data["slots"]:
        item.pop("_gone_pid", None)
        item.pop("_gone_since", None)
    pool.state_path.write_text(json.dumps(data), encoding="utf-8")

    [slot] = pool.status()
    assert slot.lease_pid is None
    released, _ = pool.release("feature")
    assert released.state == "ready"


def test_pid_alive_tells_live_from_dead():
    assert _pid_alive(os.getpid()) is True
    assert _pid_alive(dead_pid()) is False


def test_command_substitution_leases_to_the_surviving_shell(pool: Pool, repo: Path):
    # The documented workflow is `cd "$(warmtree take x)"`. POSIX shells
    # exec the command inside $() directly under the original shell, so the
    # lease must land on the shell that keeps living, not an intermediary.
    bash = shutil.which("bash")
    if bash is None or sys.platform == "win32":
        # Git Bash on Windows reports MSYS pids, which do not match the
        # Windows pids Python sees, so the comparison is meaningless there.
        pytest.skip("needs a POSIX shell with real pids")
    script = f'echo $$; p=$("{sys.executable}" -m warmtree take feature --no-refill)'
    result = subprocess.run(
        [bash, "-c", script], cwd=repo, capture_output=True, text=True, check=True
    )
    shell_pid = int(result.stdout.strip().splitlines()[0])
    assert pool.taken("feature").lease_pid == shell_pid
