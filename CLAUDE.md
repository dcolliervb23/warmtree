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
