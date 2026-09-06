"""Install the agent skill into the folders a repo's coding agents read.

The skill file ships inside this package. `warmtree init` looks for signs of
each tool in the repo (a CLAUDE.md, an AGENTS.md, a .cursor folder) and
copies the skill where that tool will find it. A copy the user has edited is
never overwritten without --force.
"""

from dataclasses import dataclass
from importlib import resources
from pathlib import Path

SKILL_NAME = "warmtree"


@dataclass(frozen=True)
class Target:
    key: str  # what --tool accepts
    tool: str  # name shown to the user
    markers: tuple[str, ...]  # files or folders that mean the tool is used here
    skills_dir: str  # where that tool reads project skills from


# A bare .github folder is not a marker: nearly every repo has one for CI.
TARGETS = (
    Target("claude", "Claude Code", ("CLAUDE.md", ".claude"), ".claude/skills"),
    Target(
        "copilot",
        "GitHub Copilot",
        (".github/copilot-instructions.md",),
        ".github/skills",
    ),
    Target("codex", "Codex", ("AGENTS.md", ".agents"), ".agents/skills"),
    Target("cursor", "Cursor", (".cursor",), ".cursor/skills"),
)

TOOL_KEYS = tuple(target.key for target in TARGETS)


@dataclass(frozen=True)
class Installed:
    target: Target
    path: Path
    action: str  # written | updated | current | kept


def skill_text() -> str:
    """The SKILL.md bundled with this version of warmtree."""
    skill_file = resources.files("warmtree").joinpath("skills", SKILL_NAME, "SKILL.md")
    return skill_file.read_text(encoding="utf-8")


def detect(repo_root: Path) -> list[Target]:
    """Tools this repo shows signs of using, in TARGETS order."""
    return [
        target
        for target in TARGETS
        if any((repo_root / marker).exists() for marker in target.markers)
    ]


def by_tool(key: str) -> Target:
    for target in TARGETS:
        if target.key == key:
            return target
    raise KeyError(key)


def install(
    repo_root: Path, targets: list[Target], force: bool = False
) -> list[Installed]:
    """Copy the skill into each target's skills folder.

    written: the file did not exist. current: identical copy already there.
    kept: a different copy exists and force is off. updated: force overwrote it.
    """
    text = skill_text()
    results = []
    for target in targets:
        path = repo_root / target.skills_dir / SKILL_NAME / "SKILL.md"
        if not path.exists():
            action = "written"
        elif path.read_text(encoding="utf-8") == text:
            action = "current"
        elif force:
            action = "updated"
        else:
            action = "kept"
        if action in ("written", "updated"):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        results.append(Installed(target, path, action))
    return results
