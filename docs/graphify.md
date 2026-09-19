# Graphify integration

> Status: §1 is the accepted design boundary (issue
> [#49](https://github.com/ahliweb/omes/issues/49)). §2 (install module, #50),
> §3 (workflow/skill, #51), §4 (MCP integration, #52), and §5 (safe Obsidian
> export, #53) are all implemented, as described. Later sections (§6+) remain
> placeholders for their own stacked issues and must not be read as
> implemented until their own issue lands.
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
maintenance subcommands (docs/cli.md §4.12 only points here — this is the authoritative
reference for the full `omes graphify` CLI contract):

**Synopsis:** `omes graphify {update|uninstall} [--yes] [--dry-run] [--json]`

- `update` wraps `uv tool upgrade graphifyy` (or `pipx upgrade graphifyy` when pipx is the
  detected installer), then re-records the resolved version in
  `module.graphify.version_installed`.
- `uninstall` wraps `uv tool uninstall graphifyy` (or the pipx equivalent). Neither subcommand
  ever touches a `graphify-out/` directory or any other data graphify itself produces; only the
  `graphifyy` tool-env install.

Both flags are parsed by the extension itself, not by `bin/omes` (docs/cli.md §4.12) — put
`--yes`/`--dry-run`/`--json` directly after `update`/`uninstall`.

**Exit codes:** 0, 1 (neither `uv` nor `pipx` found, upgrade/uninstall failed, or declined
confirmation without `--yes`), 2 (unknown/missing subcommand).

**JSON schema:** `{"command":"graphify","subcommand":"update","ok":true,"installer":"uv","version":"0.9.64","exit_code":0}`,
`{"command":"graphify","subcommand":"uninstall","ok":true,"installer":"uv","exit_code":0}`.

**Examples:**

```bash
omes graphify update --yes                # upgrade graphifyy via uv/pipx
omes graphify uninstall --yes --json      # remove the tool-env install only
```

`omes graphify run` and `omes graphify skill install`/`uninstall` are documented in §3 below
(issue #51).

### 2.6 Environment variables and state

| Variable | Default | Kind | Meaning |
|---|---|---|---|
| `OMES_GRAPHIFY_VERSION` | unset | Operator | Pins the installed `graphifyy` version (e.g. `0.9.64`); a mismatch against the currently installed `graphify --version` output triggers a reinstall via `uv tool install graphifyy==<version>` (or the pipx equivalent). |
| `OMES_GRAPHIFY_INSTALLER` | unset | Operator | Set to `uv-bootstrap` to allow `module_check`/`module_apply` to download and run the official `uv` installer (`https://astral.sh/uv/install.sh`, to a temp file, never `curl \| bash`) when neither `uv` nor `pipx` is already on `PATH`. Without this, `module_check` fails with an actionable message instead of installing anything itself. |
| `OMES_UV_INSTALLER_SHA256` | unset | Operator | Verifies the downloaded `uv` installer's sha256 before executing it (only relevant with `OMES_GRAPHIFY_INSTALLER=uv-bootstrap`); a mismatch aborts with no execution, mirroring `OMES_HERMES_INSTALLER_SHA256` (docs/configuration.md §7). |
| `OMES_GRAPHIFY_PYTHON` | `python3` | Test-only | Overrides the python interpreter `module_check`'s version gate probes. Not a documented operator knob — exists so tests can simulate "python3 missing" by pointing at a nonexistent path instead of hiding a whole `PATH` directory (which risks also hiding unrelated coreutils a real host happens to colocate with `python3`). |
| `OMES_GRAPHIFY_UV_CMD` | `uv` | Test-only | Overrides the command name/path `_graphify_installer_available` checks for `uv`. Detection-only — actual install/upgrade/uninstall calls still invoke the literal `uv` command name. |
| `OMES_GRAPHIFY_PIPX_CMD` | `pipx` | Test-only | Same idea as `OMES_GRAPHIFY_UV_CMD`, for `pipx`. |

State key `module.graphify.version_installed` records the resolved `graphify --version` output
after a successful (non-dry-run) install/update, for reproducibility.

## §3 Workflow (omes graphify run / Hermes skill)

Implemented (issue #51): `omes graphify run` (path-validated, mode-gated extraction) and
`omes graphify skill install`/`uninstall` (a thin Hermes skill wrapper around it).

### 3.1 `omes graphify run`

**Synopsis:** `omes graphify run <path> [--mode code|semantic] [--backend <name>] [--out <dir>] [--yes] [--dry-run] [--json]`

Requires the `graphify` module to already be installed and verified (`omes install --module
graphify`); refuses with a clear message pointing at that command otherwise.

**Path validation** (no traversal, must exist, must not already be inside a `graphify-out/`
directory):

1. A path argument is required; a missing/empty path is a usage error (exit 2).
2. The path must exist (file or directory) — checked with `[[ -e ]]` before anything else runs.
3. The path is canonicalized (`realpath`, resolving `..` segments and symlinks) before any
   further check or use, so a traversal-shaped input (`../../etc`) is validated against its
   real, resolved location, not its literal spelling.
4. The resolved path is refused (exit 2) if it — or any ancestor directory — is itself named
   `graphify-out`: re-extracting graphify's own output directory is never useful and OMES
   refuses it outright rather than letting an operator accidentally recurse into it.

**Mode gating** — default `code`, `semantic` is opt-in and provider-configured, exactly as
issue #51 requires:

- **`code` (default).** Runs `graphify extract <resolved-path> --code-only` (plus `--out
  <dir>` if given). 100% local, no provider credential is read, checked, or required.
- **`semantic`.** Requires `OMES_GRAPHIFY_PROVIDER_ENV` to be set to the **name** of an
  environment variable that itself holds a real provider credential (e.g.
  `OMES_GRAPHIFY_PROVIDER_ENV=ANTHROPIC_API_KEY`). `omes graphify run` checks, via indirect
  parameter expansion, only whether the *named* variable is non-empty — it never reads,
  echoes, or forwards that variable's *value* anywhere (not to a log line, not to `--json`
  output, not to the provenance sidecar — only the variable's *name* is ever recorded, see
  §3.2). Missing either half of this gate (the pointer variable, or the credential it points
  at) refuses with an actionable message and no `graphify` invocation. When satisfied, runs
  `graphify extract <resolved-path>` (no `--code-only`), plus `--backend <name>` if `--backend`
  was given, and requires confirmation (`--yes` or an interactive `y`) before proceeding, since
  this is the one path that calls an external LLM API.

**What the operator sees**, always printed before `graphify` runs: the resolved input path,
the selected mode, and the output directory (`--out <dir>` if given, else `<path>/graphify-out`
for a directory input or `<dirname of path>/graphify-out` for a file input). A one-line
EXTRACTED/INFERRED explainer is also printed, matching whichever mode ran (§1.6): code mode
notes that only `EXTRACTED` edges are produced; semantic mode notes that `INFERRED` edges may
be added alongside them.

**Exit codes:** 0, 1 (graphify itself failed, or a `--mode semantic` invocation was declined
without `--yes`), 2 (missing/invalid path, path resolves inside a `graphify-out/` directory,
graphify not installed, invalid `--mode` value, or semantic mode requested without a
satisfied `OMES_GRAPHIFY_PROVIDER_ENV` gate).

**JSON schema:** `{"command":"graphify","subcommand":"run","ok":true,"mode":"code","path":"/abs/resolved/path","out_dir":"/abs/resolved/path/graphify-out","provenance_file":"/abs/resolved/path/graphify-out/omes-provenance.json","exit_code":0}`.

**Examples:**

```bash
omes graphify run ~/code/myrepo                                    # code-only, no credentials
OMES_GRAPHIFY_PROVIDER_ENV=ANTHROPIC_API_KEY \
  omes graphify run ~/code/myrepo --mode semantic --yes             # opt-in semantic pass
omes graphify run ~/code/myrepo --out /tmp/myrepo-graph --dry-run   # preview only
```

### 3.2 Provenance sidecar

After a successful (non-dry-run) `omes graphify run`, OMES writes `omes-provenance.json` into
the resolved output directory, alongside graphify's own `graph.html`/`GRAPH_REPORT.md`/
`graph.json` (§1.7). This is OMES's own audit record, layered on top of — never replacing —
graphify's own `EXTRACTED`/`INFERRED` edge tagging (§1.6):

```json
{
  "generated_at": "2026-09-19T12:00:00Z",
  "mode": "semantic",
  "path": "/home/op/code/myrepo",
  "out_dir": "/home/op/code/myrepo/graphify-out",
  "backend": null,
  "provider_env_var": "ANTHROPIC_API_KEY",
  "graphify_version": "0.9.64",
  "omes_version": "0.1.0",
  "invoked_by": "op"
}
```

`provider_env_var` records only the **name** given via `OMES_GRAPHIFY_PROVIDER_ENV` (`null` in
`code` mode) — the credential value itself is never read into OMES beyond the single
presence/non-empty check described in §3.1, and never appears in this file, in log output, or
in `--json` output.

### 3.3 Hermes skill: `omes graphify skill install` / `omes graphify skill uninstall`

**Synopsis:** `omes graphify skill {install|uninstall} [--yes] [--dry-run] [--json]`

A thin, copy-only wrapper — not a full OMES module lifecycle (there is nothing to
check/apply/verify beyond "did the two files land correctly") — that installs the bundled
skill into Hermes's skill directory so `/graphify <path>` becomes available inside a Hermes
session:

- **Source (bundled in this repo):** `modules/graphify/skill/SKILL.md` and
  `modules/graphify/skill/run.sh`.
- **Target:** `${OMES_HERMES_HOME:-$HOME/.hermes}/skills/graphify/` (same `HERMES_HOME`
  resolution as `modules/hermes/module.sh`).
- **`install`:** copies both files into the target directory (creating it if needed), makes
  `run.sh` executable, and registers both target paths via `omes_manage_path` inside an
  explicit backup session — so a pre-existing `SKILL.md`/`run.sh` at that path (e.g. from a
  prior `graphify hermes install` run) is backed up before being overwritten, restorable via
  `omes restore`, exactly like every other OMES-managed path (docs/architecture.md §4.6).
  `run.sh` itself never contains extraction logic — it is exactly `exec omes graphify run
  "$@"`, so all path validation, mode gating, and provenance-sidecar behavior in §3.1/§3.2 is
  reused, never duplicated inside the skill.
- **`uninstall`:** removes only these two files from the target directory (confirmed unless
  `--yes`); never touches any other file under `$HERMES_HOME/skills/` or `$HERMES_HOME`
  itself.

**Exit codes:** 0, 1 (copy/remove failed, or declined confirmation without `--yes`).

**JSON schema:** `{"command":"graphify","subcommand":"skill","action":"install","ok":true,"target_dir":"/home/op/.hermes/skills/graphify","exit_code":0}`,
`{"command":"graphify","subcommand":"skill","action":"uninstall","ok":true,"target_dir":"...","exit_code":0}`.

**Examples:**

```bash
omes graphify skill install --yes     # writes SKILL.md + run.sh into $HERMES_HOME/skills/graphify/
omes graphify skill uninstall --yes   # removes only those two files
```

### 3.4 Boundary notes specific to this section

- This wrapper never reimplements Hermes's own skill/session runtime (ADR-0014) — it only
  places two files where Hermes already looks for skills.
- `graphify hermes install` (upstream's own skill writer, §1) is a separate, independent path
  an operator may still use directly; `omes graphify skill install` does not call it and does
  not need to — they simply both write to the same conventional directory, and the
  OMES-managed one is what this repository documents and tests.
- Obsidian remains untouched by every command in this section, exactly as §1.2/§1.4 require.

## §4 MCP integration

Implemented (issue #52). Upstream `graphifyy[mcp]` (an optional PyPI extra, §1) provides a
real, invocable console script — verified empirically 2026-09-19 against graphify 0.9.64
(`pip install "graphifyy[mcp]"` inside `python:3.12-slim`):

```
$ graphify-mcp --help
usage: python -m graphify.serve [-h] [--graph PATH] [--transport {stdio,http}]
                                [--host HOST] [--port PORT]
                                [--api-key API_KEY] [--path PATH]
                                [--json-response] [--stateless]
                                [--session-timeout SESSION_TIMEOUT]
                                [graph_path]

Serve a graphify knowledge graph over MCP (stdio or Streamable HTTP).
```

### 4.1 Command, working directory, graph path, lifecycle

- **Command:** `graphify-mcp` (equivalently `python -m graphify.serve`).
- **Graph path:** the positional `graph_path` argument, or `--graph PATH`; both default to
  `graphify-out/graph.json` **resolved relative to the process's current working directory** —
  there is no separate install directory, and OMES does not introduce one.
- **Transport/lifecycle:** default `--transport stdio`. This is a local process an MCP client
  (Hermes) spawns per session and talks to over stdin/stdout — **not** a persistent daemon.
  OMES never enables `--transport http` and never starts, enables, or supervises a running
  `graphify-mcp` process itself (ADR-0014, "MCP as a local process boundary first; remote graph
  servers are outside MVP" — the issue #52 brief's own framing). Wiring an actual Hermes MCP
  client configuration to spawn `graphify-mcp` is an operator action outside this repository's
  scope, exactly like configuring any other MCP server Hermes talks to.

### 4.2 `graphify-mcp` module (issue #52 criterion 1, 3)

`modules/graphify-mcp/module.sh` (`MODULE_SCOPE=user`, `MODULE_REQUIRES=(graphify)`,
`MODULE_PROFILES=()` — **disabled by default**, exactly like `graphify` itself, reachable only
via `omes install --module graphify-mcp`):

- `module_check`: requires the `graphify` CLI to already be installed (points at
  `omes install --module graphify` otherwise); detects `uv`/`pipx` the same way
  `modules/graphify/module.sh` does (honoring the same `OMES_GRAPHIFY_UV_CMD`/
  `OMES_GRAPHIFY_PIPX_CMD` test-only overrides); requires network only when not yet installed.
- `module_apply`: idempotent; installs `graphifyy[mcp]` via `uv tool install "graphifyy[mcp]"`
  (or the pinned `graphifyy[mcp]==<version>` when `OMES_GRAPHIFY_VERSION` is set, or the pipx
  equivalent) — the same PEP 668-safe, no-`pip-install` posture as the base module.
- `module_verify`: `command -v graphify-mcp` and `graphify-mcp --help` (which exits immediately
  — it never starts the actual server) must both succeed.
- `module_rollback`: reinstalls plain `graphifyy` **without** the `[mcp]` extra
  (`uv tool install --reinstall graphifyy` / `pipx install --force graphifyy`), dropping the
  `graphify-mcp` entry point; never touches `graphify-out/` directories, other graphify-produced
  data, or hermes-gateway.

### 4.3 `omes graphify mcp health` (issue #52 criterion 2)

**Synopsis:** `omes graphify mcp health [--graph <path>] [--json]`

A read-only status check, never a lifecycle command (installing/removing the extra goes through
`omes install`/`uninstall --module graphify-mcp` above, like every other OMES module) and never
a fallback trigger by itself — it only reports, so an operator or Hermes can decide:

- **`ok`** — `graphify-mcp` is installed, `--help` responds, and the graph file (given or
  default `graphify-out/graph.json`) exists.
- **`not_applicable`** — `graphify-mcp` is not installed. This is the **expected default
  state** (MCP is opt-in), not an error: exits 0, and the message points at
  `omes install --module graphify-mcp` while noting that `graphify query`/`omes graphify run`
  remain fully available regardless (the "clear CLI fallback" issue #52 criterion 2 asks for).
- **`unhealthy`** — installed but broken (`--help` fails) or the graph file is missing: exits 1.

**Exit codes:** 0 (`ok` or `not_applicable`), 1 (`unhealthy`), 2 (usage error).

**JSON schema:** `{"command":"graphify","subcommand":"mcp","action":"health","ok":true,"status":"not_applicable","detail":"...","graph":"","exit_code":0}`.

**Examples:**

```bash
omes graphify mcp health                                    # default graphify-out/graph.json
omes graphify mcp health --graph ~/code/myrepo/graphify-out/graph.json --json
```

### 4.4 Failure isolation (issue #52 criterion 4)

Neither `modules/graphify-mcp/module.sh` nor `omes graphify mcp health` ever invokes
`systemctl`, references `hermes-gateway`, or touches any Hermes runtime state — verified by
`tests/integration/graphify.bats`'s dedicated isolation tests (asserting `systemctl` never
appears in the invocation log across install/uninstall/health). A missing, broken, or
uninstalled `graphify-mcp` can only ever affect an MCP client's ability to reach Graphify's
tools over MCP; it structurally cannot affect Hermes messaging, other skills, or the
`hermes-gateway` service, because nothing in this integration touches them.

### 4.5 Non-goals (unchanged from §1.4)

No OMES-managed running MCP server process, no `--transport http` default, no networked graph
database push as part of MCP (that remains the separate, opt-in `export neo4j`/`export
falkordb` path, §1.4), and no duplication of graphify's own MCP tool implementation
(`graphify/serve.py` upstream) inside OMES.

## §5 Safe Obsidian export (`omes graphify export`)

Implemented (issue #53). `omes graphify export <graphify-out-dir> --vault <path>
[--init-vault] [--dry-run] [--yes] [--json]` renders the graph a prior `omes graphify run`
(or a manual `graphify extract`) already produced into vault-ready Markdown notes, writing
**only** under `<vault>/<subdir>/` (default subdir: `graphify/<project-name>`, overridable via
`OMES_GRAPHIFY_VAULT_SUBDIR`) — it never touches any other note in the vault, and never touches
the vault's own top-level `.obsidian/` directory. Per §1.2/§1.4, OMES still never installs,
starts, or talks to a running Obsidian process; this command only writes files a human may later
open in Obsidian.

**Synopsis:** `omes graphify export <graphify-out-dir> --vault <path> [--init-vault] [--dry-run]
[--yes] [--json]` and `omes graphify export rollback [--timestamp <ts>] [--yes] [--json]`.

**Vault resolution — never guessed:** `--vault <path>`, or the `OBSIDIAN_VAULT_PATH` environment
variable when `--vault` is omitted. Missing both is a usage error (exit 2) — the vault path is
always operator-supplied, explicitly. The resolved vault must already exist and contain a
`.obsidian/` directory (Obsidian's own vault marker), **or** the operator passes `--init-vault`
explicitly, in which case a new vault directory (and a minimal `.obsidian/` marker directory —
just enough for Obsidian to recognize it as a vault on next open; OMES writes no Obsidian
preference/plugin content into it) is created. A vault that exists but lacks `.obsidian/` is
refused without `--init-vault` — OMES never assumes an arbitrary directory is meant to be a
vault.

### 5.1 Two render paths — upstream preferred, OMES's own renderer as fallback

Verified 2026-09-19 against graphify 0.9.64 (`docker run --rm python:3.12-slim bash -c 'pip
install -q graphifyy && graphify extract /work --code-only && graphify export obsidian --graph
/work/graphify-out/graph.json --dir /work/vault-export'`): upstream's `export obsidian` is real
and DOES produce genuinely vault-ready Markdown — one note per code symbol/file with its own YAML
front matter (`source_file`, `type`, `community`, `location`, `tags:` including
`graphify/EXTRACTED`), a `## Connections` section with `[[wikilinks]]` between notes, a
`graph.canvas` (Obsidian Canvas JSON), a nested `.obsidian/graph.json` (Obsidian's own graph-view
config — written inside upstream's own output directory, never the vault's top-level
`.obsidian/`; harmless when that output directory is itself an OMES-owned subdirectory, since
Obsidian only reads `.obsidian/` at the vault root), and its own generated-files manifest,
`.graphify_obsidian_manifest.json`.

Because upstream's own output satisfies "vault-ready Markdown," `omes graphify export` **prefers
it**: it stages `graphify export obsidian --graph <graph.json> --dir <throwaway-temp-dir>` (never
writing directly into the vault), then post-processes every file upstream's own manifest lists
(`lib/omes/py/graphify/obsidian.py`'s `postprocess_upstream_export`) by injecting OMES's required
front-matter fields (§5.3) into every `.md` file **without disturbing upstream's own fields or
nested YAML lists** (`inject_front_matter_fields` edits the front-matter block textually, it does
not parse/re-serialize the whole block — ADR-0012 forbids a third-party YAML parser). Non-Markdown
files (the canvas, the nested `.obsidian/graph.json`, upstream's own manifest) are copied through
unmodified and tracked via OMES's own `.omes_export_manifest.json` bookkeeping file instead (§5.4).

**Fallback.** When the `graphify` CLI is not on `PATH`, or `graphify export obsidian` fails, or
it leaves no `.graphify_obsidian_manifest.json` behind, `omes graphify export` falls back to
rendering directly from `graph.json` using its own renderer
(`lib/omes/py/graphify/obsidian.py`'s `build_notes`) — one note per file-type node (with a
`## Symbols` list and a `## Links` section of `[[wikilinks]]` tagged `EXTRACTED`/`INFERRED`, per
§1.6), plus a generated `_index.md` and `_provenance.md`. This path never shells out to
`graphify` at all; it only reads `graph.json`. A warning is logged when the fallback triggers so
the operator knows which render path actually ran (also reported as `"render_mode"` in `--json`
output: `"upstream"` or `"fallback"`).

### 5.2 What `omes graphify export` never does

- Never writes anywhere outside `<vault>/<subdir>/` (plus, for `--init-vault`, the new vault's
  own top-level `.obsidian/` marker directory it just created).
- Never touches a pre-existing vault's top-level `.obsidian/` directory or any note outside the
  managed subdirectory, in either render path.
- Never installs, starts, or talks to a running Obsidian process.
- Never re-extracts anything — it only ever reads an already-produced `graphify-out/graph.json`.

### 5.3 Generated-note front matter and provenance

Every Markdown note this command writes or updates carries, at minimum:

```yaml
omes_generated: true
graphify_version: "0.9.64"
extraction_mode: "code"
generated_at: "2026-09-19T12:00:00Z"
graph_sha256: "<sha256 of the source graph.json>"
```

plus a `source` field (OMES's own renderer) or upstream's own `source_file` field (upstream
render path) naming the original source file the note describes. `omes_generated: true` is the
load-bearing marker: **a target path that already exists but does not carry this marker in its
front matter is never overwritten** — it is refused and reported (in `--json` output's
`conflicts` list, and as a `log_warn` line) instead, so an operator's own hand-authored note is
never silently clobbered by a re-export. OMES's own renderer additionally writes `_index.md`
(one entry per generated file note, plus a link to `_provenance.md`) and `_provenance.md` (the
source path, graphify version, extraction mode, generated-at timestamp, `graph.json` sha256, and
node/edge counts) — the audit record for that export, layered on top of, never replacing,
graphify's own `EXTRACTED`/`INFERRED` edge provenance (§1.6).

### 5.4 Non-Markdown output ownership (upstream render path only)

A YAML front-matter marker cannot be embedded in a canvas (`.canvas`, JSON) or a nested
`.obsidian/graph.json` file without risking breaking Obsidian's own schema for them, so those are
tracked instead via `.omes_export_manifest.json` — a small JSON bookkeeping file
(`{"omes_generated": true, "files": [...]}`) OMES writes into the managed subdirectory after
every successful write, recording every non-Markdown path it owns there. A future export
recognizes a previously-owned non-Markdown path (safe to overwrite) versus an unrecognized
pre-existing one at that same path (refused as a conflict, exactly like the Markdown marker
rule). `.omes_export_manifest.json` itself is always OMES-owned by construction.

### 5.5 Preview, backup, and rollback

- **`--dry-run`** plans the export (both render paths only ever *read* to build a plan; nothing
  is written to the vault) and reports, in human output and `--json`, the full list of planned
  writes (`"create"`/`"update"` per path) and any conflicts, without writing a single file.
- **Backup before write.** Before any real write, `omes graphify export` opens a
  graphify-export-scoped backup session (`backup_begin "graphify-export" ...` /
  `backup_path`/`backup_finish`, reusing `lib/omes/backup.sh` exactly like every other OMES
  mutation) covering the entire target subdirectory as it existed before this run — even on a
  first-ever export of an empty subdirectory (nothing to back up yet, an empty/no-op session is
  still recorded for consistency).
- **`omes graphify export rollback [--timestamp <ts>] [--yes] [--json]`** restores the most
  recent (or a named) `graphify-export` backup session via the same `restore_backup` (
  `lib/omes/restore.sh`) every other OMES restore path uses — scoped to sessions whose `META`
  records `module=graphify-export`, so it can never accidentally restore an unrelated backup.
  Requires confirmation (`--yes` or an interactive `y`), exactly like a real export write.

**Exit codes:** 0, 1 (python3/graphify.cli invocation failed, confirmation declined without
`--yes`, or `export rollback` found no matching backup session — exit `OMES_EX_BACKUP`=9 for
that last case specifically), 2 (usage error: missing `<graphify-out-dir>`, missing/invalid
`--vault`/`OBSIDIAN_VAULT_PATH`, `graphify-out-dir` or its `graph.json` missing, or vault exists
without `.obsidian/` and `--init-vault` was not given).

**JSON schema (write):**
`{"command":"graphify","subcommand":"export","ok":true,"vault":"/abs/vault","target_dir":"/abs/vault/graphify/myrepo","backup":"/state/backups/<ts>","render_mode":"upstream","result":{"ok":true,"written":[...],"conflicts":[...],"note_count":N},"exit_code":0}`.

**Examples:**

```bash
omes graphify export ~/code/myrepo/graphify-out --vault ~/Documents/MyVault --dry-run
omes graphify export ~/code/myrepo/graphify-out --vault ~/Documents/MyVault --yes
OBSIDIAN_VAULT_PATH=~/Documents/MyVault omes graphify export ~/code/myrepo/graphify-out --yes
omes graphify export ~/code/myrepo/graphify-out --vault ~/Documents/NewVault --init-vault --yes
omes graphify export rollback --yes                          # undo the last export
```
