# sweep: squash-merged branches and nested worktrees — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `warmtree sweep` frees slots whose branch was squash-merged, and removes worktrees nested inside slots when removing them cannot lose work made in them.

**Architecture:** Two new git predicates in `warmtree/git.py` (a whole-branch patch-id match, and "nothing happened in this worktree since it was created"). `Pool.sweep` in `warmtree/pool.py` gains a nested pass per slot, run before the slot's release decision, and a squash fallback after the ancestor check. CLI output, `--help`, README, and both SKILL.md copies change in the last task.

**Tech Stack:** Python 3.12 stdlib, git CLI, pytest with real temporary repos.

**Spec:** `docs/superpowers/specs/2026-11-25-sweep-squash-and-nested-design.md`

## Global Constraints

- Runtime is standard library only; every decision is plain git: offline, no `gh`, no tokens.
- A false "not landed" (slot stays taken, tree stays) is acceptable; a false "landed" that loses work is not.
- Worktrees outside the pool directory keep today's behavior; `--detached` still governs those.
- Squash-landed slots are released, their branch is kept; output says so. Reason text: `squash-merged as <short sha>`.
- Nested trees are handled in default sweep (no flag). Recency window is the existing `DETACHED_IDLE_DAYS` (14).
- `git worktree remove --force` (what `git.worktree_remove` runs) deletes ignored and untracked files: every guard must run before it.
- Must pass on ubuntu-latest and windows-latest. `uv run pytest -q` and `uv run ruff check .` green after every task.
- Commits: conventional, plain, no trailers or session links. No local machine paths, no dogfood repo name in any committed file.
- Tests and README are maintainer-owned: they ship in this PR flagged as drafts for his review.

## Review Focus

1. A branch whose diff contains a binary file: two different binary edits print identical "Binary files differ" text, so the fingerprint must include contents (`--binary --full-index`) or a different binary change could match.
2. User git config changing diff output (`color.ui=always`, `diff.external`, textconv, `diff.renames`): both sides of the comparison must be rendered with the same explicit flags (`--no-color --no-ext-diff --no-textconv --no-renames`).
3. A branch whose net change since the merge base is empty (reverted inside the branch): must not match anything; return None.
4. A repo with reflogs disabled (`core.logAllRefUpdates=false`): an empty HEAD history proves nothing, so the nested tree is kept, not removed.
5. A nested tree inside an adopted slot that lives outside the pool directory: handled by the nested pass, never also by the hand-made pass (which would fold or remove it under different rules).

---

### Task 1: `git.squash_landed`

**Files:**
- Modify: `warmtree/git.py` (new function after `is_ancestor`)
- Test: `tests/test_squash.py` (create)

**Interfaces:**
- Produces: `squash_landed(repo: Path, branch: str, base_ref: str) -> str | None` — the short sha of the commit on `base_ref` that holds the branch's whole change, or None.

Algorithm (not determined by the signature):
1. `mb = merge-base branch base_ref`.
2. `paths = diff --name-only -z mb branch`; if empty, return None.
3. Common flags `F = -U0 --no-color --no-ext-diff --no-textconv --no-renames --binary --full-index`.
4. Branch fingerprint: `diff F mb branch`, piped to `patch-id --stable`; take the first field.
5. Candidates: `log -p F --full-diff --format=commit %H mb..base_ref -- <paths>` piped to `patch-id --stable`; each line is `<patch-id> <commit>`.
6. Return `rev-parse --short <commit>` of the first candidate whose patch-id equals the branch's, else None.

`run` strips stdout; append `"\n"` when feeding it as stdin.

- [ ] **Step 1: Write the failing tests** in `tests/test_squash.py`, with a helper `squash_merge(repo, branch)` that does `checkout main; merge --squash branch; commit -m "squash"` and returns the new main sha:

```python
def test_a_squash_merged_branch_is_found(repo):          # 3 commits on feat, squash → returns short sha of main tip
def test_an_unmerged_branch_is_not_found(repo):          # feat never merged → None
def test_a_commit_after_the_squash_is_not_found(repo):   # squash, then one more commit on feat → None
def test_base_moving_beside_the_change_still_matches(repo):
    # feat edits line 2 of a 10-line file; main later edits line 9 before the squash
    # is applied (squash after main's edit) → still found
def test_an_empty_net_change_is_not_found(repo):         # feat adds then reverts a file → None
def test_a_different_binary_change_does_not_match(repo):
    # main gets a commit writing bytes b"\x00A" to blob.bin; feat (from before it) writes b"\x00B" → None
def test_user_diff_config_does_not_hide_a_squash(repo):
    # git config color.ui always + diff.renames copies, then squash case → found
```

- [ ] **Step 2: Run** `uv run pytest tests/test_squash.py -q` — Expected: FAIL, `AttributeError: ... squash_landed`.
- [ ] **Step 3: Implement** `squash_landed` per the algorithm above.
- [ ] **Step 4: Run** `uv run pytest tests/test_squash.py -q` — Expected: 7 passed.
- [ ] **Step 5: Commit** `test: squash-merged branches are found by whole-branch patch-id` then `feat: git.squash_landed matches a branch's whole change on base` (two commits, test first).

### Task 2: `git.untouched_since_created`

**Files:**
- Modify: `warmtree/git.py` (after `has_stranded_commits`)
- Test: `tests/test_git.py`

**Interfaces:**
- Produces: `untouched_since_created(path: Path) -> bool` — True only when `reflog show HEAD` in the worktree has exactly one entry and `for-each-ref refs/worktree/` in it is empty. Zero entries (reflogs off) is False.

- [ ] **Step 1: Write the failing tests** in `tests/test_git.py`:

```python
def test_a_fresh_detached_worktree_is_untouched(repo):       # worktree add --detach → True
def test_a_commit_inside_is_not_untouched(repo):             # + commit --allow-empty → False
def test_a_checkout_inside_is_not_untouched(repo):           # created at main~1, then checkout --detach main → False
def test_a_worktree_ref_is_not_untouched(repo):              # + update-ref refs/worktree/keep HEAD → False
def test_no_reflog_is_not_untouched(repo):                   # config core.logAllRefUpdates false before add → False
```

- [ ] **Step 2: Run** `uv run pytest tests/test_git.py -q -k untouched` — Expected: FAIL, AttributeError.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** same — Expected: 5 passed.
- [ ] **Step 5: Commit** test, then `feat: git.untouched_since_created`.

### Task 3: sweep releases squash-landed slots

**Files:**
- Modify: `warmtree/pool.py` — `Pool.sweep` taken-slot branch (around the `if not merged(slot.branch)` line), docstring
- Test: `tests/test_sweep_squash.py` (create)

**Interfaces:**
- Consumes: `git.squash_landed` (Task 1).
- Produces: inside `sweep`, a local `landed(branch) -> str | None` returning `"merged"` (ancestor), `f"squash-merged as {sha}; branch kept"`, or None. Task 4 uses it.

Behavior: for a taken slot that is not an ancestor but `squash_landed` matches, dry run uses `_release_refusal` exactly like the merged case; a real run calls `self.release(slot.branch, keep_branch=True)` and does **not** call `_certified_delete`. Result `Swept(slot.name, branch, "released", "squash-merged as <sha>; branch kept")`. Hand-made worktrees (outer loop) keep the ancestor-only `merged()` test.

- [ ] **Step 1: Write the failing tests:**

```python
def test_sweep_releases_a_squash_merged_slot_and_keeps_its_branch(repo):
    # take feat, commit in slot, squash into main → result action "released",
    # reason startswith "squash-merged as ", slot state "ready", git.branch_exists(repo, "feat")
def test_sweep_keeps_a_slot_with_work_after_the_squash(repo):      # extra commit → "kept", "not merged"
def test_dry_run_predicts_the_squash_release(repo):                # dry_run result == real result; slot still taken after dry run
def test_sweep_skips_a_dirty_squash_merged_slot(repo):             # uncommitted file → "skipped", state taken
def test_sweep_leaves_a_hand_made_squash_merged_worktree(repo):    # hand-made wt on squashed branch → "kept", "not merged"
def test_squash_release_reason_groups_as_clean(repo):              # .group == "clean"
```

- [ ] **Step 2: Run** `uv run pytest tests/test_sweep_squash.py -q` — Expected: FAIL (kept "not merged").
- [ ] **Step 3: Implement**; update the docstring sentence about squash merges.
- [ ] **Step 4: Run** `uv run pytest -q` — Expected: all pass.
- [ ] **Step 5: Commit** test, then `feat: sweep releases slots whose branch was squash-merged, keeping the branch`.

### Task 4: sweep handles worktrees nested in slots

**Files:**
- Modify: `warmtree/pool.py` — new `_nested_blocker`, `Swept.group`, `Pool.sweep`
- Test: `tests/test_sweep_nested.py` (create)

**Interfaces:**
- Consumes: `git.untouched_since_created` (Task 2), `landed` (Task 3).
- Produces:
  - `_nested_blocker(repo: Path, path: Path) -> str | None`: `"has history beyond its creation"` when not `untouched_since_created`, else `_detached_blocker(repo, path)`.
  - Nested results: `Swept(str(path), git.head_branch(path), action, reason)` with action `removed` / `kept` / `skipped`; removed reason `f"nested in {slot.name}, untouched since created"`; kept reason `f"nested in {slot.name}, but {blocker}"`.
  - A landed slot that keeps a nested tree: `Swept(slot.name, branch, "kept", f"{why}, but nested worktree {path} {blocker}")`.
  - `Swept.group`: for `kept`, a reason containing `"nested "` is in-flight when it ends with `f"within the last {DETACHED_IDLE_DAYS} days"`, needs-you otherwise.

Sweep flow change: compute `worktree_list` once; a registered worktree whose resolved path is inside a known slot's resolved path (and is not that path) belongs to that slot. Before each slot's own decision (any state), run the nested pass for its trees: skip missing directories and trees `owns_worktree` rejects; on a blocker (or `GitError` while inspecting → `"could not be inspected: …"`) keep; else dry run reports `removed`, real run calls `git.worktree_remove` (on `GitError` → `skipped`). The trees that stayed are the slot's keepers; a taken slot whose branch `landed` but has keepers is not released. The hand-made pass excludes every path that belongs to a slot.

- [ ] **Step 1: Write the failing tests**, with helper `nested(slot_path, name="r", untouched=True)` that runs `worktree add --detach <slot>/.loop/reviews/<name> HEAD` from the repo, adds `.loop/` to the repo's `.git/info/exclude`, and backdates with `idle()` (import from `tests.test_sweep`):

```python
def test_sweep_removes_an_idle_untouched_tree_nested_in_a_ready_slot(repo)   # removed; not in worktree_list; folder gone
def test_sweep_removes_a_nested_tree_in_a_taken_unmerged_slot(repo)          # removed; slot stays taken
def test_sweep_keeps_a_nested_tree_with_a_commit_made_inside(repo)           # kept, "has history beyond its creation", group needs-you
def test_sweep_keeps_a_dirty_nested_tree(repo)                               # kept, "has uncommitted changes"
def test_sweep_keeps_a_nested_tree_holding_an_env_file(repo)                 # ignored .env inside → kept, needs-you
def test_sweep_keeps_a_recent_nested_tree_as_in_flight(repo)                 # untouched=False → group in-flight
def test_sweep_does_not_release_a_merged_slot_holding_a_tree_that_stays(repo)
    # merged slot + recent nested tree → slot "kept", state taken, group in-flight
def test_sweep_releases_a_merged_slot_once_its_nested_trees_are_removed(repo)
def test_dry_run_predicts_nested_removal_and_changes_nothing(repo)
def test_a_nested_tree_in_an_adopted_slot_outside_the_pool_is_handled_as_nested(repo)
    # adopt a hand-made wt on an unmerged branch (it stays outside the pool dir); nest an idle
    # untouched tree inside it → exactly one result for that path, reason startswith "nested in slot-"
def test_sweep_reports_a_failed_nested_removal_and_carries_on(repo, monkeypatch)  # worktree_remove raises → skipped
def test_sweep_keeps_a_nested_tree_when_reflogs_are_off(repo)                # core.logAllRefUpdates false → kept
```

- [ ] **Step 2: Run** `uv run pytest tests/test_sweep_nested.py -q` — Expected: FAIL (nested trees never appear in results).
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `uv run pytest -q` — Expected: all pass, including the existing `test_sweep_never_touches_pool_slots` and `test_sweep_detached_never_removes_an_orphaned_pool_slot`.
- [ ] **Step 5: Commit** test, then `feat: sweep removes untouched worktrees nested in slots and lists the rest`.

### Task 5: output and docs

**Files:**
- Modify: `warmtree/cli.py` (`cmd_sweep`, `print_sweep_plan`, sweep parser help), `README.md` (sweep row and walkthrough), `warmtree/skills/warmtree/SKILL.md` and `.claude/skills/warmtree/SKILL.md` (identical), `warmtree/skill.py` (`# unreleased` hash)
- Test: `tests/test_sweep_squash.py`, `tests/test_sweep_nested.py`

Output decisions:
- Real sweep prints `released: <slot> (<branch>): squash-merged as <sha>; branch kept` for squash releases (plain merged lines unchanged), `removed: <path> (<reason>)` for nested removals (existing line), and a `note` `kept <name>: <reason>` for every kept result whose reason contains `"nested "`.
- Dry-run "still in flight" hint becomes: `(a squash merge edited while merging looks unmerged; release those yourself once you confirm)`.
- Help, README, skill: sweep detects squash merges by matching the branch's whole change on base and keeps the branch; it removes worktrees nested in slots when nothing was done in them since creation, they are clean, unlocked, hold no nested repo or hand-made ignored files, and have been idle 14 days; the rest appear under "needs you" or "still in flight"; a merged slot holding one that must stay is not released. Drop "Squash-merged branches are not detected". New skill troubleshooting rows: `kept <path>: nested in <slot>, but <reason>` and `released: <slot> (<branch>): squash-merged as <sha>; branch kept`. Credit is in the PR body, not the docs.

- [ ] **Step 1: Write the failing CLI tests:** `test_cli_sweep_says_a_squash_merged_branch_was_kept` (capsys contains `squash-merged as` and `branch kept`), `test_cli_sweep_explains_a_nested_tree_it_kept` (stderr contains `kept ` and `nested in slot-1, but`).
- [ ] **Step 2: Run** them — Expected: FAIL.
- [ ] **Step 3: Implement** the output, then the docs; copy SKILL.md to `.claude/skills/warmtree/SKILL.md`; set the `# unreleased` hash to `sha256sum warmtree/skills/warmtree/SKILL.md`.
- [ ] **Step 4: Run** `uv run pytest -q && uv run ruff check . && cmp warmtree/skills/warmtree/SKILL.md .claude/skills/warmtree/SKILL.md` — Expected: all pass, no output from cmp.
- [ ] **Step 5: Commit** `feat: sweep output names squash merges and nested trees it kept`, then `docs: sweep squash detection and nested worktrees in help, README, and skill`.

### After the tasks

Draft PR on `feat/sweep-squash-nested`, body credits #23 and flags the tests and README as drafts for the maintainer. Then the CLAUDE.md review: two reviewers in parallel (checklist on Opus with an attack list built from the Review Focus above plus "can any path reach `worktree_remove` without every guard"; open on Fable with the goal only), [REAL] findings fixed test-first, at most two rounds, each recorded in a PR comment.
