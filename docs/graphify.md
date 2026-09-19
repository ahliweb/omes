# Graphify integration

> Status: describes the planned integration boundary only. No graphify code,
> module, or command exists in this repository yet. §1 below is accepted as
> the design boundary for issue
> [#49](https://github.com/ahliweb/omes/issues/49); §2–§4 are placeholders
> for later, stacked issues and must not be read as implemented.
>
> Upstream facts in this document were verified 2026-09-19 against
> `graphify` version `0.9.64` (see
> [ADR-0014](adr/0014-graphify-integration-boundary.md) for how that
> verification was done and why it matters for what OMES commits to).

## §1 Identity, modes, and ownership boundary

### 1.1 Identity

- Upstream repository: `Graphify-Labs/graphify`.
- PyPI package name: `graphifyy` (double-y). This is deliberate and
  load-bearing: `graphify` (single-y) is a **different, unaffiliated**
  package on PyPI. Any install instruction, module, or script OMES ever
  writes for this integration must install `graphifyy`, never `graphify`.
- CLI executable name (installed by the `graphifyy` package): `graphify`.
- License: Apache-2.0 (per `pip show graphifyy`).
- Verified today (2026-09-19) via
  `docker run --rm python:3.12-slim bash -c 'pip install -q graphifyy && graphify --version'`:
  installed version is `graphify 0.9.64`.
- Requires Python >= 3.10.

### 1.2 Three modes

Graphify is used in three distinct modes. OMES only ever automates the
first; the other two are documented so nothing about them is assumed,
silently blocked, or accidentally reinvented later.

1. **Server/headless mode.** `graphify extract <path> --code-only` — local
   tree-sitter AST extraction only, no vault, no provider/API keys, safe to
   run unattended on a server or in CI. This is the only mode an OMES
   module is expected to invoke (implementation in #50/#51).
2. **Workstation mode.** `graphify extract <path>` without `--code-only`
   (or with `--mode deep`) adds a semantic pass over docs/papers/images —
   opt-in, requires a configured LLM backend (`--backend`/an API key env
   var, or `--backend ollama` for local inference). A human may also run
   `graphify export obsidian` afterwards to produce a vault. This mode is
   never triggered automatically by OMES; if #51 ever offers it, it must
   remain an explicit, opt-in operator action, never a default.
3. **Optional vault (Obsidian).** Obsidian is never installed, started, or
   managed by OMES. It is an optional local Markdown-vault **consumer**
   that a human may point at graphify's `graphify-out/` output (specifically
   at an `export obsidian` result) to browse the graph interactively.
   OMES/Hermes never talk to a running Obsidian process, and no OMES module
   ever assumes Obsidian is present.

### 1.3 Ownership matrix

| Owns / does NOT own | OMES | Upstream Graphify (`Graphify-Labs/graphify` / PyPI `graphifyy`) | Obsidian | Hermes |
|---|---|---|---|---|
| Installing/isolating the `graphify` CLI | **Owns** — a dedicated tool environment (`uv tool`/pipx), never system pip (see ADR-0014) | Publishes the package; does not know about OMES | No role | No role |
| Invoking the CLI, validating inputs/paths, provenance sidecar | **Owns** — module lifecycle, preflight, path validation, recording what ran and against what commit/version | No role — graphify has no concept of OMES's provenance model | No role | No role |
| AST/semantic extraction logic, graph format, `query`/`explain`/`path`/`export` subcommands | Never reimplemented by OMES | **Owns** entirely | No role | No role |
| Provider/backend routing for semantic extraction (`--backend {gemini\|kimi\|claude\|openai\|deepseek\|ollama}`) | Never duplicated; OMES never stores or routes provider credentials for graphify | **Owns** entirely, via its own env-var-driven backend selection | No role | No role |
| MCP server implementation (optional `graphifyy[mcp]` extra, stdio) | Not implemented by OMES; tracked for #52 as "wrap/invoke," never "reimplement" | **Owns** the MCP server itself | No role | No role |
| Rendering/browsing a vault a human opens | No role | Produces the vault content (`export obsidian`) but does not run Obsidian | **Owns** — a human-operated local app; OMES/Hermes never talk to a running Obsidian process | No role |
| Exposing `/graphify <path>` as an assistant skill/command | Provides the installed CLI the skill shells out to (glue only, #51) | Ships its own skill writer (`graphify hermes install`) as one option | No role | **Owns** the skill/session runtime itself; OMES never reimplements Hermes's skill or session runtime |
| Module install/preflight/version-pinning/uninstall lifecycle | **Owns** — consistent with every other OMES module (`docs/architecture.md` §4) | No role — upstream has no module contract, backup, or rollback concept | No role | No role |

### 1.4 Non-goals

- No fork of `Graphify-Labs/graphify` into this repository. OMES only ever
  installs and shells out to the upstream-published `graphifyy` package.
- No Obsidian server/API dependency. Obsidian is a local, human-operated
  Markdown viewer, never a service OMES installs, starts, or health-checks.
- No OMES-side semantic extraction logic. Backend/provider selection for
  the semantic pass is entirely graphify's own responsibility
  (`--backend`, env vars); OMES never duplicates or wraps that routing.
- No networked graph database (Neo4j/FalkorDB) as a default path. `export
  neo4j`/`export falkordb` are upstream, opt-in export targets a human may
  choose; they are out of scope for OMES's default invocation path.

### 1.5 Local-vs-external processing boundary

This distinction must be explicit anywhere OMES documents or wraps
graphify, because it is the difference between "safe to run unattended on
a server with no egress" and "calls an external LLM API with repository
content":

| Invocation path | Processing | Notes |
|---|---|---|
| `graphify extract --code-only` | 100% local | tree-sitter AST only, no network call, no API key needed |
| `graphify hook install`/`uninstall`/`status` (git hooks) | 100% local | Re-runs extraction locally on commit/checkout; no network call by itself |
| `graphify export {html,callflow-html,obsidian,wiki,svg,graphml}` | 100% local | Renders/serializes the already-extracted local graph; writes files only |
| `graphify export {neo4j,falkordb}` | Local extraction, then a network push to a graph database the operator configured | Opt-in only; not part of OMES's default path (§1.4) |
| `graphify extract` (no `--code-only`) with `--backend ollama` | 100% local | Semantic pass runs against a local Ollama model; no external API call |
| `graphify extract` (no `--code-only`) with any other `--backend`, or an API key env var set (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, etc.) | **External** | Sends extracted content to that provider's API for the semantic/`INFERRED`-edge pass |

**OMES must never enable semantic extraction by default.** Any OMES-driven
invocation of `graphify extract` defaults to `--code-only` (or otherwise
does not set a backend/provider credential); turning on the semantic pass
is always an explicit, documented, operator-initiated action, never a
module default or an implicit side effect of `omes install`.

### 1.6 Provenance model: use graphify's own tags, do not reinvent one

Every edge graphify extracts is already tagged `EXTRACTED` (explicit in
source, code path, no LLM) or `INFERRED` (resolved/derived, semantic path).
OMES should surface this existing provenance tagging in whatever wrapper
or sidecar #50/#51 build, not invent a parallel classification scheme.

### 1.7 Output convention (for reference, not yet consumed by OMES)

Upstream writes a `graphify-out/` directory (inside the target path, or
under `--out`/`--output`) containing `graph.html` (interactive
visualization), `GRAPH_REPORT.md` (summary), and `graph.json` (the full
graph, queryable via `graphify query`/`graphify explain`/`graphify path`).
OMES does not currently read, parse, or manage this directory; #50/#51 will
define how (if at all) OMES surfaces or validates it.

### 1.8 No upstream `--update`/`--watch` flag

`graphify --help` and the upstream README do not expose a literal
`--update` or `--watch` CLI flag. Re-running extraction on a change is
handled upstream via `graphify hook install` (git post-commit/post-checkout
hooks), and the installed `graphify` package itself is upgraded via `uv
tool upgrade graphifyy` / `pipx upgrade graphifyy` — an OMES-module-managed
concern (#50), not a graphify CLI flag. How OMES/Hermes trigger re-runs is
explicitly **not implemented yet (tracked in #51)**; the current
expectation is re-invoking `extract` on demand, not running a watch daemon,
but that is a design note for #51 to confirm, not a decision this PR makes.

## §2 Installation module

Not implemented yet (tracked in #50).

## §3 Workflow (omes graphify run / Hermes skill)

Not implemented yet (tracked in #51).

## §4 MCP integration

Not implemented yet (tracked in #52).
