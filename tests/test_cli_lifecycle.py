"""take, release, and remove through the command line, including two
processes calling take at the same time."""

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from warmtree import git
from warmtree.cli import main


def status_json(capsys: pytest.CaptureFixture[str]) -> list[dict]:
    capsys.readouterr()
    assert main(["status", "--json"]) == 0
    return json.loads(capsys.readouterr().out)


def test_take_prints_only_the_path_on_stdout(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.chdir(repo)
    main(["fill"])
    capsys.readouterr()

    assert main(["take", "feature/a", "--no-refill"]) == 0

    out, err = capsys.readouterr()
    path = Path(out.strip())
    assert out.count("\n") == 1
    assert path.is_dir()
    assert git.head_branch(path) == "feature/a"
    assert "slot-1" in err


def test_take_refills_the_pool_by_default(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.chdir(repo)
    main(["fill"])

    assert main(["take", "feature/a"]) == 0

    slots = status_json(capsys)
    states = sorted(s["state"] for s in slots)
    assert states == ["ready", "ready", "taken"]


def test_take_no_refill_leaves_pool_short(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.chdir(repo)
    main(["fill"])

    assert main(["take", "feature/a", "--no-refill"]) == 0

    states = sorted(s["state"] for s in status_json(capsys))
    assert states == ["ready", "taken"]


def test_take_refill_background_spawns_a_fill(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.chdir(repo)
    main(["fill"])

    assert main(["take", "feature/a", "--refill-background"]) == 0

    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        states = sorted(s["state"] for s in status_json(capsys))
        if states == ["ready", "ready", "taken"]:
            break
        time.sleep(0.2)
    assert states == ["ready", "ready", "taken"]


def test_take_on_empty_pool_says_it_went_cold(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.chdir(repo)

    assert main(["take", "feature/a", "--no-refill"]) == 0

    out, err = capsys.readouterr()
    assert Path(out.strip()).is_dir()
    assert "cold" in err


def test_take_from_ref(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.chdir(repo)
    git.run(["branch", "develop"], cwd=repo)
    main(["fill"])
    capsys.readouterr()

    assert main(["take", "feature/a", "--from", "develop", "--no-refill"]) == 0

    path = Path(capsys.readouterr().out.strip())
    assert git.rev_parse(path, "HEAD") == git.rev_parse(repo, "develop")


def test_release_via_cli(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.chdir(repo)
    main(["fill"])
    main(["take", "feature/a", "--no-refill"])

    assert main(["release", "feature/a"]) == 0

    assert "released slot-1" in capsys.readouterr().out
    assert {s["state"] for s in status_json(capsys)} == {"ready"}


def test_release_dirty_fails_then_force_succeeds(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.chdir(repo)
    main(["fill"])
    capsys.readouterr()
    main(["take", "feature/a", "--no-refill"])
    path = Path(capsys.readouterr().out.strip())
    (path / "README.md").write_text("dirty\n")

    assert main(["release", "feature/a"]) == 1
    assert "--force" in capsys.readouterr().err
    assert main(["release", "feature/a", "--force"]) == 0


def test_remove_all_via_cli(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.chdir(repo)
    main(["fill"])

    assert main(["remove", "--all"]) == 0

    assert status_json(capsys) == []
    assert git.worktree_list(repo) == [repo.resolve()]


def test_remove_needs_a_name_or_all(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.chdir(repo)
    main(["fill"])
    assert main(["remove"]) == 1
    assert "--all" in capsys.readouterr().err


def test_two_concurrent_takes_get_two_different_slots(repo: Path):
    subprocess.run([sys.executable, "-m", "warmtree", "fill"], cwd=repo, check=True)
    procs = [
        subprocess.Popen(
            [sys.executable, "-m", "warmtree", "take", branch, "--no-refill"],
            cwd=repo,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for branch in ("agent-one", "agent-two")
    ]
    results = [p.communicate(timeout=60) for p in procs]

    for p, (_, err) in zip(procs, results, strict=True):
        assert p.returncode == 0, err
    paths = {Path(out.strip()) for out, _ in results}
    assert len(paths) == 2
    assert {git.head_branch(p) for p in paths} == {"agent-one", "agent-two"}
