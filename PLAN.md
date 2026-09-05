# warmtree design

> `git worktree add` is fast. Everything after it is slow: dependency install,
> build caches, untracked env files. warmtree keeps a few worktrees already
> checked out, fast-forwarded, and warmed, so `warmtree take <branch>` hands
> you a ready workspace in under a second. Built for parallel AI coding agents.

Language: Python 3.12, standard library only.

## Positioning

The worktree-tooling space is crowded. worktrunk (Rust) already does on-create
hooks, copies ignored files, allocates ports, and cleans up merged worktrees.
Several smaller tools cover env copying and ports on their own. **No tool
pre-creates warm worktrees.** The pool is the entire contribution, and the
scope is held to it on purpose.

warmtree owns the **create** and **release** steps. Everything in between is
ordinary git, so worktrunk, portree, global git hooks, and editors all work on
a pooled worktree unchanged. The README documents two workflows: standalone,
and warmtree-for-create plus worktrunk-for-everything-else.

## Rules

- Standard library only. No runtime dependencies. `uvx warmtree` must work
  on a machine with nothing but Python and git.
- Stack-agnostic. warmtree never knows what npm or dotnet are. Projects
  declare their own warm commands.
- Boring, readable Python. Every line should be explainable to a reviewer.
- Tests run against real temporary git repos. Nothing about git is mocked.

## v1 scope

### Config: `.warmtree.toml` at the repo root (parsed with `tomllib`)

```toml
[pool]
size = 3                 # slots to keep ready
base = "main"            # branch slots are parked on
dir = "../.warmtree/app" # where slots live; default is ../.warmtree/<repo name>
lockfiles = ["package-lock.json", "uv.lock"]  # re-warm only when these change

[warm]
run = ["npm ci"]         # executed in the slot at fill/refresh time
copy = [".env", ".env.local"]  # untracked files copied from the main worktree
env = true               # write WARMTREE_SLOT=<n> into copied env files
```

Every key has a default; an empty file means "pool of 2 on the default
branch, fast-forward only."

### Commands
| Command | Behavior |
|---|---|
| `warmtree init` | Write a starter config. Detects common lockfiles only to pre-fill `lockfiles`; never guesses `run`. Installs the agent skill for any coding agent the repo shows signs of. |
| `warmtree skill [--tool T] [--force]` | Install or refresh the agent skill in detected or named tool folders. |
| `warmtree fill` | Create missing slots with `git worktree add --detach`, run `copy`, run `run`, record lockfile hashes. |
| `warmtree take <branch> [--from <ref>]` | Claim the oldest ready slot: create or check out `<branch>` inside it, print its path, mark it taken. Refill the pool afterward (`--no-refill` to skip; `--refill-background` spawns a detached process). If no slot is ready, fall back to a cold create and say so. |
| `warmtree release <branch>` | Reset the slot to `base`, discard the working tree (`--keep-branch` keeps the branch ref), return the slot to ready. Refuses if the tree is dirty unless `--force`. |
| `warmtree refresh` | Fast-forward every ready slot to `base`; if any lockfile hash changed, re-run `run` and `copy` in that slot. Meant for a nightly scheduled task or cron. |
| `warmtree status` | Table of slots: path, state (ready/taken/warming/stale), branch, age, last warm. `--json` for agents. |
| `warmtree size [N]` | Show configured size and counts by state. With `N`, write the size to the config and grow or shrink the pool to match; shrinking removes waiting slots only. |
| `warmtree which` | Name the slot the current directory is inside, so an agent can tell whether it already has a workspace. |
| `warmtree remove [SLOT...] [--all]` | Delete slots and their worktree registrations. |

### State
- `state.json` inside the pool dir: slot list with state, branch,
  created/warmed timestamps, lockfile hashes. One file, rewritten atomically.
- Concurrency: `take` holds an OS file lock (`msvcrt` on Windows, `fcntl`
  elsewhere) for the claim. Two agents calling `take` at once get two
  different slots.

### Agent integration
- `warmtree/skills/warmtree/SKILL.md` in the open Agent Skills format (read by
  Claude Code, GitHub Copilot, Codex CLI, Cursor), shipped inside the package.
  `warmtree init` copies it into the skills folder of every tool the repo
  shows signs of using (`CLAUDE.md`, `copilot-instructions.md`, `AGENTS.md`,
  `.cursor/`); `warmtree skill` does the same on demand and never overwrites
  an edited copy without `--force`. The rule: when an isolated workspace is needed,
  run `warmtree take <branch>` and `cd` into the printed path instead of
  `git worktree add`; when done and merged, `warmtree release <branch>`.
  Before that, `warmtree which` tells the agent whether it is already in a
  slot, and `warmtree size` tells it whether enough slots are ready for the
  parallel work it is about to start, and grows the pool if not.
- `AGENTS.md` paragraph in the README for projects that use that convention.
- Note in the README that Claude Code's native worktree feature is not
  intercepted; the skill is the integration.

### Out (v1)
- Port allocation beyond the `WARMTREE_SLOT` variable. Subdomain routing.
  Merging, PR creation, branch listing. Any GUI or TUI. Daemon mode. Shared
  dependency stores. Windows junction tricks for `node_modules`.

## Architecture

```
warmtree/
  __init__.py
  cli.py        argparse entry point, one function per command
  config.py     load/validate .warmtree.toml, defaults
  git.py        thin subprocess wrapper: worktree add/remove, fetch, ff, status
  pool.py       slot lifecycle: fill, take, release, refresh, state persistence
  lock.py       cross-platform file lock
  warm.py       run commands, copy files, hash lockfiles, inject WARMTREE_SLOT
  skill.py      detect coding-agent folders, install the bundled SKILL.md
  skills/warmtree/SKILL.md
tests/          pytest against real temporary git repos (no mocks of git)
```

Key decisions and the reasoning behind each:
- **Slots are detached worktrees parked on `base`.** A branch is only
  created at `take`, so a slot is never "on" a branch anyone else is using,
  and git's one-branch-per-worktree rule never bites the pool.
- **Reset on release, do not delete.** Deleting and recreating throws away
  the warm state; resetting keeps `node_modules` and friends intact. Lockfile
  hashes decide whether a re-warm is needed.
- **Fallback to cold create.** An empty pool is a slow path, never an error.
  Agents should never fail because the pool drained.
- **State in one JSON file with a lock, not SQLite.** Dozens of slots at
  most; simplest thing that is correct under concurrent `take`.
- **Standard library only.** The tool has to run before the project's own
  dependencies exist; that is the whole point.

## Build order

| # | Deliverable | Done when |
|---|---|---|
| 1 | config, git wrapper, `init`, `fill`, `status`, tests on temp repos, CI on ubuntu + windows | `fill` creates N detached slots in a temp repo; CI green on both OSes |
| 2 | `take` with file lock and refill, `release` with reset, `remove`, fallback path | Two concurrent `take` calls get two slots (test); `release` returns a slot to ready |
| 3 | `warm`: run commands, copy files, lockfile hashing, `refresh`, `WARMTREE_SLOT` | Changing a lockfile makes `refresh` re-run `run` in every slot, unchanged lockfile skips |
| 4 | SKILL.md, AGENTS.md, README with GIF and worktrunk workflow, trusted publishing to PyPI, tag v0.1.0 | `uvx warmtree status` works on a clean machine; Claude Code takes a slot via the skill |

## Acceptance criteria (v0.1.0)
- 25+ tests against real git repos, green in CI on Ubuntu and Windows.
- `take` returns a ready slot in under 1 second on a repo whose `npm ci` takes minutes (measured in the README).
- Concurrency test in step 2 passes.
- Zero runtime dependencies; `pyproject.toml` `dependencies = []`.
- PyPI release published from GitHub Actions via trusted publishing; no API token stored locally.

## Before publishing
Use it on a large real repo with `size = 2` for a week before the first
release. Every papercut found goes in IDEAS.md; only bugs are fixed before
v0.1.0.
