"""`instructions` maintains a marked block in agent instruction files."""

from pathlib import Path

import pytest

from warmtree import skill
from warmtree.cli import main
from warmtree.skill import INSTRUCTIONS_BEGIN, INSTRUCTIONS_END


def test_user_level_writes_each_tools_file(isolated_home: Path):
    (isolated_home / ".claude").mkdir()
    (isolated_home / ".copilot").mkdir()
    (isolated_home / ".codex").mkdir()

    targets = skill.detect_user(isolated_home)
    results = skill.install_instructions(isolated_home, targets, user=True)

    assert [r.action for r in results] == ["written", "written", "written"]
    for rel in (
        ".claude/CLAUDE.md",
        ".copilot/copilot-instructions.md",
        ".codex/AGENTS.md",
    ):
        text = (isolated_home / rel).read_text()
        assert INSTRUCTIONS_BEGIN in text
        assert "prefer warmtree" in text
        assert "not a prohibition" in text


def test_reruns_are_idempotent(isolated_home: Path):
    (isolated_home / ".claude").mkdir()
    targets = skill.detect_user(isolated_home)
    skill.install_instructions(isolated_home, targets, user=True)
    [result] = skill.install_instructions(isolated_home, targets, user=True)
    assert result.action == "current"


def test_existing_content_outside_the_block_is_untouched(isolated_home: Path):
    (isolated_home / ".claude").mkdir()
    memory = isolated_home / ".claude" / "CLAUDE.md"
    memory.write_text("# My rules\n\nAlways be kind to reviewers.\n")

    targets = skill.detect_user(isolated_home)
    [result] = skill.install_instructions(isolated_home, targets, user=True)

    assert result.action == "updated"
    text = memory.read_text()
    assert text.startswith("# My rules\n\nAlways be kind to reviewers.\n")
    assert INSTRUCTIONS_BEGIN in text


def test_a_stale_block_is_replaced_in_place(isolated_home: Path):
    (isolated_home / ".claude").mkdir()
    memory = isolated_home / ".claude" / "CLAUDE.md"
    stale = f"{INSTRUCTIONS_BEGIN}\nold words\n{INSTRUCTIONS_END}"
    memory.write_text(f"# Mine\n\n{stale}\n\n# Also mine\n")

    targets = skill.detect_user(isolated_home)
    [result] = skill.install_instructions(isolated_home, targets, user=True)

    assert result.action == "updated"
    text = memory.read_text()
    assert "old words" not in text
    assert text.startswith("# Mine\n")
    assert "# Also mine" in text
    assert text.count(INSTRUCTIONS_BEGIN) == 1


def test_cursor_is_skipped(isolated_home: Path):
    (isolated_home / ".cursor").mkdir()
    targets = skill.detect_user(isolated_home)
    [result] = skill.install_instructions(isolated_home, targets, user=True)
    assert result.action == "skipped"


def test_repo_level_writes_repo_files(repo: Path, monkeypatch: pytest.MonkeyPatch):
    (repo / "CLAUDE.md").write_text("# Repo rules\n")
    monkeypatch.chdir(repo)

    assert main(["instructions", "--repo"]) == 0
    text = (repo / "CLAUDE.md").read_text()
    assert text.startswith("# Repo rules\n")
    assert INSTRUCTIONS_BEGIN in text


def test_init_adds_user_instructions_by_default(
    repo: Path, isolated_home: Path, monkeypatch: pytest.MonkeyPatch
):
    (isolated_home / ".claude").mkdir()
    monkeypatch.chdir(repo)

    assert main(["init"]) == 0
    assert INSTRUCTIONS_BEGIN in (isolated_home / ".claude" / "CLAUDE.md").read_text()


def test_init_no_instructions_skips_them(
    repo: Path, isolated_home: Path, monkeypatch: pytest.MonkeyPatch
):
    (isolated_home / ".claude").mkdir()
    monkeypatch.chdir(repo)

    assert main(["init", "--no-instructions"]) == 0
    assert not (isolated_home / ".claude" / "CLAUDE.md").exists()
