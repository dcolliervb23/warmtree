"""A ready slot with a branch checked out was hijacked; never claim it."""

import shutil
from pathlib import Path

import pytest

from tests.conftest import git as run_git
from warmtree import git
from warmtree.config import Config
from warmtree.git import GitError
from warmtree.pool import Pool


def hijack(slot_path: str, branch: str) -> None:
    """What a stray checkout in a parked slot looks like."""
    run_git("checkout", "-q", "-b", branch, cwd=Path(slot_path))


@pytest.fixture
def pool(repo: Path) -> Pool:
    pool = Pool(repo, Config(size=2))
    pool.fill()
    return pool


def test_doctor_reports_a_hijacked_ready_slot(pool: Pool):
    [first, _] = pool.status()
    hijack(first.path, "stray")

    [finding] = pool.doctor()
    assert finding.slot == first.name
    assert "stray" in finding.problem
    assert not finding.fixed


def test_doctor_fix_marks_the_hijacked_slot_taken(pool: Pool):
    [first, _] = pool.status()
    hijack(first.path, "stray")

    [finding] = pool.doctor(fix=True)
    assert finding.fixed
    slot = pool.taken("stray")
    assert slot.name == first.name

    released, _ = pool.release("stray", force=True)
    assert released.state == "ready"
    assert git.head_branch(Path(released.path)) is None


def test_doctor_never_touches_an_unregistered_directory(pool: Pool, repo: Path):
    # A slot directory replaced by an unrelated repository must not have
    # its branch recorded: release would later reset a stranger's repo.
    [first, _] = pool.status()
    shutil.rmtree(first.path)
    run_git("init", "-q", "-b", "innocent", str(first.path), cwd=repo)
    (Path(first.path) / "f").write_text("x\n")
    run_git("add", "f", cwd=Path(first.path))
    run_git(
        "-c",
        "user.email=x@x",
        "-c",
        "user.name=x",
        "commit",
        "-q",
        "-m",
        "i",
        cwd=Path(first.path),
    )

    findings = pool.doctor(fix=True)
    assert any("no longer a working tree" in f.problem for f in findings)
    assert not any("checked out" in f.problem for f in findings)
    assert pool.status()[0].state == "ready"  # untouched by --fix

    impostor = pool.status()[0]
    slot, _ = pool.take("feature")  # take must also refuse the impostor
    assert slot.name != impostor.name
    assert pool.status()[0].state == "ready"  # impostor entry untouched
    assert git.head_branch(Path(impostor.path)) == "innocent"


def test_take_records_hijacks_even_when_its_own_checkout_fails(pool: Pool):
    # The observation must survive a failed claim: mark first, then check out.
    [first, second] = pool.status()
    hijack(first.path, "stray")
    hijack(second.path, "wanted")  # taking "wanted" later must fail loudly

    with pytest.raises(GitError):
        pool.take("wanted")  # git refuses: wanted is checked out in slot-2

    assert pool.taken("stray").name == first.name
    assert pool.taken("wanted").name == second.name


def test_take_skips_a_hijacked_slot_and_records_it(pool: Pool):
    slots = pool.status()
    hottest = slots[-1]  # take claims the most recently warmed slot first
    hijack(hottest.path, "stray")

    slot, cold = pool.take("feature")
    assert slot.name != hottest.name
    assert cold is False
    assert git.head_branch(Path(slot.path)) == "feature"
    assert pool.taken("stray").name == hottest.name


def test_take_falls_back_cold_when_every_ready_slot_is_hijacked(repo: Path):
    pool = Pool(repo, Config(size=1))
    [only] = pool.fill()
    hijack(only.path, "stray")

    slot, cold = pool.take("feature")
    assert cold is True
    assert slot.name != only.name
    assert pool.taken("stray").name == only.name


def test_a_plain_directory_inside_the_repo_is_never_a_slot(repo: Path):
    # With the pool dir configured inside the main checkout, a slot
    # replaced by an ordinary directory shares our git dir and reports the
    # MAIN worktree's branch. Treating it as ours would let release reset
    # the main checkout. Doctor must report, never mark taken; take must
    # skip it.
    pool = Pool(repo, Config(size=1, dir="pool-inside"))
    [slot] = pool.fill()
    shutil.rmtree(slot.path)
    Path(slot.path).mkdir()
    (Path(slot.path) / "innocent.txt").write_text("not a worktree\n")

    findings = pool.doctor(fix=True)
    assert any("no longer a working tree" in f.problem for f in findings)
    assert not any("checked out" in f.problem for f in findings)
    assert pool.status()[0].state == "ready"

    taken, cold = pool.take("feature")
    assert taken.name != slot.name
    assert cold is True


def handwork(slot_path: str) -> Path:
    """Hijack a parked slot and leave edits only that folder holds."""
    path = Path(slot_path)
    hijack(slot_path, "stray")
    (path / "README.md").write_text("edited by hand\n")
    (path / "notes.txt").write_text("never committed\n")
    return path


def test_refresh_keeps_the_work_in_a_hijacked_slot(pool: Pool):
    [first, _] = pool.status()
    path = handwork(first.path)

    pool.refresh()

    assert (path / "README.md").read_text() == "edited by hand\n"
    assert (path / "notes.txt").exists()
    assert git.head_branch(path) == "stray"
    assert pool.taken("stray").name == first.name


def test_size_shrink_keeps_a_hijacked_slot(repo: Path):
    Pool(repo, Config(size=2)).fill()
    smaller = Pool(repo, Config(size=1))
    second = smaller.status()[1]  # trim removes the highest number first
    path = handwork(second.path)

    smaller.trim()

    assert (path / "notes.txt").exists()
    assert smaller.taken("stray").name == second.name


def test_idle_trim_keeps_a_hijacked_slot(repo: Path):
    Pool(repo, Config(size=3)).fill()
    pool = Pool(repo, Config(size=1))
    then = "2000-01-01T00:00:00+00:00"
    for slot in pool.status():
        pool._update_slot(slot.name, released=then, created=then, warmed=then)
    oldest = pool.status()[0]
    path = handwork(oldest.path)

    pool.refresh()

    assert (path / "notes.txt").exists()
    assert pool.taken("stray").name == oldest.name


def test_refresh_never_runs_git_in_a_plain_directory_inside_the_repo(repo: Path):
    # git run from a plain folder inside the main checkout acts on the main
    # checkout: a reset there detaches it and reverts the user's edits.
    pool = Pool(repo, Config(size=1, dir="pool-inside"))
    [slot] = pool.fill()
    shutil.rmtree(slot.path)
    Path(slot.path).mkdir()
    (Path(slot.path) / "innocent.txt").write_text("not a worktree\n")
    (repo / "README.md").write_text("tracked edit in the main checkout\n")

    pool.refresh()

    assert git.head_branch(repo) == "main"
    assert (repo / "README.md").read_text() == "tracked edit in the main checkout\n"
    assert (Path(slot.path) / "innocent.txt").exists()
    assert_left_alone(pool, slot.name)


def assert_left_alone(pool: Pool, name: str) -> None:
    """Kept by the ownership check, not recorded as somebody's branch."""
    [slot] = [s for s in pool.status() if s.name == name]
    assert slot.state == "ready"
    assert slot.branch is None


def replace_with_foreign_repo(repo: Path, slot_path: str) -> Path:
    path = Path(slot_path)
    shutil.rmtree(path)
    run_git("init", "-q", "-b", "innocent", str(path), cwd=repo)
    (path / "theirs.txt").write_text("someone else's repo\n")
    return path


def test_trim_leaves_a_foreign_repository_in_a_slot_folder_alone(repo: Path):
    # git itself refuses to remove it; trim must not crash on it either.
    Pool(repo, Config(size=2)).fill()
    smaller = Pool(repo, Config(size=1))
    second = smaller.status()[1]
    path = replace_with_foreign_repo(repo, second.path)

    smaller.trim()

    assert (path / "theirs.txt").exists()
    assert_left_alone(smaller, second.name)


def test_idle_trim_leaves_a_foreign_repository_in_a_slot_folder_alone(repo: Path):
    Pool(repo, Config(size=3)).fill()
    pool = Pool(repo, Config(size=1))
    then = "2000-01-01T00:00:00+00:00"
    for slot in pool.status():
        pool._update_slot(slot.name, released=then, created=then, warmed=then)
    oldest = pool.status()[0]
    path = replace_with_foreign_repo(repo, oldest.path)

    pool.refresh()

    assert (path / "theirs.txt").exists()
    assert_left_alone(pool, oldest.name)
