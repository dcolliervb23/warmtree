# warmtree — rules for AI sessions in this repo

Read DESIGN.md first. It is the scope. Do not expand it; append ideas to IDEAS.md.
The scope is the pool only. Do not add port allocation, merge helpers, or
general cleanup; the design explains why.

## Working rules
- Prefer boring, readable Python over clever Python. Every line should be one
  a reviewer can follow without explanation.
- The maintainer writes the tests and the README. Draft them if asked, then
  stop and wait for review before committing.
- Small commits, one step each. Conventional messages (`feat:`, `test:`, `docs:`).
  Plain messages: no co-author trailers, no session links.

## Review
The author never reviews alone. Reviewers are fresh background agents briefed
from `.claude/agents/reviewer.md`. Each brief names the head commit, the slot
holding that commit, the diff and commit list written to the scratchpad, what
the change is meant to do, a scratch folder for experiments, and the mode.
- Open the PR as a draft, review it, fix, then mark it ready. Do not call work
  done before its review is in.
- Weight by risk:
  - Anything that deletes, moves, or resets files, or changes pool state: two
    reviewers in parallel. One works in checklist mode on Opus, with an attack
    list. The other works in open mode on Fable, with only the goal, so the
    author's list does not anchor it.
  - Other code changes: one checklist reviewer.
  - Docs only: no review.
- An outside contributor's PR: reviewers read only and run nothing; the
  contributor's tests run in CI after the maintainer approves the workflow.
- Confirm each [REAL] finding with a test that fails before the fix. A finding
  that cannot be made to fail is dropped, and the PR comment says why.
- A bug older than the PR is fixed in it only when the PR exposes it or
  vouches for it (a dry run listing it as safe). Otherwise append it to
  IDEAS.md.
- At most two rounds. Round 2 reads only the fix diff, and only for code that
  deletes, moves, or resets files.
- Record each round in a PR comment: what was found, what was fixed and by
  which commit, and every finding left alone with the reason.

## Publishing hygiene
- `git config user.email dcolliervb23@users.noreply.github.com` in this repo. Never a personal email.
- No secrets. PyPI publishing is GitHub Actions trusted publishing only.
- No local machine paths in any committed file.
- Assume every commit is public.

## Stack
- Python 3.12, standard library only at runtime (`dependencies = []`).
- Dev tools: `uv`, `ruff`, `pytest`. Tests create real temporary git repos.
- Run tests with `uv run pytest -q`. Lint with `uv run ruff check .`.
- Must work on Windows, Linux, and WSL. CI runs on ubuntu-latest and windows-latest.
