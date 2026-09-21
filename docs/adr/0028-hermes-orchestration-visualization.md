# ADR-0028 — Hermes Delegated-Task Orchestration and Live Subagent Visualization

- **Status:** Accepted
- **Date:** 2026-09-21
- **Decision maker:** @ahliweb
- **Related:** ADR-0017 (Upstream-first ownership and boundary enforcement), ADR-0019 (Hermes gateway and service management), ADR-0023 (Control Center UI/UX design system), ADR-0027 (Control Center secure pull-worker transport), [docs/control-center-and-integrations.md](../control-center-and-integrations.md), [docs/agent-orchestration-roadmap.md](../agent-orchestration-roadmap.md), [Issue #183](https://github.com/ahliweb/omes/issues/183), Epic [#195](https://github.com/ahliweb/omes/issues/195)
- **Supersedes / Amends:** Defines the observability boundary between Hermes Agent multi-task delegation and the OMES Control Center web interface.

---

## Context

Hermes Agent stable `v2026.9.14` supports parallel delegated tasks via `delegate_task(tasks=[...])`, supporting concurrency up to 10 child agents by default and recursive nested delegation. To observe runtime activity safely, Hermes exposes the `hermes.observer.v1` observer hook contract, providing lifecycle events (`subagent_start`, `subagent_stop`) with correlation identifiers.

Operators managing multi-host environments through the OMES Control Center require visibility into live delegation batches, active child agents, task progress, and failures. However, introducing subagent visibility must strictly avoid:
1. Re-implementing a second Hermes orchestration runtime, scheduler, or delegation manager in OMES.
2. Directly coupling to private Hermes SQLite databases (`messages.db`), in-process memory, or undocumented internal files.
3. Exposing sensitive data, including hidden chain-of-thought reasoning, raw prompts, full transcripts, shell commands, or credentials in web UI projections.
4. Conflating logical agent lifecycles with host OS process management (e.g. raw PIDs).

Under ADR-0017:
```text
DELEGATE -> PORT -> ADAPT -> DEFER -> REJECT
```
Hermes owns delegation, reasoning, tool execution, and child lifecycle. OMES must strictly **DELEGATE** execution to Hermes and provide only an **observability projection** for the Control Center.

---

## Evaluation of Options (11-Criteria Matrix)

We evaluated two architectural approaches:
- **Option A (Direct Subprocess / Database Inspection):** OMES monitors host process trees, inspects child PIDs, and queries Hermes internal SQLite databases (`messages.db`) to infer subagent status.
- **Option B (Standardized Observer Hook Ingestion & Normalized Projection - Recommended):** OMES ingests structured read-only events emitted by Hermes `hermes.observer.v1` hooks, normalizes them via strict schema contracts, reconstructs parent-child trees using opaque correlation IDs, and projects sanitized views to the Control Center.

| Criterion | Option A (Subprocess/DB Inspection) | Option B (Observer Hook Ingestion) |
|---|---|---|
| **1. Advantages & Disadvantages** | Pro: No adapter configuration needed. Con: Fragile schema coupling, database locking conflicts, platform-dependent PID tracking, zero abstraction. | Pro: Clean event-driven ingestion, strictly decoupled from Hermes storage internals, forward-compatible with Hermes upgrades. Con: Requires defining normalized wire contracts. |
| **2. Security & Privacy** | Critical Risk: Exposure of raw prompts, transcripts, credentials, and internal files in web payloads; direct database read bypasses RLS. | Outstanding: Mandatory fail-closed secret redaction, strict character limits (512 chars) on goals/summaries, zero chain-of-thought exposure, strict tenant scoping. |
| **3. Performance & Resource Footprint** | Polling SQLite databases and scanning `/proc` causes disk contention and CPU overhead. | Negligible: Asynchronous event ingestion into memory/projection store; minimal memory footprint for active trees. |
| **4. Maintainability & Code Quality** | Low: Breaks on any internal Hermes database schema revision or process manager change. | High: Bounded to stable `hermes.observer.v1` contracts; isolated in pure-Python `lib/omes/py/agent/orchestration.py`. |
| **5. Scalability & Concurrency** | Degrades under parallel batch workloads (10+ child agents competing for SQLite locks). | High: Naturally scales across concurrent child agents and nested child-grandchild hierarchies using opaque correlation keys. |
| **6. Accessibility** | Unstructured process lists are difficult to navigate with screen readers and assistive devices. | High: Structured, semantic HTML/CSS expandable hierarchy with ARIA tree roles and live regions in Control Center Screen 6. |
| **7. SEO Impact** | Neutral (internal administrative UI). | Neutral. |
| **8. UI/UX Implications** | Confusing raw PID/thread metrics that do not map to agent roles or task intents. | Intuitive & High-Density: Screen 6 displays clear delegation summaries, active process trees, elapsed duration, status badges, and safe metadata drawers. |
| **9. Platform Compatibility** | Fragile across varied container and sandbox environments where `/proc` or PID namespaces are restricted. | 100% compatible across Ubuntu 22.04/24.04/26.04 and Linux Mint 22 without host namespace dependencies. |
| **10. Operational Complexity** | High: Requires elevated read permissions across user profiles and SQLite locking management. | Minimal: Pure event collection and read-only projection via existing Control Center pull/push transport. |
| **11. Long-Term Architectural Health** | Creates severe technical debt by violating ADR-0017 boundaries and coupling OMES to private Hermes internals. | Enduring: Preserves clean boundaries; enables seamless migration as upstream Hermes enhances observer telemetry. |

---

## Decision

We adopt **Option B**:
1. **Upstream Authority Boundary:**
   - Hermes Agent remains the sole authority for task delegation (`delegate_task`), child concurrency, scheduling, tool execution, and prompt reasoning.
   - OMES provides strictly an **observability projection** for operators.
2. **Observer Hook Integration:**
   - Ingest events emitted by `hermes.observer.v1` (`subagent_start`, `subagent_stop`, `subagent_step`).
   - Treat correlation fields (`session_id`, `turn_id`, `parent_session_id`, `subagent_id`, `parent_subagent_id`) as opaque strings.
3. **Data Sanitization & Privacy:**
   - Prohibit display of hidden chain-of-thought, raw prompts, full transcripts, shell commands, or raw tool arguments.
   - Truncate and redact all goals and completion summaries (max 512 chars) using `_SECRET_NAME_RE`.
4. **Idempotency & Resilient Tree Reconstruction:**
   - Implement `lib/omes/py/agent/orchestration.py` to deterministically assemble process trees.
   - Tolerate out-of-order events (e.g. stop before start), deduplicate replay events, and transition unconfirmed running subagents to `STALE` after threshold expiration.
5. **Contract Enforcement:**
   - Enforce wire schemas `hermes-orchestration-event.schema.json` and `hermes-orchestration-tree.schema.json` under JSON Schema Draft 2020-12 fail-closed validation.
6. **Control Center UI Integration:**
   - Render live delegation batches and expandable process trees within Control Center Screen 6 (`isHermes` in `ui/control-center/index.html`).
