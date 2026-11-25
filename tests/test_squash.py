"""`git.squash_landed` finds a branch whose whole change sits on base as one commit."""

from pathlib import Path

from tests.conftest import git as run_git
from warmtree import git


def commit(path: Path, name: str, content: str | bytes, message: str) -> None:
    target = path / name
    if isinstance(content, bytes):
        target.write_bytes(content)
    else:
        target.write_text(content)
    run_git("add", name, cwd=path)
    run_git("commit", "-q", "-m", message, cwd=path)


def feature(repo: Path, commits: int = 3) -> None:
    """Branch feat off main with a few commits; leaves main checked out."""
    run_git("checkout", "-q", "-b", "feat", cwd=repo)
    for n in range(commits):
        commit(repo, f"f{n}.txt", f"change {n}\n", f"feat {n}")
    run_git("checkout", "-q", "main", cwd=repo)


def squash_merge(repo: Path, branch: str) -> str:
    """A forge's squash button: one commit on main with the branch's change."""
    run_git("checkout", "-q", "main", cwd=repo)
    run_git("merge", "-q", "--squash", branch, cwd=repo)
    run_git("commit", "-q", "-m", "squash", cwd=repo)
    return run_git("rev-parse", "--short", "HEAD", cwd=repo)


def test_a_squash_merged_branch_is_found(repo: Path):
    feature(repo)
    sha = squash_merge(repo, "feat")

    assert git.squash_landed(repo, "feat", "main") == sha


def test_an_unmerged_branch_is_not_found(repo: Path):
    feature(repo)
    commit(repo, "other.txt", "other\n", "unrelated work on main")

    assert git.squash_landed(repo, "feat", "main") is None


def test_a_commit_after_the_squash_is_not_found(repo: Path):
    feature(repo)
    squash_merge(repo, "feat")
    run_git("checkout", "-q", "feat", cwd=repo)
    commit(repo, "late.txt", "late\n", "work after the squash")
    run_git("checkout", "-q", "main", cwd=repo)

    assert git.squash_landed(repo, "feat", "main") is None


def test_base_moving_beside_the_change_still_matches(repo: Path):
    lines = [f"line {n}\n" for n in range(1, 11)]
    commit(repo, "ten.txt", "".join(lines), "ten lines")
    run_git("checkout", "-q", "-b", "feat", cwd=repo)
    edited = lines.copy()
    edited[1] = "line 2 from feat\n"
    commit(repo, "ten.txt", "".join(edited), "feat edits line 2")
    run_git("checkout", "-q", "main", cwd=repo)
    moved = lines.copy()
    moved[8] = "line 9 from main\n"
    commit(repo, "ten.txt", "".join(moved), "main edits line 9")

    sha = squash_merge(repo, "feat")

    assert git.squash_landed(repo, "feat", "main") == sha


def test_an_empty_net_change_is_not_found(repo: Path):
    run_git("checkout", "-q", "-b", "feat", cwd=repo)
    commit(repo, "temp.txt", "temp\n", "add")
    run_git("rm", "-q", "temp.txt", cwd=repo)
    run_git("commit", "-q", "-m", "remove", cwd=repo)
    run_git("checkout", "-q", "main", cwd=repo)
    commit(repo, "temp.txt", "temp\n", "main adds the same file")
    run_git("rm", "-q", "temp.txt", cwd=repo)
    run_git("commit", "-q", "-m", "main removes it", cwd=repo)

    assert git.squash_landed(repo, "feat", "main") is None


def test_a_different_binary_change_does_not_match(repo: Path):
    # The same text edit with different bytes in a binary file: without the
    # binary contents both diffs read "Binary files differ" and look alike.
    commit(repo, "blob.bin", b"\x00start", "binary file")
    run_git("checkout", "-q", "-b", "feat", cwd=repo)
    (repo / "blob.bin").write_bytes(b"\x00B")
    commit(repo, "notes.txt", "same text\n", "feat's bytes and text")
    run_git("checkout", "-q", "main", cwd=repo)
    (repo / "blob.bin").write_bytes(b"\x00A")
    run_git("add", "blob.bin", cwd=repo)
    commit(repo, "notes.txt", "same text\n", "main's different bytes, same text")

    assert git.squash_landed(repo, "feat", "main") is None


def test_user_diff_config_does_not_hide_a_squash(repo: Path):
    run_git("config", "color.ui", "always", cwd=repo)
    run_git("config", "diff.renames", "copies", cwd=repo)
    run_git("config", "log.showSignature", "false", cwd=repo)
    feature(repo)
    sha = squash_merge(repo, "feat")

    assert git.squash_landed(repo, "feat", "main") == sha
