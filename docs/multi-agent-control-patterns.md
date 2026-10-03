# Multi-agent control patterns (epic #269)

> **Status: phase 0 decision record.** This document and
> [ADR-0032](adr/0032-multi-agent-control-patterns-boundary.md) record which
> multi-agent control patterns OMES adopts, delegates, defers or rejects, and
> which verified gaps become child issues. **No runtime behavior changes in
> this phase.** Every capability marked "Not implemented yet" below stays
> unimplemented until its child issue lands. Owning issue:
> [#269](https://github.com/ahliweb/omes/issues/269).

OMES is host control (deterministic). Hermes Agent owns agent runtime behavior
(probabilistic). AWCMS owns tenants, approvals and presentation. This document
changes none of those authorities (AGENTS.md §2, ADR-0017). In particular,
**OMES does not mediate all Hermes-native tool execution**
([architecture §18.2](architecture.md#182-limitations--read-this-before-assuming-omes-mediates-agent-behavior));
OMES is not an ACP proxy and not an agent scheduler.

## 1. Scope and sources

Validation date: **2026-10-03**. Octop is a reference project only and is not
an OMES dependency. Statements about upstream projects are limited to the
pages below; anything not stated there is marked unverified.

| Source | URL | Used for |
|---|---|---|
| Epic #269 | <https://github.com/ahliweb/omes/issues/269> | Scope, children #270–#277, non-goals |
| Octop expert teams | <https://github.com/TencentCloud/Octop/blob/main/docs/expert-teams.md> | Team host, async dispatch, in-process tracking |
| Octop architecture | <https://github.com/TencentCloud/Octop/blob/main/docs/architecture.md> | Control-plane DB vs workspace files, single process |
| Octop ACP | <https://github.com/TencentCloud/Octop/blob/main/docs/acp.md> | Inbound server, outbound runners, `trusted` flag |
| Hermes delegation (`v2026.9.24`) | <https://github.com/NousResearch/hermes-agent/blob/v2026.9.24/website/docs/user-guide/features/delegation.md> | `delegate_task`, limits, global model pin, restart to `unknown` |
| Hermes ACP (`v2026.9.24`) | <https://github.com/NousResearch/hermes-agent/blob/v2026.9.24/website/docs/user-guide/features/acp.md> | `hermes acp`, curated toolset, approval prompts |
| Hermes outbound ACP proposal | <https://github.com/NousResearch/hermes-agent/issues/5257> | Open proposal (P4); not shipped |
| OMES repository audit | files cited inline (2026-10-03) | Current state and gaps |

The Hermes baseline `v2026.9.24` is the version pinned in
`lib/omes/versions.sh`. Features seen only on upstream `main` or in open
issues are never treated as supported (registry rule, ADR-0017).

## 2. Octop pattern to OMES disposition

Disposition vocabulary: **DELEGATE** (Hermes owns it), **ADAPT** (reuse the
concept in OMES's own authority), **OMES** (host-control concern OMES may own),
**AWCMS** (control-plane/presentation), **DEFER** (not justified yet or no
stable upstream interface), **REJECT** (contradicts scope).

| Pattern | Octop evidence | OMES-ecosystem owner | Disposition | Existing reuse | Child issue |
|---|---|---|---|---|---|
| Team host that only schedules | Team host schedules and dispatches professional work asynchronously to members ([expert-teams](https://github.com/TencentCloud/Octop/blob/main/docs/expert-teams.md)) | Hermes (`orchestrator` role, `delegate_task`) | DELEGATE | Registry `hermes.agent.delegation` (added by this change); observer projection (ADR-0028) | [#270](https://github.com/ahliweb/omes/issues/270) |
| Parallel asynchronous dispatch | Members run in parallel across members, serially per member (same page) | Hermes (`max_concurrent_children`, default 10; `max_spawn_depth`, default 1) | DELEGATE | Same as above | [#270](https://github.com/ahliweb/omes/issues/270) |
| Per-agent workspace, checkpoint and memory isolation | Per-agent files under `~/.octop/agents/<agent_id>/`, per-agent LangGraph checkpointing, row-level agent ownership ([architecture](https://github.com/TencentCloud/Octop/blob/main/docs/architecture.md)) | Hermes (sessions, memory, `worktree_isolation`); OMES for OS isolation | DELEGATE (logical), OMES (host) | systemd hardening profiles, rootless Compose (#96) | [#276](https://github.com/ahliweb/omes/issues/276) |
| Multi-user isolation and separate control-plane DB | Control-plane data (users, agents, providers, channels, cron, sessions) in SQLite/PostgreSQL, separate from workspace files (same page) | AWCMS (tenant identity, RLS) | AWCMS | Tenant scope in the job store and observer; Control Center contracts | [#272](https://github.com/ahliweb/omes/issues/272) (identifiers only) |
| MCP boundary | Not a distinct Octop claim relied on here | Logical boundary (not OMES-shipped) | DEFER (stays a logical boundary) | Registry `boundary.tool_gateway.mcp` (`logical_boundary`) | None |
| Bidirectional ACP | Inbound `octop acp` server; outbound runners flagged `"trusted": true`; permission prompts surface in chat ([acp](https://github.com/TencentCloud/Octop/blob/main/docs/acp.md)) | Hermes (inbound `hermes acp`); outbound is upstream proposal #5257 | DELEGATE (inbound), DEFER (outbound) | Registry `hermes.agent.acp_server` (added by this change) | [#273](https://github.com/ahliweb/omes/issues/273) |
| Unified channel gateway | Web UI, CLI and IM share one runtime via `ChannelManager` (architecture page) | Hermes (channels, messaging) | DELEGATE; REJECT an OMES-owned gateway | `modules/hermes-gateway` handles service lifecycle only | None |
| Mission-Control-like UI | Not relied on as a verified Octop claim | AWCMS (presentation; epic #263, ADR-0031 pending in PR [#268](https://github.com/ahliweb/omes/pull/268)) | AWCMS | Observer projection contract (ADR-0028) | [#277](https://github.com/ahliweb/omes/issues/277) |
| Process-local in-flight tracking (limitation) | In-flight team-job tracking is in-process (`TeamJobTracker`); out-of-process inbox persistence is a v1 non-target and "restart loses in-flight tasks"; "The whole stack is one process. There is no separate worker, no external queue" | Hermes for agent runs; OMES for host jobs | Do not copy; OMES reconciles host jobs | Job store, idempotency index, hash-chained audit (`lib/omes/py/jobs/`) | [#271](https://github.com/ahliweb/omes/issues/271) |

The two "not relied on" cells are deliberate: this document does not assert an
Octop MCP or UI behavior that was not verified on the pages listed in §1.

## 3. Capability decision matrix

"Current state" cites a repository file or an upstream page. "Not implemented
yet (tracked in #N)" means no code or contract exists today.

| Capability | Authority | Verified current state | Disposition | Tracked in |
|---|---|---|---|---|
| Team-host coordinator | Hermes | Hermes `delegate_task` with `orchestrator` and `leaf` roles; `max_spawn_depth` default 1 (flat), up to 3 ([delegation](https://github.com/NousResearch/hermes-agent/blob/v2026.9.24/website/docs/user-guide/features/delegation.md)). OMES has no coordinator and R9 forbids one. | DELEGATE | [#270](https://github.com/ahliweb/omes/issues/270) (policy and capability audit; posture check not implemented yet) |
| Opus to Sonnet delegation tiering | Hermes | Global delegation `model`/`provider` pin only: "`delegate_task` has no per-task model parameter" (same page). Per-task tiering is a verified upstream gap. | DELEGATE_UPSTREAM, then ADAPT (separate Hermes profiles), then SPECIALIZED_SERVICE, then DEFER | [#270](https://github.com/ahliweb/omes/issues/270) |
| Opus to Haiku delegation tiering | Hermes | Same as the row above. | Same as the row above | [#270](https://github.com/ahliweb/omes/issues/270) |
| Per-agent workspace | Hermes (logical); OMES (host paths) | Hermes `worktree_isolation` exists, default `false`. OMES manages host paths and permissions only. | DELEGATE | [#276](https://github.com/ahliweb/omes/issues/276) |
| Per-agent memory | Hermes | Children start with fresh context; the parent receives only the final summary (delegation page). OMES must not touch internal Hermes databases (registry rule). | DELEGATE | None |
| Asynchronous/parallel dispatch | Hermes | Parallel children with `delegation.max_concurrent_children` default 10; `max_iterations`; `child_timeout_seconds` (0 means no timeout). | DELEGATE | [#270](https://github.com/ahliweb/omes/issues/270) |
| Agent isolation | Hermes (logical); OMES/infrastructure (OS) | Implemented host isolation: hardening profiles (`modules/hermes-gateway/hardening.sh`), rootless Compose with non-root, `capDrop`, read-only rootfs, `no-new-privileges` and no Docker socket (`lib/omes/py/agent/compose.py`). | DELEGATE (logical), OMES (OS) | [#276](https://github.com/ahliweb/omes/issues/276) |
| MCP tool boundary | Logical boundary | Registry entry `boundary.tool_gateway.mcp` is `logical_boundary`; OMES ships no MCP gateway (architecture §16.2.1, scope non-goal 14). | DEFER (stays logical) | None |
| ACP | Hermes | `hermes acp` is a stdio JSON-RPC server with a curated `hermes-acp` toolset; dangerous terminal commands go to editor approval prompts ([acp](https://github.com/NousResearch/hermes-agent/blob/v2026.9.24/website/docs/user-guide/features/acp.md)). Outbound ACP client is an open proposal ([#5257](https://github.com/NousResearch/hermes-agent/issues/5257)). OMES adds no listener. | DELEGATE (inbound), DEFER (outbound) | [#273](https://github.com/ahliweb/omes/issues/273) (posture evidence; not implemented yet) |
| Unified IM gateway | Hermes | Channels and messaging are Hermes-owned (AGENTS.md §2). | DELEGATE; no OMES gateway | None |
| Mission Control | AWCMS | OMES defines the read-only observer contract (ADR-0028); no Mission Control view exists in this repository. | AWCMS (blocked until sources exist) | [#277](https://github.com/ahliweb/omes/issues/277) |
| Persistent host-operation jobs | OMES | Implemented: job store, idempotency index, hash-chained audit, read-back verification (`lib/omes/py/jobs/`). Gap: an orphaned `running` record is not reconciled after restart (see §4, G1). | OMES | [#271](https://github.com/ahliweb/omes/issues/271) |
| Persistent agent orchestration | Hermes | Hermes does not resume a running child after a restart; its attempt becomes `unknown` (delegation page). OMES observer ingests `hermes.observer.v1` events (`lib/omes/py/agent/orchestration.py`) but has no production caller or transport (§4, G3). | DELEGATE | [#271](https://github.com/ahliweb/omes/issues/271) (consumption of `unknown`) |
| Distributed OMES workers | OMES | Control Center pull worker exists for host-control jobs (ADR-0027, `lib/omes/py/jobs/worker.py`). It is not a distributed AI worker. | OMES (existing) | None |
| Distributed agent workers | Hermes | No OMES capability. A broker or workflow engine needs measured requirements and a new ADR (ADR-0032 rule 4). Octop is itself single-process. | DEFER | None |
| Event sourcing | OMES (per-domain audit only) | Hash-chained, append-only audit log in `lib/omes/py/jobs/audit.py`. No universal event store; none is planned. | REJECT (universal store) | None |
| Tracing | OMES contract, observability plane | `correlation_id` is widespread; `event_id` exists on outbox/events. The 2026-10-03 audit found no `causation_id`, `trace_id`, `span_id` or `traceparent`, and no OpenTelemetry in `lib/`, `modules/` or `contracts/`. Since then the versioned envelope contract `contracts/observability/v1/correlation-envelope.schema.json` and its validator/convergence reducer `lib/omes/py/observability/envelope.py` are implemented (see §4a). No OpenTelemetry SDK or exporter is added. Propagation of the identifiers through AWCMS, job and worker flows and any producer wiring: Not implemented yet (tracked in [#272](https://github.com/ahliweb/omes/issues/272)). | ADAPT | [#272](https://github.com/ahliweb/omes/issues/272) (contract and reducer implemented; propagation not implemented yet) |
| Policy engine | Existing authorities (AWCMS, OMES, Hermes, infrastructure) | `operation-request.permission` (`granted`, `policy_id`, `reason`, `requires_approval`); OMES re-derives it and never trusts `granted: true`. AI-egress decisions `allow`/`deny`/`approval_required` with `AI_EGRESS_*` reason codes. Job `DESTRUCTIVE_OPERATIONS`, `DEFAULT_AUTO_APPROVE` (`lib/omes/py/jobs/store.py`). The unified decision envelope contract and a pure composer now exist (§4b); each of those authorities still makes its own decision, and no authority emits the envelope yet (Not implemented yet, tracked in [#274](https://github.com/ahliweb/omes/issues/274)). | ADAPT (envelope only; no new engine) | [#274](https://github.com/ahliweb/omes/issues/274) (contract and composer implemented; emission and wiring not implemented yet) |
| Capability permissions | AWCMS and OMES | Entitlement checks with `RESOURCE_KEYS` (`lib/omes/py/jobs/entitlement.py`); permission re-derivation as above. Decisions about one action can be recorded as `policy-decision` envelopes and composed (§4b); OMES evaluates no capability rule for this. | OMES (existing) | [#274](https://github.com/ahliweb/omes/issues/274) (envelope and composer implemented; wiring not implemented yet) |
| Approval gates | AWCMS (business); Hermes (tool); OMES (job approve) | `omes job approve` for non-auto-approved operations; Hermes ACP approval prompts "allow once / allow always / deny"; AWCMS owns tenant approvals. An `approval_required` decision envelope carries only an opaque `approval_ref` to the existing approval; OMES adds no approval inbox and never creates or infers an approval (§4b). | AWCMS / DELEGATE / OMES per scope | [#274](https://github.com/ahliweb/omes/issues/274) (reference propagation implemented; wiring not implemented yet) |
| Budget and token governance | AWCMS (policy); Hermes and infrastructure (enforcement) | No token, usage or spend concept in OMES. `docs/security.md` states OMES sets no LLM-spend ceiling. Hermes limits (`max_iterations`, `child_timeout_seconds`, depth, concurrency) and host cgroup limits exist. | AWCMS + DELEGATE | [#275](https://github.com/ahliweb/omes/issues/275) (not implemented yet) |
| Workload isolation | OMES/infrastructure (OS); Hermes (logical) | Hardening profiles, rootless Compose (#96), opt-in `hermes-restricted` egress `IPAddressDeny=any`. Gaps in §4, G5. | OMES (OS) | [#276](https://github.com/ahliweb/omes/issues/276) |
| Node/process restart recovery | Hermes (agent runs); OMES (host jobs) | Hermes: running child becomes `unknown` after restart. OMES: no orphan reconciliation for host jobs (§4, G1). | DELEGATE / OMES | [#271](https://github.com/ahliweb/omes/issues/271) |

## 4. Verified gaps

Evidence is from the repository audit of 2026-10-03. Line numbers refer to
that tree and may move; the file and symbol names are the stable reference.

| ID | Gap | Evidence | Child |
|---|---|---|---|
| G1 | The job runner persists `running` before executing the operation, and nothing reconciles a `running` record whose process died. `expire_stale_jobs` only handles `queued`/`approved`; `cancel` rejects `running`; there is no per-job lease. | `lib/omes/py/jobs/runner.py:289-296` (`run_started` saved before execution); `lib/omes/py/jobs/store.py:396-402` (`cancel`), `lib/omes/py/jobs/store.py:405-420` (`expire_stale_jobs`) | [#271](https://github.com/ahliweb/omes/issues/271) |
| G2 | No per-node `STALE` state. Staleness only affects tree-level freshness (`live`/`stale`/`unknown`, 300 s), although ADR-0028 (Decision 4) describes transitioning unconfirmed running subagents to `STALE`. This change adds an implementation note to ADR-0028 and corrects the matching sentence in `docs/control-center-and-integrations.md` §4; per-node `STALE` is not claimed. | `lib/omes/py/agent/orchestration.py:24` (`DEFAULT_STALE_THRESHOLD_SECONDS = 300`), `:285-293` (`is_any_stale` set at tree level) | [#271](https://github.com/ahliweb/omes/issues/271) |
| G3 | `ingest_event` has no production caller or transport in this repository, so the orchestration projection has no live event source. The `HERMES_BASELINE` constant is defined but unused. | `lib/omes/py/agent/orchestration.py:115` (`ingest_event`), `:23` (`HERMES_BASELINE`); repository-wide search found no non-test caller | [#271](https://github.com/ahliweb/omes/issues/271), [#272](https://github.com/ahliweb/omes/issues/272) |
| G4 | No cross-plane causation or trace identifiers; no OpenTelemetry. **Partly closed:** the envelope contract (with `causation_id`, W3C-shaped `trace_id` and `span_id`) and a deterministic convergence reducer now exist (§4a). **Still open:** no producer emits the envelope, and nothing propagates the identifiers across AWCMS, job and worker flows. Not implemented yet (tracked in [#272](https://github.com/ahliweb/omes/issues/272)). | At audit time, a search of `lib/`, `modules/`, `contracts/` for `traceparent`, `trace_id`, `span_id`, `causation_id`, `opentelemetry` returned nothing. Now: `contracts/observability/v1/correlation-envelope.schema.json`, `lib/omes/py/observability/envelope.py`; a repository search still finds no producer or propagation caller outside tests and fixtures | [#272](https://github.com/ahliweb/omes/issues/272) |
| G5 | No unified policy/capability decision envelope; policy facts are spread across `operation-request.permission`, AI-egress decisions and job approval sets. **Partly closed:** the `policy-decision` envelope contract and a pure composer now exist (§4b). **Still open:** no authority emits the envelope and no job runner, worker or AWCMS flow consumes the composed result. Not implemented yet (tracked in [#274](https://github.com/ahliweb/omes/issues/274)). | `lib/omes/py/jobs/store.py:112-114` (`DESTRUCTIVE_OPERATIONS`, `DEFAULT_AUTO_APPROVE`); `lib/omes/py/privacy/egress_policy.py` (`AI_EGRESS_*`). Now: `contracts/observability/v1/policy-decision.schema.json`, `lib/omes/py/observability/policy_decision.py`; a repository search finds no emitter or caller outside tests and fixtures | [#274](https://github.com/ahliweb/omes/issues/274) |
| G6 | No token, usage or spend concept; OMES documents that it sets no LLM-spend ceiling. | `docs/security.md` (non-guarantees list); `lib/omes/py/jobs/entitlement.py:25-32` (`RESOURCE_KEYS` has no budget key) | [#275](https://github.com/ahliweb/omes/issues/275) |
| G7 | Compose network is a plain bridge with `internal: false`, so there is no per-container egress control. There is no real rootless-daemon evidence and no declared-versus-running drift detection. | `lib/omes/py/agent/compose.py:400-403`; compare opt-in `modules/hermes-restricted/module.sh:467` (`IPAddressDeny=any`) | [#276](https://github.com/ahliweb/omes/issues/276) |
| G8 | Hermes per-task model selection does not exist upstream at the pinned baseline. | Delegation page, quote in §3 | [#270](https://github.com/ahliweb/omes/issues/270) |

## 4a. Cross-plane correlation envelope (issue #272, implemented parts)

ADR-0032 rule 5: the envelope is **evidence, not authority**. It records that
an authority observed something; it never grants permission, approves,
mutates, or carries prompts, transcripts, commands, tool arguments/results,
headers, credentials or reasoning. A user or operator can still reach Hermes
directly, so the envelope stream cannot be complete for agent activity (see
the architecture §18.2 limitation).

| Element | State | Where |
|---|---|---|
| Contract `correlation-envelope` (area `observability/v1`, `additionalProperties: false`) | Implemented | `contracts/observability/v1/correlation-envelope.schema.json`; fixtures under `contracts/observability/v1/fixtures/correlation-envelope/` |
| Fail-closed validation (unsupported major version, forbidden raw/free-text field names, secret-shaped values, calendar-invalid timestamps) | Implemented | `lib/omes/py/observability/envelope.py` (`validate`), reusing `lib/omes/py/jobs/schema.py` |
| Deterministic, order-independent convergence reducer (dedupe by `event_id`, cross-tenant rejection, `(source_timestamp, event_id)` order, terminal status final) | Implemented | `envelope.reduce_events` (pure function; projection is rebuildable from the event set) |
| Registry entry `omes.observability.correlation_envelope` (authority `omes`, plane `observability`, `observational`) | Implemented | `architecture/capabilities.json` |
| Propagation of `correlation_id`, `causation_id`, `trace_id`, `span_id` through AWCMS to job to worker flows | Not implemented yet (tracked in #272) | none |
| Any producer that emits envelopes (OMES job runner, Hermes observer, AWCMS) and any consumer or store | Not implemented yet (tracked in #272) | none |
| OpenTelemetry SDK, collector or exporter | Not implemented; none is planned by this change | none |

Design notes:

- **Status is a closed enum**, not free text: `pending`, `running`,
  `succeeded`, `failed`, `cancelled`, `interrupted`, `expired`, `rolled_back`,
  `unknown`. Convergence needs a machine-known terminal/non-terminal
  partition, and a closed vocabulary leaves no free-text channel. `unknown`
  means the source cannot prove the outcome and is never promoted to success
  (ADR-0032 rule 3). Producers map their own vocabularies onto it (for
  example job `queued`/`approved` to `pending`, Hermes `UNKNOWN` to `unknown`).
- **Timestamps are UTC `Z` strings** (the validator subset forbids `format`).
  Ordering parses them; strings with different fraction lengths do not sort
  chronologically, so the reducer never compares them as text.
- **Projection key** is `(tenant_id, correlation_id, run_id, task_id)` with an
  absent `run_id`/`task_id` as null. A retry that must be tracked separately
  should use a new `run_id`; a terminal status is final for its key.
- **Terminal handling:** later non-terminal events, including `unknown`, never
  revert a terminal status. The earliest terminal event decides; a later,
  different terminal status is not adopted and is surfaced as
  `conflicting_terminal: true` for review.
- **Fail closed:** any invalid envelope, `event_id` reused with different
  content, or a `correlation_id` shared by more than one tenant makes the
  whole reduction fail with no projection.
- The projection `classification` is the maximum over its events, so it
  never under-classifies (ADR-0029).

## 4b. Policy/capability decision envelope (issue #274, implemented parts)

ADR-0032 rule 7: the envelope **records and composes** decisions that existing
authorities already made. It is not a policy engine, it evaluates no rule, it
has no free-text reason field, and it never creates or grants an approval.

| Element | State | Where |
|---|---|---|
| Contract `policy-decision` (area `observability/v1`, `additionalProperties: false`): tenant, correlation, actor, resource, allowlisted action name, `deciding_authority`, closed `decision`, `policy_id`, `reason_code`, evidence freshness, `approval_ref`, `expires_at`, classification, redaction state | Implemented | `contracts/observability/v1/policy-decision.schema.json`; fixtures under `contracts/observability/v1/fixtures/policy-decision/` |
| Fail-closed validation (unsupported major version, forbidden field names, secret-shaped values, control characters, calendar-invalid timestamps, `expires_at` not after `evidence.observed_at`) | Implemented | `lib/omes/py/observability/policy_decision.py` (`validate`), reusing `lib/omes/py/jobs/schema.py` and the helpers of `lib/omes/py/observability/envelope.py` |
| Pure, order-independent composition of decisions for one `(tenant_id, correlation_id, requested_action)` | Implemented | `policy_decision.compose` (clock and required authorities are parameters) |
| Registry: the existing `omes.observability.correlation_envelope` capability (module `observability`) was broadened in title and evidence URLs instead of adding a second capability for the same module | Implemented | `architecture/capabilities.json` |
| An authority (AWCMS, OMES job runner, Hermes) emitting the envelope; the job runner, worker or AWCMS consuming the composed result | Not implemented yet (tracked in #274) | none |
| A policy engine, rule evaluation, approval inbox or approval creation by OMES | Not implemented; rejected by ADR-0032 rule 7 | none |

Composition rules:

- **One action only.** All inputs must share `tenant_id`, `correlation_id` and
  `requested_action`; any mix is an error and no composed result is produced.
  A `decision_id` reused with different content is an error.
- **Normalisation.** A decision whose `evidence.freshness` is not `live`, or
  whose `expires_at` is not after `now`, is treated as `unavailable`
  (`EVIDENCE_NOT_LIVE`, `DECISION_EXPIRED`). This includes a stale `deny`.
- **Precedence:** `deny` > `unavailable` > `approval_required` > `allow`.
- **Allow is earned.** The result is `allow` only if every input is `allow` and
  every required authority contributed a decision. A missing required
  authority is an `unavailable` contribution (`AUTHORITY_DECISION_MISSING`);
  a `deny` still outranks it. Empty input is `unavailable` (`NO_DECISIONS`).
  `required_authorities` and `now` are required parameters with no default.
- **Approvals are references.** `approval_required` propagates the
  `approval_ref` values of in-effect approval-required decisions unchanged,
  sorted. A missing reference stays missing; an `approval_ref` on an `allow`
  or on a losing decision is not propagated.
- **Determinism.** The result depends only on the set of input decisions. The
  composed result carries the earliest input `expires_at` so an allow cannot
  outlive its shortest-lived input.
- The composed result is a Python value, not a second contract. A decision
  made by an authority OMES does not control (for example Hermes direct
  channels) is still not mediated by OMES (architecture §18.2).

## 5. Child issue plan

All children are **Not implemented yet** as of 2026-10-03, except the parts of #272 and #274 marked Implemented in the table below (contracts, validators, the convergence reducer and the decision composer only).

| Issue | Title (summary) | Authority | Depends on | Deliverable |
|---|---|---|---|---|
| [#270](https://github.com/ahliweb/omes/issues/270) | Coordinator policy and Hermes capability audit | Hermes | This ADR | Documented coordinator/worker policy, per-task model gap resolution order, optional OMES posture check of delegation limits |
| [#271](https://github.com/ahliweb/omes/issues/271) | Restart/orphan reconciliation and consumption of Hermes `unknown` | OMES | None | Lease or heartbeat plus reconciliation of orphaned `running` host jobs; observer treats Hermes `unknown` as unknown, never success |
| [#272](https://github.com/ahliweb/omes/issues/272) | Cross-plane correlation/event envelope and trace propagation | OMES contract (observability plane) | None | Schema with causation/trace identifiers, redaction state and convergence rules; evidence only. **Implemented:** schema, fixtures, validator, convergence reducer, registry entry. **Not implemented yet (tracked in #272):** propagation and producer wiring |
| [#273](https://github.com/ahliweb/omes/issues/273) | ACP interoperability | Hermes | #270 | Posture and evidence for inbound `hermes acp`; outbound deferred to upstream |
| [#274](https://github.com/ahliweb/omes/issues/274) | Policy/capability decision envelope | OMES envelope; decisions by AWCMS, OMES, Hermes, infrastructure | #272 | Composition schema; deny/unavailable wins; no new policy engine. **Implemented:** schema, fixtures, validator, pure composer, registry title. **Not implemented yet (tracked in #274):** emission by AWCMS/OMES/Hermes and use in the job runner, worker or AWCMS |
| [#275](https://github.com/ahliweb/omes/issues/275) | Budget, token and cost governance | AWCMS policy; Hermes and infrastructure enforce | #270 | Policy contract and evidence; unknown usage is not zero |
| [#276](https://github.com/ahliweb/omes/issues/276) | Workload isolation profile and verification | OMES/infrastructure (logical isolation: Hermes) | None | Declared-versus-running drift verification, egress findings |
| [#277](https://github.com/ahliweb/omes/issues/277) | Mission Control projection | AWCMS presentation | #270–#276, PR [#268](https://github.com/ahliweb/omes/pull/268) | Read-only projection of existing sources |

Sequencing: #272 (envelope) lands before #274 (decision envelope) and #277.
#270 lands before #275, because budget policy depends on what Hermes can
enforce. #271, #272 and #276 are independent and may proceed in parallel.
#277 is last and only after PR #268 (ADR-0031) has merged. "Depends on" lists
design dependencies as recorded in the epic; this document does not claim
those issues are in any particular GitHub state.

## 6. Durability semantics summary

State vocabulary used by the children. "Today" reflects the repository on
2026-10-03.

| Term | Meaning | Exists today | Notes |
|---|---|---|---|
| Desired versus observed | The requested state and the state read back after mutation | Yes (OMES jobs) | `_compare_desired_observed` in `lib/omes/py/jobs/runner.py:214` |
| Attempt | One execution try of a job | Yes (OMES jobs) | `attempts` and `max_attempts` on the job record |
| Retry class | Whether a failure may be retried | Yes (OMES jobs) | `error.retryable`; failed to running re-entry is gated |
| Idempotency | Duplicate request returns the same job | Yes (OMES jobs) | Idempotency index in the job store |
| Audit evidence | Append-only, hash-chained record | Yes (OMES jobs) | `lib/omes/py/jobs/audit.py` |
| Timeout is never success | A timeout is reconciled, not reported as success | Yes (AGENTS.md §3, jobs runner) | |
| Tree freshness | `live`/`stale`/`unknown` over an observed tree (300 s) | Yes (observer) | Tree-level only |
| UNKNOWN (agent attempt) | Hermes cannot prove which side effects happened | Upstream (Hermes) | OMES consumption: Not implemented yet (tracked in [#271](https://github.com/ahliweb/omes/issues/271)) |
| Lease / heartbeat | Proof that a `running` job still has a live executor | No | Not implemented yet (tracked in [#271](https://github.com/ahliweb/omes/issues/271)) |
| Orphan | A `running` record with no live executor | No detection | Not implemented yet (tracked in [#271](https://github.com/ahliweb/omes/issues/271)) |
| Cancellation intent versus confirmed | Requested stop versus verified stop of a running job | No | `cancel` rejects `running` today; Not implemented yet (tracked in [#271](https://github.com/ahliweb/omes/issues/271)) |
| Per-node STALE | A single observed node past its freshness threshold | No | See G2; not claimed |

Rules: missing or stale evidence yields `unknown` or manual review, never
`succeeded` (ADR-0032 rule 3). Reconciliation reads the owning system's state;
it never infers success from the passage of time.

## 7. Non-goals

From the epic and ADR-0032. OMES does not:

- ship or embed a second agent runtime, team host, scheduler or Octop fork;
- own agent memory, sessions, skills or model/provider routing (Hermes);
- implement an ACP proxy, server or client, and does not route ACP traffic to host mutation;
- ship an IM/channel gateway, MCP gateway or approval engine;
- introduce a broker, workflow engine or distributed AI worker pool without measured requirements and a new ADR;
- keep a universal event store or a second copy of Hermes, AWCMS or provider state;
- run Mission Control itself (AWCMS presentation only);
- set a hard LLM-spend ceiling (policy is AWCMS; enforcement is Hermes and infrastructure);
- mediate every Hermes-native tool call;
- claim compliance or certification (see §8).

## 8. Standards alignment

Alignment only; no compliance, certification or audit attestation is claimed
(see [ADR-0032](adr/0032-multi-agent-control-patterns-boundary.md) for the
full list and the explicit non-claim for ISO/IEC 15408). Informative mappings:
NIST SP 800-207 (no implicit trust between planes), NIST AI RMF (agent
autonomy limits), OWASP Top 10 for LLM Applications (excessive agency, prompt
injection), OWASP ASVS and API Security Top 10, ISO/IEC 27001/27002/27005/27034
(controls, risk register `MA-01`..`MA-11` in
[docs/threat-model.md](threat-model.md)), ISO 22301 (restart/orphan handling),
ISO/IEC 42001 (authority split), Indonesia UU No. 27/2022 and PP No. 71/2019
as governance context.

## 9. Lessons from predecessors

- **Octop:** in-process tracking is documented as losing in-flight tasks on
  restart, and the whole stack is one process. OMES therefore keeps host-job
  state durable on disk and reconciles it ([#271](https://github.com/ahliweb/omes/issues/271)),
  rather than copying the pattern.
- **Hermes:** a restarted running child becomes `unknown` because the runtime
  cannot prove which side effects happened. OMES must preserve that
  uncertainty and never promote it to success.
- **This repository:** ADR-0028 described a per-node `STALE` transition that
  the code never implemented (G2). That is the reason ADR-0032 adds a
  machine-checked guard (R9, registry entries) instead of relying on prose.
