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


def run(args: list[str], cwd: Path, stdin: str | None = None) -> str:
    """Run `git <args>` in cwd and return stripped stdout."""
    prefix = [] if _hooks_allowed else ["-c", f"core.hooksPath={_no_hooks_path()}"]
    result = subprocess.run(
        ["git", *prefix, *args],
        cwd=cwd,
        input=stdin,
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


def is_ancestor(repo: Path, ref: str, of: str) -> bool:
    """Whether `ref` is reachable from `of` — merged, in branch terms."""
    try:
        run(["merge-base", "--is-ancestor", ref, of], cwd=repo)
    except GitError:
        return False
    return True


_SHOW_ALL = ("--untracked-files=normal", "--ignore-submodules=none")


def is_dirty(path: Path) -> bool:
    """True when the worktree has modified, staged, or untracked files.

    The flags override config that would hide untracked files or submodule
    changes; --no-optional-locks keeps status from rewriting the index
    under a git command the user is running.
    """
    return bool(
        run(["--no-optional-locks", "status", "--porcelain", *_SHOW_ALL], cwd=path)
    )


def git_dir(path: Path) -> Path:
    """The worktree's private git directory (.git/worktrees/<name>)."""
    return Path(run(["rev-parse", "--absolute-git-dir"], cwd=path))


# Files git leaves in a worktree's git directory while an operation waits
# for the user, mapped to the operation's name.
OPERATION_MARKERS = (
    ("BISECT_LOG", "bisect"),
    ("rebase-merge", "rebase"),
    ("rebase-apply", "rebase"),
    ("MERGE_HEAD", "merge"),
    ("CHERRY_PICK_HEAD", "cherry-pick"),
    ("REVERT_HEAD", "revert"),
    ("sequencer", "cherry-pick"),
)


def operation_in_progress(path: Path) -> str | None:
    """Name of the git operation paused in this worktree, if any."""
    directory = git_dir(path)
    for marker, operation in OPERATION_MARKERS:
        if (directory / marker).exists():
            return operation
    return None


def head_has_unsaved_commits(path: Path, parked: str | None) -> bool:
    """True when HEAD has commits since `parked` that no ref holds.

    That is what committing on a detached HEAD leaves; moving HEAD away
    would bury those commits in this worktree's reflog. Only commits past
    the parked one count: amending or rebasing base can leave the parked
    commit itself on no branch, and that is nobody's work. Without a usable
    park (state from an older version, or a parked commit gc pruned) every
    commit no ref holds counts: at worst the slot is kept when it holds
    nothing, never reset when it holds work. The reflog is not consulted:
    a slot's keeps every branch it ever held, rebased-away commits
    included, and none of that is at risk.
    """
    saved = ["--branches", "--tags", "--remotes"]
    if parked is not None and ref_exists(path, parked):
        saved.append(parked)
    return bool(run(["rev-list", "-n", "1", "HEAD", "--not", *saved], cwd=path))


def has_stranded_commits(repo: Path, path: Path) -> bool:
    """True when this worktree holds commits no surviving ref reaches.

    Removing a worktree deletes its HEAD history and its own refs
    (refs/worktree/*); commits only they reach would be lost. The refs
    that survive are read from the main worktree.
    """
    history = run(["reflog", "show", "--format=%H", "HEAD"], cwd=path)
    own_refs = run(
        ["for-each-ref", "--format=%(objectname)", "refs/worktree/"], cwd=path
    )
    history = "\n".join(line for line in (history, own_refs) if line)
    if not history:
        return False
    refs = run(["for-each-ref", "--format=^%(objectname)"], cwd=repo)
    stranded = run(
        ["rev-list", "--stdin", "--ignore-missing"],
        cwd=repo,
        stdin=f"{history}\n{refs}\n",
    )
    return bool(stranded)


def is_locked(path: Path) -> bool:
    """True when `git worktree lock` protects this worktree."""
    return (git_dir(path) / "locked").exists()


def gitlinks(path: Path) -> list[str]:
    """Paths of the submodules the worktree's index records."""
    output = run(["ls-files", "--stage", "-z"], cwd=path)
    return [
        entry.split("\t", 1)[1]
        for entry in output.split("\0")
        if entry.startswith("160000 ")
    ]


def head_moved_at(path: Path) -> float:
    """When HEAD last moved in this worktree, as a Unix timestamp."""
    directory = git_dir(path)
    stamps = [
        (directory / name).stat().st_mtime
        for name in ("HEAD", "logs/HEAD")
        if (directory / name).exists()
    ]
    return max(stamps)


def ignored_paths(path: Path) -> list[str]:
    """Ignored files and folders in the worktree; folders end in /."""
    output = run(
        [
            "--no-optional-locks",
            "status",
            "--porcelain",
            "-z",
            "--ignored=matching",
            *_SHOW_ALL,
        ],
        cwd=path,
    )
    return [entry[3:] for entry in output.split("\0") if entry.startswith("!! ")]


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


def checkout_tracking_branch(path: Path, branch: str) -> None:
    """Create `branch` from origin/`branch` and track it, like checkout's DWIM."""
    run(["checkout", "--quiet", "--track", f"origin/{branch}"], cwd=path)


def worktree_add_tracking(repo: Path, path: Path, branch: str) -> None:
    """Cold-create a worktree on a new `branch` tracking origin/`branch`."""
    run(
        ["worktree", "add", "--track", "-b", branch, str(path), f"origin/{branch}"],
        cwd=repo,
    )


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


def branch_delete(repo: Path, name: str, force: bool = False) -> bool:
    """Delete a fully merged branch. Returns False if git refused, which is
    what happens when the branch has commits not yet merged anywhere.
    force skips git's own merged check — for callers that already hold a
    stronger certificate, like sweep's is-ancestor against origin/base."""
    flag = "--delete" if not force else "-D"
    try:
        run(["branch", flag, name], cwd=repo)
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
