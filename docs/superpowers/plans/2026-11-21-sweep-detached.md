# sweep --detached Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `warmtree sweep --detached` removes hand-made worktrees that are ours, clean, detached, and whose HEAD is an ancestor of the sweep base ref; plain sweep instead prints a hint naming the flag.

**Architecture:** One new branch in `Pool.sweep`'s hand-made worktree loop (the `branch is None` case), a `detached: bool = False` parameter threaded from a new `--detached` CLI flag, and doc rows. No new git helpers: `worktree_remove`, `rev_parse`, `is_ancestor`, `head_branch`, and `is_dirty` all exist. The fold variant is rejected (IDEAS.md); `adopt` keeps refusing detached HEADs.

**Tech Stack:** Python 3.12 stdlib only; pytest against real temporary git repos; uv + ruff.

**Spec:** the Spec section below (approved short design from conversation).

## Spec

For each worktree in `git worktree list` that is not a pool slot and not the main checkout, is ours (`owns_worktree`), and has a detached HEAD:

- HEAD **not** an ancestor of the sweep base ref → `kept`, reason `detached HEAD` (today's behavior).
- HEAD an ancestor, **no** `--detached` → `kept`, reason `detached HEAD, merged into base (sweep --detached removes it)`; the CLI prints this hint.
- HEAD an ancestor, `--detached`, tree dirty → `kept`, reason `detached HEAD with uncommitted changes`.
- HEAD an ancestor, `--detached`, tree clean → `git worktree remove --force` (via `git.worktree_remove`; force deletes only ignored files — cleanliness is pre-checked), reported `removed`, reason `detached, merged`.

A worktree detached at base's current tip qualifies and is removed (decided). Pool slots and the main checkout are excluded by the existing `known` set. The sweep base ref remains `origin/<base>` when it exists (so `--fetch` makes upstream-only merges count). A `GitError` from removal becomes `skipped` with the error text; sweep continues.

## Global Constraints

- Runtime stdlib only (`dependencies = []`).
- `uv run pytest -q`, `uv run ruff check .`, **and** `uv run ruff format --check .` must pass (CI runs all three; 88-col lines).
- Must work on Windows, Linux, WSL.
- Boring, readable Python; small conventional commits (`feat:`/`test:`/`docs:`), plain messages — no co-author trailers, no session links.
- Tests and README are maintainer-reviewed: commit them on the feature branch as drafts and flag them as drafts in the PR body.
- No local machine paths in any committed file.
- The two SKILL.md copies (`warmtree/skills/warmtree/SKILL.md` and `.claude/skills/warmtree/SKILL.md`) stay byte-identical.

## Review Focus

1. **A registered worktree whose directory was deleted by hand** — `git worktree list` still lists it, and `owns_worktree` on a missing path raises `FileNotFoundError` (the PR #22 lesson), crashing sweep today. Guard with `path.is_dir()` → test in Task 1.
2. **Detached at a commit merged only upstream** — local base is stale; with `--fetch --detached` the removal must still happen because the base ref is `origin/<base>`. Test in Task 1.
3. **Detached at base's current tip** — qualifies for removal by design; pin it so the behavior is deliberate, not accidental. Test in Task 1.
4. **`worktree_remove` failing mid-sweep** (e.g. Windows file lock) — must produce `skipped` with the git error text and not abort the rest of the sweep. Test in Task 1 (monkeypatch `git.worktree_remove` to raise `GitError`).
5. **A detached pool slot must never be removed** — ready slots are detached by construction; they are excluded via `known`, and the test asserts a filled pool survives `sweep(detached=True)` untouched. Test in Task 1.

---

### Task 1: Pool layer — detached handling in sweep

**Files:**
- Modify: `warmtree/pool.py` (Swept comment at ~line 90; hand-made loop at ~lines 481–504; `sweep` signature and docstring at ~line 444)
- Test: `tests/test_sweep.py` (append)

**Interfaces:**
- Produces: `Pool.sweep(self, fetch: bool = False, detached: bool = False) -> list[Swept]`; new `Swept.action` value `"removed"`; the four exact reason strings from the Spec.

- [ ] **Step 1: Write the failing tests** (drafts for maintainer review). Create detached worktrees with `run_git("worktree", "add", "--detach", str(path), ref, cwd=repo)`:

```python
def test_sweep_removes_merged_detached_worktree_with_flag(repo):
    # detach at an ancestor of main (e.g. the initial commit); sweep(detached=True)
    [item] = Pool(repo, Config(size=0)).sweep(detached=True)
    assert (item.action, item.reason) == ("removed", "detached, merged")
    assert not path.exists()


def test_sweep_hints_at_detached_flag_without_it(repo):
    [item] = Pool(repo, Config(size=0)).sweep()
    assert item.action == "kept"
    assert (
        item.reason == "detached HEAD, merged into base (sweep --detached removes it)"
    )
    assert path.exists()


def test_sweep_keeps_unmerged_detached_worktree(repo):
    # detach at a commit NOT on main (commit on a branch, detach at its sha)
    [item] = Pool(repo, Config(size=0)).sweep(detached=True)
    assert (item.action, item.reason) == ("kept", "detached HEAD")


def test_sweep_keeps_dirty_merged_detached_worktree(repo):
    # write an untracked file into the detached tree first
    [item] = Pool(repo, Config(size=0)).sweep(detached=True)
    assert (item.action, item.reason) == (
        "kept",
        "detached HEAD with uncommitted changes",
    )
    assert path.exists()


def test_sweep_removes_detached_at_base_tip(repo):
    # detach at main's current tip; still removed
    [item] = Pool(repo, Config(size=0)).sweep(detached=True)
    assert item.action == "removed"


def test_sweep_never_touches_pool_slots(repo):
    pool = Pool(repo, Config(size=1))
    pool.fill()
    assert pool.sweep(detached=True) == []
    assert pool.status()[0].state == "ready"


def test_sweep_survives_a_worktree_whose_directory_vanished(repo):
    # hand-made worktree (branch or detached), then shutil.rmtree(path);
    # sweep() must return without raising and not list the ghost as removed
    results = Pool(repo, Config(size=0)).sweep()
    assert all(r.action != "removed" for r in results)


def test_sweep_detached_counts_upstream_merges_with_fetch(repo, tmp_path):
    # clone fixture per test_sweep_counts_branches_merged_only_upstream:
    # detach a clone worktree at a commit merged only on origin
    [item] = Pool(clone, Config(size=0)).sweep(fetch=True, detached=True)
    assert item.action == "removed"


def test_sweep_reports_a_failed_removal_and_continues(repo, monkeypatch):
    # monkeypatch warmtree.pool.git.worktree_remove to raise git.GitError("boom")
    [item] = Pool(repo, Config(size=0)).sweep(detached=True)
    assert (item.action, item.reason) == ("skipped", "boom")
    assert path.exists()
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_sweep.py -q -k "detached or vanished"`
Expected: FAIL — `sweep() got an unexpected keyword argument 'detached'` (and the hint test fails on the old reason string).

- [ ] **Step 3: Implement in `Pool.sweep`**

Add `detached: bool = False` keyword. In the hand-made loop: insert `if not path.is_dir(): continue` before `owns_worktree`; replace the `branch is None` block with the Spec's four-way decision (HEAD sha via `git.rev_parse(path, "HEAD")`, ancestry via `git.is_ancestor(self.repo_root, head, base_ref)`, removal in `try/except git.GitError` appending `skipped` with `str(exc)`). Extend the `Swept.action` comment to `released | folded | removed | kept | skipped`. Docstring gains one sentence on `--detached`.

- [ ] **Step 4: Run the sweep suite**

Run: `uv run pytest tests/test_sweep.py -q`
Expected: all pass, including the pre-existing tests.

- [ ] **Step 5: Commit**

```bash
git add warmtree/pool.py tests/test_sweep.py
git commit -m "feat: sweep --detached removes merged detached checkouts"
```

### Task 2: CLI — flag, output, hint

**Files:**
- Modify: `warmtree/cli.py` (sweep parser at ~lines 193–211; `cmd_sweep`)
- Test: `tests/test_sweep.py` (append)

**Interfaces:**
- Consumes: Task 1's `sweep(fetch=..., detached=...)` and reason strings.

- [ ] **Step 1: Write the failing test** (draft):

```python
def test_cli_sweep_prints_removed_and_hint(repo, capsys, monkeypatch):
    # one merged detached worktree; monkeypatch.chdir(repo)
    from warmtree.cli import main

    assert main(["sweep"]) == 0
    assert "sweep --detached removes it" in capsys.readouterr().err
    assert main(["sweep", "--detached"]) == 0
    out = capsys.readouterr().out
    assert "removed: " in out and "(detached, merged)" in out
    assert "removed 1" in out
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_sweep.py -q -k cli_sweep`
Expected: FAIL — `unrecognized arguments: --detached`.

- [ ] **Step 3: Implement**

Parser: `sweep.add_argument("--detached", action="store_true", help="also remove clean detached worktrees whose commit is merged into base")`; append to the description: `"With --detached, clean hand-made worktrees parked on a detached HEAD whose commit is an ancestor of base are removed."` In `cmd_sweep`: pass `detached=args.detached`; print `removed` items as `print(f"removed: {item.name} ({item.reason})")`; print the hint via `note(f"kept {item.name}: {item.reason}")` for kept items whose reason contains `"sweep --detached"`; count removed and extend the summary line with `"; removed {n}"`-style segment only when `n > 0` (exact wording: `swept {folded} into the pool; removed {removed}; kept {kept} in flight; skipped {skipped}`).

- [ ] **Step 4: Run the suite**

Run: `uv run pytest tests/test_sweep.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add warmtree/cli.py tests/test_sweep.py
git commit -m "feat: --detached flag and removal reporting on sweep"
```

### Task 3: Docs, skill, final verification

**Files:**
- Modify: `README.md` (sweep row, ~line 9 of the command table), `warmtree/skills/warmtree/SKILL.md` + `.claude/skills/warmtree/SKILL.md` (sweep row at ~line 182), `warmtree/skill.py` (SHIPPED_SKILL_HASHES)

**Interfaces:** none produced; consumes nothing beyond the shipped behavior.

- [ ] **Step 1: README** (draft for maintainer review): in the sweep row, synopsis becomes `warmtree sweep [--fetch] [--detached]` and append: `--detached also removes clean hand-made worktrees parked on a detached HEAD whose commit is already an ancestor of base — validation checkouts of merged PRs; without the flag, sweep prints a hint when it sees one.`

- [ ] **Step 2: SKILL.md** (both copies, identical): sweep quick-reference row becomes `warmtree sweep [--fetch] [--detached]` with the cell text extended by `--detached also removes clean detached checkouts already merged into base.`

- [ ] **Step 3: skill.py hash**: append the new SKILL.md hash to `SHIPPED_SKILL_HASHES` with comment `# unreleased`, computed with the same hash function skill.py already uses (the existing hash-registry test names it; run that test to get the expected value if needed).

- [ ] **Step 4: Verify copies identical and full gate**

Run: `cmp warmtree/skills/warmtree/SKILL.md .claude/skills/warmtree/SKILL.md && uv run pytest -q && uv run ruff check . && uv run ruff format --check .`
Expected: cmp silent; all tests pass; both ruff commands clean.

- [ ] **Step 5: Commit**

```bash
git add README.md warmtree/skills/warmtree/SKILL.md .claude/skills/warmtree/SKILL.md warmtree/skill.py
git commit -m "docs: document sweep --detached and refresh the skill hash"
```
