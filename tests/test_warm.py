import hashlib
import sys
from pathlib import Path

import pytest

from warmtree import warm
from warmtree.warm import WarmError

WRITE_SLOT_VAR = (
    f"{sys.executable} -c "
    "\"import os; open('slot.txt', 'w').write(os.environ['WARMTREE_SLOT'])\""
)


def test_hash_lockfiles_hashes_existing_and_skips_missing(tmp_path: Path):
    (tmp_path / "uv.lock").write_bytes(b"locked")
    hashes = warm.hash_lockfiles(tmp_path, ("uv.lock", "package-lock.json"))
    assert hashes == {"uv.lock": hashlib.sha256(b"locked").hexdigest()}


def test_copy_files_copies_existing_and_skips_missing(tmp_path: Path):
    source = tmp_path / "main"
    slot = tmp_path / "slot"
    source.mkdir()
    slot.mkdir()
    (source / "config").mkdir()
    (source / "config" / "local.json").write_text("{}\n")

    copied = warm.copy_files(
        source, slot, ("config/local.json", "missing.txt"), slot_number=1, env=False
    )

    assert copied == ["config/local.json"]
    assert (slot / "config" / "local.json").read_text() == "{}\n"
    assert not (slot / "missing.txt").exists()


def test_copy_files_writes_slot_variable_into_env_files_only(tmp_path: Path):
    source = tmp_path / "main"
    slot = tmp_path / "slot"
    source.mkdir()
    slot.mkdir()
    (source / ".env").write_text("PORT=3000\n")
    (source / "settings.json").write_text("{}\n")

    warm.copy_files(source, slot, (".env", "settings.json"), slot_number=2, env=True)

    assert (slot / ".env").read_text() == "PORT=3000\nWARMTREE_SLOT=2\n"
    assert (slot / "settings.json").read_text() == "{}\n"


def test_copy_files_replaces_an_existing_slot_variable(tmp_path: Path):
    source = tmp_path / "main"
    slot = tmp_path / "slot"
    source.mkdir()
    slot.mkdir()
    (source / ".env.local").write_text("WARMTREE_SLOT=9\nA=1\n")

    warm.copy_files(source, slot, (".env.local",), slot_number=3, env=True)

    assert (slot / ".env.local").read_text() == "A=1\nWARMTREE_SLOT=3\n"


def test_copy_files_env_false_leaves_files_untouched(tmp_path: Path):
    source = tmp_path / "main"
    slot = tmp_path / "slot"
    source.mkdir()
    slot.mkdir()
    (source / ".env").write_text("PORT=3000\n")

    warm.copy_files(source, slot, (".env",), slot_number=1, env=False)

    assert (slot / ".env").read_text() == "PORT=3000\n"


def test_run_commands_runs_in_slot_with_slot_variable(tmp_path: Path):
    warm.run_commands(tmp_path, (WRITE_SLOT_VAR,), slot_number=4)
    assert (tmp_path / "slot.txt").read_text() == "4"


def test_run_commands_runs_in_order_and_logs(tmp_path: Path):
    seen: list[str] = []
    warm.run_commands(
        tmp_path,
        ("echo one>> out.txt", "echo two>> out.txt"),
        slot_number=1,
        log=seen.append,
    )
    lines = (tmp_path / "out.txt").read_text().split()
    assert lines == ["one", "two"]
    assert len(seen) == 2


def test_run_commands_failure_names_command_and_exit_code(tmp_path: Path):
    with pytest.raises(WarmError, match=r"exit 3.*code 3"):
        warm.run_commands(tmp_path, ("echo fine", "exit 3"), slot_number=1)


def test_run_commands_failure_includes_output(tmp_path: Path):
    with pytest.raises(WarmError, match="something broke"):
        warm.run_commands(tmp_path, ("echo something broke&& exit 1",), slot_number=1)
