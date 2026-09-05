"""take, release, and remove against real repos."""

from pathlib import Path

import pytest

from warmtree import git
from warmtree.config import Config
from warmtree.pool import Pool, PoolError


def commit_on_branch(repo: Path, branch: str, filename: str) -> str:
    """Create `branch` off main with one extra commit. Returns its hash."""
    git.run(["checkout", "-q", "-b", branch], cwd=repo)
    (repo / filename).write_text("x\n")
    git.run(["add", filename], cwd=repo)
    git.run(["commit", "-q", "-m", f"add {filename}"], cwd=repo)
    git.run(["checkout", "-q", "main"], cwd=repo)
    return git.rev_parse(repo, branch)


def branches(repo: Path) -> set[str]:
    out = git.run(["branch", "--format=%(refname:short)"], cwd=repo)
    return set(out.splitlines())


# --- take ---------------------------------------------------------------


def test_take_creates_branch_from_base_in_oldest_ready_slot(repo: Path):
    pool = Pool(repo, Config(size=2))
    pool.fill()

    slot, cold = pool.take("feature/a")

    assert cold is False
    assert slot.name == "slot-1"
    assert slot.state == "taken"
    assert slot.branch == "feature/a"
    path = Path(slot.path)
    assert git.head_branch(path) == "feature/a"
    assert git.rev_parse(path, "HEAD") == git.rev_parse(repo, "main")


def test_take_twice_hands_out_different_slots(repo: Path):
    pool = Pool(repo, Config(size=2))
    pool.fill()
    first, _ = pool.take("a")
    second, _ = pool.take("b")
    assert {first.name, second.name} == {"slot-1", "slot-2"}
    assert [s.state for s in pool.status()] == ["taken", "taken"]


def test_take_existing_branch_checks_it_out(repo: Path):
    develop = commit_on_branch(repo, "develop", "dev.txt")
    pool = Pool(repo, Config(size=1))
    pool.fill()

    slot, _ = pool.take("develop")

    path = Path(slot.path)
    assert git.head_branch(path) == "develop"
    assert git.rev_parse(path, "HEAD") == develop
    assert (path / "dev.txt").exists()


def test_take_from_ref_starts_new_branch_there(repo: Path):
    develop = commit_on_branch(repo, "develop", "dev.txt")
    pool = Pool(repo, Config(size=1))
    pool.fill()

    slot, _ = pool.take("feature/b", from_ref="develop")

    path = Path(slot.path)
    assert git.head_branch(path) == "feature/b"
    assert git.rev_parse(path, "HEAD") == develop


def test_take_with_no_ready_slot_falls_back_to_cold_create(repo: Path):
    pool = Pool(repo, Config(size=0))

    slot, cold = pool.take("feature/c")

    assert cold is True
    assert slot.state == "taken"
    assert slot.warmed is None
    path = Path(slot.path)
    assert git.head_branch(path) == "feature/c"
    assert path.resolve() in git.worktree_list(repo)


def test_cold_create_uses_next_free_slot_name(repo: Path):
    pool = Pool(repo, Config(size=1))
    pool.fill()
    pool.take("a")
    slot, cold = pool.take("b")
    assert cold is True
    assert slot.name == "slot-2"


def test_take_branch_already_in_another_slot_fails_and_keeps_slot_ready(repo: Path):
    pool = Pool(repo, Config(size=2))
    pool.fill()
    pool.take("same")

    with pytest.raises(git.GitError):
        pool.take("same")

    states = {s.name: s.state for s in pool.status()}
    assert states == {"slot-1": "taken", "slot-2": "ready"}


# --- release ------------------------------------------------------------


def test_release_returns_slot_to_ready_on_base_and_deletes_branch(repo: Path):
    pool = Pool(repo, Config(size=1))
    pool.fill()
    slot, _ = pool.take("feature/a")

    released, branch_deleted = pool.release("feature/a")

    assert released.name == slot.name
    assert released.state == "ready"
    assert released.branch is None
    assert branch_deleted is True
    path = Path(released.path)
    assert git.head_branch(path) is None
    assert git.rev_parse(path, "HEAD") == git.rev_parse(repo, "main")
    assert "feature/a" not in branches(repo)


def test_release_keep_branch(repo: Path):
    pool = Pool(repo, Config(size=1))
    pool.fill()
    pool.take("feature/a")

    _, branch_deleted = pool.release("feature/a", keep_branch=True)

    assert branch_deleted is False
    assert "feature/a" in branches(repo)


def test_release_keeps_an_unmerged_branch_instead_of_losing_work(repo: Path):
    pool = Pool(repo, Config(size=1))
    pool.fill()
    slot, _ = pool.take("feature/a")
    path = Path(slot.path)
    (path / "work.txt").write_text("unmerged\n")
    git.run(["add", "work.txt"], cwd=path)
    git.run(["commit", "-q", "-m", "work"], cwd=path)

    released, branch_deleted = pool.release("feature/a")

    assert released.state == "ready"
    assert branch_deleted is False
    assert "feature/a" in branches(repo)


def test_release_refuses_dirty_tree_without_force(repo: Path):
    pool = Pool(repo, Config(size=1))
    pool.fill()
    slot, _ = pool.take("feature/a")
    (Path(slot.path) / "README.md").write_text("changed\n")

    with pytest.raises(PoolError, match="uncommitted"):
        pool.release("feature/a")

    assert pool.status()[0].state == "taken"


def test_release_force_discards_changes_but_keeps_ignored_files(repo: Path):
    (repo / ".gitignore").write_text("node_modules/\n")
    git.run(["add", ".gitignore"], cwd=repo)
    git.run(["commit", "-q", "-m", "ignore node_modules"], cwd=repo)
    pool = Pool(repo, Config(size=1))
    pool.fill()
    slot, _ = pool.take("feature/a")
    path = Path(slot.path)
    (path / "README.md").write_text("changed\n")
    (path / "scratch.txt").write_text("untracked\n")
    (path / "node_modules").mkdir()
    (path / "node_modules" / "dep.js").write_text("warm\n")

    pool.release("feature/a", force=True)

    assert (path / "README.md").read_text() == "hello\n"
    assert not (path / "scratch.txt").exists()
    assert (path / "node_modules" / "dep.js").exists()
    assert git.is_dirty(path) is False


def test_release_unknown_branch_raises(repo: Path):
    pool = Pool(repo, Config(size=1))
    pool.fill()
    with pytest.raises(PoolError, match="nothing-here"):
        pool.release("nothing-here")


def test_released_slot_can_be_taken_again(repo: Path):
    pool = Pool(repo, Config(size=1))
    pool.fill()
    pool.take("a")
    pool.release("a")
    slot, cold = pool.take("b")
    assert cold is False
    assert slot.name == "slot-1"
    assert git.head_branch(Path(slot.path)) == "b"


# --- remove -------------------------------------------------------------


def test_remove_all_deletes_worktrees_and_state(repo: Path):
    pool = Pool(repo, Config(size=2))
    pool.fill()
    paths = [Path(s.path) for s in pool.status()]

    removed = pool.remove(all_slots=True)

    assert len(removed) == 2
    assert pool.status() == []
    assert git.worktree_list(repo) == [repo.resolve()]
    assert not any(p.exists() for p in paths)


def test_remove_named_slot_only(repo: Path):
    pool = Pool(repo, Config(size=2))
    pool.fill()

    pool.remove(names=["slot-1"])

    assert [s.name for s in pool.status()] == ["slot-2"]


def test_remove_unknown_name_raises(repo: Path):
    pool = Pool(repo, Config(size=1))
    pool.fill()
    with pytest.raises(PoolError, match="slot-9"):
        pool.remove(names=["slot-9"])


def test_remove_refuses_taken_slot_without_force(repo: Path):
    pool = Pool(repo, Config(size=1))
    pool.fill()
    pool.take("a")

    with pytest.raises(PoolError, match="taken"):
        pool.remove(all_slots=True)
    assert len(pool.status()) == 1

    pool.remove(all_slots=True, force=True)
    assert pool.status() == []
