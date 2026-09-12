"""`exec` runs a command inside the slot holding a branch."""

import sys
from pathlib import Path

import pytest

from warmtree.cli import main
from warmtree.config import Config
from warmtree.pool import Pool


@pytest.fixture
def taken(repo: Path, monkeypatch: pytest.MonkeyPatch) -> Pool:
    monkeypatch.chdir(repo)
    pool = Pool(repo, Config(size=1))
    pool.fill()
    pool.take("feature")
    return pool


def test_exec_runs_in_the_slot_with_the_slot_variable(taken: Pool):
    script = (
        "import os, pathlib; "
        "pathlib.Path('ran.txt').write_text("
        "os.environ['WARMTREE_SLOT'] + ':' + os.getcwd())"
    )
    assert main(["exec", "feature", "--", sys.executable, "-c", script]) == 0

    slot = taken.taken("feature")
    marker = Path(slot.path) / "ran.txt"
    number, cwd = marker.read_text().split(":", 1)
    assert number == str(slot.number)
    assert Path(cwd).resolve() == Path(slot.path).resolve()


def test_exec_passes_the_exit_code_through(taken: Pool):
    code = main(["exec", "feature", "--", sys.executable, "-c", "raise SystemExit(3)"])
    assert code == 3


def test_exec_without_a_command_fails(taken: Pool, capsys: pytest.CaptureFixture[str]):
    assert main(["exec", "feature"]) == 2
    assert "nothing to run" in capsys.readouterr().err


def test_exec_for_a_branch_not_in_a_slot_fails(
    taken: Pool, capsys: pytest.CaptureFixture[str]
):
    assert main(["exec", "elsewhere", "--", "true"]) == 1
    assert "no taken slot" in capsys.readouterr().err


def test_exec_missing_program_returns_127(taken: Pool):
    assert main(["exec", "feature", "--", "definitely-not-a-real-program"]) == 127
