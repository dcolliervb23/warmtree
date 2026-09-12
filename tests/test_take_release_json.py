"""`take --json` and `release --json` speak machine-readable stdout."""

import json
from pathlib import Path

import pytest

from tests.conftest import git as run_git
from warmtree.cli import main
from warmtree.config import Config
from warmtree.pool import Pool


@pytest.fixture
def filled(repo: Path, monkeypatch: pytest.MonkeyPatch) -> Pool:
    monkeypatch.chdir(repo)
    pool = Pool(repo, Config(size=1))
    pool.fill()
    return pool


def take_json(capsys: pytest.CaptureFixture[str], *extra: str) -> dict:
    capsys.readouterr()
    assert main(["take", "feature", "--json", "--no-refill", *extra]) == 0
    out = capsys.readouterr().out
    assert out.count("\n") == 1
    return json.loads(out)


def test_take_json_reports_the_claim(filled: Pool, capsys: pytest.CaptureFixture[str]):
    data = take_json(capsys)
    assert data["slot"] == "slot-1"
    assert data["branch"] == "feature"
    assert data["cold"] is False
    assert Path(data["path"]).is_dir()


def test_take_json_marks_a_cold_create(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.chdir(repo)  # empty pool: no fill
    data = take_json(capsys)
    assert data["cold"] is True


def test_release_json_reports_the_branch_fate(
    filled: Pool, capsys: pytest.CaptureFixture[str]
):
    main(["take", "feature", "--no-refill"])
    capsys.readouterr()

    assert main(["release", "feature", "--json"]) == 0
    out = capsys.readouterr().out
    data = json.loads(out)
    assert data == {"slot": "slot-1", "branch": "feature", "branch_deleted": True}


def test_release_json_keeps_the_unmerged_warning_on_stderr(
    filled: Pool, capsys: pytest.CaptureFixture[str]
):
    main(["take", "feature", "--no-refill"])
    path = Path(filled.status()[0].path)
    (path / "extra.txt").write_text("unmerged work\n")
    run_git("add", "extra.txt", cwd=path)
    run_git("commit", "-q", "-m", "unmerged", cwd=path)
    capsys.readouterr()

    assert main(["release", "feature", "--json"]) == 0
    out, err = capsys.readouterr()
    data = json.loads(out)
    assert data["branch_deleted"] is False
    assert "kept branch feature" in err


def test_without_json_the_old_contracts_hold(
    filled: Pool, capsys: pytest.CaptureFixture[str]
):
    capsys.readouterr()
    main(["take", "feature", "--no-refill"])
    out = capsys.readouterr().out
    assert not out.startswith("{")

    main(["release", "feature"])
    assert "released slot-1" in capsys.readouterr().out
