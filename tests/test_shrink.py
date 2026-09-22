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


def test_stale_slots_earn_no_credit_toward_the_floor(repo: Path):
    # size 2 with three ready and one stale: the stale entry must not
    # inflate the surplus, or trimming leaves fewer ready than size.
    # _shrink is called directly; refresh would heal the stale slot first.
    pool = burst(repo, size=2, total=4)
    pool._update_slot("slot-4", state="stale")
    for i in range(1, 5):
        age(pool, f"slot-{i}", 60)

    pool._shrink()

    ready = [s for s in pool.status() if s.state == "ready"]
    assert len(ready) == 2


def test_a_rewarm_does_not_reset_the_idle_clock(repo: Path):
    # Never-taken slots idle from `created`. A fresh `warmed` (a lockfile
    # rewarm during refresh) must not shield them from decay.
    pool = burst(repo, size=1, total=3)
    then = (datetime.now(UTC) - timedelta(days=30)).isoformat(timespec="seconds")
    fresh = datetime.now(UTC).isoformat(timespec="seconds")
    for i in range(1, 4):
        pool._update_slot(f"slot-{i}", created=then, warmed=fresh, released=None)

    pool._shrink()

    assert len(pool.status()) == 1
