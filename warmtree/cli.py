"""Command line entry point. One function per command.

`take` prints only the slot path on stdout so `cd "$(warmtree take x)"` works.
Everything a human reads goes to stderr.
"""

import argparse
import json
import os
import subprocess
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from warmtree import __version__, config, git, skill
from warmtree.config import CONFIG_NAME, ConfigError
from warmtree.git import GitError
from warmtree.pool import Pool, PoolError, Slot
from warmtree.warm import WarmError

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
    if not (sys.argv[1:] if argv is None else argv):
        parser.print_help()
        return 2
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (GitError, ConfigError, PoolError, WarmError) as exc:
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
    init_skill = init.add_mutually_exclusive_group()
    init_skill.add_argument(
        "--no-skill", action="store_true", help="do not install the agent skill"
    )
    init_skill.add_argument(
        "--user-skill",
        action="store_true",
        help="install the agent skill into your home directory instead of "
        "the repo, so the repo never contains it",
    )
    init.set_defaults(func=cmd_init)

    skill_cmd = commands.add_parser(
        "skill", help="install the agent skill where this repo's coding agents look"
    )
    skill_cmd.add_argument(
        "--tool",
        action="append",
        choices=skill.TOOL_KEYS,
        help="install for this tool even if it was not detected; repeatable",
    )
    skill_cmd.add_argument(
        "--force", action="store_true", help="overwrite a copy that was edited"
    )
    skill_cmd.add_argument(
        "--user",
        action="store_true",
        help="install into the tools' personal skills folders in your home "
        "directory instead of the repo, so no repo ever contains the file",
    )
    skill_cmd.set_defaults(func=cmd_skill)

    fill = commands.add_parser("fill", help="create any missing slots")
    fill.set_defaults(func=cmd_fill)

    take = commands.add_parser("take", help="claim a ready slot for a branch")
    take.add_argument(
        "branch", nargs="?", help="branch to create or check out in the slot"
    )
    take.add_argument(
        "--scratch",
        action="store_true",
        help="claim a slot on a generated scratch/<id> branch, for throwaway "
        "work that does not deserve a name",
    )
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

    adopt = commands.add_parser(
        "adopt", help="move an existing worktree into the pool as a taken slot"
    )
    adopt.add_argument("path", help="path of the worktree to adopt")
    adopt.set_defaults(func=cmd_adopt)

    exec_cmd = commands.add_parser(
        "exec", help="run a command inside the slot holding a branch"
    )
    exec_cmd.add_argument("branch", help="branch whose slot to run in")
    exec_cmd.add_argument(
        "command",
        nargs=argparse.REMAINDER,
        help="command to run, after `--`",
    )
    exec_cmd.set_defaults(func=cmd_exec)

    remove = commands.add_parser("remove", help="delete slots")
    remove.add_argument("names", nargs="*", metavar="SLOT", help="slots to delete")
    remove.add_argument("--all", action="store_true", help="delete every slot")
    remove.add_argument("--force", action="store_true", help="delete taken slots too")
    remove.set_defaults(func=cmd_remove)

    refresh = commands.add_parser(
        "refresh", help="move waiting slots to base and re-warm if lockfiles changed"
    )
    refresh.set_defaults(func=cmd_refresh)

    size = commands.add_parser("size", help="show or change how many slots to keep")
    size.add_argument(
        "size", nargs="?", type=int, help="new size; grows or shrinks the pool to match"
    )
    size.set_defaults(func=cmd_size)

    which = commands.add_parser(
        "which", help="name the slot the current directory is in"
    )
    which.set_defaults(func=cmd_which)

    status = commands.add_parser("status", help="show every slot")
    status.add_argument("--json", action="store_true", help="print JSON for agents")
    status.add_argument(
        "--du", action="store_true", help="measure and show disk usage per slot"
    )
    status.set_defaults(func=cmd_status)

    doctor = commands.add_parser(
        "doctor", help="check the pool for drift between state, git, and disk"
    )
    doctor.add_argument("--fix", action="store_true", help="apply the safe repairs")
    doctor.set_defaults(func=cmd_doctor)

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
    if args.user_skill:
        home = Path.home()
        targets = skill.detect_user(home)
        if targets:
            _report_skill(home, skill.install(home, targets, user=True), user=True)
        else:
            print(
                "no agent folder found in your home directory; install later "
                "with e.g. `warmtree skill --user --tool claude` "
                f"(tools: {', '.join(skill.TOOL_KEYS)})"
            )
    elif not args.no_skill:
        targets = skill.detect(root)
        if targets:
            _report_skill(root, skill.install(root, targets))
        else:
            print(
                "no agent config detected; install the skill later with "
                "e.g. `warmtree skill --tool claude` "
                f"(tools: {', '.join(skill.TOOL_KEYS)})"
            )
    return 0


def cmd_skill(args: argparse.Namespace) -> int:
    # A user-level install touches only the home directory, so it works
    # outside any repo and never risks a skill file being committed.
    root = Path.home() if args.user else git.repo_root(Path.cwd())
    if args.tool:
        targets = [skill.by_tool(key) for key in args.tool]
    elif args.user:
        targets = skill.detect_user(root)
    else:
        targets = skill.detect(root)
    if not targets:
        if args.user:
            fail(
                "no agent folder found in your home directory "
                "(.claude, .copilot, .codex, .cursor); pick one with e.g. "
                "`warmtree skill --user --tool claude` "
                f"(tools: {', '.join(skill.TOOL_KEYS)})"
            )
        else:
            fail(
                "no agent config detected (CLAUDE.md, AGENTS.md, .cursor, "
                "copilot-instructions.md); pick one with e.g. "
                "`warmtree skill --tool claude` "
                f"(tools: {', '.join(skill.TOOL_KEYS)})"
            )
        return 1
    results = skill.install(root, targets, force=args.force, user=args.user)
    _report_skill(root, results, user=args.user)
    return 0


def _report_skill(
    root: Path, results: list[skill.Installed], user: bool = False
) -> None:
    prefix = "~/" if user else ""
    for result in results:
        where = result.path.relative_to(root).as_posix()
        print(f"{result.action}: {result.target.tool} skill at {prefix}{where}")
    if any(result.action == "kept" for result in results):
        flags = "--user --force" if user else "--force"
        note(f"a copy you edited was kept; use `warmtree skill {flags}` to replace it")


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
    if args.scratch and args.branch:
        fail("--scratch generates the branch name; do not pass one")
        return 2
    if not args.scratch and not args.branch:
        fail("name a branch, or use --scratch for a generated one")
        return 2
    pool = _pool()
    slot, cold = pool.take(args.branch, from_ref=args.from_ref, scratch=args.scratch)
    branch = slot.branch
    if cold:
        note(f"no ready slot; created {slot.name} cold for {branch}")
    else:
        note(f"took {slot.name} for {branch}")
    if args.scratch:
        note(f"scratch branch: {branch}; release it with `warmtree release {branch}`")
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


def cmd_adopt(args: argparse.Namespace) -> int:
    pool = _pool()
    slot = pool.adopt(Path(args.path))
    note(f"adopted {args.path} as {slot.name} (branch {slot.branch})")
    note("editors and shells still open on the old path need repointing")
    print(slot.path)
    return 0


def cmd_exec(args: argparse.Namespace) -> int:
    command = args.command
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        fail("nothing to run; usage: warmtree exec <branch> -- <command...>")
        return 2
    slot = _pool().taken(args.branch)
    if not Path(slot.path).is_dir():
        fail(f"{slot.name} directory is missing; run `warmtree doctor`")
        return 1
    env = dict(os.environ)
    env["WARMTREE_SLOT"] = str(slot.number)
    # Streams are inherited on purpose: exec is a passthrough, and the
    # child's output and exit code are the whole point. Like any process
    # working inside a slot, the child does not pin it; do not release the
    # branch while a command is still running in its slot.
    try:
        completed = subprocess.run(command, cwd=slot.path, env=env)
    except FileNotFoundError:
        fail(f"command not found: {command[0]}")
        return 127
    except PermissionError:
        fail(f"not executable: {command[0]}")
        return 126
    except OSError as exc:
        fail(f"cannot run {command[0]}: {exc}")
        return 1
    return completed.returncode


def cmd_remove(args: argparse.Namespace) -> int:
    if not args.names and not args.all:
        fail("name a slot or pass --all")
        return 1
    removed = _pool().remove(names=args.names, all_slots=args.all, force=args.force)
    for slot in removed:
        print(f"removed {slot.name}")
    return 0


def cmd_refresh(args: argparse.Namespace) -> int:
    results = _pool().refresh()
    if not results:
        print("nothing to refresh")
        return 0
    for result in results:
        base = "moved to base" if result.moved else "at base"
        warmth = "re-warmed" if result.rewarmed else "still warm"
        print(f"{result.slot.name}: {base}, {warmth}")
    return 0


def cmd_size(args: argparse.Namespace) -> int:
    root = git.repo_root(Path.cwd())
    if args.size is not None:
        config.write_size(root, args.size)
        pool = Pool(root, config.load(root), log=note)
        for slot in pool.trim():
            print(f"removed {slot.name}")
        for slot in pool.fill():
            print(f"created {slot.name} at {slot.path}")
    cfg = config.load(root)
    counts = Counter(slot.state for slot in Pool(root, cfg).status())
    print(f"size: {cfg.size}")
    print(
        f"ready {counts['ready']}, taken {counts['taken']}, "
        f"warming {counts['warming']}, stale {counts['stale']}; "
        f"{counts.total()} slots in total"
    )
    return 0


def cmd_which(args: argparse.Namespace) -> int:
    here = Path.cwd().resolve()
    for slot in _pool().status():
        path = Path(slot.path).resolve()
        if here == path or path in here.parents:
            print(f"{slot.name} {slot.state} {slot.branch or '-'}")
            return 0
    fail("not inside a warmtree slot")
    return 1


def cmd_status(args: argparse.Namespace) -> int:
    slots = _pool().status()
    sizes = None
    if args.du:
        sizes = {slot.name: _tree_size(Path(slot.path)) for slot in slots}
    if args.json:
        items = []
        for slot in slots:
            item = slot.to_dict()
            if sizes is not None:
                item["du_bytes"] = sizes[slot.name]
            items.append(item)
        print(json.dumps(items, indent=2))
        return 0
    if not slots:
        print("no slots yet; run `warmtree fill`")
        return 0
    print_table(slots, sizes)
    if sizes is not None:
        print(f"total {_human(sum(sizes.values()))}")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    findings = _pool().doctor(fix=args.fix)
    if not findings:
        print("pool is healthy")
        return 0
    for finding in findings:
        where = finding.slot or "pool"
        line = f"{where}: {finding.problem}"
        if finding.fixed:
            line += " (fixed)"
        elif finding.hint:
            line += f" ({finding.hint})"
        print(line)
    return 0 if all(finding.fixed for finding in findings) else 1


def print_table(slots: list[Slot], sizes: dict[str, int] | None = None) -> None:
    rows = [("SLOT", "STATE", "BRANCH", "AGE", "WARMED", "PATH")]
    if sizes is not None:
        rows = [("SLOT", "STATE", "BRANCH", "AGE", "WARMED", "SIZE", "PATH")]
    for slot in slots:
        row = [
            slot.name,
            slot.state,
            slot.branch or "-",
            _age(slot.created),
            _age(slot.warmed) + " ago" if slot.warmed else "-",
            slot.path,
        ]
        if sizes is not None:
            row.insert(5, _human(sizes[slot.name]))
        rows.append(tuple(row))
    widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]) - 1)]
    for row in rows:
        cells = [cell.ljust(widths[i]) for i, cell in enumerate(row[:-1])]
        print("  ".join([*cells, row[-1]]))


def _tree_size(path: Path) -> int:
    """Total bytes under `path`. Symlinks are counted, not followed.

    os.walk puts a symlink to a directory in dirnames and does not recurse
    into it, so those links are sized explicitly or they would be missed.
    """
    total = 0
    for dirpath, dirnames, filenames in os.walk(path):
        links = [n for n in dirnames if os.path.islink(os.path.join(dirpath, n))]
        for name in filenames + links:
            try:
                total += os.lstat(os.path.join(dirpath, name)).st_size
            except OSError:
                pass  # deleted or unreadable mid-walk; skip it
    return total


def _human(size: int) -> str:
    value = float(size)
    unit = "B"
    for bigger in ("KB", "MB", "GB", "TB"):
        if value < 1024:
            break
        value /= 1024
        unit = bigger
    if unit == "B":
        return f"{int(value)} B"
    return f"{value:.1f} {unit}"


def _pool() -> Pool:
    root = git.repo_root(Path.cwd())
    return Pool(root, config.load(root), log=note)


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
