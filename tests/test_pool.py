import json
from pathlib import Path

from warmtree import git
from warmtree.config import Config
from warmtree.pool import Pool


def test_fill_creates_size_detached_slots_on_base(repo: Path):
    pool = Pool(repo, Config(size=3))
    created = pool.fill()

    assert [slot.name for slot in created] == ["slot-1", "slot-2", "slot-3"]
    base_commit = git.rev_parse(repo, "main")
    for slot in created:
        path = Path(slot.path)
        assert path.is_dir()
        assert git.head_branch(path) is None
        assert git.rev_parse(path, "HEAD") == base_commit


def test_slots_live_in_pool_dir_beside_the_repo(repo: Path, tmp_path: Path):
    pool = Pool(repo, Config(size=1))
    [slot] = pool.fill()
    assert Path(slot.path) == (tmp_path / ".warmtree" / "repo" / "slot-1").resolve()


def test_fill_is_idempotent(repo: Path):
    pool = Pool(repo, Config(size=2))
    pool.fill()
    assert pool.fill() == []
    assert len(pool.status()) == 2


def test_fill_after_size_increase_adds_only_the_difference(repo: Path):
    Pool(repo, Config(size=1)).fill()
    created = Pool(repo, Config(size=3)).fill()
    assert [slot.name for slot in created] == ["slot-2", "slot-3"]


def test_fill_writes_state_json_with_ready_slots(repo: Path):
    pool = Pool(repo, Config(size=2))
    pool.fill()

    data = json.loads(pool.state_path.read_text())
    assert [slot["state"] for slot in data["slots"]] == ["ready", "ready"]
    assert all(slot["branch"] is None for slot in data["slots"])
    assert all(slot["created"] for slot in data["slots"])


def test_save_state_leaves_no_temp_file_behind(repo: Path):
    pool = Pool(repo, Config(size=1))
    pool.fill()
    leftovers = [p for p in pool.dir.iterdir() if p.suffix == ".tmp"]
    assert leftovers == []


def test_slots_are_registered_worktrees_of_the_repo(repo: Path):
    pool = Pool(repo, Config(size=2))
    pool.fill()
    registered = git.worktree_list(repo)
    for slot in pool.status():
        assert Path(slot.path).resolve() in registered


def test_explicit_base_is_respected(repo: Path):
    git.run(["branch", "develop"], cwd=repo)
    (repo / "extra.txt").write_text("x\n")
    git.run(["add", "extra.txt"], cwd=repo)
    git.run(["commit", "-q", "-m", "second"], cwd=repo)

    pool = Pool(repo, Config(size=1, base="develop"))
    [slot] = pool.fill()
    assert git.rev_parse(Path(slot.path), "HEAD") == git.rev_parse(repo, "develop")
    assert git.rev_parse(Path(slot.path), "HEAD") != git.rev_parse(repo, "main")


def test_status_on_empty_pool_is_empty(repo: Path):
    assert Pool(repo, Config()).status() == []
