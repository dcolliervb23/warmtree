"""Install the agent skill into the folders a repo's coding agents read.

The skill file ships inside this package. `warmtree init` looks for signs of
each tool in the repo (a CLAUDE.md, an AGENTS.md, a .cursor folder) and
copies the skill where that tool will find it. A copy the user has edited is
never overwritten without --force.
"""

import hashlib
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

SKILL_NAME = "warmtree"

# sha256 of every SKILL.md this package has ever shipped, oldest first.
# Append the new hash whenever SKILL.md changes. A copy on disk that matches
# any of these is an unedited install of some past version, so a refresh may
# overwrite it without --force. A copy matching none of them was edited by
# the user and is kept.
SHIPPED_SKILL_HASHES = (
    "854d4ee963af2fc0636a1574d7d615a6d3d4a54fcb21b809076c1b30f5460bc0",  # v0.1.0
    "498d5df8ad3d4e408bafa06598a5114a8711e1a5a0b574309e6da15292c971b6",  # v0.1.1
    "d9570ba9f2a72ff34534e2610f40c2a477a09dc276bcf8d3cad8be6a0e574dfc",
)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Target:
    key: str  # what --tool accepts
    tool: str  # name shown to the user
    markers: tuple[str, ...]  # files or folders that mean the tool is used here
    skills_dir: str  # where that tool reads project skills from
    user_dir: str  # folder in the home directory the tool keeps its config in

    @property
    def user_skills_dir(self) -> str:
        """Where the tool reads personal skills from, relative to home."""
        return f"{self.user_dir}/skills"


# A bare .github folder is not a marker: nearly every repo has one for CI.
TARGETS = (
    Target(
        "claude", "Claude Code", ("CLAUDE.md", ".claude"), ".claude/skills", ".claude"
    ),
    Target(
        "copilot",
        "GitHub Copilot",
        (".github/copilot-instructions.md",),
        ".github/skills",
        ".copilot",
    ),
    Target("codex", "Codex", ("AGENTS.md", ".agents"), ".agents/skills", ".codex"),
    Target("cursor", "Cursor", (".cursor",), ".cursor/skills", ".cursor"),
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


def detect_user(home: Path) -> list[Target]:
    """Tools installed for this user: their config folder is in home."""
    return [target for target in TARGETS if (home / target.user_dir).is_dir()]


def by_tool(key: str) -> Target:
    for target in TARGETS:
        if target.key == key:
            return target
    raise KeyError(key)


def install(
    root: Path, targets: list[Target], force: bool = False, user: bool = False
) -> list[Installed]:
    """Copy the skill into each target's skills folder.

    `root` is the repo root, or the home directory with user=True, which
    installs into each tool's personal skills folder so nothing is added
    to any repository.

    written: the file did not exist. current: identical copy already there.
    updated: an unedited copy of a past version, or --force, was overwritten.
    kept: a copy the user edited exists and force is off.
    """
    text = skill_text()
    results = []
    for target in targets:
        skills_dir = target.user_skills_dir if user else target.skills_dir
        path = root / skills_dir / SKILL_NAME / "SKILL.md"
        if not path.exists():
            action = "written"
        else:
            existing = path.read_text(encoding="utf-8")
            if existing == text:
                action = "current"
            elif force or _sha256(existing) in SHIPPED_SKILL_HASHES:
                action = "updated"
            else:
                action = "kept"
        if action in ("written", "updated"):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        results.append(Installed(target, path, action))
    return results
