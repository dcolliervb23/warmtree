"""Command line entry point. One function per command."""

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from warmtree import __version__, config, git
from warmtree.config import CONFIG_NAME, ConfigError
from warmtree.git import GitError
from warmtree.pool import Pool, Slot

# Lockfiles `init` looks for to pre-fill `lockfiles`. It never guesses `run`.
KNOWN_LOCKFILES = (
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "bun.lockb",
    "bun.lock",
    "uv.lock",
    "poetry.lock",
    "Pipfile.lock",
    "requirements.txt",
    "Cargo.lock",
    "go.sum",
    "packages.lock.json",
    "Gemfile.lock",
    "composer.lock",
)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (GitError, ConfigError) as exc:
        print(f"warmtree: {exc}", file=sys.stderr)
        return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="warmtree", description="A pool of pre-warmed git worktrees."
    )
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)

    init = commands.add_parser("init", help=f"write a starter {CONFIG_NAME}")
    init.add_argument("--force", action="store_true", help="overwrite an existing file")
    init.set_defaults(func=cmd_init)

    fill = commands.add_parser("fill", help="create any missing slots")
    fill.set_defaults(func=cmd_fill)

    status = commands.add_parser("status", help="show every slot")
    status.add_argument("--json", action="store_true", help="print JSON for agents")
    status.set_defaults(func=cmd_status)

    return parser


def cmd_init(args: argparse.Namespace) -> int:
    root = git.repo_root(Path.cwd())
    target = root / CONFIG_NAME
    if target.exists() and not args.force:
        print(f"warmtree: {target} already exists (use --force)", file=sys.stderr)
        return 1
    lockfiles = [name for name in KNOWN_LOCKFILES if (root / name).exists()]
    target.write_text(config.starter_toml(lockfiles), encoding="utf-8")
    print(f"wrote {target}")
    if lockfiles:
        print(f"lockfiles: {', '.join(lockfiles)}")
    print("edit [warm] run to add your install command; warmtree never guesses it")
    return 0


def cmd_fill(args: argparse.Namespace) -> int:
    pool = _pool()
    created = pool.fill()
    for slot in created:
        print(f"created {slot.name} at {slot.path}")
    if not created:
        print(f"pool is full ({len(pool.status())} slots)")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    slots = _pool().status()
    if args.json:
        print(json.dumps([slot.to_dict() for slot in slots], indent=2))
        return 0
    if not slots:
        print("no slots yet; run `warmtree fill`")
        return 0
    print_table(slots)
    return 0


def print_table(slots: list[Slot]) -> None:
    rows = [("SLOT", "STATE", "BRANCH", "AGE", "WARMED", "PATH")]
    for slot in slots:
        rows.append(
            (
                slot.name,
                slot.state,
                slot.branch or "-",
                _age(slot.created),
                _age(slot.warmed) + " ago" if slot.warmed else "-",
                slot.path,
            )
        )
    widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]) - 1)]
    for row in rows:
        cells = [cell.ljust(widths[i]) for i, cell in enumerate(row[:-1])]
        print("  ".join([*cells, row[-1]]))


def _pool() -> Pool:
    root = git.repo_root(Path.cwd())
    return Pool(root, config.load(root))


def _age(timestamp: str) -> str:
    """Compact human age like `4m`, `3h`, `2d`."""
    then = datetime.fromisoformat(timestamp)
    seconds = int((datetime.now(UTC) - then).total_seconds())
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"
