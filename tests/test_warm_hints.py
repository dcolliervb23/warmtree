"""Empty `warm.run` is called out where it bites (#17)."""

from pathlib import Path

import pytest

from warmtree.cli import main


def test_fill_warns_when_run_is_empty_and_a_lockfile_exists(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    (repo / "package-lock.json").write_text("{}\n")
    monkeypatch.chdir(repo)

    assert main(["fill"]) == 0
    err = capsys.readouterr().err
    assert "no dependencies installed" in err
    assert 'run = ["npm ci"]' in err


def test_fill_stays_quiet_when_run_is_configured(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    (repo / "package-lock.json").write_text("{}\n")
    (repo / ".warmtree.toml").write_text('[warm]\nrun = ["true"]\n')
    monkeypatch.chdir(repo)

    assert main(["fill"]) == 0
    assert "no dependencies installed" not in capsys.readouterr().err


def test_fill_stays_quiet_without_a_known_lockfile(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.chdir(repo)
    assert main(["fill"]) == 0
    assert "no dependencies installed" not in capsys.readouterr().err


def test_init_suggests_the_matching_install_command(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    (repo / "uv.lock").write_text("")
    monkeypatch.chdir(repo)

    assert main(["init", "--no-skill", "--no-instructions"]) == 0
    assert 'suggests run = ["uv sync"]' in capsys.readouterr().out
