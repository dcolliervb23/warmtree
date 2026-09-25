"""`sweep` folds finished work — slots and hand-made worktrees — into the pool."""

from pathlib import Path

from tests.conftest import git as run_git
from warmtree import git
from warmtree.config import Config
from warmtree.pool import Pool


def merge_into_main(repo: Path, branch: str) -> None:
    run_git("checkout", "-q", "main", cwd=repo)
    run_git("merge", "-q", "--no-ff", branch, cwd=repo)


def hand_made(repo: Path, branch: str, merged: bool) -> Path:
    path = repo.parent / f"wt-{branch}"
    run_git("worktree", "add", "-q", "-b", branch, str(path), cwd=repo)
    (path / f"{branch}.txt").write_text("work\n")
    run_git("add", ".", cwd=path)
    run_git("commit", "-q", "-m", branch, cwd=path)
    if merged:
        merge_into_main(repo, branch)
    return path


def test_sweep_folds_merged_worktrees_and_keeps_unmerged(repo: Path):
    done = hand_made(repo, "done", merged=True)
    wip = hand_made(repo, "wip", merged=False)
    pool = Pool(repo, Config(size=0))

    results = pool.sweep()

    actions = {r.branch: r.action for r in results}
    assert actions["done"] == "folded"
    assert actions["wip"] == "kept"
    assert not done.exists()  # graduated into the pool
    assert wip.exists()
    assert not git.branch_exists(repo, "done")
    assert git.branch_exists(repo, "wip")
    ready = [s for s in pool.status() if s.state == "ready"]
    assert len(ready) == 1
    assert git.head_branch(Path(ready[0].path)) is None


def test_sweep_releases_merged_taken_slots(repo: Path):
    pool = Pool(repo, Config(size=1))
    pool.fill()
    slot, _ = pool.take("shipped")
    (Path(slot.path) / "f.txt").write_text("x\n")
    run_git("add", ".", cwd=Path(slot.path))
    run_git("commit", "-q", "-m", "shipped", cwd=Path(slot.path))
    merge_into_main(repo, "shipped")

    results = pool.sweep()

    assert any(r.action == "released" and r.branch == "shipped" for r in results)
    assert all(s.state == "ready" for s in pool.status())
    assert not git.branch_exists(repo, "shipped")


def test_sweep_keeps_unmerged_taken_slots(repo: Path):
    pool = Pool(repo, Config(size=1))
    pool.fill()
    slot, _ = pool.take("inflight")
    (Path(slot.path) / "f.txt").write_text("x\n")
    run_git("add", ".", cwd=Path(slot.path))
    run_git("commit", "-q", "-m", "inflight", cwd=Path(slot.path))

    results = pool.sweep()

    assert any(r.action == "kept" and r.branch == "inflight" for r in results)
    assert pool.taken("inflight").state == "taken"


def test_sweep_skips_dirty_merged_worktrees(repo: Path):
    path = hand_made(repo, "messy", merged=True)
    (path / "uncommitted.txt").write_text("wip\n")
    pool = Pool(repo, Config(size=0))

    results = pool.sweep()

    [item] = [r for r in results if r.branch == "messy"]
    assert item.action == "skipped"
    assert "uncommitted" in item.reason
    assert path.exists()


def test_sweep_counts_branches_merged_only_upstream(repo: Path, tmp_path: Path):
    # Local main is stale; the branch merged on origin only. --fetch makes
    # origin/main the yardstick, so the fold still happens.
    clone = tmp_path / "clone"
    run_git("clone", "-q", str(repo), str(clone), cwd=tmp_path)
    run_git("config", "user.email", "t@t", cwd=clone)
    run_git("config", "user.name", "t", cwd=clone)
    wt = clone.parent / "wt-upstream"
    run_git("worktree", "add", "-q", "-b", "upstream-done", str(wt), cwd=clone)
    (wt / "u.txt").write_text("u\n")
    run_git("add", ".", cwd=wt)
    run_git("commit", "-q", "-m", "u", cwd=wt)
    run_git("push", "-q", "-u", "origin", "upstream-done", cwd=wt)
    merge_into_main(repo, "upstream-done")  # merged on origin; clone unaware

    results = Pool(clone, Config(size=0)).sweep(fetch=True)

    [item] = [r for r in results if r.branch == "upstream-done"]
    assert item.action == "folded"
    assert not wt.exists()
