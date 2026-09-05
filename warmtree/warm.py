"""Make a slot warm.

Copy the untracked files git does not carry, run the project's own install
commands, and hash the lockfiles so `refresh` knows when to do it again.
warmtree has no idea what those commands are; the project declares them.
"""

import hashlib
import os
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

ENV_VAR = "WARMTREE_SLOT"


class WarmError(Exception):
    """A warm command failed. The message names the command and exit code."""


def hash_lockfiles(root: Path, names: tuple[str, ...]) -> dict[str, str]:
    """SHA-256 of each lockfile that exists under `root`. Missing ones are
    left out, so a lockfile appearing or disappearing also counts as a change."""
    hashes = {}
    for name in names:
        path = root / name
        if path.is_file():
            hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashes


def copy_files(
    source_root: Path,
    slot_path: Path,
    names: tuple[str, ...],
    slot_number: int,
    env: bool,
) -> list[str]:
    """Copy each named file from the main worktree into the slot.

    Files that do not exist in the main worktree are skipped. When `env` is
    on, copied env files get a `WARMTREE_SLOT=<n>` line so a project can
    derive per-slot ports or database names. Returns the names copied.
    """
    copied = []
    for name in names:
        source = source_root / name
        if not source.is_file():
            continue
        target = slot_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        if env and is_env_file(name):
            set_slot_variable(target, slot_number)
        copied.append(name)
    return copied


def is_env_file(name: str) -> bool:
    """`.env`, `.env.local`, `app.env`: dotenv-style files we can safely append to."""
    base = Path(name).name
    return base.startswith(".env") or base.endswith(".env")


def set_slot_variable(path: Path, slot_number: int) -> None:
    lines = path.read_text(encoding="utf-8").splitlines()
    lines = [line for line in lines if not line.startswith(f"{ENV_VAR}=")]
    lines.append(f"{ENV_VAR}={slot_number}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_commands(
    slot_path: Path,
    commands: tuple[str, ...],
    slot_number: int,
    log: Callable[[str], None] | None = None,
) -> None:
    """Run each command through the shell inside the slot, in order.

    Output is captured and only shown when a command fails, so `take` can
    keep stdout clean for the slot path. WARMTREE_SLOT is in the environment.
    """
    env = {**os.environ, ENV_VAR: str(slot_number)}
    for command in commands:
        if log is not None:
            log(f"running: {command}")
        result = subprocess.run(
            command,
            shell=True,
            cwd=slot_path,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode != 0:
            output = (result.stdout + result.stderr).strip()
            tail = "\n".join(output.splitlines()[-20:])
            raise WarmError(
                f"`{command}` exited with code {result.returncode} in {slot_path}"
                + (f"\n{tail}" if tail else "")
            )
