# Ideas

Things that came up while building warmtree and are out of scope for v1.
Append here instead of expanding DESIGN.md.

- **Stream warm output.** `run` output is captured and shown only on failure
  so `take` keeps stdout clean. A `--verbose` flag on `fill` and `refresh`
  could stream it to stderr for a first-time `npm ci` that takes minutes.
- **`refresh --fetch`.** `refresh` moves slots to the local base tip. It could
  run `git fetch` first, or accept `base = "origin/main"`, which already works
  because the base is resolved with `rev-parse`.
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
  a full node_modules on disk. Needs `max_size`, and shrinking back toward
  the configured size edges into daemon territory. Cold fallback plus the
  skill telling agents to grow before fanning out covers most of it today.
- **`adopt <path>`: fold an existing worktree into the pool.** Registers a
  hand-made worktree as a taken slot (moving it into the pool dir with
  `git worktree move`, opt-in and one at a time), so a later `release`
  recycles its warm dependencies instead of the user deleting them. The
  onboarding story for repos mid-development. PR-aware cleanup of non-pool
  worktrees stays out: forge APIs, and it is worktrunk's job.
- **`release --merged`: sweep taken slots whose branch is merged.** The same
  local `git branch -d` test release already uses, applied across the pool.
  Cleanup confined to worktrees warmtree owns, no forge APIs.
- **README FAQ: onboarding an existing repo.** The pool is additive; old
  worktrees age out naturally. Say so explicitly for mid-development users.
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
- **Slot leases.** Nothing stops `release` while a process is working
  inside a slot: a shell after `cd $(warmtree take x)`, an editor, or
  `warmtree exec`. The dirty-tree refusal is the only guard. A lease field
  in state (holder pid + expiry, set by take/exec, checked by release)
  would close it for every entry point at once; per-command fixes would
  not. From the exec review, 2026-09-12.
