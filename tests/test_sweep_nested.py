"""`sweep` removes worktrees nested in slots when nothing was done in them."""

from pathlib import Path

from tests.conftest import git as run_git
from tests.test_sweep import hand_made, idle
from tests.test_sweep_dry_run import committed_slot
from warmtree import git
from warmtree.config import Config
from warmtree.pool import DETACHED_IDLE_DAYS, Pool


def nested(
    repo: Path, slot_path: Path, name: str = "r", untouched: bool = True
) -> Path:
    """A review tool's worktree inside a slot, in a folder the repo ignores."""
    exclude = repo / ".git" / "info" / "exclude"
    if ".loop/" not in exclude.read_text():
        with exclude.open("a") as file:
            file.write(".loop/\n")
    path = slot_path / ".loop" / "reviews" / name
    run_git("worktree", "add", "-q", "--detach", str(path), "main", cwd=repo)
    if untouched:
        idle(path)
    return path.resolve()


def result_for(results, path: Path):
    [found] = [r for r in results if r.name == str(path)]
    return found


def ready_slot(repo: Path) -> tuple[Pool, Path]:
    pool = Pool(repo, Config(size=1))
    [slot] = pool.fill()
    return pool, Path(slot.path)


def test_sweep_removes_an_idle_untouched_tree_nested_in_a_ready_slot(repo: Path):
    pool, slot_path = ready_slot(repo)
    tree = nested(repo, slot_path)

    result = result_for(pool.sweep(), tree)

    assert (result.action, result.reason) == (
        "removed",
        "nested in slot-1, untouched since created",
    )
    assert not tree.exists()
    assert tree not in git.worktree_list(repo)


def test_sweep_removes_a_nested_tree_in_a_taken_unmerged_slot(repo: Path):
    pool = Pool(repo, Config(size=0))
    slot_path = committed_slot(pool, repo, "feat", merged=False)
    tree = nested(repo, slot_path)

    results = pool.sweep()

    assert result_for(results, tree).action == "removed"
    assert pool.status()[0].state == "taken"


def test_sweep_keeps_a_nested_tree_with_a_commit_made_inside(repo: Path):
    _, slot_path = ready_slot(repo)
    tree = nested(repo, slot_path, untouched=False)
    run_git("commit", "-q", "--allow-empty", "-m", "made inside", cwd=tree)
    idle(tree)

    result = result_for(Pool(repo, Config(size=1)).sweep(), tree)

    assert (result.action, result.reason) == (
        "kept",
        "nested in slot-1, but may hold work made inside it",
    )
    assert result.group == "needs-you"
    assert tree.exists()


def test_sweep_keeps_a_dirty_nested_tree(repo: Path):
    pool, slot_path = ready_slot(repo)
    tree = nested(repo, slot_path)
    (tree / "notes.txt").write_text("notes\n")

    result = result_for(pool.sweep(), tree)

    assert result.reason == "nested in slot-1, but has uncommitted changes"
    assert (tree / "notes.txt").exists()


def test_sweep_keeps_a_nested_tree_holding_an_env_file(repo: Path):
    pool, slot_path = ready_slot(repo)
    exclude = repo / ".git" / "info" / "exclude"
    exclude.write_text(exclude.read_text() + ".env\n")
    tree = nested(repo, slot_path)
    (tree / ".env").write_text("API_KEY=only-copy\n")

    result = result_for(pool.sweep(), tree)

    assert result.action == "kept"
    assert result.reason.endswith("holds ignored files sweep cannot rebuild: .env")
    assert result.group == "needs-you"
    assert (tree / ".env").exists()


def test_sweep_keeps_a_recent_nested_tree_as_in_flight(repo: Path):
    pool, slot_path = ready_slot(repo)
    tree = nested(repo, slot_path, untouched=False)

    result = result_for(pool.sweep(), tree)

    assert result.action == "kept"
    assert result.reason.endswith(f"within the last {DETACHED_IDLE_DAYS} days")
    assert result.group == "in-flight"


def test_sweep_does_not_release_a_merged_slot_holding_a_tree_that_stays(repo: Path):
    pool = Pool(repo, Config(size=0))
    slot_path = committed_slot(pool, repo, "feat", merged=True)
    tree = nested(repo, slot_path, untouched=False)

    [slot_result] = [r for r in pool.sweep() if r.name == "slot-1"]

    assert slot_result.action == "kept"
    assert slot_result.reason == (
        f"merged, but nested worktree {tree} was used within the last "
        f"{DETACHED_IDLE_DAYS} days"
    )
    assert slot_result.group == "in-flight"
    assert pool.status()[0].state == "taken"
    assert git.branch_exists(repo, "feat")


def test_sweep_releases_a_merged_slot_once_its_nested_trees_are_removed(repo: Path):
    pool = Pool(repo, Config(size=0))
    slot_path = committed_slot(pool, repo, "feat", merged=True)
    tree = nested(repo, slot_path)

    results = pool.sweep()

    assert result_for(results, tree).action == "removed"
    assert [r.action for r in results if r.name == "slot-1"] == ["released"]
    assert pool.status()[0].state == "ready"


def test_dry_run_predicts_nested_removal_and_changes_nothing(repo: Path):
    pool = Pool(repo, Config(size=0))
    slot_path = committed_slot(pool, repo, "feat", merged=True)
    removable = nested(repo, slot_path, name="old")
    nested(repo, slot_path, name="busy", untouched=False)

    planned = pool.sweep(dry_run=True)

    assert removable.exists()
    assert pool.status()[0].state == "taken"
    done = pool.sweep()
    assert [(r.name, r.action, r.reason) for r in planned] == [
        (r.name, r.action, r.reason) for r in done
    ]


def test_a_nested_tree_in_an_adopted_slot_outside_the_pool_is_handled_as_nested(
    repo: Path,
):
    pool = Pool(repo, Config(size=0))
    outside = hand_made(repo, "wip", merged=False)
    slot = pool.adopt(outside)
    tree = nested(repo, outside)

    results = pool.sweep(dry_run=True)

    mine = [r for r in results if r.name == str(tree)]
    assert len(mine) == 1
    assert mine[0].reason.startswith(f"nested in {slot.name}")


def test_sweep_reports_a_failed_nested_removal_and_carries_on(repo: Path, monkeypatch):
    pool = Pool(repo, Config(size=0))
    slot_path = committed_slot(pool, repo, "feat", merged=True)
    tree = nested(repo, slot_path)

    def refuse(repo_root: Path, path: Path) -> None:
        raise git.GitError("refused")

    monkeypatch.setattr(git, "worktree_remove", refuse)
    results = pool.sweep()

    assert (result_for(results, tree).action, result_for(results, tree).reason) == (
        "skipped",
        "refused",
    )
    [slot_result] = [r for r in results if r.name == "slot-1"]
    assert slot_result.action == "kept"
    assert pool.status()[0].state == "taken"


def test_sweep_keeps_a_nested_tree_when_reflogs_are_off(repo: Path):
    pool, slot_path = ready_slot(repo)
    run_git("config", "core.logAllRefUpdates", "false", cwd=repo)
    tree = nested(repo, slot_path)

    result = result_for(pool.sweep(), tree)

    assert result.reason == "nested in slot-1, but may hold work made inside it"
    assert tree.exists()


def test_cli_sweep_explains_a_nested_tree_it_kept(repo: Path, capsys, monkeypatch):
    from warmtree.cli import main

    _, slot_path = ready_slot(repo)
    tree = nested(repo, slot_path)
    (tree / "notes.txt").write_text("notes\n")
    monkeypatch.chdir(repo)

    assert main(["sweep"]) == 0
    err = capsys.readouterr().err
    assert f"kept {tree}: nested in slot-1, but has uncommitted changes" in err
