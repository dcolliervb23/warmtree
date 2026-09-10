# Ideas

Things that came up while building warmtree and are out of scope for v1.
Append here instead of expanding DESIGN.md.

- **Stream warm output.** `run` output is captured and shown only on failure
  so `take` keeps stdout clean. A `--verbose` flag on `fill` and `refresh`
  could stream it to stderr for a first-time `npm ci` that takes minutes.
- **`refresh --fetch`.** `refresh` moves slots to the local base tip. It could
  run `git fetch` first, or accept `base = "origin/main"`, which already works
  because the base is resolved with `rev-parse`.
- **A real docs site.** The README is the documentation for now, on
  purpose. If warmtree grows an audience, split command reference and
  workflows into docs/ and keep the README as the pitch plus quick start.
- **Warn when `fill` warms nothing.** With the init default `run = []`, slots
  come up without dependencies and a sub-second `take` buys little. `init`
  guessing `run` from lockfiles is decided against (it never guesses), but
  `fill` could print a one-line hint when `run` is empty and a known lockfile
  is present. From dogfooding, 2026-09-07.
- **Hint at session reload after skill install.** Skills are scanned at
  agent session start (Claude Code: `/reload-plugins`; Copilot CLI:
  `/skills reload`; Codex: restart only). `init` and `skill` could print one
  line telling the user a running session needs a restart or skill reload to
  see the new skill. Keep it vague enough not to go stale with tool renames.
- **Relative size: `warmtree size +2` / `size -1`.** Agents and humans both
  have to read the current size and do arithmetic before growing the pool for
  a fan-out; relative syntax removes the round trip.
- **Autogrow on cold take, with a cap.** A config like `autogrow = 2` bumping
  size whenever take falls back cold only ever ratchets up, each step costing
  a full node_modules on disk. Needs `max_size`, and shrinking back toward
  the configured size edges into daemon territory. Cold fallback plus the
  skill telling agents to grow before fanning out covers most of it today.
