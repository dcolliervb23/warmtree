"""Every capability is documented in --help, the README, and the shipped skill."""

import argparse
from pathlib import Path

import pytest

from warmtree import cli, skill

ROOT = Path(__file__).resolve().parent.parent
README = (ROOT / "README.md").read_text(encoding="utf-8")
SKILL = skill.skill_text()

# Setup commands a person runs once; agents are not told to run them.
SETUP_ONLY = {"init", "skill", "instructions"}


def commands() -> dict[str, argparse.ArgumentParser]:
    parser = cli.build_parser()
    [sub] = [a for a in parser._actions if isinstance(a, argparse._SubParsersAction)]
    return dict(sub.choices)


def long_flags(parser: argparse.ArgumentParser) -> list[str]:
    return [
        flag
        for action in parser._actions
        for flag in action.option_strings
        if flag.startswith("--") and flag != "--help"
    ]


def rows(text: str, name: str) -> str:
    """The command's own table rows, so a mention elsewhere cannot stand in."""
    starts = (f"| `warmtree {name} ", f"| `warmtree {name}`")
    return "\n".join(line for line in text.splitlines() if line.startswith(starts))


COMMANDS = commands()


@pytest.mark.parametrize("name", sorted(COMMANDS))
def test_every_command_has_a_description(name: str):
    assert COMMANDS[name].description, f"warmtree {name} -h has no description"


@pytest.mark.parametrize("name", sorted(COMMANDS))
def test_every_argument_has_a_real_help_line(name: str):
    for action in COMMANDS[name]._actions:
        if "--help" in action.option_strings:
            continue
        words = (action.help or "").split()
        where = f"warmtree {name}: {action.dest}"
        assert len(words) >= 3, f"{where} help is {action.help!r}"


@pytest.mark.parametrize("name", sorted(COMMANDS))
def test_readme_documents_every_command_and_flag(name: str):
    row = rows(README, name)
    assert row, f"README command table has no row for warmtree {name}"
    for flag in long_flags(COMMANDS[name]):
        assert flag in row, f"README row for warmtree {name} lacks {flag}"


@pytest.mark.parametrize("name", sorted(set(COMMANDS) - SETUP_ONLY))
def test_shipped_skill_documents_every_agent_command_and_flag(name: str):
    row = rows(SKILL, name)
    assert row, f"SKILL.md quick reference has no row for warmtree {name}"
    for flag in long_flags(COMMANDS[name]):
        assert flag in row, f"SKILL.md row for warmtree {name} lacks {flag}"
