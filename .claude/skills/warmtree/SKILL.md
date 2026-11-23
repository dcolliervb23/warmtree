---
name: warmtree
description: Get an isolated git worktree for a branch in under a second from this repo's warmtree pool. Use whenever you would otherwise run `git worktree add`, before starting parallel tasks that each need their own checkout, and when a branch is finished and its worktree should go back. Also covers checking whether you are already in a slot and growing the pool before fanning out.
---

# warmtree

This repo keeps a pool of pre-warmed git worktrees called slots: parked on
the base branch with a detached HEAD, dependencies installed and env files
copied when `.warmtree.toml`'s `[warm]` section says how. With an empty
`run`, slots are bare checkouts; `warmtree fill` says so when it
recognizes a lockfile in the repo.
`warmtree take` hands you one instantly.

Where `.warmtree.toml` exists, warmtree is the default, not a fallback:
never run `git worktree add` yourself, and start every task that needs a
branch with `warmtree which`.

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
- Do not shrink the pool unless the user asks. Shrinking removes waiting (ready or stale)
  slots and their installed dependencies.

For scripts, `warmtree status --json` returns a list of slots with `name`,
`path`, `state`, `branch`, `created`, `warmed`, `lockfiles`, `lease_pid`
(the session that took it), `lease_since`, `released`, and `parked`
(the commit the slot was last parked on).

## 3. Take a slot

```sh
warmtree take <branch>
```

`take` prints the slot's absolute path on stdout. Record that path; it is
your handle for everything that follows.

Do not plan around `cd`. Agent harnesses reset the working directory
between commands, and file tools resolve relative paths against the
directory the session started in — so a `cd` into the slot quietly stops
holding, and later commands run against the wrong worktree. Address the
slot explicitly every time instead:

- Shell commands: `git -C <slot path> ...`, or
  `warmtree exec <branch> -- <command>`.
- File reads and edits: absolute paths under the slot.
- Lost the path? `warmtree path <branch>` prints it again, from anywhere
  in the repo. Running `warmtree take <branch>` again is also safe: a
  branch already in a taken slot gets that same slot's path back, with a
  note on stderr.

Humans in a persistent terminal can hop in directly:
`cd "$(warmtree take <branch>)"` in bash or zsh,
`cd (warmtree take <branch>)` in PowerShell.

`take` checks out `<branch>` if it exists — locally, or on origin, where the new
local branch is created from the remote and tracks it, never a fresh branch
wearing the remote's name. Otherwise creates it from the base branch. When
local and origin tips differ, a note on stderr says so; reconcile before
pushing.
Options:

- `--from <ref>`: start the new branch somewhere other than base. It
  wins over origin: a branch that exists only on origin is created fresh at
  `<ref>` and tracks nothing, so leave `--from` off to pick up a remote branch.
- `--scratch` (instead of a branch name): a generated `scratch/<id>` branch
  for throwaway experiments. The branch name is printed on stderr; release
  it like any other branch when done.
- `--json`: print `{"path", "slot", "branch", "cold"}` instead of the bare
  path, when you are orchestrating rather than cd-ing. `cold` tells you the
  pool was empty and this take was slow.
- `--refill-background`: return at once and let the pool refill in a
  detached process. Use this when the user is waiting on you.
- `--no-refill`: do not refill. Use when you are taking several slots in a
  row and will refill once at the end with `warmtree fill`.

If stderr says `no ready slot; created slot-N cold`, the pool was empty and
you got an ordinary worktree. Nothing is wrong; it was just slow. Mention it to
the user if it keeps happening, and suggest a larger `warmtree size`.

## 4. Work

Inside the slot everything is ordinary git: edit, commit, push, open a pull
request. Slots are pool property with house rules:

- Do not delete the slot directory or run `git worktree remove` on it.
- Verify the slot's branch before operating in it (`warmtree which`, or
  `git -C <slot path> branch --show-current`); if the branch changed under
  you, stop and check `warmtree status` before continuing.
- Target the slot path explicitly (`warmtree exec`, `git -C <slot path>`)
  rather than relying on an earlier `cd`; a command that silently runs in
  the main worktree is how unrelated changes leak into PRs.
- The branch name is the durable identity, not the slot number: slots are
  recycled, branches are yours.

For one command in a branch's slot, skip the cd entirely:

```sh
warmtree exec <branch> -- npm test
```

It runs in that slot with `WARMTREE_SLOT` set, streams pass through, and the
exit code is the command's. Do not release a branch while a command is still
running in its slot.

## 5. Release when done

```sh
warmtree release <branch>
```

Parks the slot back on the base branch, keeps its installed dependencies, and
marks it ready. Run this after the branch is merged, or when the work is
abandoned.

- It refuses a dirty tree. Commit or stash first. Ask the user before using
  `--force`, which discards uncommitted changes and also takes the slot
  from another still-running session that holds it.
- It deletes the branch only if it is merged. An unmerged branch, and the
  base branch, are always kept, and `--keep-branch` keeps it regardless.

## Troubleshooting

| Symptom | Meaning | Do this |
|---|---|---|
| `stale` in `warmtree status` | The last warm failed in that slot. | `warmtree refresh` retries and prints the failing command's output. |
| `warmtree: fatal: not a git repository` | You are outside the repo. | `cd` into the repo or any of its worktrees. |
| `no taken slot has branch '<x>'` | That branch is not in a slot. | `warmtree status` shows which branches are. |
| `slot-N has uncommitted changes` | `release` refused a dirty tree. | Commit or stash, or ask the user about `--force`. |
| `slot-N is held by process <pid>` | Another live session took that slot. | Release from that session, wait for it to exit, or ask the user about `--force`. |
| `<branch> is already taken in slot-N` | A repeat take; the printed path is that existing slot. | Use the path. If the note names another live process, coordinate before working there. |
| `kept <path>: detached HEAD, merged, but <reason>` | `sweep` found work in a detached checkout and will not delete it. | Tell the user the reason; they decide what to keep. |
| `skipped <slot> (<branch>): <git error>` | Releasing that slot failed; sweep moved on to the rest. | Show the user the error; retry `warmtree release <branch>` once it is fixed. |
| ``slot-N is no longer a working tree of this repository; run `warmtree doctor` `` or `slot-N directory is missing` | The slot's folder was deleted or replaced outside warmtree. | Run `warmtree doctor` and show the user its report; ask before `--fix`. |
| `kept branch <x>: it has commits that are not merged` or `kept branch <x>: it is the base branch` | `release` returned the slot but kept the branch on purpose. | Nothing; never delete the branch yourself. Ask the user before deleting an unmerged branch. |
| `pool refresh overdue; refreshing in the background` | A scheduled refresh started on its own. It never resets or trims a slot holding someone's work. | Nothing; carry on. |
| `slot-N was marked ready (or stale) but has <branch> checked out; marked taken instead`, or a `taken` slot in `warmtree status` that no session took | Someone checked out a branch by hand in a waiting slot; warmtree kept their work and now counts the slot as taken. | Tell the user. The slot returns to the pool with `warmtree release <branch>` once they are done with it. |
| `slot-N is not a working tree of this repository; left alone (see warmtree doctor)` | The slot's folder was replaced by something else; warmtree will not reset or delete it. | Run `warmtree doctor` and show the user its report. The folder is someone else's: let the user decide what to do with it. |
| `slot-N has a <rebase or bisect> in progress; left alone` or `slot-N has commits on its detached HEAD that no branch holds; left alone` | Someone is working in a waiting slot without a branch. warmtree will not reset, trim, or hand it out. | Tell the user. Never finish, abort, or discard their work to free the slot. |
| `slot-N folder is missing; skipped (run warmtree doctor)` | The slot's folder was deleted by hand; `refresh` and `take` skip it. | Run `warmtree doctor`; `--fix` drops the entry. |
| `<slots> holds work that removing would delete; use --force` | `remove` found someone's work in a waiting slot. | Tell the user what is there; never add `--force` on your own. |

## Quick reference

| Command | Purpose |
|---|---|
| `warmtree which` | Am I in a slot? |
| `warmtree path <branch>` | Print the taken slot's absolute path for a branch. |
| `warmtree size` | Configured size and counts. `warmtree size N` grows or shrinks to N. |
| `warmtree status [--json] [--du]` | Every slot and its state; `--du` adds disk usage. |
| `warmtree take <branch> [--from REF] [--json] [--refill-background \| --no-refill]` | Claim a slot, print its path (or JSON). |
| `warmtree take --scratch` | Claim a slot on a generated throwaway branch. |
| `warmtree exec <branch> -- <command...>` | Run one command inside that branch's slot. |
| `warmtree release <branch> [--keep-branch] [--force] [--json]` | Return a slot. |
| `warmtree fill` | Create and warm missing slots. |
| `warmtree refresh [--fetch]` | Move waiting slots to base (`origin/<base>` with `--fetch`), re-warm where lockfiles changed, trim long-idle surplus. Never touches a slot holding someone's work. |
| `warmtree doctor [--fix]` | Report drift between pool state, git, and disk; `--fix` applies the safe repairs. |
| `warmtree remove <slot...> [--all] [--force]` | Delete slots and their dependencies. Refuses taken slots and slots holding work without `--force`. Only when the user asks. |
| `warmtree adopt <path>` | Register an existing hand-made worktree as a taken slot, in place. |
| `warmtree sweep [--fetch] [--detached] [--dry-run]` | Fold every merged branch's slot or worktree back into the pool. `--detached` also removes idle, clean detached checkouts already merged into base; anything holding work stays. `--dry-run` changes nothing and lists what it would clean up and what needs the user. |

`adopt` changes nothing on disk: the worktree is registered where it is,
and the folder moves into the pool later, at release, once the branch is
done. Only run it when the user asks you to onboard an existing worktree.
`sweep` touches only branches git certifies as merged and skips dirty
trees, but it does move the merged worktrees' folders into the pool; run
it when the user asks for a cleanup, not on your own initiative.

Start every cleanup with `warmtree sweep --dry-run` (add `--fetch` when the
repo has a remote). It changes nothing and groups every worktree it looked
at: "would clean up" is what a real sweep will do; "needs you" lists
worktrees only the user can settle, such as uncommitted changes, commits no
branch holds, an `.env`, or a paused bisect; "still in flight" is normal
work. Show the user the "needs you" list and leave those trees alone: never
discard work to make sweep take them. Squash-merged branches look unmerged
to git and stay in flight; release them only once the user confirms they
are merged. `--detached` deletes detached checkouts rather than folding
them; add it only when the user asks to remove old detached checkouts.
