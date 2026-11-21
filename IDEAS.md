# Ideas

Things that came up while building warmtree and are out of scope for v1.
Append here instead of expanding DESIGN.md.

- **Stream warm output.** `run` output is captured and shown only on failure
  so `take` keeps stdout clean. A `--verbose` flag on `fill` and `refresh`
  could stream it to stderr for a first-time `npm ci` that takes minutes.
- **A real docs site.** The README is the documentation for now, on
  purpose. If warmtree grows an audience, split command reference and
  workflows into docs/ and keep the README as the pitch plus quick start.
- **Warn when `fill` warms nothing.** With the init default `run = []`, slots
  come up without dependencies and a sub-second `take` buys little. `init`
  guessing `run` from lockfiles is decided against (it never guesses), but
  `fill` could print a one-line hint when `run` is empty and a known lockfile
  is present. From dogfooding, 2026-09-07.
- **Hint at session reload after skill install.** Skills are scanned at
  agent session start (Claude Code: `/reload-plugins`; Copilot CLI:
  `/skills reload`; Codex: restart only). `init` and `skill` could print one
  line telling the user a running session needs a restart or skill reload to
  see the new skill. Keep it vague enough not to go stale with tool renames.
- **Relative size: `warmtree size +2` / `size -1`.** Agents and humans both
  have to read the current size and do arithmetic before growing the pool for
  a fan-out; relative syntax removes the round trip.
- **Autogrow on cold take, with a cap.** A config like `autogrow = 2` bumping
  size whenever take falls back cold only ever ratchets up, each step costing
  a full node_modules on disk. Needs `max_slots` (PR #23's name for the cap
  on total managed slots — better than `max_size`, which reads as a cap on
  the `size` setting), and shrinking back toward
  the configured size edges into daemon territory. Cold fallback plus the
  skill telling agents to grow before fanning out covers most of it today.
- **Reflink spike results (2026-09-13, spike/reflink branch).** Measured on
  real btrfs, xfs, and apfs via CI loop mounts: cloning a 529 MB, 45,000-file
  synthetic node_modules took 2.4s / 4.0s / 10.6s — only 1-3x faster than a
  plain copy, because many-small-files trees are metadata-bound and reflink
  only skips data blocks. Never milliseconds. Disk sharing and independence
  both held (clones cost 12-48 MB). Conclusion: cloning cannot replace the
  pool (a pool take is 0.15s because nothing is copied), but it is a strong
  COLD-PATH fallback: a drained pool could clone from a warm template in
  seconds instead of reinstalling for minutes. Revised shape: pool first,
  template clone second, full install last; probe support per pool dir and
  skip silently where unsupported (ecryptfs/ext4, i.e. most defaults).
- **Reflink template slots (v0.4+ direction).** One warm template worktree
  instead of N pre-made slots: `take` registers a fresh worktree the normal
  cheap way, then clones the template's warm payload (node_modules, .venv,
  whatever `git clean -ndX` would list) with copy-on-write, and `release`
  simply deletes the clone. Effectively an infinite pool at roughly one
  slot's disk cost, and refresh only ever has one template to keep warm.
  Mechanics: `cp --reflink=always` on XFS/btrfs/ZFS, `cp -c` (clonefile) on
  APFS; detect support once per pool dir by cloning a probe file and cache
  the answer in state.json; anywhere unsupported (NTFS), fall back to the
  classic pool unchanged. Never reflink the whole slot dir: the `.git` file
  and gitdir links must come from a real `git worktree add`, only the
  ignored payload is cloned. Open questions: enumerating the payload
  (config key vs `git clean -ndX`), Windows story (ReFS block cloning is
  rare on dev machines), and whether release-deletes-clone should retire
  reset-on-release entirely once the template path exists.
- **Fold detached checkouts instead of removing them.** `sweep --detached`
  (2026-11) removes clean detached worktrees whose HEAD is an ancestor of
  base. A fold variant could register such a tree directly as a ready slot:
  next slot name, move the folder into the pool dir, `reset_to_detached` on
  base, record no lockfile hashes so the next refresh re-warms it. Decided
  against for now: validation checkouts are stale, so the recycled state
  needs a full re-warm anyway and remove-plus-fill lands in the same place.
  Do not teach `adopt` a branchless path; taken slots keep their branch
  invariant. Revisit only on real demand.
- **Hidden edits are invisible to `sweep --detached` (known gap, left alone).**
  An edit to a tracked file hidden with `git update-index --skip-worktree`
  or `--assume-unchanged` does not show in `git status`, so a merged, idle
  detached worktree holding one looks clean and is removed with the edit.
  Found by the adversarial review of #24 (2026-11). Not guarded: hiding a
  change from git is deliberate, and the obvious check (any `S` or
  lowercase entry in `git ls-files -v`) would keep every sparse checkout,
  since sparse checkout marks its excluded files skip-worktree. A real fix
  would have to tell sparse-checkout's marks from hand-set ones. Revisit
  only on real demand.
- **Edits in a detached waiting slot are still reset (known gap, left alone).**
  `refresh` and shrinking now keep a waiting slot with a branch checked out
  by hand. One edited while still detached, with no branch, is reset or
  removed as before. Found by the review of #26 (2026-11). Not guarded:
  warm commands can leave tracked files modified (an install rewriting its
  lockfile), so a dirty detached slot does not prove someone worked there,
  and keeping every one would stop the pool recycling. A real fix would
  record the tree's state after warming and compare. Revisit on real demand.
