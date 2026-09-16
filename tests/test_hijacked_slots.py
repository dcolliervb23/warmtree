"""A ready slot with a branch checked out was hijacked; never claim it."""

from pathlib import Path

import pytest

from tests.conftest import git as run_git
from warmtree import git
from warmtree.config import Config
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


def test_take_skips_a_hijacked_slot_and_records_it(pool: Pool):
    slots = pool.status()
    oldest = slots[0]
    hijack(oldest.path, "stray")

    slot, cold = pool.take("feature")
    assert slot.name != oldest.name
    assert cold is False
    assert git.head_branch(Path(slot.path)) == "feature"
    assert pool.taken("stray").name == oldest.name


def test_take_falls_back_cold_when_every_ready_slot_is_hijacked(repo: Path):
    pool = Pool(repo, Config(size=1))
    [only] = pool.fill()
    hijack(only.path, "stray")

    slot, cold = pool.take("feature")
    assert cold is True
    assert slot.name != only.name
    assert pool.taken("stray").name == only.name
