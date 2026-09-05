"""refresh and warming through the command line."""

from pathlib import Path

import pytest

from warmtree import git
from warmtree.cli import main

CONFIG = """
[pool]
size = 1
lockfiles = ["uv.lock"]

[warm]
run = ["echo warmed>> warmed.txt"]
"""


def setup_repo(repo: Path) -> None:
    (repo / ".warmtree.toml").write_text(CONFIG)
    (repo / "uv.lock").write_text("v1\n")
    git.run(["add", "uv.lock"], cwd=repo)
    git.run(["commit", "-q", "-m", "lock"], cwd=repo)


def test_fill_reports_warm_progress_on_stderr(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.chdir(repo)
    setup_repo(repo)

    assert main(["fill"]) == 0

    out, err = capsys.readouterr()
    assert "created slot-1" in out
    assert "echo warmed" in err


def test_refresh_reports_per_slot(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.chdir(repo)
    setup_repo(repo)
    main(["fill"])
    capsys.readouterr()

    assert main(["refresh"]) == 0
    assert "slot-1: at base, still warm" in capsys.readouterr().out

    (repo / "uv.lock").write_text("v2\n")
    git.run(["commit", "-q", "-am", "bump lock"], cwd=repo)

    assert main(["refresh"]) == 0
    assert "slot-1: moved to base, re-warmed" in capsys.readouterr().out


def test_refresh_with_no_slots(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.chdir(repo)
    assert main(["refresh"]) == 0
    assert "nothing to refresh" in capsys.readouterr().out


def test_failed_warm_is_reported_and_shows_stale(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.chdir(repo)
    (repo / ".warmtree.toml").write_text('[pool]\nsize = 1\n[warm]\nrun = ["exit 5"]\n')

    assert main(["fill"]) == 1
    assert "code 5" in capsys.readouterr().err

    assert main(["status"]) == 0
    assert "stale" in capsys.readouterr().out
