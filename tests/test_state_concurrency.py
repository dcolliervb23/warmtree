"""Reading state while another process rewrites it must never fail.

On Windows a file open for reading blocks the writer's atomic replace, and a
reader can catch the file mid-replace. Both sides take the pool lock, so
neither happens. On Linux this test passes trivially; it exists for Windows.
"""

import subprocess
import sys
import time
from pathlib import Path

from warmtree.config import Config
from warmtree.pool import Pool

WRITER = """
import sys, time
from pathlib import Path
from warmtree.config import Config
from warmtree.pool import Pool, Slot

pool = Pool(Path(sys.argv[1]), Config())
end = time.monotonic() + float(sys.argv[2])
while time.monotonic() < end:
    with pool.locked():
        pool.save_state([Slot("slot-1", "x", "ready")])
"""


def test_status_never_fails_while_another_process_writes(repo: Path):
    pool = Pool(repo, Config())
    pool.save_state([])
    seconds = 1.5
    writer = subprocess.Popen(
        [sys.executable, "-c", WRITER, str(repo), str(seconds)],
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        reads = 0
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            pool.status()  # raises on the bug
            reads += 1
    finally:
        _, err = writer.communicate(timeout=30)
    assert writer.returncode == 0, err
    assert reads > 10
