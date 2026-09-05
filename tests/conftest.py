"""Shared fixtures. Every test runs against a real temporary git repo."""

import subprocess
from pathlib import Path

import pytest


def git(*args: str, cwd: Path) -> str:
    """Run git in cwd and return stripped stdout. Raises on failure."""
    result = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A fresh git repo on branch main with one commit."""
    path = tmp_path / "repo"
    path.mkdir()
    git("init", "-q", "-b", "main", cwd=path)
    git("config", "user.email", "test@example.com", cwd=path)
    git("config", "user.name", "Test", cwd=path)
    (path / "README.md").write_text("hello\n")
    git("add", "README.md", cwd=path)
    git("commit", "-q", "-m", "initial", cwd=path)
    return path
