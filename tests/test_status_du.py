"""`status --du` measures what the pool costs on disk."""

import json
from pathlib import Path

import pytest

from warmtree.cli import _human, main


def test_status_du_adds_size_column_and_total(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.chdir(repo)
    main(["fill"])
    capsys.readouterr()

    assert main(["status", "--du"]) == 0
    out = capsys.readouterr().out
    assert "SIZE" in out
    assert "total " in out


def test_status_without_du_has_no_size_column(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.chdir(repo)
    main(["fill"])
    capsys.readouterr()

    assert main(["status"]) == 0
    assert "SIZE" not in capsys.readouterr().out


def test_status_json_du_includes_bytes(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.chdir(repo)
    main(["fill"])
    capsys.readouterr()

    assert main(["status", "--json", "--du"]) == 0
    slots = json.loads(capsys.readouterr().out)
    assert slots
    assert all(slot["du_bytes"] > 0 for slot in slots)

    main(["status", "--json"])
    slots = json.loads(capsys.readouterr().out)
    assert all("du_bytes" not in slot for slot in slots)


def test_human_sizes_read_like_du():
    assert _human(512) == "512 B"
    assert _human(2048) == "2.0 KB"
    assert _human(700 * 1024 * 1024) == "700.0 MB"
    assert _human(3 * 1024**4) == "3.0 TB"
