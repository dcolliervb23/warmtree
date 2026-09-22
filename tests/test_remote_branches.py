"""`take` honors remote branches instead of shadowing them (#16)."""

from pathlib import Path

import pytest

from tests.conftest import git as run_git
from warmtree import git
from warmtree.config import Config
from warmtree.pool import Pool


@pytest.fixture
def cloned(repo: Path, tmp_path: Path) -> tuple[Path, Path]:
    clone = tmp_path / "clone"
    run_git("clone", "-q", str(repo), str(clone), cwd=tmp_path)
    run_git("config", "user.email", "test@example.com", cwd=clone)
    run_git("config", "user.name", "Test", cwd=clone)
    return repo, clone


def push_branch(origin: Path, branch: str) -> str:
    """A branch with real work that exists only on the remote."""
    run_git("checkout", "-q", "-b", branch, cwd=origin)
    (origin / "pr-work.txt").write_text("someone's open PR work\n")
    run_git("add", "pr-work.txt", cwd=origin)
    run_git("commit", "-q", "-m", "pr work", cwd=origin)
    tip = run_git("rev-parse", "HEAD", cwd=origin)
    run_git("checkout", "-q", "main", cwd=origin)
    return tip


def test_take_tracks_a_remote_branch_instead_of_shadowing_it(
    cloned: tuple[Path, Path],
):
    origin, clone = cloned
    tip = push_branch(origin, "feat/pr-branch")
    run_git("fetch", "-q", "origin", cwd=clone)

    pool = Pool(clone, Config(size=1))
    pool.fill()
    slot, cold = pool.take("feat/pr-branch")

    assert cold is False
    assert git.rev_parse(Path(slot.path), "HEAD") == tip
    assert (Path(slot.path) / "pr-work.txt").exists()


def test_cold_take_tracks_the_remote_too(cloned: tuple[Path, Path]):
    origin, clone = cloned
    tip = push_branch(origin, "feat/pr-branch")
    run_git("fetch", "-q", "origin", cwd=clone)

    slot, cold = Pool(clone, Config(size=0)).take("feat/pr-branch")
    assert cold is True
    assert git.rev_parse(Path(slot.path), "HEAD") == tip


def test_diverged_local_and_remote_tips_are_noted(cloned: tuple[Path, Path]):
    origin, clone = cloned
    push_branch(origin, "feat/pr-branch")
    run_git("fetch", "-q", "origin", cwd=clone)
    run_git("branch", "feat/pr-branch", "main", cwd=clone)  # stale local

    notes: list[str] = []
    pool = Pool(clone, Config(size=1), log=notes.append)
    pool.fill()
    slot, _ = pool.take("feat/pr-branch")

    assert any("differ" in message for message in notes)
    # The local branch is checked out as-is; nothing is silently rewritten.
    assert git.rev_parse(Path(slot.path), "HEAD") == git.rev_parse(clone, "main")


def test_explicit_from_overrides_the_remote(cloned: tuple[Path, Path]):
    origin, clone = cloned
    push_branch(origin, "feat/pr-branch")
    run_git("fetch", "-q", "origin", cwd=clone)
    base = git.rev_parse(clone, "main")

    pool = Pool(clone, Config(size=1))
    pool.fill()
    slot, _ = pool.take("feat/pr-branch", from_ref="main")

    assert git.rev_parse(Path(slot.path), "HEAD") == base
