# ADR-0025 — Graphify upstream delegation and skill/MCP boundary

- **Status:** Accepted
- **Date:** 2026-09-21
- **Decision maker:** @ahliweb
- **Related:** ADR-0014 (Graphify integration boundary), ADR-0017 (Upstream-first ownership and boundary enforcement), ADR-0019 (Hermes gateway and service management), ADR-0022 (Hermes runtime health), [docs/graphify.md](../graphify.md), [Issue #180](https://github.com/ahliweb/omes/issues/180)
- **Supersedes / Amends:** Amends ADR-0014 by delegating workflow, query, hook, and skill installation to upstream Graphify CLI and Hermes while preserving OMES host lifecycle, provenance, and privacy boundaries.

---

## Context

Issue [#49](https://github.com/ahliweb/omes/issues/49) through [#55](https://github.com/ahliweb/omes/issues/55) established the initial Graphify integration under ADR-0014. OMES installed the `graphifyy` package into an isolated tool environment (`uv tool` / `pipx`), created a workflow runner (`omes graphify run`), bundled a custom Hermes skill (`modules/graphify/skill/`), implemented export post-processing (`omes graphify export`), filesystem change detection (`omes graphify sync`), and an MCP health probe (`omes graphify mcp health`).

Since that initial implementation:
1. Upstream Graphify (`Graphify-Labs/graphify`, PyPI `graphifyy`) has matured and now ships native extraction, query (`graphify query`), export (`graphify export`), git hook automation (`graphify hook {install,uninstall,status}`), and native Hermes skill installation (`graphify install --platform hermes`).
2. PyPI reports newer releases (e.g. `0.9.65`) advancing beyond the original verified `0.9.64` baseline.
3. Under ADR-0017, OMES mandates an upstream-first hierarchy:
   ```text
   DELEGATE -> PORT -> ADAPT -> DEFER -> REJECT
   ```
4. Maintaining parallel wrapper logic inside OMES duplicates upstream effort and creates maintenance drag whenever Graphify evolves.

Issue [#180](https://github.com/ahliweb/omes/issues/180) directs OMES to delegate Graphify workflow and skill/MCP behavior upstream to Graphify and Hermes, while retaining only what OMES uniquely owns: supported host installation, version pinning, provenance audit trails, and privacy/path safety controls.

---

## Evaluation of Options (11-Criteria Matrix)

We evaluated two primary architectures:
- **Option A (Status Quo Wrapper Model):** Retain all custom OMES wrappers (`omes graphify run`, custom bundled skill copy, custom query/sync routines), treating upstream CLI as an internal implementation detail.
- **Option B (Thin Delegation & Upstream-First Model - Recommended):** Delegate knowledge extraction, querying, hooks, and skill installation directly to upstream `graphify` and Hermes, preserving `omes graphify` wrapper commands only as compatibility aliases with explicit deprecation notices, while retaining OMES-owned installation, version pinning, provenance recording, and privacy guardrails.

| Criterion | Option A (Wrapper Model) | Option B (Thin Upstream Delegation) |
|---|---|---|
| **1. Advantages & Disadvantages** | Pro: Exact control over every byte written. Con: High duplication, fragile across upstream releases, slow adoption of new Graphify features. | Pro: Zero wrapper drift, immediate benefit from upstream AST/query enhancements, clear authority boundaries. Con: Requires version/capability gating. |
| **2. Security** | Custom wrappers validate paths and redact secrets, but may diverge from upstream security fixes. | Preserves OMES path traversal validation, code-only default (`--code-only`), explicit semantic opt-in (`OMES_GRAPHIFY_PROVIDER_ENV`), and zero secret leakage in logs/provenance. |
| **3. Performance** | Extra subprocess and argument translation layers. | Direct CLI execution and upstream native caching; zero unnecessary overhead. |
| **4. Maintainability** | Poor: High maintenance cost whenever upstream adds commands, options, or platforms. | Excellent: Adheres strictly to ADR-0017 (`DELEGATE`); OMES code footprint reduced. |
| **5. Scalability** | Rigid: Each new upstream capability requires a corresponding OMES PR. | High: Upstream commands (`query`, `hook`, `export`) are consumed directly or via thin pass-through. |
| **6. Accessibility** | CLI output only; custom error messages. | Standard CLI exit codes (0, 1, 2) and structured JSON envelopes preserved for automation. |
| **7. SEO Impact** | Neutral (developer/operator tool). | Neutral. |
| **8. UI/UX Implications** | Requires operators to remember `omes graphify run` instead of standard `graphify` CLI. | Consistent developer experience: standard `graphify` syntax works everywhere; compatibility aliases guide users with informative notices. |
| **9. Compatibility** | Full backward compatibility with existing tests. | Full backward compatibility: existing `omes graphify run/skill/export` commands remain functional as compatibility aliases with clear deprecation guidance. |
| **10. Operational Complexity** | High: Operators must understand both OMES wrappers and upstream Graphify semantics. | Low: Operators follow upstream Graphify documentation, with OMES handling installation and environment safety. |
| **11. Long-Term Implications** | Accumulates technical debt and diverges from upstream Hermes and Graphify roadmaps. | Positions OMES as a robust deployment/runtime harness rather than an imitation of upstream tools. |

---

## Decision

We adopt **Option B**:
1. **Command-by-Command Ownership Matrix:**
   - **`graphify extract` / `omes graphify extract` / `omes graphify run`:** Upstream Graphify owns the extraction engine. OMES provides host path validation, code-only local default, explicit semantic opt-in with non-empty credential variable name checking (`OMES_GRAPHIFY_PROVIDER_ENV`), and writes the `omes-provenance.json` audit sidecar. `omes graphify run` remains as a compatibility alias for `omes graphify extract` with a documented deprecation notice.
   - **`graphify query` / `omes graphify query`:** Upstream owns graph querying entirely. `omes graphify query` is a thin, pass-through delegate passing exact argv to upstream `graphify query`.
   - **`graphify hook` / `omes graphify hook`:** Upstream owns git hook lifecycle. `omes graphify hook` is a thin, pass-through delegate passing exact argv to upstream `graphify hook`.
   - **`omes graphify skill install`:** OMES delegates to upstream `graphify install --platform hermes` when supported by the installed Graphify version. If the installed Graphify CLI does not support `--platform hermes` (e.g. baseline 0.9.64), OMES falls back to the bundled skill with an actionable notification. Rollback/uninstall safely cleans Hermes skill directory.
   - **`graphifyy[mcp]` / `omes graphify mcp health`:** Upstream owns the MCP server implementation (`graphify-mcp`). Hermes configures the MCP server directly via stdio. OMES does not supervise, proxy, or daemonize the MCP process. `omes graphify mcp health` remains as a read-only health diagnostic.
   - **`omes graphify export`:** Delegates note generation to upstream `graphify export obsidian` when available, with OMES-owned front-matter markers, collision prevention, and atomic vault staging.
   - **`omes graphify sync` / `status` / `init-ignore` / `purge`:** Retained for non-git directory debounced synchronization and privacy boundary enforcement (`.graphifyignore` and safe deletion).

2. **Privacy & Security Guardrails:**
   - Default mode remains strictly `--code-only` (100% local AST extraction, no network egress, no API keys).
   - Semantic mode requires explicit operator opt-in (`--mode semantic`), confirmation, and a designated non-empty environment variable named by `OMES_GRAPHIFY_PROVIDER_ENV`.
   - Provider credential values are never accepted as CLI flags, logged to stdout/stderr, written to JSON envelopes, or stored in provenance files.

3. **Capability Gating & Version Baseline:**
   - Supported baseline remains `0.9.64` until newer releases (e.g., `0.9.65`) undergo full compatibility and security verification.
   - The capability registry `architecture/capabilities.json` tracks `graphify.knowledge.extraction` as `released_supported` and marks the skill/MCP delegation as complete, removing the temporary duplication allowance.

---

## Consequences

### Positive
- Strict alignment with ADR-0017: OMES delegates knowledge extraction and skill/MCP runtime behavior upstream.
- Operators can use native `graphify` commands directly while retaining OMES host safety.
- Zero credential leakage and preserved local-first privacy defaults.
- Transparent deprecation path for legacy OMES wrappers.

### Negative / Trade-offs
- Upstream skill installation behavior depends on the installed Graphify version, necessitating a capability probe and fallback mechanism for older versions.
