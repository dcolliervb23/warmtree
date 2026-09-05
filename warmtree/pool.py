"""Slot lifecycle and state persistence.

A slot is a detached git worktree parked on the base branch. The pool
remembers its slots in one `state.json` inside the pool directory, rewritten
atomically so a crash mid-write never leaves a half file.
"""

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from warmtree import git
from warmtree.config import Config, pool_dir

STATE_FILE = "state.json"
STATE_VERSION = 1

# ready:   parked on base, warmed, free to take
# taken:   a branch is checked out and somebody is working in it
# warming: `run` commands are executing right now
# stale:   base or a lockfile moved on since the last warm
STATES = ("ready", "taken", "warming", "stale")


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

    def fill(self) -> list[Slot]:
        """Create worktrees until the pool has `size` slots. Returns the new ones."""
        slots = self.load_state()
        base = self.base()
        taken_names = {slot.name for slot in slots}
        created: list[Slot] = []

        number = 1
        while len(slots) < self.config.size:
            name = f"slot-{number}"
            number += 1
            if name in taken_names:
                continue
            path = self.dir / name
            git.worktree_add_detached(self.repo_root, path, base)
            now = _now()
            slot = Slot(
                name=name, path=str(path), state="ready", created=now, warmed=now
            )
            slots.append(slot)
            created.append(slot)
            # Save after every slot so an interrupted fill leaves state that
            # matches the worktrees that actually exist.
            self.save_state(slots)
        return created

    def status(self) -> list[Slot]:
        return self.load_state()


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
