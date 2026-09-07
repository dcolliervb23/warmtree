---
name: warmtree
description: Get an isolated git worktree for a branch in under a second from this repo's warmtree pool. Use whenever you would otherwise run `git worktree add`, before starting parallel tasks that each need their own checkout, and when a branch is finished and its worktree should go back. Also covers checking whether you are already in a slot and growing the pool before fanning out.
---

# warmtree

This repo keeps a pool of pre-warmed git worktrees called slots: dependencies
installed, env files copied, parked on the base branch with a detached HEAD.
`warmtree take` hands you one instantly. In a repo that has a `.warmtree.toml`,
never run `git worktree add` yourself.

All commands work from anywhere inside the repo, including inside a slot.
`take` prints only the slot path on stdout; everything else goes to stderr.

## 1. Check where you are

```sh
warmtree which
```

Prints `slot-N <state> <branch>` and exits 0 when the current directory is
inside a slot. Exits 1 with `not inside a warmtree slot` otherwise.

- Already in a slot for the branch you are working on: keep using it. Do not
  take another.
- In the main worktree, or in a slot for a different branch: take a slot for
  the new branch.

## 2. Check capacity before fanning out

```sh
warmtree size
```

Prints the configured size and counts, for example:

```
size: 2
ready 2, taken 1, warming 0, stale 0; 3 slots in total
```

`size` counts the slots kept ready. A taken slot does not count against it,
so a pool with branches in flight holds more than `size` worktrees until they
are released; that is normal.

Decide with this rule:

- You need N slots now and `ready >= N`: just take them.
- `ready < N`: run `warmtree size <current size + shortfall>` first. It grows
  the pool and warms the new slots before returning, which takes as long as
  the project's install command. Or take anyway and accept a cold create for
  the shortfall; that works, it is only slow.
- Do not shrink the pool unless the user asks. Shrinking removes ready slots
  and their installed dependencies.

For scripts, `warmtree status --json` returns a list of slots with `name`,
`path`, `state`, `branch`, `created`, `warmed`, and `lockfiles`.

## 3. Take a slot

```sh
cd "$(warmtree take <branch>)"          # bash / zsh
cd (warmtree take <branch>)             # PowerShell
```

Creates `<branch>` from the base branch, or checks it out if it exists.
Options:

- `--from <ref>`: start the new branch somewhere other than base.
- `--refill-background`: return at once and let the pool refill in a
  detached process. Use this when the user is waiting on you.
- `--no-refill`: do not refill. Use when you are taking several slots in a
  row and will refill once at the end with `warmtree fill`.

If stderr says `no ready slot; created slot-N cold`, the pool was empty and
you got an ordinary worktree. Nothing is wrong; it was just slow. Mention it to
the user if it keeps happening, and suggest a larger `warmtree size`.

## 4. Work

Inside the slot everything is ordinary git: edit, commit, push, open a pull
request. Do not delete the slot directory and do not run `git worktree remove`
on it. The slot belongs to the pool.

## 5. Release when done

```sh
warmtree release <branch>
```

Parks the slot back on the base branch, keeps its installed dependencies, and
marks it ready. Run this after the branch is merged, or when the work is
abandoned.

- It refuses a dirty tree. Commit or stash first. Ask the user before using
  `--force`, which discards uncommitted changes.
- It deletes the branch only if it is merged. An unmerged branch is always
  kept, and `--keep-branch` keeps it regardless.

## Troubleshooting

| Symptom | Meaning | Do this |
|---|---|---|
| `stale` in `warmtree status` | The last warm failed in that slot. | `warmtree refresh` retries and prints the failing command's output. |
| `warmtree: fatal: not a git repository` | You are outside the repo. | `cd` into the repo or any of its worktrees. |
| `no taken slot has branch '<x>'` | That branch is not in a slot. | `warmtree status` shows which branches are. |
| `slot-N has uncommitted changes` | `release` refused a dirty tree. | Commit or stash, or ask the user about `--force`. |

## Quick reference

| Command | Purpose |
|---|---|
| `warmtree which` | Am I in a slot? |
| `warmtree size` | Configured size and counts. `warmtree size N` grows or shrinks to N. |
| `warmtree status [--json]` | Every slot and its state. |
| `warmtree take <branch> [--from REF] [--refill-background]` | Claim a slot, print its path. |
| `warmtree release <branch> [--keep-branch] [--force]` | Return a slot. |
| `warmtree fill` | Create and warm missing slots. |
| `warmtree refresh` | Fast-forward waiting slots and re-warm if lockfiles changed. |
