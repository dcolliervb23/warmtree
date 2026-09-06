"""Installing the agent skill into the tool folders a repo already uses."""

from pathlib import Path

import pytest

from warmtree import skill
from warmtree.cli import main

SKILL_REL = Path("warmtree") / "SKILL.md"


def test_skill_text_ships_inside_the_package():
    text = skill.skill_text()
    assert text.startswith("---\nname: warmtree\n")
    assert "warmtree take" in text


@pytest.mark.parametrize(
    ("marker", "skills_dir"),
    [
        ("CLAUDE.md", ".claude/skills"),
        (".claude/", ".claude/skills"),
        (".github/copilot-instructions.md", ".github/skills"),
        ("AGENTS.md", ".agents/skills"),
        (".agents/", ".agents/skills"),
        (".cursor/", ".cursor/skills"),
    ],
)
def test_detect_maps_marker_to_skills_dir(tmp_path: Path, marker: str, skills_dir: str):
    path = tmp_path / marker
    if marker.endswith("/"):
        path.mkdir(parents=True)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("")

    targets = skill.detect(tmp_path)

    assert [t.skills_dir for t in targets] == [skills_dir]


def test_detect_ignores_a_bare_github_folder(tmp_path: Path):
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    assert skill.detect(tmp_path) == []


def test_detect_finds_several_tools_in_a_fixed_order(tmp_path: Path):
    (tmp_path / "AGENTS.md").write_text("")
    (tmp_path / "CLAUDE.md").write_text("")
    tools = [t.tool for t in skill.detect(tmp_path)]
    assert tools == ["Claude Code", "Codex"]


def test_by_tool_looks_up_a_target_by_key():
    assert skill.by_tool("cursor").skills_dir == ".cursor/skills"
    with pytest.raises(KeyError):
        skill.by_tool("emacs")


def test_install_writes_missing_skill(tmp_path: Path):
    [result] = skill.install(tmp_path, [skill.by_tool("claude")])

    assert result.action == "written"
    path = tmp_path / ".claude" / "skills" / SKILL_REL
    assert result.path == path
    assert path.read_text(encoding="utf-8") == skill.skill_text()


def test_install_leaves_identical_copy_alone(tmp_path: Path):
    skill.install(tmp_path, [skill.by_tool("claude")])
    [result] = skill.install(tmp_path, [skill.by_tool("claude")])
    assert result.action == "current"


def test_install_keeps_an_edited_copy_unless_forced(tmp_path: Path):
    target = skill.by_tool("claude")
    [first] = skill.install(tmp_path, [target])
    first.path.write_text("my own version\n", encoding="utf-8")

    [kept] = skill.install(tmp_path, [target])
    assert kept.action == "kept"
    assert first.path.read_text(encoding="utf-8") == "my own version\n"

    [updated] = skill.install(tmp_path, [target], force=True)
    assert updated.action == "updated"
    assert first.path.read_text(encoding="utf-8") == skill.skill_text()


# --- through the CLI ------------------------------------------------------


def test_init_installs_skill_where_markers_are_found(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.chdir(repo)
    (repo / "CLAUDE.md").write_text("# rules\n")
    (repo / ".github").mkdir()
    (repo / ".github" / "copilot-instructions.md").write_text("")

    assert main(["init"]) == 0

    out = capsys.readouterr().out
    assert (repo / ".claude" / "skills" / SKILL_REL).exists()
    assert (repo / ".github" / "skills" / SKILL_REL).exists()
    assert not (repo / ".agents").exists()
    assert "Claude Code" in out
    assert "GitHub Copilot" in out


def test_init_without_markers_says_how_to_install(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.chdir(repo)
    assert main(["init"]) == 0
    assert "warmtree skill --tool" in capsys.readouterr().out
    assert not (repo / ".claude").exists()


def test_init_no_skill_skips_installation(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.chdir(repo)
    (repo / "CLAUDE.md").write_text("# rules\n")
    assert main(["init", "--no-skill"]) == 0
    assert not (repo / ".claude").exists()


def test_skill_command_reports_each_target(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.chdir(repo)
    (repo / "AGENTS.md").write_text("")

    assert main(["skill"]) == 0
    assert "written" in capsys.readouterr().out

    assert main(["skill"]) == 0
    assert "current" in capsys.readouterr().out


def test_skill_command_with_explicit_tool(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.chdir(repo)
    assert main(["skill", "--tool", "cursor"]) == 0
    assert (repo / ".cursor" / "skills" / SKILL_REL).exists()


def test_skill_command_with_nothing_detected(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.chdir(repo)
    assert main(["skill"]) == 1
    assert "--tool" in capsys.readouterr().err


def test_skill_command_force_overwrites_edited_copy(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.chdir(repo)
    main(["skill", "--tool", "claude"])
    path = repo / ".claude" / "skills" / SKILL_REL
    path.write_text("edited\n")

    assert main(["skill", "--tool", "claude"]) == 0
    assert "kept" in capsys.readouterr().out
    assert path.read_text() == "edited\n"

    assert main(["skill", "--tool", "claude", "--force"]) == 0
    assert "updated" in capsys.readouterr().out
    assert path.read_text(encoding="utf-8") == skill.skill_text()
