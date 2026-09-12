"""`skill --user` installs into home-directory skills folders, never a repo."""

from pathlib import Path

import pytest

from warmtree import skill
from warmtree.cli import main


def test_detect_user_finds_tools_by_home_folder(tmp_path: Path):
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".codex").mkdir()

    keys = [target.key for target in skill.detect_user(tmp_path)]
    assert keys == ["claude", "codex"]


def test_detect_user_ignores_files_with_marker_names(tmp_path: Path):
    (tmp_path / ".cursor").write_text("not a folder\n")
    assert skill.detect_user(tmp_path) == []


def test_install_user_writes_into_personal_skills_dirs(tmp_path: Path):
    targets = [skill.by_tool("claude"), skill.by_tool("copilot")]
    results = skill.install(tmp_path, targets, user=True)

    assert [result.action for result in results] == ["written", "written"]
    assert (tmp_path / ".claude" / "skills" / "warmtree" / "SKILL.md").exists()
    assert (tmp_path / ".copilot" / "skills" / "warmtree" / "SKILL.md").exists()


def test_install_user_keeps_an_edited_copy(tmp_path: Path):
    target = skill.by_tool("claude")
    [first] = skill.install(tmp_path, [target], user=True)
    first.path.write_text("my own version\n", encoding="utf-8")

    [kept] = skill.install(tmp_path, [target], user=True)
    assert kept.action == "kept"


def test_skill_user_cli_works_outside_a_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    outside = tmp_path / "not-a-repo"
    outside.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    monkeypatch.chdir(outside)

    assert main(["skill", "--user"]) == 0
    out = capsys.readouterr().out
    assert "~/.claude/skills/warmtree/SKILL.md" in out
    assert (home / ".claude" / "skills" / "warmtree" / "SKILL.md").exists()


def test_skill_user_kept_note_names_the_user_flags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    monkeypatch.chdir(tmp_path)

    main(["skill", "--user"])
    copy = home / ".claude" / "skills" / "warmtree" / "SKILL.md"
    copy.write_text("my own version\n", encoding="utf-8")
    capsys.readouterr()

    assert main(["skill", "--user"]) == 0
    assert "warmtree skill --user --force" in capsys.readouterr().err


def test_skill_user_cli_without_agent_folders_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    monkeypatch.chdir(tmp_path)

    assert main(["skill", "--user"]) == 1
    assert "no agent folder" in capsys.readouterr().err


def test_skill_user_cli_with_explicit_tool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    monkeypatch.chdir(tmp_path)

    assert main(["skill", "--user", "--tool", "codex"]) == 0
    assert (home / ".codex" / "skills" / "warmtree" / "SKILL.md").exists()
