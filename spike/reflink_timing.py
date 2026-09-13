"""Spike: measure copy-on-write cloning of a node_modules-shaped tree.

Run on a filesystem to test: generates a synthetic dependency tree
(~45,000 small files, ~560 MB), then times a plain copy against a
copy-on-write clone and checks that the clone is independent.

Usage: python3 spike/reflink_timing.py <base-dir-on-target-fs> <label>
"""

import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path

CHUNK = os.urandom(65536)
DIRS = 1500
FILES_PER_DIR = 30


def generate(root: Path) -> tuple[int, int]:
    files = 0
    total = 0
    for d in range(DIRS):
        sub = root / f"pkg{d // 50}" / f"mod{d}"
        sub.mkdir(parents=True, exist_ok=True)
        for f in range(FILES_PER_DIR):
            size = (f % 9 + 1) * 2500
            (sub / f"f{f}.js").write_bytes(CHUNK[:size])
            files += 1
            total += size
    return files, total


def timed(cmd: list[str]) -> float:
    start = time.monotonic()
    subprocess.run(cmd, check=True)
    return time.monotonic() - start


def fs_used(path: Path) -> int:
    stat = os.statvfs(path)
    return (stat.f_blocks - stat.f_bfree) * stat.f_frsize


def main() -> int:
    base = Path(sys.argv[1])
    label = sys.argv[2]
    base.mkdir(parents=True, exist_ok=True)
    src = base / "template"

    print(f"generating synthetic tree on {label} ...", flush=True)
    start = time.monotonic()
    files, total = generate(src)
    print(f"RESULT {label} generate: {files} files, {total / 1e6:.0f} MB, "
          f"{time.monotonic() - start:.1f}s", flush=True)

    if platform.system() == "Darwin":
        plain = ["cp", "-R", "-p", str(src), str(base / "plain")]
        clone = ["cp", "-c", "-R", "-p", str(src), str(base / "clone")]
    else:
        plain = ["cp", "-a", str(src), str(base / "plain")]
        clone = ["cp", "-a", "--reflink=always", str(src), str(base / "clone")]

    plain_s = timed(plain)
    print(f"RESULT {label} plain copy: {plain_s:.2f}s", flush=True)

    used_before = fs_used(base)
    clone_s = timed(clone)
    used_delta = fs_used(base) - used_before
    print(f"RESULT {label} cow clone: {clone_s:.2f}s "
          f"(disk delta {used_delta / 1e6:.0f} MB)", flush=True)
    print(f"RESULT {label} speedup: {plain_s / clone_s:.0f}x", flush=True)

    # Independence: growing a file in the clone must not touch the template.
    probe = "pkg0/mod0/f0.js"
    original = (src / probe).stat().st_size
    with open(base / "clone" / probe, "ab") as fh:
        fh.write(b"x" * 4096)
    assert (src / probe).stat().st_size == original, "template mutated!"
    print(f"RESULT {label} independence: ok", flush=True)

    shutil.rmtree(base / "plain")
    shutil.rmtree(base / "clone")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
