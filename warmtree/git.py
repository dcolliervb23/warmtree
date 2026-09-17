"""Thin wrapper around the git command line.

Each function runs one git command and returns the little we need. Nothing
here is mocked in tests; they run against real temporary repos.

By default every command runs with the repo's hooks disabled: pool
operations are plumbing, and a checkout hook that lingers — telemetry that
spawns children, say — holds git's inherited pipes and hangs the captured
call even after git itself exits. Per-slot setup belongs to the config's
`run` and `copy`, not to hooks firing while the pool moves its furniture.
`allow_hooks(True)` (from `[pool] git_hooks = true`) restores them.
"""

import atexit
import shutil
import subprocess
import tempfile
from pathlib import Path

_no_hooks_dir: str | None = None

_hooks_allowed = False


def _no_hooks_path() -> str:
    """A hooks path nothing else can own.

    A predictable name under the shared temp directory could be created by
    another user with hooks of their own, which git would then run while we
    promise hooks are off. So the path lives inside a fresh private
    directory (mkdtemp, mode 0700) and is itself never created.
    """
    global _no_hooks_dir
    if _no_hooks_dir is None:
        private = tempfile.mkdtemp(prefix="warmtree-no-hooks-")
        atexit.register(shutil.rmtree, private, ignore_errors=True)
        _no_hooks_dir = str(Path(private) / "hooks")
    return _no_hooks_dir


class GitError(Exception):
    """git exited non-zero. The message is git's own stderr."""


def allow_hooks(allowed: bool) -> None:
    """Let the repo's git hooks run during pool operations."""
    global _hooks_allowed
    _hooks_allowed = allowed


def run(args: list[str], cwd: Path) -> str:
    """Run `git <args>` in cwd and return stripped stdout."""
    prefix = [] if _hooks_allowed else ["-c", f"core.hooksPath={_no_hooks_path()}"]
    result = subprocess.run(
        ["git", *prefix, *args],
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


def owns_worktree(repo: Path, path: Path) -> bool:
    """Whether `path` is itself a working tree of this repository.

    Two conditions, both required. The shared git dir must be ours:
    `git worktree list` keeps listing a path whose directory was replaced,
    so registration alone proves nothing. And git's reported top level must
    be `path` itself: a plain subdirectory inside one of our worktrees
    shares our git dir without being a worktree root, and mistaking one
    for a slot would let release reset the tree that contains it.
    """
    try:
        theirs = run(
            ["rev-parse", "--path-format=absolute", "--git-common-dir"], cwd=path
        )
        ours = run(
            ["rev-parse", "--path-format=absolute", "--git-common-dir"], cwd=repo
        )
        top = run(["rev-parse", "--show-toplevel"], cwd=path)
    except GitError:
        return False
    return (
        Path(theirs).resolve() == Path(ours).resolve()
        and Path(top).resolve() == path.resolve()
    )


def is_dirty(path: Path) -> bool:
    """True when the worktree has modified, staged, or untracked files."""
    return bool(run(["status", "--porcelain"], cwd=path))


def branch_exists(repo: Path, name: str) -> bool:
    try:
        run(["rev-parse", "--verify", "--quiet", f"refs/heads/{name}"], cwd=repo)
    except GitError:
        return False
    return True


def ref_exists(repo: Path, ref: str) -> bool:
    """Whether `ref` resolves to a commit; remote refs included."""
    try:
        run(["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"], cwd=repo)
    except GitError:
        return False
    return True


def fetch(repo: Path) -> None:
    """Fetch origin explicitly. A repo without an origin is a quiet no-op.

    Explicit, because a bare `git fetch` follows the current branch's
    upstream, which may be a different remote than the `origin/<base>`
    the caller is about to resolve.
    """
    remotes = run(["remote"], cwd=repo).splitlines()
    if "origin" in remotes:
        run(["fetch", "--quiet", "origin"], cwd=repo)


def checkout_branch(path: Path, branch: str) -> None:
    run(["checkout", "--quiet", branch], cwd=path)


def checkout_new_branch(path: Path, branch: str, start: str) -> None:
    run(["checkout", "--quiet", "-b", branch, start], cwd=path)


def reset_to_detached(path: Path, ref: str) -> None:
    """Park the worktree on `ref` with a detached HEAD and a clean tree.

    `clean -fd` removes untracked files but not ignored ones, so installed
    dependencies such as `node_modules` survive. That is the warm state.
    """
    run(["checkout", "--quiet", "--force", "--detach", ref], cwd=path)
    run(["clean", "-fd", "--quiet"], cwd=path)


def branch_delete(repo: Path, name: str) -> bool:
    """Delete a fully merged branch. Returns False if git refused, which is
    what happens when the branch has commits not yet merged anywhere."""
    try:
        run(["branch", "--delete", name], cwd=repo)
    except GitError:
        return False
    return True


def worktree_add_branch(repo: Path, path: Path, branch: str, start: str) -> None:
    """Create a worktree with `branch` checked out, creating the branch at
    `start` if it does not exist yet. This is the cold path."""
    if branch_exists(repo, branch):
        run(["worktree", "add", str(path), branch], cwd=repo)
    else:
        run(["worktree", "add", "-b", branch, str(path), start], cwd=repo)


def worktree_move(repo: Path, path: Path, dest: Path) -> None:
    """Move a worktree to `dest`, ignored files included.

    Git updates its registration; everything inside the tree, installed
    dependencies included, travels with the directory.
    """
    run(["worktree", "move", str(path), str(dest)], cwd=repo)


def worktree_remove(repo: Path, path: Path) -> None:
    """Delete a worktree and its registration, ignored files included.

    Targeted removal works even when the directory is already gone. The
    repo-wide prune is only a fallback for a git too old to accept that,
    so it applies only to the missing-directory case; any other failure
    propagates rather than being papered over by a prune.
    """
    try:
        run(["worktree", "remove", "--force", str(path)], cwd=repo)
    except GitError:
        if path.exists():
            raise
        run(["worktree", "prune"], cwd=repo)
