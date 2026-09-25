"""Slot lifecycle and state persistence.

A slot is a detached git worktree parked on the base branch. The pool
remembers its slots in one `state.json` inside the pool directory, rewritten
atomically so a crash mid-write never leaves a half file.

Every change to state happens under the pool lock, but warming does not: a
slot is marked `warming` under the lock, the slow commands run with the lock
released so `take` keeps working, and the result is written under the lock
again. Never call one locked method from inside another.
"""

import json
import os
import secrets
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from warmtree import git, warm
from warmtree.config import Config, pool_dir
from warmtree.config import interval_seconds as config_interval
from warmtree.lock import FileLock

STATE_FILE = "state.json"
LOCK_FILE = "lock"
REFRESH_STAMP = "last-refresh"  # mtime = when a refresh last finished
STATE_VERSION = 1

# ready:   parked on base, warmed, free to take
# taken:   a branch is checked out and somebody is working in it
# warming: `run` commands are executing right now
# stale:   the last warm failed; `refresh` will try again
STATES = ("ready", "taken", "warming", "stale")

# States refresh works on. Warming slots belong to another process.
REFRESHABLE = ("ready", "stale")


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
    # Lease: the session (shell or agent) that took the slot. Set by take
    # and adopt to their parent process, cleared by release. Optional with
    # defaults so state files from older versions load unchanged.
    lease_pid: int | None = None
    lease_since: str | None = None
    # When the slot last went back to ready. The idle clock for shrinking;
    # a fill-created slot that was never taken idles from `created`.
    released: str | None = None

    @property
    def number(self) -> int:
        return int(self.name.rsplit("-", 1)[1])

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Slot":
        return cls(**data)


@dataclass
class Refreshed:
    slot: Slot
    moved: bool  # HEAD moved to a newer base commit
    rewarmed: bool  # the run commands executed again


@dataclass
class Swept:
    """One sweep decision: what was looked at and what happened to it."""

    name: str  # slot name, or a worktree path
    branch: str | None
    action: str  # released | folded | kept | skipped
    reason: str


@dataclass
class Finding:
    """One problem `doctor` found, and what it did or suggests."""

    slot: str | None  # None for problems not tied to a state entry
    problem: str
    fixed: bool = False
    hint: str = ""


class Pool:
    def __init__(
        self,
        repo_root: Path,
        config: Config,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self.repo_root = repo_root
        self.config = config
        self.dir = pool_dir(repo_root, config)
        self.state_path = self.dir / STATE_FILE
        self.log = log or (lambda message: None)
        # Hook policy follows the pool's config wherever the pool is built:
        # the CLI, warmtree size's fresh Pool, or the API directly.
        git.allow_hooks(config.git_hooks)

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
        """Read state under the lock.

        Writers already hold the lock. Reading under it too matters on
        Windows, where a file open for reading makes the writer's atomic
        replace fail, and a reader can catch the file mid-replace.
        """
        with self.locked():
            return self.load_state()

    def taken(self, branch: str) -> Slot:
        """The taken slot holding `branch`. Raises PoolError if none does."""
        with self.locked():
            return _find_taken(self.load_state(), branch)

    def _update_slot(self, name: str, **fields: object) -> Slot:
        """Reload state, change one slot's fields, save. Returns the slot."""
        with self.locked():
            slots = self.load_state()
            slot = _find(slots, name)
            for key, value in fields.items():
                setattr(slot, key, value)
            self.save_state(slots)
            return slot

    # --- lifecycle --------------------------------------------------------

    def fill(self) -> list[Slot]:
        """Create and warm slots until `size` are waiting. Returns the new ones.

        `size` counts slots that are ready or on their way to ready; taken
        slots do not count, which is what makes a refill after `take` work.
        """
        created: list[Slot] = []
        while True:
            with self.locked():
                slots = self.load_state()
                if _waiting(slots) >= self.config.size:
                    return created
                name = _next_name(slots)
                path = self.dir / name
                git.worktree_add_detached(self.repo_root, path, self.base())
                slot = Slot(name=name, path=str(path), state="warming", created=_now())
                slots.append(slot)
                self.save_state(slots)
            # Warming can take minutes. The lock is released so take() and
            # status() keep working while it runs.
            created.append(self._warm(slot, run=True))

    def take(
        self,
        branch: str | None,
        from_ref: str | None = None,
        scratch: bool = False,
    ) -> tuple[Slot, bool]:
        """Claim a slot for `branch`. Returns the slot and whether it was cold.

        The most recently used ready slot gets `branch` checked out — hot
        allocation keeps cold surplus idle so shrinking can reclaim it. A
        new branch starts
        at `from_ref` when given, and otherwise where the slot is parked:
        refresh positioned it on the freshest known base, and re-resolving
        the local base here would drag the slot back to a stale commit. Only
        the cold path, which has no parked position, resolves base itself.
        With scratch=True the branch name is generated under the claim lock,
        so two concurrent scratch takes can never pick the same name.
        """
        with self.locked():
            if scratch:
                branch = _scratch_name(self.repo_root)
            if branch is None:
                raise PoolError("take needs a branch name")
            slots = self.load_state()
            # Most recently used first: concentrating takes in hot slots
            # lets the surplus go genuinely idle, which is what allows
            # shrinking to reclaim it. Round-robin would reset every
            # slot's idle clock in turn and nothing would ever decay.
            ready = sorted(
                (slot for slot in slots if slot.state == "ready"),
                key=lambda slot: (
                    slot.released or slot.warmed or slot.created,
                    slot.name,
                ),
                reverse=True,
            )
            # A ready slot must be parked detached. One with a branch checked
            # out was hijacked behind warmtree's back (a manual checkout in
            # the slot); claiming it would switch branches under whoever did
            # that. Record what git says and leave it for release to sort out.
            claimed = None
            saw_hijack = False
            for candidate in ready:
                cpath = Path(candidate.path)
                if not git.owns_worktree(self.repo_root, cpath):
                    # Not our worktree any more; never claim it, never
                    # record its branch. Doctor reports it.
                    self.log(
                        f"{candidate.name} is not a working tree of this "
                        "repository; skipping it"
                    )
                    continue
                head = git.head_branch(cpath)
                if head is None:
                    claimed = candidate
                    break
                candidate.state = "taken"
                candidate.branch = head
                saw_hijack = True
                self.log(
                    f"{candidate.name} was marked ready but has {head} "
                    "checked out; marked taken instead"
                )
            if saw_hijack:
                # Persist the observation now: a failure in the checkout
                # below must not lose what git told us about these slots.
                self.save_state(slots)
            if claimed:
                slot = claimed
                _checkout(self.repo_root, Path(slot.path), branch, from_ref, self.log)
                cold = False
            else:
                name = _next_name(slots)
                path = self.dir / name
                if (
                    from_ref is None
                    and not git.branch_exists(self.repo_root, branch)
                    and git.ref_exists(self.repo_root, f"origin/{branch}")
                ):
                    git.worktree_add_tracking(self.repo_root, path, branch)
                    self.log(f"created {branch} from origin/{branch}")
                else:
                    start = from_ref or self.base()
                    git.worktree_add_branch(self.repo_root, path, branch, start)
                slot = Slot(name=name, path=str(path), state="taken", created=_now())
                slots.append(slot)
                cold = True
            # State changes only after git succeeded, so a failed checkout
            # leaves the slot ready for the next caller.
            slot.state = "taken"
            slot.branch = branch
            slot.lease_pid = os.getppid()
            slot.lease_since = _now()
            self.save_state(slots)
            return slot, cold

    def release(
        self, branch: str, keep_branch: bool = False, force: bool = False
    ) -> tuple[Slot, bool]:
        """Return the slot holding `branch` to the pool.

        The tree is parked back on base with a detached HEAD. Ignored files
        stay, so the slot is still warm. Returns the slot and whether the
        branch was deleted; a branch with unmerged commits is always kept.

        A slot leased to another session that is still running is refused
        without force: releasing under a live occupant would reset the tree
        out from under it. The lease is the session that took the slot, so
        releasing from that same session always passes, and a lease whose
        process is gone is stale and ignored.
        """
        with self.locked():
            slots = self.load_state()
            slot = _find_taken(slots, branch)
            path = Path(slot.path)
            if not git.owns_worktree(self.repo_root, path):
                raise PoolError(
                    f"{slot.name} is no longer a working tree of this "
                    "repository; run `warmtree doctor`"
                )
            if (
                not force
                and slot.lease_pid is not None
                and slot.lease_pid != os.getppid()
                and _pid_alive(slot.lease_pid)
            ):
                raise PoolError(
                    f"{slot.name} is held by process {slot.lease_pid} "
                    f"(since {slot.lease_since}); release from that session, "
                    "wait for it to exit, or use --force"
                )
            if not force and git.is_dirty(path):
                raise PoolError(
                    f"{slot.name} has uncommitted changes; commit or stash them, "
                    "or use --force to discard them"
                )
            git.reset_to_detached(path, self.base())
            branch_deleted = False
            if not keep_branch:
                branch_deleted = git.branch_delete(self.repo_root, branch)
            # An adopted slot graduates into the pool directory now, not at
            # adoption: with the branch done, the tree reset, and the lease
            # surrendered, nothing legitimate is watching the old path.
            home = self.dir / slot.name
            if path.resolve() != home.resolve():
                try:
                    # git worktree move into an existing directory NESTS the
                    # tree inside it and still exits zero; guard explicitly.
                    if home.exists():
                        raise git.GitError(f"{home} already exists")
                    self.dir.mkdir(parents=True, exist_ok=True)
                    git.worktree_move(self.repo_root, path, home)
                    slot.path = str(home)
                    self.log(f"{slot.name} moved into the pool at {home}")
                except git.GitError as exc:
                    # Still a working slot at its old address; doctor and
                    # status show where it lives.
                    self.log(f"{slot.name} stays at {path}: the move failed ({exc})")
            slot.state = "ready"
            slot.branch = None
            slot.lease_pid = None
            slot.lease_since = None
            slot.released = _now()
            self.save_state(slots)
            return slot, branch_deleted

    def adopt(self, path: Path) -> Slot:
        """Register an existing worktree as a taken slot, in place.

        Nothing on disk changes: the folder keeps its path and name, so
        editors, shells, and running processes pointed at it keep working.
        The pool collects the folder later, at release, when the branch is
        done and nothing legitimate is still inside. The tree may be dirty;
        adoption records it, nothing more. Adoption assumes the tree is
        warm: its lockfile hashes are recorded as the warm state.
        """
        with self.locked():
            slots = self.load_state()
            source = path.resolve()
            if source == self.repo_root.resolve():
                raise PoolError("cannot adopt the main worktree")
            for slot in slots:
                if Path(slot.path).resolve() == source:
                    raise PoolError(f"{source} is already {slot.name}")
            if not git.owns_worktree(self.repo_root, source):
                raise PoolError(f"{source} is not a worktree of this repository")
            branch = git.head_branch(source)
            if branch is None:
                raise PoolError(
                    f"{source} has a detached HEAD; check out a branch first"
                )
            name = _next_name(slots)
            slot = Slot(
                name=name,
                path=str(source),
                state="taken",
                branch=branch,
                created=_now(),
                warmed=_now(),
                lockfiles=warm.hash_lockfiles(source, self.config.lockfiles),
                lease_pid=os.getppid(),
                lease_since=_now(),
            )
            slots.append(slot)
            self.save_state(slots)
            return slot

    def sweep(self, fetch: bool = False) -> list["Swept"]:
        """Fold finished work back into the pool.

        Two passes with one test: a branch git certifies as merged into
        base. Taken slots on merged branches are released; hand-made
        worktrees on merged branches are adopted and released in one
        motion, so their folders and dependencies join the pool. Dirty
        trees, unmerged branches, slots other live sessions hold, and
        anything that is not ours are skipped with a reason. Squash-merged
        branches are not detected: their commits are not ancestors of base.
        """
        if fetch:
            git.fetch(self.repo_root)
        base_ref = self.base()
        remote_ref = f"origin/{base_ref}"
        if git.ref_exists(self.repo_root, remote_ref):
            base_ref = remote_ref
        results: list[Swept] = []

        def merged(branch: str) -> bool:
            return git.is_ancestor(self.repo_root, branch, base_ref)

        for slot in self.status():
            if slot.state != "taken" or slot.branch is None:
                continue
            if not merged(slot.branch):
                results.append(Swept(slot.name, slot.branch, "kept", "not merged"))
                continue
            try:
                self.release(slot.branch)
                self._certified_delete(slot.branch)
                results.append(Swept(slot.name, slot.branch, "released", "merged"))
            except PoolError as exc:
                results.append(Swept(slot.name, slot.branch, "skipped", str(exc)))

        known = {Path(s.path).resolve() for s in self.status()}
        known.add(self.repo_root.resolve())
        for path in git.worktree_list(self.repo_root):
            if path in known:
                continue
            label = str(path)
            if not git.owns_worktree(self.repo_root, path):
                continue  # not ours to reason about
            branch = git.head_branch(path)
            if branch is None:
                results.append(Swept(label, None, "kept", "detached HEAD"))
                continue
            if not merged(branch):
                results.append(Swept(label, branch, "kept", "not merged"))
                continue
            if git.is_dirty(path):
                results.append(Swept(label, branch, "skipped", "uncommitted changes"))
                continue
            try:
                self.adopt(path)
                self.release(branch)
                self._certified_delete(branch)
                results.append(Swept(label, branch, "folded", "merged"))
            except (PoolError, git.GitError) as exc:
                results.append(Swept(label, branch, "skipped", str(exc)))
        return results

    def _certified_delete(self, branch: str) -> None:
        """Remove a branch sweep has already certified as merged.

        release's own deletion validates against the LOCAL base, so with a
        stale local base a branch merged only upstream survives it — and
        sweep would report a fold that was not. The is-ancestor check
        against sweep's base ref is the stronger certificate, so the
        deletion is finished under it.
        """
        if git.branch_exists(self.repo_root, branch):
            git.branch_delete(self.repo_root, branch, force=True)

    def refresh(self, fetch: bool = False) -> list[Refreshed]:
        """Bring every waiting slot up to date, one at a time.

        Each slot is moved to the current base commit and its copied files
        are refreshed. The run commands only execute again when a lockfile
        hash changed or the slot's last warm failed.

        With fetch=True the remote is fetched first and slots park on
        `origin/<base>` where it exists, so they track the remote even when
        the local base branch is behind. The local branch itself is never
        moved; the main worktree stays exactly as the user left it.
        """
        base_ref = self.base()
        if fetch:
            git.fetch(self.repo_root)
            remote_ref = f"origin/{base_ref}"
            if git.ref_exists(self.repo_root, remote_ref):
                base_ref = remote_ref

        # Shrink first: a surplus slot due for trimming must not have its
        # lockfiles re-warmed moments before deletion, and a warm failure
        # below must not cancel reclamation. Slots recovered from stale in
        # this pass get their shrink consideration on the next one.
        self._shrink()
        with self.locked():
            candidates = [s.name for s in self.load_state() if s.state in REFRESHABLE]

        results = []
        for name in candidates:
            with self.locked():
                slots = self.load_state()
                slot = _find(slots, name)
                if slot.state not in REFRESHABLE:
                    continue  # taken since we looked
                was_stale = slot.state == "stale"
                slot.state = "warming"
                self.save_state(slots)
                base_commit = git.rev_parse(self.repo_root, base_ref)

            path = Path(slot.path)
            moved = git.rev_parse(path, "HEAD") != base_commit
            git.reset_to_detached(path, base_commit)
            lockfiles_changed = (
                warm.hash_lockfiles(path, self.config.lockfiles) != slot.lockfiles
            )
            rewarm = was_stale or lockfiles_changed
            slot = self._warm(slot, run=rewarm)
            results.append(Refreshed(slot, moved, rewarm))
        if fetch or results:
            # A no-op pass over an empty pool must not mark it fresh, or
            # slots created just after would sit stale for a full interval.
            # A fetch counts even with nothing to move: currency changed.
            self.dir.mkdir(parents=True, exist_ok=True)
            (self.dir / REFRESH_STAMP).touch()
        return results

    def _shrink(self) -> None:
        """Trim surplus ready slots that idled past `shrink_after`.

        A burst grows the pool; this is how it comes back down. `size` is
        the floor — the pool never trims below its configured ready count —
        and the clock is per slot: released (or created, for a slot never
        taken) older than the window. Oldest idle goes first. Slots living
        outside the pool directory are the user's folders and are left
        alone, as everywhere else.
        """
        window = config_interval(self.config.shrink_after, "shrink_after")
        if window == 0:
            return
        cutoff = datetime.now(UTC).timestamp() - window
        with self.locked():
            slots = self.load_state()
            all_ready = [slot for slot in slots if slot.state == "ready"]
            removable = [slot for slot in all_ready if self._at_home(slot)]
            # The floor guards READY slots specifically: warming and stale
            # slots may never make it back, so they earn no credit toward
            # size, or trimming could leave fewer ready than configured.
            surplus = len(all_ready) - self.config.size
            if surplus <= 0:
                return
            # The idle clock is released, or created for a slot never
            # taken. warmed is deliberately not consulted: a rewarm after
            # a lockfile change would reset the clock of an unused slot.
            idle_first = sorted(removable, key=lambda s: s.released or s.created)
            trimmed = 0
            for slot in idle_first:
                if trimmed >= surplus:
                    break
                stamp = slot.released or slot.created
                if datetime.fromisoformat(stamp).timestamp() > cutoff:
                    break  # everything after this is younger still
                git.worktree_remove(self.repo_root, Path(slot.path))
                slots.remove(slot)
                # Persist immediately: a failure removing the next slot
                # must not leave state listing this already-deleted one.
                self.save_state(slots)
                trimmed += 1
                self.log(
                    f"trimmed {slot.name}: unused since {stamp}, "
                    f"pool heading back to {self.config.size} ready"
                )

    def _at_home(self, slot: Slot) -> bool:
        """Whether the slot's folder lives in the pool directory."""
        return Path(slot.path).resolve() == (self.dir / slot.name).resolve()

    def trim(self) -> list[Slot]:
        """Remove waiting slots beyond `size`, highest number first.

        Taken slots are never touched, slots another process is warming are
        left alone, and a slot still living outside the pool directory (an
        adoption whose graduation failed) is never auto-deleted: its folder
        is in the user's own space. Returns the slots removed.
        """
        with self.locked():
            slots = self.load_state()
            surplus = _waiting(slots) - self.config.size
            candidates = sorted(
                (
                    slot
                    for slot in slots
                    if slot.state in REFRESHABLE and self._at_home(slot)
                ),
                key=lambda slot: slot.number,
                reverse=True,
            )
            removed = candidates[: max(surplus, 0)]
            for slot in removed:
                git.worktree_remove(self.repo_root, Path(slot.path))
                slots.remove(slot)
            self.save_state(slots)
            return removed

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
            away = [slot.name for slot in targets if not self._at_home(slot)]
            if away and not force:
                raise PoolError(
                    f"{', '.join(away)} lives outside the pool directory; "
                    "deleting it would remove your own folder, use --force"
                )
            for slot in targets:
                git.worktree_remove(self.repo_root, Path(slot.path))
            self.save_state([slot for slot in slots if slot not in targets])
            return targets

    def doctor(self, fix: bool = False) -> list[Finding]:
        """Compare state.json against git and the filesystem, report drift.

        With fix=True the two safe repairs are applied: a state entry whose
        directory is gone is removed from git's registration and, once git
        confirms it is unregistered, dropped from state; and a registered
        ready slot with a branch checked out is marked taken on that branch,
        which preserves whatever work is sitting there. Everything else is
        reported with a hint and left alone: a warming slot may belong to a
        live fill or refresh in another process, so repairing it here could
        start a second warm in the same directory.
        """
        findings: list[Finding] = []
        with self.locked():
            slots = self.load_state()
            registered = set(git.worktree_list(self.repo_root))
            kept: list[Slot] = []
            changed = False
            for slot in slots:
                path = Path(slot.path)
                if not path.is_dir():
                    # A warming slot belongs to a live fill or refresh in
                    # another process; dropping its entry here would make
                    # that worker fail when it writes its result.
                    if slot.state == "warming":
                        kept.append(slot)
                        findings.append(
                            Finding(
                                slot.name,
                                "directory is gone while marked warming",
                                hint="wait for the fill or refresh to fail, "
                                "then run doctor again",
                            )
                        )
                        continue
                    fixed = False
                    if fix:
                        git.worktree_remove(self.repo_root, path)
                        fixed = path.resolve() not in git.worktree_list(self.repo_root)
                    if fixed:
                        changed = True
                    else:
                        kept.append(slot)
                    findings.append(
                        Finding(
                            slot.name,
                            "directory is gone but the slot is still in state",
                            fixed=fixed,
                            hint=""
                            if fixed
                            else "--fix removes the registration and drops the slot",
                        )
                    )
                    continue
                kept.append(slot)
                is_registered = path.resolve() in registered
                if not is_registered:
                    findings.append(
                        Finding(
                            slot.name,
                            "directory exists but git does not list it as a worktree",
                            hint="`git worktree repair` in the main "
                            "worktree may recover it",
                        )
                    )
                if slot.state == "warming":
                    findings.append(
                        Finding(
                            slot.name,
                            "marked warming; fine if a fill or refresh is "
                            "running, stuck if that process died",
                            hint="wait for it to finish; if it is dead, "
                            f"`warmtree remove {slot.name} --force` "
                            "and `warmtree fill`",
                        )
                    )
                # Only for directories that really are our worktrees: a
                # replaced directory might be an unrelated repository, and
                # recording its branch would let release reset it later.
                # Registration is not enough — git keeps listing a replaced
                # path — so ownership is checked through the shared git dir.
                if slot.state == "ready" and not git.owns_worktree(
                    self.repo_root, path
                ):
                    findings.append(
                        Finding(
                            slot.name,
                            "directory is no longer a working tree of this repository",
                            hint="something replaced it; "
                            f"`warmtree remove {slot.name} --force` "
                            "and `warmtree fill`",
                        )
                    )
                elif slot.state == "ready":
                    head = git.head_branch(path)
                    if head is not None:
                        # Someone checked a branch out in a parked slot
                        # behind warmtree's back. Marking it taken keeps
                        # their work; re-detaching would destroy it.
                        if fix:
                            slot.state = "taken"
                            slot.branch = head
                            changed = True
                        findings.append(
                            Finding(
                                slot.name,
                                f"marked ready but has {head} checked out",
                                fixed=fix,
                                hint=""
                                if fix
                                else "--fix marks it taken so release can recycle it",
                            )
                        )
            known = {Path(slot.path).resolve() for slot in kept}
            if self.dir.is_dir():
                for child in sorted(self.dir.iterdir()):
                    if child.is_dir() and child.resolve() not in known:
                        findings.append(
                            Finding(
                                None,
                                f"{child.name} is in the pool directory but "
                                "not in state",
                                hint="delete it, or adopt it if it is a "
                                "worktree you want",
                            )
                        )
            if changed:
                self.save_state(kept)
        return findings

    # --- warming ----------------------------------------------------------

    def _warm(self, slot: Slot, run: bool) -> Slot:
        """Copy files, optionally run the commands, record hashes, mark ready.

        Called with the lock released. On failure the slot is marked stale
        and the error propagates so the caller can report it.
        """
        path = Path(slot.path)
        try:
            copied = warm.copy_files(
                self.repo_root, path, self.config.copy, slot.number, self.config.env
            )
            for name in copied:
                self.log(f"{slot.name}: copied {name}")
            if run:
                warm.run_commands(
                    path,
                    self.config.run,
                    slot.number,
                    log=lambda message: self.log(f"{slot.name}: {message}"),
                )
        except warm.WarmError:
            self._update_slot(slot.name, state="stale")
            raise
        hashes = warm.hash_lockfiles(path, self.config.lockfiles)
        warmed = _now() if run else slot.warmed
        return self._update_slot(
            slot.name, state="ready", warmed=warmed, lockfiles=hashes
        )


def _checkout(
    repo_root: Path,
    path: Path,
    branch: str,
    from_ref: str | None,
    log: Callable[[str], None],
) -> None:
    """Put `branch` on the slot the way `git checkout` would.

    A branch that exists only on origin is created from origin and tracks
    it — handing back a fresh branch wearing a remote branch's name invites
    a force-push over someone's open pull request. When both exist and
    disagree, say so: the next command should be an informed one.
    """
    remote = f"origin/{branch}"
    if git.branch_exists(repo_root, branch):
        git.checkout_branch(path, branch)
        if git.ref_exists(repo_root, remote):
            local_tip = git.rev_parse(repo_root, branch)
            remote_tip = git.rev_parse(repo_root, remote)
            if local_tip != remote_tip:
                log(
                    f"note: {branch} ({local_tip[:7]}) and {remote} "
                    f"({remote_tip[:7]}) differ; reconcile before pushing"
                )
    elif from_ref is None and git.ref_exists(repo_root, remote):
        git.checkout_tracking_branch(path, branch)
        log(f"created {branch} from {remote}")
    else:
        git.checkout_new_branch(path, branch, from_ref or "HEAD")


def _find(slots: list[Slot], name: str) -> Slot:
    for slot in slots:
        if slot.name == name:
            return slot
    raise PoolError(f"{name} is no longer in the pool")


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


def _pid_alive(pid: int) -> bool:
    """Whether a process with this id is running. Says alive when unsure.

    Windows has no signal 0 (os.kill there terminates), so it asks the
    kernel for a query-only process handle instead.
    """
    if sys.platform == "win32":
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        ERROR_ACCESS_DENIED = 5
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if handle:
            kernel32.CloseHandle(handle)
            return True
        return ctypes.get_last_error() == ERROR_ACCESS_DENIED
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, owned by someone else
    return True


def _scratch_name(repo_root: Path) -> str:
    """A scratch/<id> branch name no existing ref uses.

    Called under the pool lock, so concurrent takes cannot both pick the
    same free name.
    """
    for _ in range(32):
        candidate = f"scratch/{secrets.token_hex(4)}"
        if not git.branch_exists(repo_root, candidate):
            return candidate
    raise PoolError("could not find a free scratch branch name")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
