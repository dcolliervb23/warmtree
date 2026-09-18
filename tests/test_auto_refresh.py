"""Ordinary commands keep the pool current by spawning overdue refreshes."""

import os
import time
from pathlib import Path

import pytest

from tests.conftest import git as run_git
from warmtree import config as config_module
from warmtree import git
from warmtree.cli import main
from warmtree.config import Config, ConfigError, interval_seconds, parse
from warmtree.pool import REFRESH_STAMP, Pool


@pytest.fixture
def auto_refresh_on(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("WARMTREE_AUTO_REFRESH")


def test_interval_seconds_understands_the_formats():
    assert interval_seconds("30m") == 1800
    assert interval_seconds("24h") == 86400
    assert interval_seconds("7d") == 604800
    assert interval_seconds("off") == 0
    assert interval_seconds("0") == 0


def test_bad_intervals_are_config_errors():
    with pytest.raises(ConfigError, match="refresh_every"):
        interval_seconds("soon")
    with pytest.raises(ConfigError, match="refresh_every"):
        parse('[pool]\nrefresh_every = "1 day"\n')
    assert parse('[pool]\nrefresh_every = "12h"\n').refresh_every == "12h"
    assert parse("").refresh_every == "24h"


def test_refresh_touches_the_stamp(repo: Path):
    pool = Pool(repo, Config(size=1))
    pool.fill()
    pool.refresh()
    assert (pool.dir / REFRESH_STAMP).exists()


def test_overdue_status_spawns_a_refresh_that_really_refreshes(
    repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    auto_refresh_on,
):
    # The completion proof is observable work, not the stamp: the spawn
    # itself touches the stamp (the debounce), so only the slot landing on
    # an advanced origin tip shows the detached refresh actually ran.
    clone = tmp_path / "clone"
    run_git("clone", "-q", str(repo), str(clone), cwd=tmp_path)
    monkeypatch.chdir(clone)
    main(["fill"])
    (repo / "news.txt").write_text("upstream moved\n")
    run_git("add", "news.txt", cwd=repo)
    run_git("commit", "-q", "-m", "upstream", cwd=repo)
    upstream = run_git("rev-parse", "HEAD", cwd=repo)

    pool = Pool(clone, Config())
    stamp = pool.dir / REFRESH_STAMP
    two_days_ago = time.time() - 2 * 86400
    stamp.touch()
    os.utime(stamp, (two_days_ago, two_days_ago))
    capsys.readouterr()

    assert main(["status"]) == 0
    assert "refreshing in the background" in capsys.readouterr().err
    assert stamp.stat().st_mtime > two_days_ago  # the debounce claim

    slot = pool.status()[0]
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if git.rev_parse(Path(slot.path), "HEAD") == upstream:
            break
        time.sleep(0.2)
    else:
        pytest.fail("the background refresh never moved the slot")


def test_fresh_stamp_spawns_nothing(
    repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    auto_refresh_on,
):
    monkeypatch.chdir(repo)
    main(["fill"])
    Pool(repo, Config()).refresh()  # fresh stamp
    capsys.readouterr()

    assert main(["status"]) == 0
    assert "refreshing" not in capsys.readouterr().err


def test_refresh_every_off_disables_the_self_refresh(
    repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    auto_refresh_on,
):
    (repo / config_module.CONFIG_NAME).write_text('[pool]\nrefresh_every = "off"\n')
    monkeypatch.chdir(repo)
    main(["fill"])
    capsys.readouterr()

    assert main(["status"]) == 0
    assert "refreshing" not in capsys.readouterr().err
