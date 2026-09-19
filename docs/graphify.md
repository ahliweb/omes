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

Implemented (issue #50): `modules/graphify/module.sh` (`MODULE_NAME=graphify`,
`MODULE_SCOPE=user`, `MODULE_REQUIRES=()`, `MODULE_PROFILES=()`) installs and manages the
`graphify` CLI (PyPI package `graphifyy`) inside a per-user, isolated Python tool environment.
It follows the standard OMES module lifecycle (`docs/architecture.md` §4):
`module_check` (read-only preflight), `module_apply` (idempotent install), `module_verify`
(proves `graphify --help` exits 0), `module_rollback` (uninstalls the tool-env package only).

**Deliberately not wired into any profile.** Graphify is optional (§1.4); it is reachable via
`omes install --module graphify`, and `profiles/hermes.profile` carries only a comment
(`# graphify (optional: omes install --module graphify)`), never an active module entry.

### 2.1 Preflight (`module_check`)

- **Never root.** Like `hermes`, graphify installs entirely within the invoking user's tool
  environment; `module_check` refuses immediately if run as root.
- **Python >= 3.10.** `module_check` runs `python3 --version` and fails with an actionable
  message (`python3 >= 3.10 is required and was not found (or is older than 3.10)`) if python3
  is absent or older.
- **Installer detection, `uv` preferred.** `module_check` looks for `uv`, then `pipx`, on
  `PATH`. If neither is found:
  - **Default:** fails with `neither uv nor pipx found on PATH - install pipx via 'sudo apt
    install pipx' (available in Ubuntu noble), or install uv by setting
    OMES_GRAPHIFY_INSTALLER=uv-bootstrap`. OMES never silently installs either tool.
  - **Opt-in (`OMES_GRAPHIFY_INSTALLER=uv-bootstrap`):** `module_check` instead verifies
    network reachability (`detect_network`, reused from the same mechanism `hermes` uses) so a
    later `module_apply` can bootstrap `uv`.
- **Network, only when needed.** `module_check` requires network only when `graphify` is not
  yet installed (mirroring `modules/hermes/module.sh`'s own network-only-if-needed check) —
  an already-installed host does not need connectivity to pass `check`.

### 2.2 Install (`module_apply`)

- **Idempotent.** If `graphify` is already installed and either no `OMES_GRAPHIFY_VERSION` pin
  is set, or the installed version already matches the pin, `module_apply` skips the install
  entirely (no `uv tool install`/`pipx install` call).
- **PEP 668 compliance.** Install always runs through the detected isolated tool manager:
  `uv tool install graphifyy` (or `uv tool install "graphifyy==<version>"` when
  `OMES_GRAPHIFY_VERSION` is set) preferred, `pipx install graphifyy` (or the pinned
  `pipx install "graphifyy==<version>"`) as fallback. **`pip install` is never invoked, under
  any code path** — system Python is never touched.
- **`OMES_GRAPHIFY_INSTALLER=uv-bootstrap`.** When neither `uv` nor `pipx` is present and this
  opt-in is set, `module_apply` downloads the official `uv` installer
  (`https://astral.sh/uv/install.sh`) to a temp file (never `curl | bash`, mirroring
  `modules/hermes/module.sh`'s `_hermes_download_and_install`), optionally verifies it against
  `OMES_UV_INSTALLER_SHA256` (a mismatch aborts with nothing executed), then runs the
  downloaded file. If `uv` is still not reachable afterward, `module_apply` fails loudly
  rather than silently proceeding as if an installer were present.
- **Records the resolved version.** After a successful (non-dry-run) install, `module_apply`
  resolves `graphify --version` and records it via
  `state_set "module.graphify.version_installed" "<version>"`, for reproducibility.
- **Dry-run.** Under `--dry-run`, `module_apply` prints the planned action and performs no
  install call and no state write.

### 2.3 Verify (`module_verify`)

Proves the install actually works, not just that a binary exists: `command -v graphify` must
succeed, and `graphify --help` must exit 0. Either failing fails verification (exit 7 from
`omes install`).

### 2.4 Rollback (`module_rollback`)

Uninstalls **only** the `graphifyy` tool-env package (`uv tool uninstall graphifyy` or
`pipx uninstall graphifyy`) and clears `module.graphify.version_installed`. It **never**
touches a `graphify-out/` directory, vault content, or any other data graphify itself
produces — exactly the same "OMES only removes what it created" boundary
`modules/hermes/module.sh` applies to `$HERMES_HOME`. If neither `uv` nor `pipx` is present at
rollback time, it is a no-op, not a failure.

### 2.5 `omes graphify update` / `omes graphify uninstall`

Outside the normal install/uninstall lifecycle, `lib/omes/cmd/graphify.sh` adds two
maintenance subcommands — see `docs/cli.md` §4.12 for the full synopsis, exit codes, and JSON
schema. `omes graphify run` (the Hermes workflow wrapper) is **not implemented yet (tracked in
#51)** and exits with a usage error if invoked.

### 2.6 Environment variables and state

See `docs/configuration.md` §9 for `OMES_GRAPHIFY_VERSION`, `OMES_GRAPHIFY_INSTALLER`, and
`OMES_UV_INSTALLER_SHA256`, and the `module.graphify.version_installed` state key.

## §3 Workflow (omes graphify run / Hermes skill)

Not implemented yet (tracked in #51).

## §4 MCP integration

Not implemented yet (tracked in #52).
