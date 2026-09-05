import json
from pathlib import Path

import pytest

from warmtree import __version__, config
from warmtree.cli import main


def test_version_flag(capsys: pytest.CaptureFixture[str]):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_init_writes_starter_config_with_detected_lockfiles(
    repo: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.chdir(repo)
    (repo / "uv.lock").write_text("")

    assert main(["init"]) == 0

    cfg = config.load(repo)
    assert cfg.lockfiles == ("uv.lock",)
    assert cfg.run == ()


def test_init_works_from_a_subdirectory(repo: Path, monkeypatch: pytest.MonkeyPatch):
    sub = repo / "src"
    sub.mkdir()
    monkeypatch.chdir(sub)
    assert main(["init"]) == 0
    assert (repo / config.CONFIG_NAME).exists()


def test_init_refuses_to_overwrite_without_force(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.chdir(repo)
    (repo / config.CONFIG_NAME).write_text("[pool]\nsize = 7\n")

    assert main(["init"]) == 1
    assert "already exists" in capsys.readouterr().err
    assert config.load(repo).size == 7

    assert main(["init", "--force"]) == 0
    assert config.load(repo).size == 2


def test_fill_then_status_lists_ready_slots(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.chdir(repo)
    (repo / config.CONFIG_NAME).write_text("[pool]\nsize = 2\n")

    assert main(["fill"]) == 0
    capsys.readouterr()

    assert main(["status"]) == 0
    out = capsys.readouterr().out
    assert "slot-1" in out
    assert "slot-2" in out
    assert out.count("ready") == 2


def test_status_json_is_machine_readable(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.chdir(repo)
    main(["fill"])
    capsys.readouterr()

    assert main(["status", "--json"]) == 0
    slots = json.loads(capsys.readouterr().out)
    assert len(slots) == 2
    assert {slot["state"] for slot in slots} == {"ready"}
    assert set(slots[0]) >= {"name", "path", "state", "branch", "created", "warmed"}


def test_second_fill_reports_pool_is_full(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.chdir(repo)
    main(["fill"])
    capsys.readouterr()
    assert main(["fill"]) == 0
    assert "full" in capsys.readouterr().out


def test_outside_a_git_repo_fails_cleanly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.chdir(tmp_path)
    assert main(["status"]) == 1
    assert "not a git repository" in capsys.readouterr().err


def test_bad_config_fails_cleanly(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.chdir(repo)
    (repo / config.CONFIG_NAME).write_text("[pool]\nsizes = 2\n")
    assert main(["fill"]) == 1
    assert "sizes" in capsys.readouterr().err
