"""`sweep` folds finished work — slots and hand-made worktrees — into the pool."""

import shutil
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
    # No -u: without a tracking upstream, git's own branch -d would refuse
    # even after the merge lands upstream. Sweep's certificate must finish
    # the deletion anyway.
    run_git("push", "-q", "origin", "upstream-done", cwd=wt)
    merge_into_main(repo, "upstream-done")  # merged on origin; clone unaware

    pool = Pool(clone, Config(size=0))
    results = pool.sweep(fetch=True)

    [item] = [r for r in results if r.branch == "upstream-done"]
    assert item.action == "folded"
    assert not wt.exists()
    assert not git.branch_exists(clone, "upstream-done")


def test_sweep_never_releases_a_replaced_slot_directory(repo: Path):
    pool = Pool(repo, Config(size=1))
    pool.fill()
    slot, _ = pool.take("done")
    (Path(slot.path) / "f.txt").write_text("x\n")
    run_git("add", ".", cwd=Path(slot.path))
    run_git("commit", "-q", "-m", "done", cwd=Path(slot.path))
    merge_into_main(repo, "done")
    # Replace the slot directory with an unrelated repository.
    shutil.rmtree(slot.path)
    foreign = Path(slot.path)
    run_git("init", "-q", "-b", "innocent", str(foreign), cwd=repo)
    (foreign / "precious.txt").write_text("someone else's files\n")

    results = pool.sweep()

    [item] = [r for r in results if r.branch == "done"]
    assert item.action == "skipped"
    assert "no longer a working tree" in item.reason
    assert (foreign / "precious.txt").exists()  # untouched


def detached_at(repo: Path, ref: str, name: str = "wt-detached") -> Path:
    path = repo.parent / name
    run_git("worktree", "add", "-q", "--detach", str(path), ref, cwd=repo)
    return path


def older_commit(repo: Path) -> str:
    """A commit on main that is no longer its tip."""
    first = git.rev_parse(repo, "main")
    (repo / "later.txt").write_text("later\n")
    run_git("add", ".", cwd=repo)
    run_git("commit", "-q", "-m", "later", cwd=repo)
    return first


def test_sweep_removes_merged_detached_worktree_with_flag(repo: Path):
    path = detached_at(repo, older_commit(repo))

    [item] = Pool(repo, Config(size=0)).sweep(detached=True)

    assert (item.action, item.reason) == ("removed", "detached, merged")
    assert not path.exists()


def test_sweep_hints_at_detached_flag_without_it(repo: Path):
    path = detached_at(repo, older_commit(repo))

    [item] = Pool(repo, Config(size=0)).sweep()

    assert item.action == "kept"
    assert item.reason == (
        "detached HEAD, merged into base (sweep --detached removes it)"
    )
    assert path.exists()


def test_sweep_keeps_unmerged_detached_worktree(repo: Path):
    hand_made(repo, "wip", merged=False)
    path = detached_at(repo, "wip", name="wt-val")

    results = Pool(repo, Config(size=0)).sweep(detached=True)

    [item] = [r for r in results if r.name == str(path)]
    assert (item.action, item.reason) == ("kept", "detached HEAD")
    assert path.exists()


def test_sweep_keeps_dirty_merged_detached_worktree(repo: Path):
    path = detached_at(repo, older_commit(repo))
    (path / "scratch.txt").write_text("notes\n")

    [item] = Pool(repo, Config(size=0)).sweep(detached=True)

    assert (item.action, item.reason) == (
        "kept",
        "detached HEAD with uncommitted changes",
    )
    assert path.exists()


def test_sweep_removes_detached_at_base_tip(repo: Path):
    path = detached_at(repo, "main")

    [item] = Pool(repo, Config(size=0)).sweep(detached=True)

    assert item.action == "removed"
    assert not path.exists()


def test_sweep_never_touches_pool_slots(repo: Path):
    pool = Pool(repo, Config(size=1))
    pool.fill()

    assert pool.sweep(detached=True) == []
    assert pool.status()[0].state == "ready"


def test_sweep_survives_a_worktree_whose_directory_vanished(repo: Path):
    path = hand_made(repo, "ghost", merged=False)
    shutil.rmtree(path)

    results = Pool(repo, Config(size=0)).sweep()

    assert all(r.action != "removed" for r in results)


def test_sweep_detached_counts_upstream_merges_with_fetch(repo: Path, tmp_path: Path):
    # Local main in the clone is stale; the commit is merged on origin only,
    # so removal must measure against origin/main, not local main.
    clone = tmp_path / "clone"
    run_git("clone", "-q", str(repo), str(clone), cwd=tmp_path)
    run_git("config", "user.email", "t@t", cwd=clone)
    run_git("config", "user.name", "t", cwd=clone)
    wt = clone.parent / "wt-upstream"
    run_git("worktree", "add", "-q", "-b", "upstream-done", str(wt), cwd=clone)
    (wt / "u.txt").write_text("u\n")
    run_git("add", ".", cwd=wt)
    run_git("commit", "-q", "-m", "u", cwd=wt)
    run_git("push", "-q", "origin", "upstream-done", cwd=wt)
    merge_into_main(repo, "upstream-done")  # merged on origin; clone unaware
    val = detached_at(clone, "upstream-done", name="wt-val")

    results = Pool(clone, Config(size=0)).sweep(fetch=True, detached=True)

    [item] = [r for r in results if r.name == str(val)]
    assert item.action == "removed"
    assert not val.exists()


def test_sweep_reports_a_failed_removal_and_continues(repo: Path, monkeypatch):
    path = detached_at(repo, older_commit(repo))

    def refuse(repo_root: Path, target: Path) -> None:
        raise git.GitError("boom")

    monkeypatch.setattr(git, "worktree_remove", refuse)

    [item] = Pool(repo, Config(size=0)).sweep(detached=True)

    assert (item.action, item.reason) == ("skipped", "boom")
    assert path.exists()


def test_cli_sweep_prints_removed_and_hint(repo: Path, capsys, monkeypatch):
    from warmtree.cli import main

    detached_at(repo, older_commit(repo))
    monkeypatch.chdir(repo)

    assert main(["sweep"]) == 0
    assert "sweep --detached removes it" in capsys.readouterr().err

    assert main(["sweep", "--detached"]) == 0
    out = capsys.readouterr().out
    assert "removed: " in out
    assert "(detached, merged)" in out
    assert "removed 1" in out


def test_sweep_detached_never_removes_an_orphaned_pool_slot(repo: Path):
    # state.json lost: the slot is no longer in `known`, but it still lives
    # in the pool directory and must not be swept away.
    pool = Pool(repo, Config(size=1))
    [slot] = pool.fill()
    pool.state_path.unlink()

    results = pool.sweep(detached=True)

    assert all(r.action != "removed" for r in results)
    assert Path(slot.path).is_dir()


def test_cli_sweep_reports_failed_removal_without_none(repo: Path, capsys, monkeypatch):
    from warmtree.cli import main

    detached_at(repo, older_commit(repo))
    monkeypatch.chdir(repo)

    def refuse(repo_root: Path, target: Path) -> None:
        raise git.GitError("boom")

    monkeypatch.setattr(git, "worktree_remove", refuse)

    assert main(["sweep", "--detached"]) == 0
    err = capsys.readouterr().err
    assert "(detached): boom" in err
    assert "None" not in err
