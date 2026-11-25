"""`sweep` frees slots whose branch landed on base through a squash merge."""

from pathlib import Path

from tests.conftest import git as run_git
from tests.test_sweep import hand_made
from tests.test_sweep_dry_run import committed_slot
from warmtree import git
from warmtree.config import Config
from warmtree.pool import Pool


def squash_into_main(repo: Path, branch: str) -> str:
    run_git("checkout", "-q", "main", cwd=repo)
    run_git("merge", "-q", "--squash", branch, cwd=repo)
    run_git("commit", "-q", "-m", f"{branch} (squashed)", cwd=repo)
    return run_git("rev-parse", "--short", "HEAD", cwd=repo)


def squashed_slot(repo: Path) -> tuple[Pool, Path, str]:
    pool = Pool(repo, Config(size=0))
    path = committed_slot(pool, repo, "feat", merged=False)
    (path / "more.txt").write_text("more\n")
    run_git("add", ".", cwd=path)
    run_git("commit", "-q", "-m", "more", cwd=path)
    return pool, path, squash_into_main(repo, "feat")


def test_sweep_releases_a_squash_merged_slot_and_keeps_its_branch(repo: Path):
    pool, _, sha = squashed_slot(repo)

    [result] = pool.sweep()

    assert (result.action, result.reason) == (
        "released",
        f"squash-merged as {sha}; branch kept",
    )
    [slot] = pool.status()
    assert slot.state == "ready"
    assert git.branch_exists(repo, "feat")


def test_sweep_keeps_a_slot_with_work_after_the_squash(repo: Path):
    pool, path, _ = squashed_slot(repo)
    (path / "late.txt").write_text("late\n")
    run_git("add", ".", cwd=path)
    run_git("commit", "-q", "-m", "after the squash", cwd=path)

    [result] = pool.sweep()

    assert (result.action, result.reason) == ("kept", "not merged")
    assert pool.status()[0].state == "taken"


def test_dry_run_predicts_the_squash_release(repo: Path):
    pool, _, _ = squashed_slot(repo)

    planned = pool.sweep(dry_run=True)
    assert pool.status()[0].state == "taken"
    done = pool.sweep()

    assert [(r.name, r.action, r.reason) for r in planned] == [
        (r.name, r.action, r.reason) for r in done
    ]


def test_sweep_skips_a_dirty_squash_merged_slot(repo: Path):
    pool, path, _ = squashed_slot(repo)
    (path / "uncommitted.txt").write_text("wip\n")

    [result] = pool.sweep()

    assert result.action == "skipped"
    assert pool.status()[0].state == "taken"
    assert (path / "uncommitted.txt").exists()


def test_sweep_leaves_a_hand_made_squash_merged_worktree(repo: Path):
    path = hand_made(repo, "done", merged=False)
    squash_into_main(repo, "done")

    [result] = Pool(repo, Config(size=0)).sweep()

    assert (result.name, result.action, result.reason) == (
        str(path.resolve()),
        "kept",
        "not merged",
    )


def test_squash_release_groups_as_clean(repo: Path):
    pool, _, _ = squashed_slot(repo)

    [result] = pool.sweep(dry_run=True)

    assert result.group == "clean"


def test_cli_sweep_says_a_squash_merged_branch_was_kept(
    repo: Path, capsys, monkeypatch
):
    from warmtree.cli import main

    _, _, sha = squashed_slot(repo)
    monkeypatch.chdir(repo)

    assert main(["sweep"]) == 0
    out = capsys.readouterr().out
    assert f"released: slot-1 (feat): squash-merged as {sha}; branch kept" in out


def test_cli_dry_run_names_the_squash_commit(repo: Path, capsys, monkeypatch):
    from warmtree.cli import main

    _, _, sha = squashed_slot(repo)
    monkeypatch.chdir(repo)

    assert main(["sweep", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert f"release slot-1 (feat): squash-merged as {sha}; branch kept" in out


def test_a_slot_whose_branch_was_deleted_does_not_stop_sweep(repo: Path):
    pool = Pool(repo, Config(size=0))
    path = committed_slot(pool, repo, "gone", merged=False)
    run_git("checkout", "-q", "--detach", cwd=path)
    run_git("branch", "-q", "-D", "gone", cwd=repo)

    [result] = pool.sweep()

    assert (result.action, result.reason) == ("kept", "not merged")
