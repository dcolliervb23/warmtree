"""`refresh --fetch` tracks the remote without touching the local branch."""

from pathlib import Path

import pytest

from tests.conftest import git as run_git
from warmtree import git
from warmtree.config import Config
from warmtree.pool import Pool


@pytest.fixture
def cloned(repo: Path, tmp_path: Path) -> tuple[Path, Path]:
    """An origin repo and a clone of it, like a normal checkout."""
    clone = tmp_path / "clone"
    run_git("clone", "-q", str(repo), str(clone), cwd=tmp_path)
    run_git("config", "user.email", "test@example.com", cwd=clone)
    run_git("config", "user.name", "Test", cwd=clone)
    return repo, clone


def advance_origin(origin: Path) -> str:
    (origin / "news.txt").write_text("upstream moved\n")
    run_git("add", "news.txt", cwd=origin)
    run_git("commit", "-q", "-m", "upstream", cwd=origin)
    return run_git("rev-parse", "HEAD", cwd=origin)


def test_refresh_without_fetch_stays_on_the_stale_local_base(
    cloned: tuple[Path, Path],
):
    origin, clone = cloned
    pool = Pool(clone, Config(size=1))
    [slot] = pool.fill()
    old = git.rev_parse(Path(slot.path), "HEAD")
    advance_origin(origin)

    pool.refresh()
    assert git.rev_parse(Path(slot.path), "HEAD") == old


def test_refresh_fetch_parks_slots_on_the_remote_tip(cloned: tuple[Path, Path]):
    origin, clone = cloned
    pool = Pool(clone, Config(size=1))
    [slot] = pool.fill()
    upstream = advance_origin(origin)
    local_before = git.rev_parse(clone, "main")

    [result] = pool.refresh(fetch=True)
    assert result.moved
    assert git.rev_parse(Path(slot.path), "HEAD") == upstream
    # The user's own branch and checkout are untouched.
    assert git.rev_parse(clone, "main") == local_before


def test_refresh_fetch_without_a_remote_uses_the_local_base(repo: Path):
    pool = Pool(repo, Config(size=1))
    [slot] = pool.fill()
    base = git.rev_parse(repo, "main")

    pool.refresh(fetch=True)
    assert git.rev_parse(Path(slot.path), "HEAD") == base
