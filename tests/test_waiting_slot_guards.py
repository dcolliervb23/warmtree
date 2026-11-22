"""Work in a waiting slot that refresh, shrinking, remove, and take must keep.

Found by the reviews of #27: a paused rebase or bisect detaches HEAD, so a
check for a checked-out branch misses it; a commit made on the detached HEAD
lives only in that slot; and a deleted slot folder must not crash refresh.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

from tests.conftest import git as run_git
from warmtree import git
from warmtree.config import Config
from warmtree.pool import Pool, PoolError


@pytest.fixture
def pool(repo: Path) -> Pool:
    pool = Pool(repo, Config(size=2, base="main"))
    pool.fill()
    return pool


def paused_rebase(repo: Path, slot_path: str) -> Path:
    """Leave a rebase stopped on a conflict, half resolved, in the slot."""
    path = Path(slot_path)
    run_git("checkout", "-q", "-b", "work", cwd=path)
    (path / "README.md").write_text("from work\n")
    run_git("commit", "-q", "-am", "work", cwd=path)
    (repo / "README.md").write_text("from main\n")
    run_git("commit", "-q", "-am", "main moved", cwd=repo)
    with pytest.raises(subprocess.CalledProcessError):
        run_git("rebase", "main", cwd=path)
    (path / "README.md").write_text("half resolved\n")
    assert git.head_branch(path) is None  # rebase detaches HEAD
    return path


def detached_commit(slot_path: str) -> tuple[Path, str]:
    """Commit straight onto a parked slot's detached HEAD."""
    path = Path(slot_path)
    (path / "fix.txt").write_text("quick fix\n")
    run_git("add", "fix.txt", cwd=path)
    run_git("commit", "-q", "-m", "quick fix", cwd=path)
    return path, run_git("rev-parse", "HEAD", cwd=path)


def test_refresh_keeps_a_paused_rebase(pool: Pool, repo: Path):
    path = paused_rebase(repo, pool.status()[0].path)

    pool.refresh()

    assert git.operation_in_progress(path) == "rebase"
    assert (path / "README.md").read_text() == "half resolved\n"


def test_trim_keeps_a_paused_rebase(pool: Pool, repo: Path):
    second = pool.status()[1]
    path = paused_rebase(repo, second.path)

    Pool(repo, Config(size=1, base="main")).trim()

    assert git.operation_in_progress(path) == "rebase"


def test_take_never_hands_out_a_slot_with_a_paused_operation(pool: Pool):
    for slot in pool.status():
        run_git("bisect", "start", cwd=Path(slot.path))

    slot, cold = pool.take("feature")

    assert cold is True
    assert git.operation_in_progress(Path(slot.path)) is None


def test_refresh_keeps_a_commit_made_on_the_detached_head(pool: Pool):
    path, commit = detached_commit(pool.status()[0].path)

    pool.refresh()

    assert run_git("rev-parse", "HEAD", cwd=path) == commit


def test_trim_keeps_a_commit_made_on_the_detached_head(pool: Pool, repo: Path):
    path, _ = detached_commit(pool.status()[1].path)

    Pool(repo, Config(size=1, base="main")).trim()

    assert (path / "fix.txt").exists()


def test_remove_refuses_a_hand_checkout_without_force(pool: Pool):
    first = pool.status()[0]
    path = Path(first.path)
    run_git("checkout", "-q", "-b", "stray", cwd=path)
    (path / "notes.txt").write_text("never committed\n")

    with pytest.raises(PoolError):
        pool.remove(all_slots=True)

    assert (path / "notes.txt").exists()


def test_remove_refuses_a_kept_detached_slot_without_force(pool: Pool):
    path, _ = detached_commit(pool.status()[0].path)

    with pytest.raises(PoolError):
        pool.remove(all_slots=True)

    assert (path / "fix.txt").exists()


def test_refresh_skips_a_slot_folder_deleted_by_hand(pool: Pool):
    [first, second] = pool.status()
    shutil.rmtree(first.path)

    pool.refresh()

    states = {slot.name: slot.state for slot in pool.status()}
    assert states[first.name] != "warming"
    assert states[second.name] == "ready"


def test_release_never_deletes_the_base_branch(repo: Path):
    run_git("checkout", "-q", "-b", "feature", cwd=repo)
    pool = Pool(repo, Config(size=1, base="main"))
    [slot] = pool.fill()
    run_git("checkout", "-q", "main", cwd=Path(slot.path))
    pool.refresh()  # marks the hand checkout taken on main

    pool.release("main")

    assert git.branch_exists(repo, "main")


def test_doctor_reports_a_stale_slot_that_is_not_our_worktree(pool: Pool, repo: Path):
    first = pool.status()[0]
    pool._update_slot(first.name, state="stale")
    shutil.rmtree(first.path)
    run_git("init", "-q", "-b", "innocent", first.path, cwd=repo)

    findings = pool.doctor()

    assert any("no longer a working tree" in f.problem for f in findings)


def amend_base(repo: Path) -> None:
    """Commit on main, park the pool there, then rewrite that commit away."""
    (repo / "README.md").write_text("draft\n")
    run_git("commit", "-q", "-am", "draft", cwd=repo)
    Pool(repo, Config(size=2, base="main")).refresh()
    (repo / "README.md").write_text("final\n")
    run_git("commit", "-q", "--amend", "-am", "final", cwd=repo)


def test_rewriting_the_base_tip_does_not_strand_the_pool(pool: Pool, repo: Path):
    amend_base(repo)

    pool.refresh()

    tip = run_git("rev-parse", "main", cwd=repo)
    for slot in pool.status():
        assert run_git("rev-parse", "HEAD", cwd=Path(slot.path)) == tip
    _, cold = pool.take("feature")
    assert cold is False


def test_a_slot_with_no_recorded_park_is_not_kept_for_commits(pool: Pool, repo: Path):
    # State files from before this change have no parked commit: those
    # slots are recycled as they always were, never frozen.
    amend_base(repo)
    for slot in pool.status():
        pool._update_slot(slot.name, parked=None)

    pool.refresh()

    tip = run_git("rev-parse", "main", cwd=repo)
    for slot in pool.status():
        assert run_git("rev-parse", "HEAD", cwd=Path(slot.path)) == tip
