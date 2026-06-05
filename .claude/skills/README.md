# Committed Claude skills

This directory is the **one tracked exception** under `.claude/`. Everything else
in `.claude/` is per-machine / per-session state and is gitignored (agent
worktrees, session metadata, local settings); see the `.claude/*` +
`!.claude/skills/` rule in the repo's root `.gitignore`.

Skills here are portable engineering disciplines that are meant to travel with
the repo — anyone who clones it gets them, and Claude Code loads them
automatically. Treat them like any other source artifact: change them through a
PR.

## Skills

- **`real-data-first/`** — Fetch a REAL sample of any external artifact (API
  response, spreadsheet, CSV, ZIP/bucket layout, Parquet, shapefile, …) before
  writing code or test fixtures that depend on its schema; never invent column
  names / paths / sheet names / encodings from docs or intuition. This is the
  portable, project-agnostic generalization of the "Real Data First" section in
  the repo's top-level `CLAUDE.md` (which keeps its ABS-specific version). Bundles
  a re-usable `scripts/probe_artifact.py` schema probe and a `references/`
  catalogue of the schema-detail bugs the discipline prevents.
