import subprocess
import sys
import time
from pathlib import Path

from warmtree.lock import FileLock

HOLD_LOCK = """
import sys, time
from pathlib import Path
from warmtree.lock import FileLock
with FileLock(Path(sys.argv[1])):
    print("locked", flush=True)
    time.sleep(float(sys.argv[2]))
"""


def test_lock_creates_file_and_releases(tmp_path: Path):
    lock_path = tmp_path / "pool" / "lock"
    with FileLock(lock_path):
        assert lock_path.exists()
    # Re-acquiring right away proves the first hold was released.
    with FileLock(lock_path):
        pass


def test_second_holder_waits_for_first(tmp_path: Path):
    lock_path = tmp_path / "lock"
    hold_seconds = 1.5
    holder = subprocess.Popen(
        [sys.executable, "-c", HOLD_LOCK, str(lock_path), str(hold_seconds)],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "locked"
        start = time.monotonic()
        with FileLock(lock_path):
            waited = time.monotonic() - start
    finally:
        holder.wait(timeout=30)
    assert waited >= hold_seconds * 0.6, f"acquired after only {waited:.2f}s"
