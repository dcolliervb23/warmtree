"""`sweep` folds finished work — slots and hand-made worktrees — into the pool."""

import shutil
from pathlib import Path

import pytest

from tests.conftest import git as run_git
from warmtree import git
from warmtree.config import Config
from warmtree.pool import Pool


def merge_into_main(repo: Path, branch: str) -> None:
    run_git("checkout", "-q", "main", cwd=repo)
    run_git("merge", "-q", "--no-ff", branch, cwd=repo)


def hand_made(repo: Path, branch: str, merged: bool) -> Path:
    path = repo.parent / f"wt-{branch}"
    run_git("worktree", "add", "-q", "-b", branch, str(path), cwd=repo)
    (path / f"{branch}.txt").write_text("work\n")
    run_git("add", ".", cwd=path)
    run_git("commit", "-q", "-m", branch, cwd=path)
    if merged:
        merge_into_main(repo, branch)
    return path


def test_sweep_folds_merged_worktrees_and_keeps_unmerged(repo: Path):
    done = hand_made(repo, "done", merged=True)
    wip = hand_made(repo, "wip", merged=False)
    pool = Pool(repo, Config(size=0))

    results = pool.sweep()

    actions = {r.branch: r.action for r in results}
    assert actions["done"] == "folded"
    assert actions["wip"] == "kept"
    assert not done.exists()  # graduated into the pool
    assert wip.exists()
    assert not git.branch_exists(repo, "done")
    assert git.branch_exists(repo, "wip")
    ready = [s for s in pool.status() if s.state == "ready"]
    assert len(ready) == 1
    assert git.head_branch(Path(ready[0].path)) is None


def test_sweep_releases_merged_taken_slots(repo: Path):
    pool = Pool(repo, Config(size=1))
    pool.fill()
    slot, _ = pool.take("shipped")
    (Path(slot.path) / "f.txt").write_text("x\n")
    run_git("add", ".", cwd=Path(slot.path))
    run_git("commit", "-q", "-m", "shipped", cwd=Path(slot.path))
    merge_into_main(repo, "shipped")

    results = pool.sweep()

    assert any(r.action == "released" and r.branch == "shipped" for r in results)
    assert all(s.state == "ready" for s in pool.status())
    assert not git.branch_exists(repo, "shipped")


def test_sweep_keeps_unmerged_taken_slots(repo: Path):
    pool = Pool(repo, Config(size=1))
    pool.fill()
    slot, _ = pool.take("inflight")
    (Path(slot.path) / "f.txt").write_text("x\n")
    run_git("add", ".", cwd=Path(slot.path))
    run_git("commit", "-q", "-m", "inflight", cwd=Path(slot.path))

    results = pool.sweep()

    assert any(r.action == "kept" and r.branch == "inflight" for r in results)
    assert pool.taken("inflight").state == "taken"


def test_sweep_skips_dirty_merged_worktrees(repo: Path):
    path = hand_made(repo, "messy", merged=True)
    (path / "uncommitted.txt").write_text("wip\n")
    pool = Pool(repo, Config(size=0))

    results = pool.sweep()

    [item] = [r for r in results if r.branch == "messy"]
    assert item.action == "skipped"
    assert "uncommitted" in item.reason
    assert path.exists()


@pytest.mark.parametrize("squash", [False, True])
def test_sweep_counts_branches_merged_only_upstream(
    repo: Path, tmp_path: Path, squash: bool
):
    # Local main is stale; the branch merged on origin only. --fetch makes
    # origin/main the yardstick, so the fold still happens.
    clone = tmp_path / "clone"
    run_git("clone", "-q", str(repo), str(clone), cwd=tmp_path)
    run_git("config", "user.email", "t@t", cwd=clone)
    run_git("config", "user.name", "t", cwd=clone)
    wt = clone.parent / "wt-upstream"
    run_git("worktree", "add", "-q", "-b", "upstream-done", str(wt), cwd=clone)
    (wt / "u.txt").write_text("u\n")
    run_git("add", ".", cwd=wt)
    run_git("commit", "-q", "-m", "u", cwd=wt)
    # No -u: without a tracking upstream, git's own branch -d would refuse
    # even after the merge lands upstream. Sweep's certificate must finish
    # the deletion anyway.
    run_git("push", "-q", "origin", "upstream-done", cwd=wt)
    if squash:
        squash_into_main(repo, "upstream-done")
    else:
        merge_into_main(repo, "upstream-done")  # merged on origin; clone unaware

    pool = Pool(clone, Config(size=0))
    results = pool.sweep(fetch=True)

    [item] = [r for r in results if r.branch == "upstream-done"]
    assert item.action == "folded"
    assert not wt.exists()
    assert git.branch_exists(clone, "upstream-done") == squash


def test_sweep_never_releases_a_replaced_slot_directory(repo: Path):
    pool = Pool(repo, Config(size=1))
    pool.fill()
    slot, _ = pool.take("done")
    (Path(slot.path) / "f.txt").write_text("x\n")
    run_git("add", ".", cwd=Path(slot.path))
    run_git("commit", "-q", "-m", "done", cwd=Path(slot.path))
    merge_into_main(repo, "done")
    # Replace the slot directory with an unrelated repository.
    shutil.rmtree(slot.path)
    foreign = Path(slot.path)
    run_git("init", "-q", "-b", "innocent", str(foreign), cwd=repo)
    (foreign / "precious.txt").write_text("someone else's files\n")

    results = pool.sweep()

    [item] = [r for r in results if r.branch == "done"]
    assert item.action == "skipped"
    assert "no longer a working tree" in item.reason
    assert (foreign / "precious.txt").exists()  # untouched


def squash_into_main(repo: Path, branch: str) -> None:
    run_git("merge", "--squash", branch, cwd=repo)
    run_git("commit", "-q", "-m", "squash merge", cwd=repo)


def test_sweep_releases_multi_commit_squash_and_keeps_branch(repo: Path):
    pool = Pool(repo, Config(size=1))
    pool.fill()
    slot, _ = pool.take("squashed")
    path = Path(slot.path)
    for i in range(2):
        (path / "work.txt").write_text(f"work {i}\n")
        run_git("add", ".", cwd=path)
        run_git("commit", "-q", "-m", f"work {i}", cwd=path)
    tip = git.rev_parse(repo, "squashed")
    squash_into_main(repo, "squashed")
    # Later main edits do not hide the historical squash patch.
    (repo / "work.txt").write_text("later edit\n")
    run_git("add", ".", cwd=repo)
    run_git("commit", "-q", "-m", "later", cwd=repo)
    assert not git.is_ancestor(repo, "squashed", "main")

    [item] = pool.sweep()

    assert item.action == "released"
    assert item.reason == "squash merged; branch kept"
    assert pool.status()[0].state == "ready"
    assert git.rev_parse(repo, "squashed") == tip


def test_sweep_folds_squash_merged_handmade_worktree(repo: Path):
    path = hand_made(repo, "squashed", merged=False)
    squash_into_main(repo, "squashed")
    pool = Pool(repo, Config(size=0))
    [item] = pool.sweep()
    assert item.action == "folded"
    assert not path.exists()
    assert git.branch_exists(repo, "squashed")


def test_sweep_keeps_new_commits_after_squash(repo: Path):
    path = hand_made(repo, "squashed", merged=False)
    squash_into_main(repo, "squashed")
    (path / "extra.txt").write_text("not shipped\n")
    run_git("add", ".", cwd=path)
    run_git("commit", "-q", "-m", "extra", cwd=path)
    [item] = Pool(repo, Config(size=0)).sweep()
    assert item.action == "kept"
    assert path.exists()


def test_sweep_skips_dirty_squash_merged_slot(repo: Path):
    pool = Pool(repo, Config(size=1))
    pool.fill()
    slot, _ = pool.take("squashed")
    path = Path(slot.path)
    (path / "work.txt").write_text("work\n")
    run_git("add", ".", cwd=path)
    run_git("commit", "-q", "-m", "work", cwd=path)
    squash_into_main(repo, "squashed")
    (path / "untracked.txt").write_text("keep me\n")
    [item] = pool.sweep()
    assert item.action == "skipped"
    assert "uncommitted changes" in item.reason
    assert pool.taken("squashed").state == "taken"
    assert (path / "untracked.txt").read_text() == "keep me\n"


def test_squash_match_preserves_whitespace(repo: Path):
    path = hand_made(repo, "spaces", merged=False)
    (path / "README.md").write_text("    hello\n")
    run_git("add", ".", cwd=path)
    run_git("commit", "-q", "-m", "indent", cwd=path)
    squash_into_main(repo, "spaces")
    (path / "README.md").write_text("        hello\n")
    run_git("add", ".", cwd=path)
    run_git("commit", "-q", "-m", "more indent", cwd=path)
    assert not git.is_squash_merged(repo, "spaces", "main", {})


def test_empty_net_patch_is_not_squash_proof(repo: Path):
    path = hand_made(repo, "undone", merged=False)
    run_git("revert", "--no-edit", "HEAD", cwd=path)
    assert not git.is_squash_merged(repo, "undone", "main", {})


@pytest.mark.parametrize(
    "answer", ["y\n", "yes\n", "YES\n", "n\n", "\n", "", "maybe\n"]
)
def test_interactive_sweep_lists_files_and_requires_explicit_yes(
    repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    answer: str,
):
    import io

    from warmtree.cli import main

    (repo / "deleted.txt").write_text("tracked content\n")
    run_git("add", ".", cwd=repo)
    run_git("commit", "-q", "-m", "tracked file", cwd=repo)
    pool = Pool(repo, Config(size=1))
    pool.fill()
    slot, _ = pool.take("done")  # Already reachable from main.
    path = Path(slot.path)
    (repo / ".git/info/exclude").write_text("cache/\n")
    (path / "cache").mkdir()
    (path / "cache/dependency.txt").write_text("keep installed files\n")
    (path / "README.md").write_text("local edit\n")
    (path / "deleted.txt").unlink()
    (path / "staged.txt").write_text("staged work\n")
    run_git("add", "staged.txt", cwd=path)
    (path / "test-data").mkdir()
    (path / "test-data/temporary.json").write_text("{}\n")
    monkeypatch.chdir(repo)
    monkeypatch.setattr("sys.stdin", io.StringIO(answer))

    assert main(["sweep", "--interactive"]) == 0

    output = capsys.readouterr()
    assert str(path) in output.err
    assert "done" in output.err
    assert " M README.md" in output.err
    assert "A  staged.txt" in output.err
    assert " D deleted.txt" in output.err
    assert "?? test-data/temporary.json" in output.err
    assert "cache/dependency.txt" not in output.err
    assert "[y/N]" in output.err
    assert (path / "cache/dependency.txt").read_text() == "keep installed files\n"
    if answer.strip().lower() in {"y", "yes"}:
        assert pool.status()[0].state == "ready"
        assert (path / "README.md").read_text() == "hello\n"
        assert (path / "deleted.txt").read_text() == "tracked content\n"
        assert not (path / "staged.txt").exists()
        assert not (path / "test-data").exists()
    else:
        assert pool.taken("done").state == "taken"
        assert (path / "README.md").read_text() == "local edit\n"
        assert not (path / "deleted.txt").exists()
        assert (path / "staged.txt").exists()
        assert (path / "test-data/temporary.json").exists()


def test_interactive_sweep_does_not_override_live_lease(repo: Path):
    import os

    pool = Pool(repo, Config(size=1))
    pool.fill()
    slot, _ = pool.take("done")
    path = Path(slot.path)
    (path / "precious.txt").write_text("still working\n")
    pool._update_slot(slot.name, lease_pid=os.getpid())

    def never_prompt(*args):
        pytest.fail("must check the lease before asking to discard files")

    [item] = pool.sweep(confirm_discard=never_prompt)
    assert item.action == "skipped"
    assert "held by process" in item.reason
    assert (path / "precious.txt").exists()


def test_interactive_sweep_does_not_prompt_for_unmerged_or_clean_work(repo: Path):
    pool = Pool(repo, Config(size=1))
    pool.fill()
    pool.take("clean-done")
    path = hand_made(repo, "unfinished", merged=False)
    (path / "untracked.txt").write_text("keep\n")

    def never_prompt(*args):
        pytest.fail("only prompt for dirty merged work")

    results = {r.branch: r for r in pool.sweep(confirm_discard=never_prompt)}
    assert results["clean-done"].action == "released"
    assert results["unfinished"].action == "kept"
    assert (path / "untracked.txt").exists()


@pytest.mark.parametrize("approve", [False, True])
def test_interactive_sweep_dirty_handmade_worktree(repo: Path, approve: bool):
    path = hand_made(repo, "done", merged=True)
    (path / "untracked.txt").write_text("test data\n")
    pool = Pool(repo, Config(size=0))
    calls = []

    def confirm(path, branch, changes):
        calls.append((path, branch, changes))
        return approve

    [item] = pool.sweep(confirm_discard=confirm)
    assert len(calls) == 1
    assert calls[0][0] == path
    assert "untracked.txt" in calls[0][2]
    if approve:
        assert item.action == "folded"
        assert not path.exists()
        assert pool.status()[0].state == "ready"
    else:
        assert item.action == "skipped"
        assert (path / "untracked.txt").exists()
        assert pool.status() == []


def test_interactive_sweep_prompts_separately_for_each_slot(repo: Path):
    pool = Pool(repo, Config(size=2))
    pool.fill()
    first, _ = pool.take("first")
    second, _ = pool.take("second")
    for slot in (first, second):
        (Path(slot.path) / "temporary.txt").write_text("test data\n")
    answers = iter([False, True])
    calls = []

    def confirm(path, branch, changes):
        calls.append(branch)
        return next(answers)

    results = pool.sweep(confirm_discard=confirm)
    assert len(calls) == 2
    assert [r.action for r in results] == ["skipped", "released"]
    paths = {slot.branch: Path(slot.path) for slot in (first, second)}
    assert (paths[calls[0]] / "temporary.txt").exists()
    assert not (paths[calls[1]] / "temporary.txt").exists()


def test_sweep_old_unmerged_preserves_branch(repo: Path):
    pool = Pool(repo, Config(size=1))
    pool.fill()
    slot, _ = pool.take("old-work")
    path = Path(slot.path)
    (path / "work.txt").write_text("work")
    run_git("add", ".", cwd=path)
    run_git("commit", "-q", "-m", "work", cwd=path)
    pool._update_slot(slot.name, lease_since="2000-01-01T00:00:00+00:00")
    assert pool.sweep()[0].action == "kept"
    assert pool.sweep(older_than="7d")[0].action == "released"
    assert git.branch_exists(repo, "old-work")
