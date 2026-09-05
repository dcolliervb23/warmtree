"""Command line entry point. One function per command.

`take` prints only the slot path on stdout so `cd "$(warmtree take x)"` works.
Everything a human reads goes to stderr.
"""

import argparse
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from warmtree import __version__, config, git
from warmtree.config import CONFIG_NAME, ConfigError
from warmtree.git import GitError
from warmtree.pool import Pool, PoolError, Slot

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
    except (GitError, ConfigError, PoolError) as exc:
        fail(str(exc))
        return 1


def fail(message: str) -> None:
    print(f"warmtree: {message}", file=sys.stderr)


def note(message: str) -> None:
    print(message, file=sys.stderr)


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

    take = commands.add_parser("take", help="claim a ready slot for a branch")
    take.add_argument("branch", help="branch to create or check out in the slot")
    take.add_argument("--from", dest="from_ref", metavar="REF", help="start point")
    refill = take.add_mutually_exclusive_group()
    refill.add_argument("--no-refill", action="store_true", help="do not refill")
    refill.add_argument(
        "--refill-background",
        action="store_true",
        help="refill in a detached process and return at once",
    )
    take.set_defaults(func=cmd_take)

    release = commands.add_parser("release", help="return a slot to the pool")
    release.add_argument("branch", help="branch currently checked out in the slot")
    release.add_argument("--keep-branch", action="store_true", help="keep the ref")
    release.add_argument("--force", action="store_true", help="discard changes")
    release.set_defaults(func=cmd_release)

    remove = commands.add_parser("remove", help="delete slots")
    remove.add_argument("names", nargs="*", metavar="SLOT", help="slots to delete")
    remove.add_argument("--all", action="store_true", help="delete every slot")
    remove.add_argument("--force", action="store_true", help="delete taken slots too")
    remove.set_defaults(func=cmd_remove)

    status = commands.add_parser("status", help="show every slot")
    status.add_argument("--json", action="store_true", help="print JSON for agents")
    status.set_defaults(func=cmd_status)

    return parser


def cmd_init(args: argparse.Namespace) -> int:
    root = git.repo_root(Path.cwd())
    target = root / CONFIG_NAME
    if target.exists() and not args.force:
        fail(f"{target} already exists (use --force)")
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
        ready = sum(1 for slot in pool.status() if slot.state == "ready")
        print(f"pool is full ({ready} ready)")
    return 0


def cmd_take(args: argparse.Namespace) -> int:
    pool = _pool()
    slot, cold = pool.take(args.branch, from_ref=args.from_ref)
    if cold:
        note(f"no ready slot; created {slot.name} cold for {args.branch}")
    else:
        note(f"took {slot.name} for {args.branch}")
    print(slot.path)

    if args.refill_background:
        _spawn_background_fill(pool.repo_root)
        note("refilling in the background")
    elif not args.no_refill:
        for created in pool.fill():
            note(f"refilled {created.name}")
    return 0


def cmd_release(args: argparse.Namespace) -> int:
    slot, branch_deleted = _pool().release(
        args.branch, keep_branch=args.keep_branch, force=args.force
    )
    print(f"released {slot.name}")
    if branch_deleted:
        print(f"deleted branch {args.branch}")
    elif not args.keep_branch:
        note(f"kept branch {args.branch}: it has commits that are not merged")
    return 0


def cmd_remove(args: argparse.Namespace) -> int:
    if not args.names and not args.all:
        fail("name a slot or pass --all")
        return 1
    removed = _pool().remove(names=args.names, all_slots=args.all, force=args.force)
    for slot in removed:
        print(f"removed {slot.name}")
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


def _spawn_background_fill(repo_root: Path) -> None:
    """Start `warmtree fill` detached from this process and terminal."""
    command = [sys.executable, "-m", "warmtree", "fill"]
    options: dict = {
        "cwd": repo_root,
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if sys.platform == "win32":
        options["creationflags"] = (
            subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        )
    else:
        options["start_new_session"] = True
    subprocess.Popen(command, **options)


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
