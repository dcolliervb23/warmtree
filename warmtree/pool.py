"""Slot lifecycle and state persistence.

A slot is a detached git worktree parked on the base branch. The pool
remembers its slots in one `state.json` inside the pool directory, rewritten
atomically so a crash mid-write never leaves a half file. Every method that
changes state holds the pool lock for its whole duration; never call one from
inside another.
"""

import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from warmtree import git
from warmtree.config import Config, pool_dir
from warmtree.lock import FileLock

STATE_FILE = "state.json"
LOCK_FILE = "lock"
STATE_VERSION = 1

# ready:   parked on base, warmed, free to take
# taken:   a branch is checked out and somebody is working in it
# warming: `run` commands are executing right now
# stale:   base or a lockfile moved on since the last warm
STATES = ("ready", "taken", "warming", "stale")


class PoolError(Exception):
    """A pool operation cannot proceed. The message is written for the user."""


@dataclass
class Slot:
    name: str
    path: str
    state: str
    branch: str | None = None
    created: str = ""
    warmed: str | None = None
    lockfiles: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Slot":
        return cls(**data)


class Pool:
    def __init__(self, repo_root: Path, config: Config) -> None:
        self.repo_root = repo_root
        self.config = config
        self.dir = pool_dir(repo_root, config)
        self.state_path = self.dir / STATE_FILE

    def base(self) -> str:
        """Branch slots park on: configured, or detected from the repo."""
        return self.config.base or git.default_branch(self.repo_root)

    # --- state ------------------------------------------------------------

    @contextmanager
    def locked(self) -> Iterator[None]:
        with FileLock(self.dir / LOCK_FILE):
            yield

    def load_state(self) -> list[Slot]:
        if not self.state_path.exists():
            return []
        data = json.loads(self.state_path.read_text(encoding="utf-8"))
        return [Slot.from_dict(item) for item in data["slots"]]

    def save_state(self, slots: list[Slot]) -> None:
        """Write state to a temp file, then swap it into place in one step."""
        self.dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": STATE_VERSION,
            "slots": [slot.to_dict() for slot in slots],
        }
        temp_path = self.state_path.with_suffix(".json.tmp")
        temp_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        os.replace(temp_path, self.state_path)

    def status(self) -> list[Slot]:
        return self.load_state()

    # --- lifecycle --------------------------------------------------------

    def fill(self) -> list[Slot]:
        """Create worktrees until `size` slots are waiting. Returns the new ones.

        `size` counts slots that are ready or on their way to ready; taken
        slots do not count, which is what makes a refill after `take` work.
        """
        with self.locked():
            slots = self.load_state()
            base = self.base()
            created: list[Slot] = []
            while _waiting(slots) < self.config.size:
                name = _next_name(slots)
                path = self.dir / name
                git.worktree_add_detached(self.repo_root, path, base)
                now = _now()
                slot = Slot(
                    name=name, path=str(path), state="ready", created=now, warmed=now
                )
                slots.append(slot)
                created.append(slot)
                # Save after every slot so an interrupted fill leaves state
                # that matches the worktrees that actually exist.
                self.save_state(slots)
            return created

    def take(self, branch: str, from_ref: str | None = None) -> tuple[Slot, bool]:
        """Claim a slot for `branch`. Returns the slot and whether it was cold.

        The oldest ready slot gets `branch` checked out (created from
        `from_ref` or base if it does not exist). With no ready slot, a new
        worktree is created the slow way and joins the pool as a taken slot.
        """
        with self.locked():
            slots = self.load_state()
            start = from_ref or self.base()
            ready = sorted(
                (slot for slot in slots if slot.state == "ready"),
                key=lambda slot: (slot.warmed or slot.created, slot.name),
            )
            if ready:
                slot = ready[0]
                _checkout(self.repo_root, Path(slot.path), branch, start)
                cold = False
            else:
                name = _next_name(slots)
                path = self.dir / name
                git.worktree_add_branch(self.repo_root, path, branch, start)
                slot = Slot(name=name, path=str(path), state="taken", created=_now())
                slots.append(slot)
                cold = True
            # State changes only after git succeeded, so a failed checkout
            # leaves the slot ready for the next caller.
            slot.state = "taken"
            slot.branch = branch
            self.save_state(slots)
            return slot, cold

    def release(
        self, branch: str, keep_branch: bool = False, force: bool = False
    ) -> tuple[Slot, bool]:
        """Return the slot holding `branch` to the pool.

        The tree is parked back on base with a detached HEAD. Ignored files
        stay, so the slot is still warm. Returns the slot and whether the
        branch was deleted; a branch with unmerged commits is always kept.
        """
        with self.locked():
            slots = self.load_state()
            slot = _find_taken(slots, branch)
            path = Path(slot.path)
            if not force and git.is_dirty(path):
                raise PoolError(
                    f"{slot.name} has uncommitted changes; commit or stash them, "
                    "or use --force to discard them"
                )
            git.reset_to_detached(path, self.base())
            branch_deleted = False
            if not keep_branch:
                branch_deleted = git.branch_delete(self.repo_root, branch)
            slot.state = "ready"
            slot.branch = None
            self.save_state(slots)
            return slot, branch_deleted

    def remove(
        self,
        names: list[str] | None = None,
        all_slots: bool = False,
        force: bool = False,
    ) -> list[Slot]:
        """Delete slots and their worktree registrations. Returns what went."""
        with self.locked():
            slots = self.load_state()
            if all_slots:
                targets = list(slots)
            else:
                wanted = set(names or [])
                targets = [slot for slot in slots if slot.name in wanted]
                missing = wanted - {slot.name for slot in targets}
                if missing:
                    raise PoolError(f"no such slot: {', '.join(sorted(missing))}")
            taken = [slot.name for slot in targets if slot.state == "taken"]
            if taken and not force:
                raise PoolError(
                    f"{', '.join(taken)} taken; release first or use --force"
                )
            for slot in targets:
                git.worktree_remove(self.repo_root, Path(slot.path))
            self.save_state([slot for slot in slots if slot not in targets])
            return targets


def _checkout(repo_root: Path, path: Path, branch: str, start: str) -> None:
    if git.branch_exists(repo_root, branch):
        git.checkout_branch(path, branch)
    else:
        git.checkout_new_branch(path, branch, start)


def _find_taken(slots: list[Slot], branch: str) -> Slot:
    for slot in slots:
        if slot.state == "taken" and slot.branch == branch:
            return slot
    raise PoolError(f"no taken slot has branch {branch!r}")


def _waiting(slots: list[Slot]) -> int:
    """Slots that are, or will be, available to take."""
    return sum(1 for slot in slots if slot.state != "taken")


def _next_name(slots: list[Slot]) -> str:
    """Lowest slot number not in use, so names stay short and stable."""
    used = {slot.name for slot in slots}
    number = 1
    while f"slot-{number}" in used:
        number += 1
    return f"slot-{number}"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
