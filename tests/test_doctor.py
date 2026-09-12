"""`doctor` finds drift between state.json, git, and the filesystem."""

import shutil
from pathlib import Path

import pytest

from tests.conftest import git as run_git
from warmtree import git
from warmtree.cli import main
from warmtree.config import Config
from warmtree.pool import Pool


def test_healthy_pool_has_no_findings(repo: Path):
    pool = Pool(repo, Config(size=1))
    pool.fill()
    assert pool.doctor() == []


def test_missing_directory_is_reported_and_fixed(repo: Path):
    pool = Pool(repo, Config(size=1))
    [slot] = pool.fill()
    shutil.rmtree(slot.path)

    [finding] = pool.doctor()
    assert finding.slot == slot.name
    assert not finding.fixed
    assert pool.status()  # nothing was changed without --fix

    [finding] = pool.doctor(fix=True)
    assert finding.fixed
    assert pool.status() == []
    assert pool.doctor() == []


def test_stuck_warming_slot_is_reported_never_repaired(repo: Path):
    # A warming slot may belong to a live fill or refresh in another
    # process; changing its state here could start a second warm in the
    # same directory, so doctor only reports it.
    pool = Pool(repo, Config(size=1))
    [slot] = pool.fill()
    pool._update_slot(slot.name, state="warming")

    [finding] = pool.doctor()
    assert "warming" in finding.problem
    assert "remove" in finding.hint

    [finding] = pool.doctor(fix=True)
    assert not finding.fixed
    assert pool.status()[0].state == "warming"


def test_fixing_a_missing_slot_leaves_other_registrations_alone(repo: Path):
    pool = Pool(repo, Config(size=1))
    [slot] = pool.fill()
    other = repo.parent / "hand-made"
    run_git("worktree", "add", "-q", "--detach", str(other), cwd=repo)
    shutil.rmtree(slot.path)

    [finding] = pool.doctor(fix=True)
    assert finding.fixed
    assert other.resolve() in git.worktree_list(repo)


def test_stranger_directory_in_pool_dir_is_reported(repo: Path):
    pool = Pool(repo, Config(size=1))
    pool.fill()
    (pool.dir / "slot-99").mkdir()

    [finding] = pool.doctor()
    assert finding.slot is None
    assert "slot-99" in finding.problem
    assert not finding.fixed


def test_unregistered_worktree_is_reported_not_fixed(repo: Path):
    pool = Pool(repo, Config(size=1))
    [slot] = pool.fill()
    shutil.rmtree(repo / ".git" / "worktrees" / Path(slot.path).name)

    [finding] = pool.doctor(fix=True)
    assert finding.slot == slot.name
    assert not finding.fixed
    assert "repair" in finding.hint


def test_doctor_cli_healthy_and_broken(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.chdir(repo)
    main(["fill"])
    capsys.readouterr()

    assert main(["doctor"]) == 0
    assert "healthy" in capsys.readouterr().out

    slots = Pool(repo, Config()).status()
    shutil.rmtree(slots[0].path)
    assert main(["doctor"]) == 1
    assert main(["doctor", "--fix"]) == 0
    assert main(["doctor"]) == 0
