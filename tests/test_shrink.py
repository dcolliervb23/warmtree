"""Surplus ready slots decay back to `size` after `shrink_after` (burst cleanup)."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from warmtree.config import Config, parse
from warmtree.pool import Pool


def age(pool: Pool, name: str, days: int) -> None:
    """Backdate a slot's idle clock."""
    then = (datetime.now(UTC) - timedelta(days=days)).isoformat(timespec="seconds")
    pool._update_slot(name, released=then, warmed=then, created=then)


def burst(repo: Path, size: int, total: int) -> Pool:
    """A pool that grew past `size` and settled back to all-ready."""
    pool = Pool(repo, Config(size=total))
    pool.fill()
    return Pool(repo, Config(size=size))


def test_idle_surplus_decays_to_size_oldest_first(repo: Path):
    pool = burst(repo, size=2, total=5)
    for i, days in [(1, 30), (2, 20), (3, 16), (4, 1), (5, 1)]:
        age(pool, f"slot-{i}", days)

    pool.refresh()

    names = sorted(s.name for s in pool.status())
    assert names == ["slot-4", "slot-5"]  # the young survive, floor holds


def test_young_surplus_is_kept(repo: Path):
    pool = burst(repo, size=2, total=5)
    for i in range(1, 6):
        age(pool, f"slot-{i}", 5)  # idle, but inside the 14d window

    pool.refresh()
    assert len(pool.status()) == 5


def test_the_floor_is_never_breached(repo: Path):
    pool = burst(repo, size=3, total=4)
    for i in range(1, 5):
        age(pool, f"slot-{i}", 100)

    pool.refresh()
    assert len(pool.status()) == 3  # one over, one trimmed, floor kept


def test_off_disables_shrinking(repo: Path):
    (repo / ".warmtree.toml").write_text('[pool]\nshrink_after = "off"\n')
    pool = burst(repo, size=2, total=5)
    for i in range(1, 6):
        age(pool, f"slot-{i}", 365)

    Pool(repo, parse((repo / ".warmtree.toml").read_text())).refresh()
    assert len(pool.status()) == 5


def test_taken_slots_never_count_or_decay(repo: Path):
    pool = burst(repo, size=1, total=3)
    pool.take("feature", from_ref=None)
    for i in range(1, 4):
        age(pool, f"slot-{i}", 90)
    # take stamped nothing on the taken slot's released field beyond aging
    taken_name = next(s.name for s in pool.status() if s.state == "taken")

    pool.refresh()

    states = {s.name: s.state for s in pool.status()}
    assert states[taken_name] == "taken"
    assert sum(1 for s in states.values() if s == "ready") == 1


def test_shrink_after_is_validated():
    assert parse('[pool]\nshrink_after = "30m"\n').shrink_after == "30m"
    with pytest.raises(Exception, match="refresh_every|shrink_after|30m|format|off"):
        parse('[pool]\nshrink_after = "fortnight"\n')
