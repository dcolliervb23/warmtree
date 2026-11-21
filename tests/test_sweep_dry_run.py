"""`sweep --dry-run` reports what sweep would do, and why, without doing it."""

import shutil
import subprocess
import sys
from pathlib import Path

from tests.conftest import git as run_git
from tests.test_sweep import detached_at, hand_made, merge_into_main, older_commit
from warmtree import git
from warmtree.config import Config
from warmtree.pool import Pool


def committed_slot(pool: Pool, repo: Path, branch: str, merged: bool) -> Path:
    slot, _ = pool.take(branch)
    path = Path(slot.path)
    (path / f"{branch}.txt").write_text("x\n")
    run_git("add", ".", cwd=path)
    run_git("commit", "-q", "-m", branch, cwd=path)
    if merged:
        merge_into_main(repo, branch)
    return path


def mixed_pile(repo: Path) -> dict[str, Path]:
    """One worktree for every way sweep can decide."""
    pool = Pool(repo, Config(size=0))
    paths = {
        "shipped": committed_slot(pool, repo, "shipped", merged=True),
        "slot-messy": committed_slot(pool, repo, "slot-messy", merged=True),
        "done": hand_made(repo, "done", merged=True),
        "wip": hand_made(repo, "wip", merged=False),
        "messy": hand_made(repo, "messy", merged=True),
    }
    (paths["slot-messy"] / "uncommitted.txt").write_text("wip\n")
    (paths["messy"] / "uncommitted.txt").write_text("wip\n")
    older = older_commit(repo)
    paths["stale"] = detached_at(repo, older, name="wt-stale")
    paths["dirty-detached"] = detached_at(repo, older, name="wt-dirty")
    (paths["dirty-detached"] / "notes.txt").write_text("notes\n")
    paths["recent"] = detached_at(repo, older, name="wt-recent", untouched=False)
    return paths


def decisions(results) -> list[tuple]:
    return [(r.name, r.branch, r.action, r.reason) for r in results]


def test_dry_run_changes_nothing(repo: Path):
    paths = mixed_pile(repo)
    pool = Pool(repo, Config(size=0))
    before_worktrees = git.worktree_list(repo)
    before_status = [(s.name, s.state, s.branch) for s in pool.status()]

    pool.sweep(detached=True, dry_run=True)

    assert git.worktree_list(repo) == before_worktrees
    assert [(s.name, s.state, s.branch) for s in pool.status()] == before_status
    assert all(path.exists() for path in paths.values())
    for branch in ("shipped", "done"):
        assert git.branch_exists(repo, branch)


def test_dry_run_predicts_exactly_what_sweep_does(repo: Path):
    mixed_pile(repo)
    pool = Pool(repo, Config(size=0))

    predicted = pool.sweep(detached=True, dry_run=True)
    actual = pool.sweep(detached=True)

    assert decisions(predicted) == decisions(actual)


def test_dry_run_without_detached_matches_plain_sweep(repo: Path):
    mixed_pile(repo)
    pool = Pool(repo, Config(size=0))

    assert decisions(pool.sweep(dry_run=True)) == decisions(pool.sweep())


def test_every_decision_lands_in_a_group(repo: Path):
    paths = mixed_pile(repo)
    pool = Pool(repo, Config(size=0))

    def groups(results) -> dict[str, str]:
        by_path = {str(p): key for key, p in paths.items()}
        named = {}
        for r in results:
            key = r.branch if r.branch is not None else by_path[r.name]
            named[key] = r.group
        return named

    assert groups(pool.sweep(detached=True, dry_run=True)) == {
        "shipped": "clean",
        "slot-messy": "needs-you",
        "done": "clean",
        "wip": "in-flight",
        "messy": "needs-you",
        "stale": "clean",
        "dirty-detached": "needs-you",
        "recent": "in-flight",
    }
    assert groups(pool.sweep(dry_run=True))["stale"] == "with-detached"


def test_cli_dry_run_lists_groups_and_touches_nothing(repo: Path, capsys, monkeypatch):
    from warmtree.cli import main

    paths = mixed_pile(repo)
    monkeypatch.chdir(repo)

    assert main(["sweep", "--dry-run", "--detached"]) == 0

    out = capsys.readouterr().out
    clean, rest = out.split("needs you:")
    needs_you, in_flight = rest.split("still in flight:")
    assert clean.startswith("would clean up:")
    assert "release " in clean and "(shipped)" in clean
    assert "fold " in clean and "(done)" in clean
    assert "remove " in clean and str(paths["stale"]) in clean
    assert "(messy): " in needs_you and "uncommitted changes" in needs_you
    assert str(paths["dirty-detached"]) in needs_you
    assert "(wip): not merged" in in_flight
    assert str(paths["recent"]) in in_flight
    assert "squash-merged" in in_flight
    assert out.rstrip().endswith("dry run: nothing changed")
    assert all(path.exists() for path in paths.values())


def test_cli_dry_run_without_detached_shows_removable_group(
    repo: Path, capsys, monkeypatch
):
    from warmtree.cli import main

    stale = detached_at(repo, older_commit(repo), name="wt-stale")
    monkeypatch.chdir(repo)

    assert main(["sweep", "--dry-run"]) == 0

    out = capsys.readouterr().out
    assert "removable with --detached:" in out
    assert str(stale) in out
    assert "squash-merged" not in out  # no unmerged branches to caution about


def test_cli_dry_run_on_nothing(repo: Path, capsys, monkeypatch):
    from warmtree.cli import main

    monkeypatch.chdir(repo)

    assert main(["sweep", "--dry-run"]) == 0
    assert capsys.readouterr().out.strip() == (
        "nothing to sweep\ndry run: nothing changed"
    )


def test_cli_sweep_points_at_dry_run_when_it_leaves_things(
    repo: Path, capsys, monkeypatch
):
    from warmtree.cli import main

    messy = hand_made(repo, "messy", merged=True)
    (messy / "uncommitted.txt").write_text("wip\n")
    monkeypatch.chdir(repo)

    assert main(["sweep"]) == 0
    assert "warmtree sweep --dry-run" in capsys.readouterr().err


def test_cli_sweep_stays_quiet_when_only_work_in_flight_remains(
    repo: Path, capsys, monkeypatch
):
    from warmtree.cli import main

    hand_made(repo, "wip", merged=False)
    monkeypatch.chdir(repo)

    assert main(["sweep"]) == 0
    assert "--dry-run" not in capsys.readouterr().err


def test_dry_run_and_sweep_survive_a_slot_folder_deleted_by_hand(repo: Path):
    pool = Pool(repo, Config(size=0))
    gone = committed_slot(pool, repo, "gone", merged=True)
    shutil.rmtree(gone)

    predicted = pool.sweep(dry_run=True)
    actual = pool.sweep()

    assert decisions(predicted) == decisions(actual)
    [item] = predicted
    assert item.action == "skipped"
    assert "run `warmtree doctor`" in item.reason
    assert item.group == "needs-you"


def test_sweep_never_folds_the_base_branch(repo: Path, tmp_path: Path):
    # The main checkout sits on a feature branch; main lives in its own
    # hand-made worktree. Sweep must not reset it or delete local main.
    run_git("checkout", "-q", "-b", "feat", cwd=repo)
    wt_main = repo.parent / "wt-main"
    run_git("worktree", "add", "-q", str(wt_main), "main", cwd=repo)
    pool = Pool(repo, Config(size=0, base="main"))

    predicted = pool.sweep(dry_run=True)
    actual = pool.sweep()

    assert decisions(predicted) == decisions(actual)
    [item] = actual
    assert (item.branch, item.action, item.reason) == (
        "main",
        "kept",
        "is the base branch",
    )
    assert git.branch_exists(repo, "main")
    assert git.head_branch(wt_main) == "main"


def test_dry_run_creates_no_pool_directory(repo: Path):
    pool = Pool(repo, Config(size=0))
    hand_made(repo, "wip", merged=False)

    pool.sweep(dry_run=True)

    assert not pool.dir.exists()


def test_dry_run_predicts_a_slot_held_by_another_live_session(repo: Path):
    pool = Pool(repo, Config(size=0))
    committed_slot(pool, repo, "held", merged=True)
    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        with pool.locked():
            slots = pool.load_state()
            slots[0].lease_pid = other.pid
            pool.save_state(slots)

        predicted = pool.sweep(dry_run=True)
        actual = pool.sweep()
    finally:
        other.kill()
        other.wait()

    assert decisions(predicted) == decisions(actual)
    [item] = predicted
    assert item.action == "skipped"
    assert f"held by process {other.pid}" in item.reason
    assert item.group == "in-flight"
