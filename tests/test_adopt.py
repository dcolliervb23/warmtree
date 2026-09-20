"""Adopting an existing worktree into the pool."""

from pathlib import Path

import pytest

from tests.conftest import git as run_git
from warmtree import git
from warmtree.cli import main
from warmtree.config import Config
from warmtree.pool import Pool, PoolError


def hand_made_worktree(repo: Path, branch: str) -> Path:
    """A worktree the way a user would make one, outside the pool dir."""
    path = repo.parent / f"{repo.name}-{branch}"
    run_git("worktree", "add", "-q", "-b", branch, str(path), cwd=repo)
    return path


def test_adopt_registers_in_place_and_changes_nothing_on_disk(repo: Path):
    (repo / ".gitignore").write_text("node_modules/\n")
    run_git("add", ".gitignore", cwd=repo)
    run_git("commit", "-q", "-m", "ignore dependencies", cwd=repo)
    worktree = hand_made_worktree(repo, "feature")
    (worktree / "node_modules").mkdir()
    (worktree / "node_modules" / "dep.js").write_text("cached\n")
    assert not git.is_dirty(worktree)  # proves node_modules is truly ignored

    pool = Pool(repo, Config(size=1))
    slot = pool.adopt(worktree)

    assert slot.state == "taken"
    assert slot.branch == "feature"
    # Nothing moved: editors and shells on this path keep working.
    assert Path(slot.path) == worktree.resolve()
    assert worktree.exists()
    assert (worktree / "node_modules" / "dep.js").exists()


def test_release_graduates_the_adopted_folder_into_the_pool(repo: Path):
    (repo / ".gitignore").write_text("node_modules/\n")
    run_git("add", ".gitignore", cwd=repo)
    run_git("commit", "-q", "-m", "ignore dependencies", cwd=repo)
    worktree = hand_made_worktree(repo, "feature")
    (worktree / "node_modules").mkdir()
    (worktree / "node_modules" / "dep.js").write_text("cached\n")
    pool = Pool(repo, Config(size=0))
    slot = pool.adopt(worktree)

    released, _ = pool.release("feature")
    assert released.state == "ready"
    assert Path(released.path) == pool.dir / slot.name
    assert not worktree.exists()  # the old address is gone, at a safe moment
    assert (Path(released.path) / "node_modules" / "dep.js").exists()
    assert git.head_branch(Path(released.path)) is None


def test_release_keeps_the_slot_working_when_the_move_fails(repo: Path):
    worktree = hand_made_worktree(repo, "feature")
    notes: list[str] = []
    pool = Pool(repo, Config(size=0), log=notes.append)
    slot = pool.adopt(worktree)
    squatter = pool.dir / slot.name  # a non-empty stranger blocks the move
    squatter.mkdir(parents=True)
    (squatter / "occupied.txt").write_text("here first")

    released, _ = pool.release("feature")
    assert released.state == "ready"
    assert Path(released.path) == worktree.resolve()
    assert any("stays at" in message for message in notes)

    taken, cold = pool.take("again")
    assert cold is False
    assert Path(taken.path) == worktree.resolve()


def test_adopt_keeps_a_dirty_tree_intact(repo: Path):
    worktree = hand_made_worktree(repo, "feature")
    (worktree / "wip.txt").write_text("not committed\n")

    slot = Pool(repo, Config(size=0)).adopt(worktree)
    assert (Path(slot.path) / "wip.txt").read_text() == "not committed\n"


def test_adopt_records_lockfile_hashes_from_the_tree(repo: Path):
    worktree = hand_made_worktree(repo, "feature")
    (worktree / "uv.lock").write_text("locked\n")

    config = Config(size=0, lockfiles=["uv.lock"])
    slot = Pool(repo, config).adopt(worktree)
    assert "uv.lock" in slot.lockfiles


def test_adopt_refuses_the_main_worktree(repo: Path):
    with pytest.raises(PoolError, match="main worktree"):
        Pool(repo, Config(size=0)).adopt(repo)


def test_adopt_refuses_a_path_that_is_not_a_worktree(repo: Path, tmp_path: Path):
    stranger = tmp_path / "stranger"
    stranger.mkdir()
    with pytest.raises(PoolError, match="not a worktree"):
        Pool(repo, Config(size=0)).adopt(stranger)


def test_adopt_refuses_a_detached_worktree(repo: Path):
    path = repo.parent / "detached"
    run_git("worktree", "add", "-q", "--detach", str(path), cwd=repo)
    with pytest.raises(PoolError, match="detached"):
        Pool(repo, Config(size=0)).adopt(path)


def test_adopt_refuses_an_existing_slot(repo: Path):
    pool = Pool(repo, Config(size=1))
    pool.fill()
    slot_path = Path(pool.status()[0].path)
    with pytest.raises(PoolError, match="already"):
        pool.adopt(slot_path)


def test_adopt_cli_prints_only_the_path_on_stdout(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    worktree = hand_made_worktree(repo, "feature")
    monkeypatch.chdir(repo)
    capsys.readouterr()

    assert main(["adopt", str(worktree)]) == 0

    out, err = capsys.readouterr()
    path = Path(out.strip())
    assert out.count("\n") == 1
    assert path.is_dir()
    assert git.head_branch(path) == "feature"
    assert "adopted" in err


def test_ungraduated_slots_are_never_auto_deleted(repo: Path):
    worktree = hand_made_worktree(repo, "feature")
    pool = Pool(repo, Config(size=0))
    slot = pool.adopt(worktree)
    squatter = pool.dir / slot.name
    squatter.mkdir(parents=True)
    (squatter / "occupied.txt").write_text("here first")
    pool.release("feature")  # graduation fails; ready at the old address

    assert pool.trim() == []  # size 0 with one waiting slot: surplus exists
    assert worktree.exists()

    with pytest.raises(PoolError, match="outside the pool directory"):
        pool.remove(names=[slot.name])
    assert worktree.exists()

    removed = pool.remove(names=[slot.name], force=True)
    assert [s.name for s in removed] == [slot.name]
