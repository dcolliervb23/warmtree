# warmtree

A pool of pre-warmed git worktrees for parallel AI coding agents.

`git worktree add` is fast. Everything after it is slow: installing
dependencies, rebuilding caches, copying the `.env` files git does not track.
On a large repo that cold start is minutes, and with several agents working in
parallel you pay it several times a day. warmtree keeps a few worktrees already
checked out, warmed, and ready, so `warmtree take <branch>` hands you a
workspace in under a second.

![warmtree demo](docs/demo.gif)

Measured on a private TypeScript app (457 packages, 664 MB of node_modules):
a cold `git worktree add` + `npm ci` took 6.6 s even with a fast machine and
network; `warmtree take` handed over a warm slot in 0.15 s.

warmtree owns the **create** and **release** steps only. Everything in between
is ordinary git, so your editor, hooks, and other worktree tools work on a
pooled worktree unchanged.

## Status

Early. Stable enough to dogfood, and dogfooded daily on a large private repo.

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

Or from PyPI: `uvx warmtree` works on a machine with nothing but Python
and git.

Updating is your package manager's job, not warmtree's:
`uv tool upgrade warmtree` for a tool install, and `uvx` resolves new
releases on its own (`uvx warmtree@latest` forces it).

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

The pool keeps itself current: any `take`, `fill`, or `status` that finds
the last refresh older than `refresh_every` (a day, by default) spawns a
background `warmtree refresh --fetch` and returns immediately. A nightly
cron running `warmtree refresh --fetch` still works if you want slots fresh
before first use; set `refresh_every = "off"` (or `WARMTREE_AUTO_REFRESH=off`
in the environment) to opt out of the self-refresh.

## Already have worktrees?

The pool is additive: `init` and `fill` change nothing about worktrees you
made by hand, and you can start using `take` for your next branch today.

A worktree is a directory with one branch checked out; `git worktree list`
shows yours. For each directory you want to keep working in, adopt it:

```sh
warmtree adopt ../myrepo-feature-x
```

Nothing on disk changes: the worktree is registered as a taken slot right
where it is, so open editors, running servers, and shell sessions keep
working. When the branch is done, `warmtree release` resets the tree and
moves the folder into the pool — the one moment nothing legitimate is still
inside it — and the slot, dependencies intact, joins the ready pool instead
of being deleted.

Branches without a checkout need no onboarding: they are just refs, and
`warmtree take <branch>` is how they get a workspace from now on. A worktree
whose branch merged long ago is not worth adopting; delete it and let the
pool's fresh slots do the work.

## Commands

| Command | What it does |
|---|---|
| `warmtree init [--force] [--no-skill] [--user-skill] [--no-instructions]` | Write a starter `.warmtree.toml`. Pre-fills `lockfiles` from what it finds in the repo. Never guesses your install command. Installs the agent skill for any coding agent it detects; `--user-skill` puts it in your home directory instead so the repo never contains it, `--no-skill` skips it entirely. Also adds the prefer-warmtree block to your user-level agent instructions (`--no-instructions` skips that). |
| `warmtree instructions [--repo] [--tool T]` | Add a marked prefer-warmtree block to agent instruction files — your user-level ones by default (`~/.claude/CLAUDE.md`, `~/.copilot/copilot-instructions.md`, `~/.codex/AGENTS.md`), or the repo's with `--repo`. Only the marked block is ever touched; re-runs update it in place. |
| `warmtree skill [--tool T] [--user] [--force]` | Install or refresh the agent skill in the tool folders this repo uses, or in the ones named with `--tool`. `--user` installs into the tools' personal skills folders in your home directory (`~/.claude/skills`, `~/.copilot/skills`, `~/.codex/skills`, `~/.cursor/skills`) so no repo ever contains the file; it works outside a repo and covers every repo at once. Never overwrites an edited copy without `--force`. |
| `warmtree fill` | Create slots until `size` are ready. Each slot is a worktree with a detached HEAD at the base branch, with `copy` files copied in and `run` commands executed. |
| `warmtree take <branch> [--from REF]` | Claim the oldest ready slot. Creates `<branch>` there (from `REF` or the base branch) or checks it out if it already exists. Prints the path, then refills the pool. |
| `warmtree take --scratch` | Claim a slot on a generated `scratch/<id>` branch, for throwaway work that does not deserve a name. |
| `warmtree take ... --no-refill` | Skip the refill. |
| `warmtree take ... --json` | Print a JSON object (`path`, `slot`, `branch`, `cold`) instead of the bare path. |
| `warmtree take ... --refill-background` | Refill in a detached process and return immediately. |
| `warmtree release <branch> [--keep-branch] [--force]` | Park the slot back on the base branch and mark it ready. Refuses a dirty tree unless `--force`. Deletes the branch if it is merged; an unmerged branch is always kept. Refuses a slot leased to another still-running session (the one that ran `take` or `adopt`); `--force` overrides. `--json` prints `slot`, `branch`, `branch_deleted`. |
| `warmtree exec <branch> -- <command...>` | Run a command inside the slot holding `<branch>`, with `WARMTREE_SLOT` set. Streams and exit code pass through, so `warmtree exec fix/tests -- npm test` behaves like running it there. Does not pin the slot: do not release a branch while a command still runs in it. |
| `warmtree adopt <path>` | Register an existing worktree as a taken slot, in place; nothing on disk changes. At `release` the folder moves into the pool and its dependencies are recycled. |
| `warmtree refresh [--fetch]` | Move every waiting slot to the current base commit and re-copy files. `--fetch` fetches first and parks slots on `origin/<base>`, so they track the remote even when your local base branch is behind; your own checkout is never touched. Re-runs `run` only in slots whose lockfile hashes changed or whose last warm failed. Skips taken slots. |
| `warmtree size [N]` | Show the configured size and a count of slots by state. With `N`, write the new size to `.warmtree.toml` and grow or shrink the pool to match. Shrinking removes ready slots only. |
| `warmtree which` | Name the slot the current directory is inside, as `slot-N <state> <branch>`. Exit 1 if not in a slot. |
| `warmtree remove [SLOT...] [--all] [--force]` | Delete slots and their worktree registrations. Taken slots need `--force`. |
| `warmtree status [--json] [--du]` | Table of slots: name, state, branch, age, last warm, path. `--json` for scripts and agents. `--du` adds a SIZE column and a total, measuring what the pool costs on disk; with `--json` it adds a `du_bytes` field per slot. |
| `warmtree doctor [--fix]` | Report drift between `state.json`, git, and the filesystem: deleted directories, lost registrations, stuck warming states, ready slots hijacked by a stray checkout, untracked directories in the pool. `--fix` applies the safe repairs. |

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
# git_hooks = false            # run the repo's git hooks during pool operations
# refresh_every = "24h"        # self-refresh when a command finds the pool staler; "off" disables

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
- `warmtree size N` edits the `size` line in place. Your comments and other
  keys are left alone.
- Pool operations skip the repo's git hooks by default. A checkout hook
  that lingers — telemetry spawning children, say — can hang a `take`
  mid-claim, and per-slot setup belongs in `run` and `copy` anyway. Set
  `git_hooks = true` if your hooks are quick and you want them.
- Unknown keys are errors, so a typo never silently disables warming.

## Using it with coding agents

Skills teach agents how to use warmtree; instructions change what they
reach for by default. Without the latter, agents keep running
`git worktree add` from habit. `warmtree init` therefore also maintains a
marked prefer-warmtree block in your user-level agent instructions, and
`warmtree instructions --repo` writes the same block into a repo's own
`CLAUDE.md`, `AGENTS.md`, or `.github/copilot-instructions.md` for
everyone who works there. The block is a strong preference, not a ban:
agents may still use plain git when warmtree is unavailable or a task
calls for it. Note that running agent sessions load instructions at
startup; restart or reload them after installing.


warmtree ships an [Agent Skill](https://agentskills.io): a short set of
instructions an agent loads when the task calls for an isolated workspace. It
tells the agent to check whether it is already in a slot (`warmtree which`),
check capacity before fanning out (`warmtree size`), take a slot instead of
running `git worktree add`, and release it when the branch is merged.

`warmtree init` installs it automatically wherever it sees signs of a coding
agent in the repo, and prints each path it wrote:

| Found in the repo | Skill installed at |
|---|---|
| `CLAUDE.md` or `.claude/` | `.claude/skills/warmtree/SKILL.md` (Claude Code) |
| `.github/copilot-instructions.md` | `.github/skills/warmtree/SKILL.md` (GitHub Copilot) |
| `AGENTS.md` or `.agents/` | `.agents/skills/warmtree/SKILL.md` (Codex and others) |
| `.cursor/` | `.cursor/skills/warmtree/SKILL.md` (Cursor) |

Commit those folders so every agent on the project gets the skill. To install
for a tool that was not detected, or to refresh the copies after upgrading
warmtree:

```sh
warmtree skill --tool claude        # claude, copilot, codex, or cursor; repeatable
warmtree skill                      # re-detect and refresh
warmtree skill --force              # replace a copy you edited by hand
```

A copy you have edited is never overwritten without `--force`. Pass
`--no-skill` to `init` to skip all of this.

If your project uses an `AGENTS.md` instead, this paragraph is enough:

> This repo has a warmtree pool. When you need an isolated workspace, run
> `warmtree take <branch>` and `cd` into the printed path instead of
> `git worktree add`. Run `warmtree which` first to see if you are already in
> a slot, and `warmtree size` to check how many are ready before starting
> parallel work. When the branch is merged, run `warmtree release <branch>`.

Claude Code's built-in worktree feature is not intercepted. The skill is the
integration.

## With worktrunk

[worktrunk](https://github.com/max-sixty/worktrunk) covers the rest of the
worktree lifecycle: hooks, port allocation, a merge workflow, cleanup of
merged branches. The two fit together because warmtree only touches create
and release.

- Use `warmtree take` instead of `wt switch --create` to get a warm checkout.
- Inside the slot, worktrunk's commands work as they do in any worktree;
  `wt list` shows pool slots like any other worktree.
- When the branch is merged, run `warmtree release <branch>` rather than
  worktrunk's remove, so the slot goes back to the pool instead of being
  deleted.

### Why both?

worktrunk makes worktrees pleasant; warmtree makes them instant. When
`wt switch --create` runs, its post-create hooks execute your install while
you wait. warmtree moves that work off the critical path: the install
already happened, in the background, before anyone asked. Same work,
different moment.

| | worktrunk | warmtree |
|---|---|---|
| Time to a usable workspace | create, then hooks run your install now | under a second; the work was pre-paid |
| New workspace setup | post-create hooks | `warm.run` and `copy`, at fill and refresh, never at take |
| Env files | copies ignored files | copies them and injects `WARMTREE_SLOT` for per-slot ports |
| Port allocation | yes | only the `WARMTREE_SLOT` variable, on purpose |
| Merge workflow | `wt merge`: squash, rebase, cleanup | none, on purpose |
| Status | rich `wt list` | table plus `--json` for orchestrators |
| Dependencies at end of life | deleted with the worktree | recycled: `release` keeps them warm for the next branch |
| Staleness | tracks whatever you created from | `refresh --fetch` parks slots on origin's tip |
| Pool health | — | `doctor --fix` |
| Concurrent agents | — | file-locked claims, slot leases, hijacked-slot detection |
| Agent integration | shell integration | agent skill, installed by `init` |

If installs are fast or worktrees are rare, worktrunk alone is fine; the
pool's edge scales with install cost times workspace churn. On a large repo
with parallel agents churning branches daily, every `wt switch --create`
costs the install and every `warmtree take` costs a fraction of a second.

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

## Development

```sh
uv sync                        # creates .venv with Python 3.12, pytest, ruff
uv run pytest -q               # the whole suite, about a minute
uv run ruff check .
uv run ruff format --check .
```

Those three commands are exactly what CI runs on Ubuntu and Windows. Tests
create real temporary git repos; nothing about git is mocked, so `git` must
be on your PATH.

Useful variations:

```sh
uv run pytest -q tests/test_lifecycle.py   # one file
uv run pytest -q -k concurrent             # tests whose name matches
uv run pytest -x                           # stop at the first failure
uv run pytest -v                           # show every test name
uv run ruff format .                       # fix formatting instead of checking
```

### Trying a feature branch

`uvx --from` runs any ref in a throwaway environment, so you can dogfood an
unmerged branch on a real repo without touching your installed warmtree:

```sh
uvx --from git+https://github.com/dcolliervb23/warmtree@some-branch warmtree status
```

An alias keeps it readable while a branch is under test:

```sh
alias wtnext='uvx --from git+https://github.com/dcolliervb23/warmtree@some-branch warmtree'
wtnext status
```

There is nothing to switch back: plain `warmtree` was never touched, and the
alias dies with the shell. The environment is cached per ref, so after new
pushes to the branch add `--refresh` to pick them up. Both versions read the
same `.warmtree.toml` and pool state, which is the point — you are testing
the new code against your real pool.

### Releasing

Releases go to PyPI through GitHub Actions trusted publishing; no API token is
stored anywhere. One-time setup on pypi.org: add a trusted publisher for owner
`dcolliervb23`, repository `warmtree`, workflow `publish.yml`, environment
`pypi`. Then:

```sh
uv version 0.1.0            # sets the version in pyproject.toml
git commit -am "chore: release 0.1.0"
git tag v0.1.0
git push && git push --tags
```

The workflow refuses to publish if the tag and the package version disagree.

## License

MIT
