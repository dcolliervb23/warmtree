from pathlib import Path

import pytest

from warmtree import git
from warmtree.git import GitError


def test_run_returns_stripped_stdout(repo: Path):
    assert git.run(["rev-parse", "--abbrev-ref", "HEAD"], cwd=repo) == "main"


def test_run_raises_git_error_with_stderr(repo: Path):
    with pytest.raises(GitError, match="Needed a single revision"):
        git.run(["rev-parse", "--verify", "no-such-ref"], cwd=repo)


def test_repo_root_from_subdirectory(repo: Path):
    sub = repo / "a" / "b"
    sub.mkdir(parents=True)
    assert git.repo_root(sub) == repo.resolve()


def test_repo_root_from_linked_worktree_is_main_worktree(repo: Path, tmp_path: Path):
    linked = tmp_path / "linked"
    git.worktree_add_detached(repo, linked, "main")
    assert git.repo_root(linked) == repo.resolve()


def test_repo_root_outside_a_repo_raises(tmp_path: Path):
    with pytest.raises(GitError):
        git.repo_root(tmp_path)


def test_default_branch_is_current_branch_without_remote(repo: Path):
    assert git.default_branch(repo) == "main"


def test_rev_parse_resolves_branch_to_commit(repo: Path):
    head = git.run(["rev-parse", "HEAD"], cwd=repo)
    assert git.rev_parse(repo, "main") == head
    assert len(head) == 40


def test_worktree_add_detached_creates_detached_checkout(repo: Path, tmp_path: Path):
    path = tmp_path / "pool" / "slot-1"
    git.worktree_add_detached(repo, path, "main")
    assert (path / "README.md").read_text() == "hello\n"
    assert git.head_branch(path) is None
    assert git.rev_parse(path, "HEAD") == git.rev_parse(repo, "main")


def test_head_branch_names_checked_out_branch(repo: Path):
    assert git.head_branch(repo) == "main"


def test_worktree_list_includes_main_and_linked(repo: Path, tmp_path: Path):
    linked = tmp_path / "linked"
    git.worktree_add_detached(repo, linked, "main")
    paths = git.worktree_list(repo)
    assert repo.resolve() in paths
    assert linked.resolve() in paths
