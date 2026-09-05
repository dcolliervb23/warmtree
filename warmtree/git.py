"""Thin wrapper around the git command line.

Each function runs one git command and returns the little we need. Nothing
here is mocked in tests; they run against real temporary repos.
"""

import subprocess
from pathlib import Path


class GitError(Exception):
    """git exited non-zero. The message is git's own stderr."""


def run(args: list[str], cwd: Path) -> str:
    """Run `git <args>` in cwd and return stripped stdout."""
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        message = result.stderr.strip()
        if not message:
            message = f"git {' '.join(args)} exited with code {result.returncode}"
        raise GitError(message)
    return result.stdout.strip()


def repo_root(start: Path) -> Path:
    """Root of the main worktree that contains `start`.

    `--git-common-dir` points at the shared `.git` directory even when `start`
    is inside a linked worktree, so the pool always belongs to the main repo.
    """
    common_dir = run(["rev-parse", "--git-common-dir"], cwd=start)
    return (start / common_dir).resolve().parent


def default_branch(repo: Path) -> str:
    """The branch slots park on when the config does not name one.

    Prefer what the remote calls its default. Without a remote, fall back to
    whatever branch the main worktree has checked out.
    """
    try:
        remote_head = run(
            ["symbolic-ref", "--short", "refs/remotes/origin/HEAD"], cwd=repo
        )
        return remote_head.removeprefix("origin/")
    except GitError:
        pass
    try:
        return run(["symbolic-ref", "--short", "HEAD"], cwd=repo)
    except GitError as exc:
        raise GitError(
            "cannot detect the default branch (HEAD is detached and there is no "
            "origin/HEAD); set base in .warmtree.toml"
        ) from exc


def rev_parse(repo: Path, ref: str) -> str:
    """Full commit hash that `ref` points at."""
    return run(["rev-parse", "--verify", f"{ref}^{{commit}}"], cwd=repo)


def worktree_add_detached(repo: Path, path: Path, ref: str) -> None:
    """Create a worktree at `path` with a detached HEAD at `ref`.

    Detached means no branch is checked out, so the slot never collides with
    git's one-branch-per-worktree rule until `take` puts a branch on it.
    """
    run(["worktree", "add", "--detach", str(path), ref], cwd=repo)


def worktree_list(repo: Path) -> list[Path]:
    """Paths of every worktree git knows about, main worktree first."""
    output = run(["worktree", "list", "--porcelain"], cwd=repo)
    paths = []
    for line in output.splitlines():
        if line.startswith("worktree "):
            paths.append(Path(line.removeprefix("worktree ")).resolve())
    return paths


def head_branch(path: Path) -> str | None:
    """Name of the checked-out branch, or None when HEAD is detached."""
    try:
        return run(["symbolic-ref", "--quiet", "--short", "HEAD"], cwd=path)
    except GitError:
        return None
