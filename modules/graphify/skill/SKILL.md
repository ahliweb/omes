---
name: graphify
description: Turn a codebase (or a docs/paper/image tree) into a knowledge graph via the OMES-installed Graphify CLI. Use for "explain this repo", "map the dependencies", "what calls what", or "build a knowledge graph of this path".
---

# Graphify

This skill shells out to `omes graphify run`, which wraps the OMES-installed `graphify` CLI
(upstream: `Graphify-Labs/graphify`, PyPI package `graphifyy`). It never talks to `graphify`
directly and never re-implements OMES's install/path-validation/provenance logic — see
`docs/graphify.md` for the full boundary and `docs/adr/0014-graphify-integration-boundary.md`
for why.

## Invocation

```
/graphify <path>
/graphify <path> --mode semantic
```

`run.sh` in this directory is the literal command Hermes executes: `omes graphify run "$@"`.
Everything below documents what that command does, not a separate implementation.

## Modes

- **`code` (default).** Local-only tree-sitter AST extraction (`graphify extract <path>
  --code-only`). No network call, no provider credentials, safe to run unattended. Every edge
  produced this way is tagged `EXTRACTED` — explicit in the source, derived purely from the
  code's own structure (imports, calls, definitions).
- **`semantic` (opt-in only).** Adds an LLM-driven semantic pass over docs/papers/images in
  addition to code. This mode is refused unless `OMES_GRAPHIFY_PROVIDER_ENV` is set to the name
  of an environment variable that itself holds a real provider credential (e.g.
  `OMES_GRAPHIFY_PROVIDER_ENV=ANTHROPIC_API_KEY`) — OMES checks that the named variable is
  non-empty but never reads or prints its value. Edges this pass adds or resolves are tagged
  `INFERRED` — semantically derived, not explicit in the source text. Never enabled by default;
  an operator must configure this before `/graphify <path> --mode semantic` will do anything.

## What you'll see

`omes graphify run` always prints, before doing anything: the resolved input path, the
extraction mode, and the output directory it will write to (`<path>/graphify-out/` unless
`--out <dir>` was given). After a successful run it writes `omes-provenance.json` next to
graphify's own output (`graph.html`, `GRAPH_REPORT.md`, `graph.json`) recording the mode,
resolved path, `graphify`/OMES versions, and — for semantic mode — the *name* of the provider
env var used (never its value). Read `GRAPH_REPORT.md` for a human summary, or `graph.json` for
the full graph (queryable via `graphify query`/`graphify explain`/`graphify path` directly).

## Boundary reminders (do not cross these from inside a skill run)

- Never pass a path that resolves inside an existing `graphify-out/` directory — `omes graphify
  run` refuses this itself (re-extracting graphify's own output is never useful).
- Never fetch, echo, or log a provider credential value; only its env var *name* is ever
  recorded.
- Never install, start, or otherwise manage Obsidian — that stays a human's local, optional
  action on the resulting vault export, never something this skill or Hermes does.
- Never write directly into `$HERMES_HOME` outside this skill's own directory; if the skill
  itself needs reinstalling/updating, use `omes graphify skill install`, not manual edits.
