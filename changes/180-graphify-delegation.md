---
issue: 180
type: refactor
summary: delegate skill, MCP and workflow behavior to upstream Graphify and Hermes (ADR-0025)
---

### Summary of changes

1. **Architectural Decision Record (ADR-0025)**:
   - Authored `docs/adr/0025-graphify-upstream-delegation.md` evaluating the Graphify integration boundary across 11 architectural criteria.
   - Built a comprehensive command-by-command ownership matrix for `omes graphify *`:
     - Upstream Graphify owns AST/semantic extraction, graph formatting, querying (`graphify query`), git hooks (`graphify hook`), and platform skill generation (`graphify install --platform hermes`).
     - Hermes owns the assistant skill runtime and directly connects to the `graphify-mcp` stdio server.
     - OMES owns isolated tool-environment package installation/uninstallation (`uv tool` / `pipx`), version pinning (`OMES_GRAPHIFY_VERSION`), host path validation, code-only local defaults (`--code-only`), explicit semantic mode gating (`OMES_GRAPHIFY_PROVIDER_ENV`), and provenance audit recording (`omes-provenance.json`).
   - Registered ADR-0025 in `docs/adr/README.md`.
   - Updated `architecture/capabilities.json` recording `graphify.knowledge.extraction` and `graphify.mcp.server` as `released_supported` with upstream delegation, removing temporary duplication allowances, and adding `graphify.skill.hermes_installer` candidate tracking.

2. **Upstream Delegation and Compatibility Commands**:
   - Added `omes graphify extract` delegating directly to upstream `graphify extract` while enforcing path validation, code-only defaults, and provenance sidecar writing.
   - Preserved `omes graphify run` as a compatibility alias with a documented deprecation notice.
   - Added pass-through delegation commands: `omes graphify query` (wrapping `graphify query`) and `omes graphify hook` (wrapping `graphify hook`) passing exact fixed arguments.
   - Enhanced `omes graphify skill install`: probes installed Graphify CLI for `graphify install --platform hermes` and invokes upstream installation when available; falls back cleanly to the bundled skill with an actionable deprecation notice when running against baseline 0.9.64.
   - Added `_graphify_version_policy_check` enforcing minimum supported baseline (0.9.64), verifying pinned `OMES_GRAPHIFY_VERSION`, and emitting candidate warnings when upstream releases newer than the verified baseline are detected.

3. **Privacy and Safety Guardrails**:
   - Extraction strictly defaults to `--code-only` (100% local, no provider credentials).
   - `--mode semantic` requires explicit operator opt-in, non-empty pointer variable name via `OMES_GRAPHIFY_PROVIDER_ENV`, and confirmation before calling external APIs.
   - Provider API keys and secret values are never stored in argv, logged, emitted in JSON envelopes, or recorded in provenance sidecars.
