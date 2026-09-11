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


def test_adopt_moves_worktree_into_pool_as_taken(repo: Path):
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
    assert Path(slot.path) == pool.dir / slot.name
    assert not worktree.exists()
    assert (Path(slot.path) / "node_modules" / "dep.js").exists()
    assert Path(slot.path).resolve() in git.worktree_list(repo)


def test_adopt_then_release_recycles_the_slot_as_ready(repo: Path):
    worktree = hand_made_worktree(repo, "feature")
    pool = Pool(repo, Config(size=0))
    pool.adopt(worktree)

    released, _ = pool.release("feature")
    assert released.state == "ready"
    assert git.head_branch(Path(released.path)) is None


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
