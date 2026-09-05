# Ideas

Things that came up while building warmtree and are out of scope for v1.
Append here instead of expanding PLAN.md.

- **Pool dir collision.** The default `dir = "../.warmtree"` is a sibling of
  the repo, so two repos that share a parent folder (`~/dev/a`, `~/dev/b`) both
  resolve to `~/dev/.warmtree` and would share one `state.json`. Options:
  default to `../.warmtree/<repo-name>`, or key `state.json` by repo. Decide
  before dogfooding in more than one repo. (found 2026-09-05, milestone 1)
