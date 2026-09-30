"""Repeat takes and `warmtree path`: anchors for agents that lose their cwd."""

import os
from pathlib import Path

import pytest

from warmtree.cli import main
from warmtree.config import Config
from warmtree.pool import Pool

CONFIG = "[pool]\nsize = 1\n"


def test_repeat_take_notes_the_slot_and_a_live_foreign_holder(repo: Path):
    messages: list[str] = []
    pool = Pool(repo, Config(size=1), log=messages.append)
    pool.fill()
    slot, _ = pool.take("same")
    # Our own pid is alive and differs from this process's parent, so it
    # stands in for another session still working in the slot.
    pool._update_slot(slot.name, lease_pid=os.getpid())

    again, cold = pool.take("same")

    assert again.name == slot.name
    assert cold is False
    assert f"same is already taken in {slot.name}" in messages
    assert any("held by process" in message for message in messages)


def test_path_prints_the_taken_slots_path(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    (repo / ".warmtree.toml").write_text(CONFIG)
    monkeypatch.chdir(repo)
    assert main(["take", "feature/a", "--no-refill"]) == 0
    taken = capsys.readouterr().out.strip()

    assert main(["path", "feature/a"]) == 0
    out, _ = capsys.readouterr()
    assert out.strip() == taken


def test_path_fails_when_the_branch_is_not_taken(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    (repo / ".warmtree.toml").write_text(CONFIG)
    monkeypatch.chdir(repo)

    assert main(["path", "feature/a"]) == 1
    _, err = capsys.readouterr()
    assert "no taken slot has branch" in err
