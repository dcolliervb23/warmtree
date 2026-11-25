# sweep: squash-merged branches and worktrees nested in slots

## Why

Sweep leaves finished work stranded in two ordinary cases, and the user is
left to find and clean it up by hand:

- **Squash merges.** A branch squash-merged on the forge is not an ancestor
  of base, so sweep reports it "not merged" and its slot stays taken. In
  the dogfood repo, two slots held branches merged weeks earlier (#853,
  #763). Their remote branches had been deleted, too.
- **Worktrees nested inside a slot.** Agent tooling (a review loop) creates
  worktrees inside the slot it runs in, e.g. `slot-9/.loop/reviews/<sha>-tree`.
  Sweep skips everything under the pool directory, so it never sees them;
  release keeps them, because the folder is git-ignored. One slot had
  accumulated 33, about 1.2 GB, from every PR it ever hosted.

## Goals

- Sweep frees a slot whose branch landed through a squash merge.
- Sweep removes worktrees nested in slots when removing them cannot lose
  work made in them, and lists the rest under "needs you".
- Every decision is plain git: deterministic, offline, no forge login, no
  model judgement.

## Non-goals and anti-goals

- No forge queries (`gh`, API tokens). They were considered and rejected:
  the git-only check gives the same answer for the observed cases.
- Worktrees outside the pool directory are unchanged; `--detached` still
  governs those.
- Never delete a tree with uncommitted changes, a paused operation,
  hand-made ignored files, or commits made inside it. A false "not landed"
  (slot stays taken) is acceptable; a false "landed" that loses work is not.

## Approaches considered

1. **Ask the forge** (`gh pr list --state merged --head <branch>`, compare
   the PR's head commit to the branch tip). Exact, but needs `gh` and a
   login. Rejected by the maintainer: no tokens.
2. **Per-commit patch-id** (`git cherry`). Fails on squash merges: a squash
   folds many commits into one, so most have no equivalent (8 of 10 for
   #853).
3. **Three-way merge-tree** (does merging the branch into base change
   base?). Fails once base has moved on and touched the same files.
4. **Whole-branch patch-id** (chosen). The fingerprint of the branch's
   whole change since its merge base equals the fingerprint of the squash
   commit on base. Verified: matches #853 and #763, no match for an open PR.

## Design

### "Landed", for a branch

A branch has landed when it is an ancestor of the sweep base ref (today's
rule), or when some commit on base after the merge base has the same
`git patch-id --stable` as `git diff -U0 <merge-base> <branch tip>`. The
search is limited to base commits that touch the files the branch changed
(`git log -p -U0 --full-diff <merge-base>..<base> -- <paths>`), so it
stays cheap on large repositories.

- `-U0`: no context lines, so base moving around the change does not
  alter the fingerprint.
- `--full-diff`: the paths select commits but each commit's whole diff is
  fingerprinted. Without it, a path-limited log can show only part of the
  squash commit, and the fingerprints differ.

Evidence from the dogfood repo (2026-11-25): of 29 local branches git
sees as unmerged, GitHub merged 22 at their exact tips. This check caught
all 22 and flagged none of the other 7.

Only a forge could catch a squash merge edited during the merge (conflict
fixes): base then holds a different change, and the slot stays taken for
the user to release. Add a forge fallback only if that shows up in use.

- A miss (the squash needed conflict fixes) leaves the slot taken: safe.
- A match proves base already holds that exact change.
- A branch with commits after the squashed version has a different
  fingerprint, so it does not match.

Sweep releases a taken slot whose branch landed this way, with the reason
"squash-merged as <short sha>". It keeps the branch itself (release only
deletes branches git certifies as merged); the output says so. *Builder's
call, open to the maintainer: deleting squash-landed branches would lose
their individual commits' history.*

### Worktrees nested in a slot

For every slot, sweep looks at registered worktrees whose folder is inside
that slot's folder. One is removed (`git worktree remove`) when all hold:

- no uncommitted changes, not locked, no bisect, rebase, or merge paused;
- no submodule or nested repository;
- no ignored files other than whole dependency, cache, and build folders
  (the existing `--detached` rule);
- **nothing was ever committed inside it**: its own HEAD history records
  only its creation, and it has no `refs/worktree/*` refs.

Otherwise it is listed under "needs you" with the reason. The commit a
removed tree pointed at is not deleted by the removal. When it is held by
no ref, its last pointer may go with the tree; the maintainer accepted
this, because nothing was committed in that tree.

The `--detached` recency rule (HEAD moved within 14 days) applies too, so a
review in progress is not removed from under its tool.

This runs in default sweep, not behind `--detached`: these trees are inside
pool property, and the guards are the strictest sweep has. *Builder's call.*

A slot whose branch landed but which still holds a nested tree that must
stay is not released; it is listed under "needs you" with that tree.

### Output and dry run

`--dry-run` lists nested trees in its existing groups: "would clean up"
or "needs you". Squash-landed slots appear under "would clean up" with
their squash commit.

### Docs

`--help`, README (the sweep row, which today says squash merges are not
detected), the shipped skill (guidance and troubleshooting rows), and the
skill hash, per CLAUDE.md.

## Relation to #23

The outside contribution in #23 proposed squash detection by patch-id over
full history, always on. This design takes the same idea in a bounded,
whole-branch form; the PR should credit #23 and say so there.

## Left to the builder

Function names and structure, how nested trees are grouped in the output,
and the exact log wording.
