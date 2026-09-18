# ADR-0012 — Graphify integration boundary

- **Status:** Accepted as design boundary; implementation tracked in issues
  [#50](https://github.com/ahliweb/omes/issues/50),
  [#51](https://github.com/ahliweb/omes/issues/51),
  [#52](https://github.com/ahliweb/omes/issues/52)
- **Date:** 2026-09-19
- **Decision maker:** @ahliweb
- **Related:** ADR-0003 (module contract), ADR-0004 (check before mutate),
  ADR-0005 (root/user scope separation), ADR-0006 (upstream installer with
  pinning — the same pattern this ADR reuses for graphify),
  [docs/graphify.md](../graphify.md), [Issue #49](https://github.com/ahliweb/omes/issues/49)

## Context

Issue #49 opens a four-part chain (#49 → #50 → #51 → #52) to integrate the
upstream "Graphify" tool (`Graphify-Labs/graphify`, PyPI package
`graphifyy`) with OMES and Hermes. Graphify turns a codebase (and,
optionally, docs/papers/images) into a queryable knowledge graph, with an
optional Obsidian-vault export and an optional MCP stdio server extra.

Before any install/workflow/MCP code is written, this ADR fixes where the
boundary sits: what OMES will own, what stays entirely upstream's
responsibility, and what Obsidian's role is. Getting this wrong first would
either (a) duplicate upstream extraction/provider-routing logic inside
OMES, or (b) make OMES depend on a human-operated desktop app (Obsidian) as
if it were a service, or (c) skip OMES's normal module lifecycle
(preflight, version pinning, uninstall) because "it's just a CLI wrapper."

Full identity facts, mode definitions, the ownership matrix, and the
local-vs-external processing boundary are recorded in
[docs/graphify.md](../graphify.md) §1; this ADR records the decision and
alternatives, not the full reference content.

## Options considered

### 1. Fork `Graphify-Labs/graphify` into this repository

- **Security:** worse — OMES would inherit responsibility for a Python
  dependency tree, provider-credential handling, and AST/LLM extraction
  code it did not write and has no ongoing reason to audit line-by-line.
- **Maintainability:** worse — duplicates upstream maintenance indefinitely;
  every upstream fix or provider addition has to be manually re-ported.
- **Compatibility/long-term:** worse — Apache-2.0 attribution and license
  compliance become an ongoing repository concern for code with no
  OMES-specific need to modify it.
- **Rejected:** no OMES-specific reason to fork; upstream already ships a
  usable CLI and MCP extra.

### 2. Invoke the upstream `graphify` CLI as a subprocess from an isolated Python tool environment, wrapped by an OMES module + Hermes skill wrapper

- **Security:** best available — `uv tool`/pipx isolate the install from
  system Python (PEP 668 compliance), consistent with how OMES already
  avoids `curl | bash` and system-wide installs elsewhere (ADR-0006).
- **Maintainability:** good — OMES's job stays "install, pin, invoke,
  validate, record provenance," never "understand graphify's internals."
- **Operational complexity:** matches the existing module contract
  (`docs/architecture.md` §4: check/apply/verify/rollback), so operators
  get the same install/uninstall/backup guarantees as every other module.
- **Chosen.**

### 3. Hermes-skill-only (rely purely on `graphify hermes install`'s own skill writer; no OMES module)

- **Operational complexity:** lower short-term (no new module to write),
  but insufficient long-term: OMES still needs install/preflight/
  version-pinning/uninstall lifecycle management consistent with every
  other OMES module (`docs/architecture.md` §4), which
  `graphify hermes install` does not provide — it only writes a skill file
  to `~/.hermes/skills/graphify/`, with no OMES-tracked install state,
  backup, or rollback.
- **Rejected as the sole mechanism** — it MAY still be used or wrapped by
  the Hermes skill wrapper in #51 (i.e. #51 could shell out to
  `graphify hermes install` as part of what it wires up), but an OMES
  module (#50) still owns the CLI install itself.

## Decision

1. OMES installs the `graphify` CLI (PyPI package `graphifyy`) into an
   isolated Python tool environment — `uv tool install graphifyy`
   preferred, `pipx install graphifyy` as fallback — managed by an OMES
   user-scope module (#50), following the same "download to file, verify,
   then execute" and version-pinning posture ADR-0006 established for the
   Hermes installer. OMES never runs `pip install` against system Python.
2. A thin `omes graphify run` command plus a Hermes skill wrapper (#51)
   shells out to the OMES-installed `graphify` CLI (defaulting to
   `graphify extract --code-only`, per `docs/graphify.md` §1.5 — semantic
   extraction is never enabled by default). This wrapper may itself invoke
   `graphify hermes install` as part of its wiring, but the CLI install and
   lifecycle remain owned by the #50 module, not by this wrapper.
3. Obsidian is documented as a local Markdown-vault consumer only. OMES
   never installs, starts, manages, or talks to a running Obsidian process.
4. The optional MCP stdio server (`graphifyy[mcp]` extra) is recorded as a
   fact for #52 to design against; this ADR does not decide how OMES
   wraps it, only that it exists upstream and is not implemented here.

## Consequences

### Positive

- OMES never touches system Python; PEP 668 compliance is structural, not
  a documentation reminder.
- Upgrades and uninstalls are isolated to the tool environment
  (`uv tool upgrade graphifyy` / `uv tool uninstall graphifyy`, or the
  pipx equivalents), reachable through the same `module_rollback`/`omes
  uninstall` path every other OMES module uses.
- Semantic/provider configuration (backend selection, API keys) stays
  entirely inside graphify's own env-var-driven routing; OMES never stores,
  proxies, or duplicates a provider credential for graphify.
- Obsidian never becomes an operational dependency OMES has to health-check
  or manage.

### Trade-offs

- OMES's wrapper module (#50) and workflow command (#51) add another
  Python-tooling dependency (`uv` or `pipx`) to the host, alongside the
  existing Bash/systemd/Hermes stack — this is the same trade-off ADR-0006
  already accepted for pinning an external installer, applied to a second
  upstream tool.
- Because graphify is upstream-versioned independently of OMES, drift
  between what this ADR/`docs/graphify.md` records and the currently
  installed `graphify --help`/README output must be re-verified whenever
  #50/#51/#52 actually implement against it — this document is a snapshot
  from 2026-09-19 against version `0.9.64`, not a live contract.

## Rejected alternatives

- **Fork `Graphify-Labs/graphify`:** rejected — duplicates upstream
  maintenance, adds license/attribution overhead, no OMES-specific need to
  modify extraction logic.
- **Hermes-skill-only, no OMES module:** rejected as the sole mechanism —
  insufficient for install/preflight/version-pinning/uninstall lifecycle
  parity with every other OMES module; may still be wrapped inside #51.
- **Make OMES install or manage Obsidian:** rejected — Obsidian is a
  human-operated desktop app, not a service; OMES/Hermes never talk to a
  running Obsidian process (see `docs/graphify.md` §1.2, §1.4).
- **Enable semantic extraction (`--backend`) by default in any OMES-driven
  invocation:** rejected — every OMES-triggered `extract` call defaults to
  `--code-only`; enabling a provider-backed semantic pass is always an
  explicit operator action (`docs/graphify.md` §1.5).

## Evidence and implementation tracking

- Boundary and this ADR: [#49](https://github.com/ahliweb/omes/issues/49)
- Installation module: [#50](https://github.com/ahliweb/omes/issues/50)
- Workflow command and Hermes skill wrapper:
  [#51](https://github.com/ahliweb/omes/issues/51)
- MCP integration: [#52](https://github.com/ahliweb/omes/issues/52)
