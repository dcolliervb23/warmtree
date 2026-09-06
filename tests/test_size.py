"""Pool size management: config edits, trim, and the size/which commands."""

from pathlib import Path

import pytest

from warmtree import config, git
from warmtree.cli import main
from warmtree.config import Config, ConfigError
from warmtree.pool import Pool

# --- config.write_size --------------------------------------------------


def test_write_size_creates_config_when_missing(tmp_path: Path):
    config.write_size(tmp_path, 4)
    cfg = config.load(tmp_path)
    assert cfg.size == 4
    assert cfg.run == ()


def test_write_size_edits_the_line_in_place_and_keeps_comments(tmp_path: Path):
    (tmp_path / config.CONFIG_NAME).write_text(
        '# my pool\n[pool]\nsize = 2   # keep me\nbase = "main"\n'
        '\n[warm]\nrun = ["x"]\n'
    )
    config.write_size(tmp_path, 5)
    text = (tmp_path / config.CONFIG_NAME).read_text()
    assert "size = 5   # keep me" in text
    assert "# my pool" in text
    cfg = config.load(tmp_path)
    assert (cfg.size, cfg.base, cfg.run) == (5, "main", ("x",))


def test_write_size_adds_key_to_existing_pool_table(tmp_path: Path):
    (tmp_path / config.CONFIG_NAME).write_text('[pool]\nbase = "main"\n')
    config.write_size(tmp_path, 3)
    cfg = config.load(tmp_path)
    assert (cfg.size, cfg.base) == (3, "main")


def test_write_size_adds_pool_table_when_absent(tmp_path: Path):
    (tmp_path / config.CONFIG_NAME).write_text('[warm]\nrun = ["x"]\n')
    config.write_size(tmp_path, 3)
    cfg = config.load(tmp_path)
    assert (cfg.size, cfg.run) == (3, ("x",))


def test_write_size_rejects_negative(tmp_path: Path):
    with pytest.raises(ConfigError):
        config.write_size(tmp_path, -1)
    assert not (tmp_path / config.CONFIG_NAME).exists()


# --- Pool.trim ------------------------------------------------------------


def test_trim_removes_surplus_ready_slots_highest_number_first(repo: Path):
    Pool(repo, Config(size=3)).fill()

    removed = Pool(repo, Config(size=1)).trim()

    assert [slot.name for slot in removed] == ["slot-3", "slot-2"]
    remaining = Pool(repo, Config(size=1)).status()
    assert [slot.name for slot in remaining] == ["slot-1"]
    assert not Path(removed[0].path).exists()
    assert len(git.worktree_list(repo)) == 2


def test_trim_never_touches_taken_slots(repo: Path):
    pool = Pool(repo, Config(size=2))
    pool.fill()
    pool.take("feature/a")

    removed = Pool(repo, Config(size=0)).trim()

    assert [slot.name for slot in removed] == ["slot-2"]
    [taken] = Pool(repo, Config(size=0)).status()
    assert taken.state == "taken"


def test_trim_is_a_no_op_when_pool_is_not_oversized(repo: Path):
    pool = Pool(repo, Config(size=2))
    pool.fill()
    assert pool.trim() == []
    assert len(pool.status()) == 2


# --- warmtree size ----------------------------------------------------------


def test_size_shows_configured_size_and_counts(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.chdir(repo)
    main(["fill"])
    main(["take", "feature/a", "--no-refill"])
    capsys.readouterr()

    assert main(["size"]) == 0

    out = capsys.readouterr().out
    assert "size: 2" in out
    assert "ready 1" in out
    assert "taken 1" in out


def test_size_grows_the_pool_and_updates_config(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.chdir(repo)
    main(["fill"])

    assert main(["size", "4"]) == 0

    assert config.load(repo).size == 4
    states = [slot.state for slot in Pool(repo, config.load(repo)).status()]
    assert states == ["ready"] * 4


def test_size_shrinks_the_pool(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.chdir(repo)
    main(["size", "3"])
    capsys.readouterr()

    assert main(["size", "1"]) == 0

    out = capsys.readouterr().out
    assert "removed slot-3" in out
    assert "removed slot-2" in out
    assert config.load(repo).size == 1
    assert len(Pool(repo, config.load(repo)).status()) == 1


def test_size_shrink_keeps_taken_slots(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.chdir(repo)
    main(["fill"])
    main(["take", "feature/a", "--no-refill"])

    assert main(["size", "0"]) == 0

    [slot] = Pool(repo, config.load(repo)).status()
    assert (slot.state, slot.branch) == ("taken", "feature/a")


# --- warmtree which ---------------------------------------------------------


def test_which_inside_a_slot_names_it(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.chdir(repo)
    main(["fill"])
    capsys.readouterr()
    main(["take", "feature/a", "--no-refill"])
    slot_path = Path(capsys.readouterr().out.strip())
    sub = slot_path / "deep" / "inside"
    sub.mkdir(parents=True)
    monkeypatch.chdir(sub)

    assert main(["which"]) == 0

    assert capsys.readouterr().out.strip() == "slot-1 taken feature/a"


def test_which_in_the_main_worktree_fails(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.chdir(repo)
    main(["fill"])
    capsys.readouterr()

    assert main(["which"]) == 1

    assert "not inside a warmtree slot" in capsys.readouterr().err
