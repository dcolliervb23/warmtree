"""Warming during fill, and refresh against real repos."""

from pathlib import Path

import pytest

from warmtree import git
from warmtree.config import Config
from warmtree.pool import Pool
from warmtree.warm import WarmError

# Appends one line per run, so the line count is the number of times `run` ran.
MARK = "echo warmed>> warmed.txt"


def commit_file(repo: Path, name: str, content: str) -> None:
    (repo / name).write_text(content)
    git.run(["add", name], cwd=repo)
    git.run(["commit", "-q", "-m", f"update {name}"], cwd=repo)


def runs(slot_path: str) -> int:
    marker = Path(slot_path) / "warmed.txt"
    return len(marker.read_text().split()) if marker.exists() else 0


@pytest.fixture
def warm_repo(repo: Path) -> Path:
    commit_file(repo, "uv.lock", "v1\n")
    (repo / ".env").write_text("PORT=3000\n")
    return repo


WARM = Config(size=2, lockfiles=("uv.lock",), run=(MARK,), copy=(".env",))


# --- fill ---------------------------------------------------------------


def test_fill_runs_commands_and_copies_files_into_each_slot(warm_repo: Path):
    pool = Pool(warm_repo, WARM)
    created = pool.fill()

    for slot in created:
        assert slot.state == "ready"
        assert runs(slot.path) == 1
        env = (Path(slot.path) / ".env").read_text()
        assert env == f"PORT=3000\nWARMTREE_SLOT={slot.number}\n"
        assert set(slot.lockfiles) == {"uv.lock"}
        assert slot.warmed


def test_fill_logs_progress(warm_repo: Path):
    seen: list[str] = []
    Pool(warm_repo, Config(size=1, run=(MARK,), copy=(".env",)), log=seen.append).fill()
    assert any("copied .env" in line for line in seen)
    assert any(MARK in line for line in seen)


def test_failed_warm_leaves_slot_stale_and_raises(warm_repo: Path):
    pool = Pool(warm_repo, Config(size=1, run=("exit 2",)))

    with pytest.raises(WarmError):
        pool.fill()

    [slot] = pool.status()
    assert slot.state == "stale"
    assert slot.warmed is None


def test_take_never_hands_out_a_stale_slot(warm_repo: Path):
    pool = Pool(warm_repo, Config(size=1, run=("exit 2",)))
    with pytest.raises(WarmError):
        pool.fill()

    slot, cold = pool.take("feature/a")

    assert cold is True
    assert slot.name == "slot-2"


# --- refresh ------------------------------------------------------------


def test_refresh_moves_slots_to_new_base_tip(warm_repo: Path):
    pool = Pool(warm_repo, WARM)
    pool.fill()
    commit_file(warm_repo, "new.txt", "later\n")

    results = pool.refresh()

    assert [r.moved for r in results] == [True, True]
    tip = git.rev_parse(warm_repo, "main")
    for slot in pool.status():
        assert git.rev_parse(Path(slot.path), "HEAD") == tip
        assert git.head_branch(Path(slot.path)) is None
        assert slot.state == "ready"


def test_refresh_with_unchanged_lockfile_does_not_rerun_commands(warm_repo: Path):
    pool = Pool(warm_repo, WARM)
    pool.fill()
    commit_file(warm_repo, "new.txt", "later\n")

    results = pool.refresh()

    assert [r.rewarmed for r in results] == [False, False]
    # The marker is untracked, so the reset removed it and nothing rewrote it.
    assert all(runs(slot.path) == 0 for slot in pool.status())


def test_refresh_with_changed_lockfile_reruns_commands_in_every_slot(warm_repo: Path):
    pool = Pool(warm_repo, WARM)
    old_hashes = {slot.name: slot.lockfiles for slot in pool.fill()}
    commit_file(warm_repo, "uv.lock", "v2\n")

    results = pool.refresh()

    assert [r.rewarmed for r in results] == [True, True]
    for slot in pool.status():
        assert runs(slot.path) == 1
        assert slot.lockfiles != old_hashes[slot.name]


def test_refresh_without_changes_is_a_quiet_no_op(warm_repo: Path):
    pool = Pool(warm_repo, WARM)
    pool.fill()
    before = {slot.name: slot.warmed for slot in pool.status()}

    results = pool.refresh()

    assert [(r.moved, r.rewarmed) for r in results] == [(False, False)] * 2
    assert {slot.name: slot.warmed for slot in pool.status()} == before


def test_refresh_always_recopies_files(warm_repo: Path):
    pool = Pool(warm_repo, WARM)
    pool.fill()
    (warm_repo / ".env").write_text("PORT=4000\n")

    pool.refresh()

    for slot in pool.status():
        env = (Path(slot.path) / ".env").read_text()
        assert env == f"PORT=4000\nWARMTREE_SLOT={slot.number}\n"


def test_refresh_skips_taken_slots(warm_repo: Path):
    pool = Pool(warm_repo, WARM)
    pool.fill()
    taken, _ = pool.take("feature/a")
    commit_file(warm_repo, "new.txt", "later\n")

    results = pool.refresh()

    assert [r.slot.name for r in results] == ["slot-2"]
    assert git.head_branch(Path(taken.path)) == "feature/a"


def test_refresh_rewarms_a_stale_slot(warm_repo: Path):
    broken = Config(size=1, lockfiles=("uv.lock",), run=("exit 2",))
    with pytest.raises(WarmError):
        Pool(warm_repo, broken).fill()

    fixed = Config(size=1, lockfiles=("uv.lock",), run=(MARK,))
    results = Pool(warm_repo, fixed).refresh()

    assert [r.rewarmed for r in results] == [True]
    [slot] = Pool(warm_repo, fixed).status()
    assert slot.state == "ready"
    assert runs(slot.path) == 1


def test_refresh_failure_marks_slot_stale(warm_repo: Path):
    pool = Pool(warm_repo, WARM)
    pool.fill()
    commit_file(warm_repo, "uv.lock", "v2\n")

    with pytest.raises(WarmError):
        Pool(
            warm_repo, Config(size=2, lockfiles=("uv.lock",), run=("exit 2",))
        ).refresh()

    assert pool.status()[0].state == "stale"
