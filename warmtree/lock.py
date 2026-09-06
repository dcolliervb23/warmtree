"""A cross-platform exclusive file lock.

`take` holds this while it picks a slot, so two agents calling `take` at the
same moment get two different slots. Windows uses `msvcrt.locking`; everything
else uses `fcntl.flock`. Both are advisory locks that the OS releases if the
process dies, so a crash never leaves the pool locked.

Never nest two holds in one process: both APIs block on the second open file
descriptor and the process deadlocks with itself.
"""

import sys
import time
from pathlib import Path
from types import TracebackType

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl


class FileLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._file = None

    def __enter__(self) -> "FileLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file = open(self.path, "a+")  # noqa: SIM115 - closed in __exit__
        if sys.platform == "win32":
            self._file.seek(0)
            # The blocking variant retries once a second, which is too slow
            # for callers such as status polling. Try without blocking and
            # sleep briefly between attempts until the lock is ours.
            while True:
                try:
                    msvcrt.locking(self._file.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    time.sleep(0.01)
        else:
            fcntl.flock(self._file.fileno(), fcntl.LOCK_EX)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        assert self._file is not None
        if sys.platform == "win32":
            self._file.seek(0)
            msvcrt.locking(self._file.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
        self._file.close()
        self._file = None
