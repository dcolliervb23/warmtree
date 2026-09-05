# warmtree

A pool of pre-warmed git worktrees for parallel AI coding agents.

`git worktree add` is fast. Everything after it is slow: installing
dependencies, rebuilding caches, copying the `.env` files git does not track.
On a large repo that cold start is minutes, and with several agents working in
parallel you pay it several times a day. warmtree keeps a few worktrees already
checked out, warmed, and ready, so `warmtree take <branch>` hands you a
workspace in under a second.

warmtree owns the **create** and **release** steps only. Everything in between
is ordinary git, so your editor, hooks, and other worktree tools work on a
pooled worktree unchanged.

## Status

Early. Stable enough to dogfood, not yet on PyPI.

| Works today | Not yet |
|---|---|
| `init`, `fill`, `take`, `release`, `refresh`, `remove`, `status` | `SKILL.md` for agents |
| Slots warmed with your own `run` commands and copied `.env` files | PyPI release, `uvx warmtree` |
| `refresh` fast-forwards slots and re-warms only when a lockfile changed | |
| File-locked `take`, safe for concurrent agents | |
| Cold-create fallback when the pool is empty | |

## Install

Requires Python 3.12+ and git. No other runtime dependencies.

From a clone, for dogfooding while the code is still changing:

```sh
uv tool install --editable .
```

Or straight from GitHub:

```sh
uv tool install git+https://github.com/dcolliervb23/warmtree
```

Once published, `uvx warmtree` will work on a machine with nothing but Python
and git.

## Quick start

Run these from inside the repo you want to pool.

```sh
warmtree init          # writes .warmtree.toml with defaults
# edit .warmtree.toml: add your install command to [warm] run
warmtree fill          # creates and warms the slots, two by default
warmtree status        # see them
```

Take a slot when you need an isolated workspace, work in it, then release it:

```sh
cd "$(warmtree take feature/login)"   # bash / zsh
# ... commit, push, open a PR ...
warmtree release feature/login
```

PowerShell:

```powershell
cd (warmtree take feature/login)
warmtree release feature/login
```

`take` prints only the slot path on stdout. Everything else it says goes to
stderr, so the `cd` idiom works.

Keep slots current with a nightly `warmtree refresh` from cron or Task
Scheduler, after whatever pulls your base branch.

## Commands

| Command | What it does |
|---|---|
| `warmtree init [--force]` | Write a starter `.warmtree.toml`. Pre-fills `lockfiles` from what it finds in the repo. Never guesses your install command. |
| `warmtree fill` | Create slots until `size` are ready. Each slot is a worktree with a detached HEAD at the base branch, with `copy` files copied in and `run` commands executed. |
| `warmtree take <branch> [--from REF]` | Claim the oldest ready slot. Creates `<branch>` there (from `REF` or the base branch) or checks it out if it already exists. Prints the path, then refills the pool. |
| `warmtree take ... --no-refill` | Skip the refill. |
| `warmtree take ... --refill-background` | Refill in a detached process and return immediately. |
| `warmtree release <branch> [--keep-branch] [--force]` | Park the slot back on the base branch and mark it ready. Refuses a dirty tree unless `--force`. Deletes the branch if it is merged; an unmerged branch is always kept. |
| `warmtree refresh` | Move every waiting slot to the current base commit and re-copy files. Re-runs `run` only in slots whose lockfile hashes changed or whose last warm failed. Skips taken slots. |
| `warmtree remove [SLOT...] [--all] [--force]` | Delete slots and their worktree registrations. Taken slots need `--force`. |
| `warmtree status [--json]` | Table of slots: name, state, branch, age, last warm, path. `--json` for scripts and agents. |

Slot states:

- `ready`: parked on base, warm, free to take.
- `taken`: a branch is checked out and someone is working in it.
- `warming`: `run` commands are executing right now, in `fill` or `refresh`.
- `stale`: the last warm failed. `refresh` retries it. `take` never hands out a
  stale slot.

If no slot is ready, `take` falls back to a normal `git worktree add`, tells
you on stderr, and the new worktree joins the pool as a taken slot. An empty
pool is a slow path, never an error.

## Configuration

`.warmtree.toml` at the repo root. Every key has a default; an empty file or
no file at all means a pool of two on the repo's default branch with nothing
to warm.

```toml
[pool]
size = 2                       # slots to keep ready; taken slots do not count
# base = "main"                # branch slots park on; default: the repo's default branch
# dir = "../.warmtree/app"     # where slots live; default: ../.warmtree/<repo name>
lockfiles = ["package-lock.json", "uv.lock"]  # re-warm only when one of these changes

[warm]
run = ["npm ci"]               # commands run inside a slot at fill and refresh time
copy = [".env", ".env.local"]  # untracked files copied from the main worktree
env = true                     # write WARMTREE_SLOT=<n> into the copied env files
```

Details worth knowing:

- `run` commands go through the shell, in order, inside the slot, with
  `WARMTREE_SLOT=<n>` in the environment. Output is captured and shown only
  when a command fails. warmtree has no idea what npm or dotnet are; you do.
- `copy` paths are relative to the repo root. Files missing from the main
  worktree are skipped. With `env = true`, copied files named like `.env`,
  `.env.local`, or `app.env` get a `WARMTREE_SLOT=<n>` line appended, so your
  project can derive a per-slot port or database name from it.
- `lockfiles` are hashed inside the slot after each warm. `refresh` re-runs
  `run` only when a hash differs.
- Unknown keys are errors, so a typo never silently disables warming.

## How it works

- **Slots are detached worktrees parked on the base branch.** A branch is only
  created at `take`, so a slot is never on a branch someone else is using and
  git's one-branch-per-worktree rule never bites the pool.
- **Release resets, it does not delete.** `release` checks out the base
  branch detached and runs `git clean -fd`, which removes untracked files but
  keeps ignored ones. `node_modules`, `.venv`, and friends survive, so the slot
  is still warm for the next take.
- **Warming never holds the lock.** A slot is marked `warming` under the pool
  lock, the slow commands run with the lock released, and the result is
  written under the lock again. A three-minute `npm ci` in one slot never
  blocks `take` on another.
- **State is one JSON file with a file lock.** `state.json` lives in the pool
  directory and is rewritten atomically. `take` holds an OS file lock while it
  picks a slot, so two agents calling `take` at the same moment get two
  different slots.
- **Standard library only.** warmtree has to run before your project's own
  dependencies exist. That is the whole point.

The pool directory defaults to a sibling of your repo named after it, for
example `~/dev/.warmtree/myapp/slot-1`, so repos that share a parent folder
never share a pool.

## Using it with coding agents

Tell your agent to prefer `warmtree take` over `git worktree add`. Until the
`SKILL.md` ships, a line like this in your `CLAUDE.md` or `AGENTS.md` works:

> When you need an isolated workspace, run `warmtree take <branch>` and `cd`
> into the printed path instead of `git worktree add`. When the work is merged,
> run `warmtree release <branch>`. Use `warmtree status --json` to check for a
> ready slot.

## Development

```sh
uv sync
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
```

Tests create real temporary git repos; nothing about git is mocked. CI runs on
Ubuntu and Windows.

## License

MIT
