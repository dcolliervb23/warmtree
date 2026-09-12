"""`take --scratch` claims a slot on a generated throwaway branch."""

from pathlib import Path

import pytest

from tests.conftest import git as run_git
from warmtree import git
from warmtree.cli import main
from warmtree.config import Config
from warmtree.pool import Pool


@pytest.fixture
def filled(repo: Path, monkeypatch: pytest.MonkeyPatch) -> Pool:
    monkeypatch.chdir(repo)
    pool = Pool(repo, Config(size=2))
    pool.fill()
    return pool


def test_scratch_generates_a_branch_and_prints_the_path(
    filled: Pool, capsys: pytest.CaptureFixture[str]
):
    assert main(["take", "--scratch", "--no-refill"]) == 0
    out, err = capsys.readouterr()
    path = Path(out.strip())
    assert out.count("\n") == 1
    branch = git.head_branch(path)
    assert branch is not None and branch.startswith("scratch/")
    assert f"warmtree release {branch}" in err


def test_two_scratch_takes_get_different_branches(filled: Pool):
    main(["take", "--scratch", "--no-refill"])
    main(["take", "--scratch", "--no-refill"])
    branches = {slot.branch for slot in filled.status() if slot.state == "taken"}
    assert len(branches) == 2


def test_scratch_skips_a_name_that_already_exists(
    filled: Pool, monkeypatch: pytest.MonkeyPatch
):
    run_git("branch", "scratch/deadbeef", cwd=filled.repo_root)
    tokens = iter(["deadbeef", "0badf00d"])
    monkeypatch.setattr("warmtree.pool.secrets.token_hex", lambda n: next(tokens))

    assert main(["take", "--scratch", "--no-refill"]) == 0
    branches = {slot.branch for slot in filled.status() if slot.state == "taken"}
    assert branches == {"scratch/0badf00d"}


def test_scratch_with_a_branch_name_is_refused(
    filled: Pool, capsys: pytest.CaptureFixture[str]
):
    assert main(["take", "--scratch", "feature", "--no-refill"]) == 2
    assert "do not pass one" in capsys.readouterr().err


def test_take_without_branch_or_scratch_is_refused(
    filled: Pool, capsys: pytest.CaptureFixture[str]
):
    assert main(["take", "--no-refill"]) == 2
    assert "--scratch" in capsys.readouterr().err
