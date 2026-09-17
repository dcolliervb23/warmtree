"""Pool operations skip the repo's git hooks unless configured to run them."""

import time
from pathlib import Path

from warmtree.config import Config, parse
from warmtree.pool import Pool


def install_post_checkout(repo: Path, script: str) -> None:
    hook = repo / ".git" / "hooks" / "post-checkout"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text(script)
    hook.chmod(0o755)


def test_pool_operations_do_not_run_hooks(repo: Path):
    # The marker is relative: hooks run with the new worktree as cwd, and a
    # relative path survives the Windows sh that runs hook scripts.
    install_post_checkout(repo, "#!/bin/sh\ntouch .hook-ran\nexit 0\n")

    pool = Pool(repo, Config(size=1))
    [slot] = pool.fill()
    pool.take("feature")
    assert not (Path(slot.path) / ".hook-ran").exists()
    assert not (repo / ".hook-ran").exists()


def test_git_hooks_true_runs_them(repo: Path):
    # Constructing the Pool applies its config; no other setup needed.
    install_post_checkout(repo, "#!/bin/sh\ntouch .hook-ran\nexit 0\n")

    pool = Pool(repo, Config(size=1, git_hooks=True))
    [slot] = pool.fill()
    assert (Path(slot.path) / ".hook-ran").exists()

    # A later pool with default config restores the hookless policy.
    (Path(slot.path) / ".hook-ran").unlink()  # clear the fill's marker
    quiet = Pool(repo, Config(size=1))
    taken, _ = quiet.take("feature")
    assert not (Path(taken.path) / ".hook-ran").exists()


def test_a_lingering_hook_child_cannot_hang_the_pool(repo: Path):
    # The dogfooded hang: a hook backgrounds a process that inherits git's
    # pipes, and the captured call blocks long after git itself exited.
    # With hooks skipped the fill must return promptly.
    install_post_checkout(repo, "#!/bin/sh\nsleep 30 &\nexit 0\n")

    start = time.monotonic()
    Pool(repo, Config(size=1)).fill()
    assert time.monotonic() - start < 10


def test_config_accepts_git_hooks():
    assert parse("[pool]\ngit_hooks = true\n").git_hooks is True
    assert parse("").git_hooks is False
