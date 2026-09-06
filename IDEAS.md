# Ideas

Things that came up while building warmtree and are out of scope for v1.
Append here instead of expanding DESIGN.md.

- **Stream warm output.** `run` output is captured and shown only on failure
  so `take` keeps stdout clean. A `--verbose` flag on `fill` and `refresh`
  could stream it to stderr for a first-time `npm ci` that takes minutes.
- **`refresh --fetch`.** `refresh` moves slots to the local base tip. It could
  run `git fetch` first, or accept `base = "origin/main"`, which already works
  because the base is resolved with `rev-parse`.
