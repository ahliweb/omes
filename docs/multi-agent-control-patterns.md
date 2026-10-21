# Multi-agent control patterns (epic #269)

> **Status: phase 0 decision record.** This document and
> [ADR-0032](adr/0032-multi-agent-control-patterns-boundary.md) record which
> multi-agent control patterns OMES adopts, delegates, defers or rejects, and
> which verified gaps become child issues. **No runtime behavior changes in
> this phase.** Every capability marked "Not implemented yet" below stays
> unimplemented until its child issue lands. The one exception so far is the
> read-only posture checks of
> [#270](https://github.com/ahliweb/omes/issues/270) (`omes health
> agent-runtime`, delegation limits),
> [#273](https://github.com/ahliweb/omes/issues/273) (`omes health acp`, inbound ACP tool
> surface) and [#275](https://github.com/ahliweb/omes/issues/275) (`omes health budget`,
> configured run limits; usage is unobservable), which report configuration and change no
> Hermes state. Owning issue:
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
| Hermes CLI reference (`v2026.9.24`) | <https://github.com/NousResearch/hermes-agent/blob/v2026.9.24/website/docs/reference/cli-commands.md> | `hermes config get <key> [--json] [--raw]`, the supported read path for the #270 posture check |
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
| Bidirectional ACP | Inbound `octop acp` server; outbound runners flagged `"trusted": true`; permission prompts surface in chat ([acp](https://github.com/TencentCloud/Octop/blob/main/docs/acp.md)) | Hermes (inbound `hermes acp`); outbound is upstream proposal #5257 | DELEGATE (inbound), DEFER (outbound) | Registry `hermes.agent.acp_server` (added by this change); read-only `omes health acp` posture (§3.2) | [#273](https://github.com/ahliweb/omes/issues/273) |
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
| Team-host coordinator | Hermes | Hermes `delegate_task` with `orchestrator` and `leaf` roles; `max_spawn_depth` default 1 (flat), up to 3 ([delegation](https://github.com/NousResearch/hermes-agent/blob/v2026.9.24/website/docs/user-guide/features/delegation.md)). OMES has no coordinator and R9 forbids one. | DELEGATE | [#270](https://github.com/ahliweb/omes/issues/270) (policy and capability audit; configured-limits posture check implemented as `omes health agent-runtime`, see §3.1) |
| Opus to Sonnet delegation tiering | Hermes | Global delegation `model`/`provider` pin only: "`delegate_task` has no per-task model parameter" (same page). Per-task tiering is a verified upstream gap. The posture check reports whether `delegation.model` is pinned (§3.1). | DELEGATE_UPSTREAM, then ADAPT (separate Hermes profiles), then SPECIALIZED_SERVICE, then DEFER | [#270](https://github.com/ahliweb/omes/issues/270) |
| Opus to Haiku delegation tiering | Hermes | Same as the row above. | Same as the row above | [#270](https://github.com/ahliweb/omes/issues/270) |
| Per-agent workspace | Hermes (logical); OMES (host paths) | Hermes `worktree_isolation` exists, default `false`. OMES manages host paths and permissions only. | DELEGATE | [#276](https://github.com/ahliweb/omes/issues/276) |
| Per-agent memory | Hermes | Children start with fresh context; the parent receives only the final summary (delegation page). OMES must not touch internal Hermes databases (registry rule). | DELEGATE | None |
| Asynchronous/parallel dispatch | Hermes | Parallel children with `delegation.max_concurrent_children` default 10; `max_iterations`; `child_timeout_seconds` (0 means no timeout). The configured values are reported by `omes health agent-runtime` (§3.1); OMES hardcodes no default. | DELEGATE | [#270](https://github.com/ahliweb/omes/issues/270) |
| Agent isolation | Hermes (logical); OMES/infrastructure (OS) | Implemented host isolation: hardening profiles (`modules/hermes-gateway/hardening.sh`), rootless Compose with non-root, `capDrop`, read-only rootfs, `no-new-privileges` and no Docker socket (`lib/omes/py/agent/compose.py`). | DELEGATE (logical), OMES (OS) | [#276](https://github.com/ahliweb/omes/issues/276) |
| MCP tool boundary | Logical boundary | Registry entry `boundary.tool_gateway.mcp` is `logical_boundary`; OMES ships no MCP gateway (architecture §16.2.1, scope non-goal 14). | DEFER (stays logical) | None |
| ACP | Hermes | `hermes acp` is a stdio JSON-RPC server with a curated `hermes-acp` toolset; dangerous terminal commands go to editor approval prompts ([acp](https://github.com/NousResearch/hermes-agent/blob/v2026.9.24/website/docs/user-guide/features/acp.md)). Outbound ACP client is an open proposal ([#5257](https://github.com/NousResearch/hermes-agent/issues/5257)). OMES adds no listener. The configured ACP tool surface is reported by `omes health acp` (§3.2); live session exposure is not observable. | DELEGATE (inbound), DEFER (outbound) | [#273](https://github.com/ahliweb/omes/issues/273) (posture evidence implemented; outbound deferred upstream) |
| Unified IM gateway | Hermes | Channels and messaging are Hermes-owned (AGENTS.md §2). | DELEGATE; no OMES gateway | None |
| Mission Control | AWCMS | OMES defines the read-only observer contract (ADR-0028); no Mission Control view exists in this repository. | AWCMS (blocked until sources exist) | [#277](https://github.com/ahliweb/omes/issues/277) |
| Persistent host-operation jobs | OMES | Implemented: job store, idempotency index, hash-chained audit, read-back verification (`lib/omes/py/jobs/`). Orphaned `running` records are detected by executor identity (boot id, pid, process start time) and reconciled by `omes job reconcile` via read-back, never to success without evidence (`lib/omes/py/jobs/identity.py`, `lib/omes/py/jobs/reconcile.py`; see §4, G1). Confirmed cancellation of a running job is Not implemented yet (tracked in [#271](https://github.com/ahliweb/omes/issues/271)). | OMES | [#271](https://github.com/ahliweb/omes/issues/271) (implemented) |
| Persistent agent orchestration | Hermes | Hermes does not resume a running child after a restart; its attempt becomes `unknown` (delegation page). OMES observer ingests `hermes.observer.v1` events (`lib/omes/py/agent/orchestration.py`) but has no production caller or transport (§4, G3). The observer treats `UNKNOWN` as terminal and non-success (counted in `failed_count`, never completed, never reverted to active). | DELEGATE | [#271](https://github.com/ahliweb/omes/issues/271) (consumption of `unknown` implemented) |
| Distributed OMES workers | OMES | Control Center pull worker exists for host-control jobs (ADR-0027, `lib/omes/py/jobs/worker.py`). It is not a distributed AI worker. | OMES (existing) | None |
| Distributed agent workers | Hermes | No OMES capability. A broker or workflow engine needs measured requirements and a new ADR (ADR-0032 rule 4). Octop is itself single-process. | DEFER | None |
| Event sourcing | OMES (per-domain audit only) | Hash-chained, append-only audit log in `lib/omes/py/jobs/audit.py`. No universal event store; none is planned. | REJECT (universal store) | None |
| Tracing | OMES contract, observability plane | `correlation_id` is widespread; `event_id` exists on outbox/events. The 2026-10-03 audit found no `causation_id`, `trace_id`, `span_id` or `traceparent`, and no OpenTelemetry in `lib/`, `modules/` or `contracts/`. Since then the versioned envelope contract `contracts/observability/v1/correlation-envelope.schema.json` and its validator/convergence reducer `lib/omes/py/observability/envelope.py` are implemented (see §4a). No OpenTelemetry SDK or exporter is added. Propagation of the identifiers through AWCMS, job and worker flows and any producer wiring: Not implemented yet (tracked in [#272](https://github.com/ahliweb/omes/issues/272)). | ADAPT | [#272](https://github.com/ahliweb/omes/issues/272) (contract and reducer implemented; propagation not implemented yet) |
| Policy engine | Existing authorities (AWCMS, OMES, Hermes, infrastructure) | `operation-request.permission` (`granted`, `policy_id`, `reason`, `requires_approval`); OMES re-derives it and never trusts `granted: true`. AI-egress decisions `allow`/`deny`/`approval_required` with `AI_EGRESS_*` reason codes. Job `DESTRUCTIVE_OPERATIONS`, `DEFAULT_AUTO_APPROVE` (`lib/omes/py/jobs/store.py`). No unified envelope. | ADAPT (envelope only; no new engine) | [#274](https://github.com/ahliweb/omes/issues/274) (not implemented yet) |
| Capability permissions | AWCMS and OMES | Entitlement checks with `RESOURCE_KEYS` (`lib/omes/py/jobs/entitlement.py`); permission re-derivation as above. | OMES (existing) | [#274](https://github.com/ahliweb/omes/issues/274) |
| Approval gates | AWCMS (business); Hermes (tool); OMES (job approve) | `omes job approve` for non-auto-approved operations; Hermes ACP approval prompts "allow once / allow always / deny"; AWCMS owns tenant approvals. | AWCMS / DELEGATE / OMES per scope | [#274](https://github.com/ahliweb/omes/issues/274) |
| Budget and token governance | AWCMS (policy); Hermes and infrastructure (enforcement) | No token, usage or spend concept in OMES. `docs/security.md` states OMES sets no LLM-spend ceiling. Hermes limits (`max_iterations`, `child_timeout_seconds`, depth, concurrency) and host cgroup limits exist. | AWCMS + DELEGATE | [#275](https://github.com/ahliweb/omes/issues/275) (governance model defined in §4.1; read-only configured run-limit posture implemented as `omes health budget`; token, spend and cost metering is not implemented and is not observable, so it is reported as unknown) |
| Workload isolation | OMES/infrastructure (OS); Hermes (logical) | Implemented: hardening profiles, rootless Compose (#96), opt-in `hermes-restricted` egress `IPAddressDeny=any`, per-deployment compose `egress: "none"` (`internal: true`, all-or-nothing) and the read-only `omes agent isolation-drift` declared-versus-running check (`lib/omes/py/agent/isolation_drift.py`, shim-tested; covers `cap_drop`, `CapAdd`, `no-new-privileges`, seccomp/AppArmor overrides, `Devices`, `IpcMode`, `Privileged`, mounts and the network `internal` flag). Not implemented yet (tracked in [#276](https://github.com/ahliweb/omes/issues/276)): a per-destination egress allowlist, a runtime egress probe, real rootless-daemon evidence, orphan cleanup and retention, and scheduled or `omes agent doctor` drift reporting. See §4, G7. | OMES (OS) | [#276](https://github.com/ahliweb/omes/issues/276) |
| Node/process restart recovery | Hermes (agent runs); OMES (host jobs) | Hermes: running child becomes `unknown` after restart; the OMES observer consumes it as non-success. OMES: orphaned `running` host jobs are reconciled by `omes job reconcile` (§4, G1). | DELEGATE / OMES | [#271](https://github.com/ahliweb/omes/issues/271) (implemented) |

### 3.1 Delegation-limits posture check and the tiering ADAPT path (issue #270)

Implemented: `omes health agent-runtime` ([docs/cli.md §4.13](cli.md#413-omes-health-layered-healthreadiness-checks),
[docs/hermes-integration.md §17.1](hermes-integration.md#171-delegation-limits-posture-and-the-config-read-path-issue-270)).
It reads the non-secret `delegation.*` keys (`max_concurrent_children`, `max_spawn_depth`,
`max_iterations`, `child_timeout_seconds`, `subagent_auto_approve`, `model`, `provider` and three
informational keys) through `hermes config get <key> --json` and reports `ok`, `warn` or `unknown`
with findings: no per-child timeout, auto-approval of subagents, spawn depth above 1, and no pinned
delegation model. What it does not do:

- It reports **configured** values only. Hermes can override limits from environment variables
  (for example `DELEGATION_MAX_CONCURRENT_CHILDREN`) and OMES cannot observe that, so the output is
  labelled `scope: "configured"` and is not proof of the limits a running gateway enforces.
- It enforces and changes nothing, schedules nothing and routes no models. OMES remains neither a
  coordinator nor a delegation owner (R9, ADR-0032 rules 1 and 2).
- It hardcodes no Hermes default. Upstream documentation and code disagree on some defaults
  (observed while reading the `v2026.9.24` source), so only live values are reported.
- It is a point-in-time read: it is not budget governance (see §4.1 and
  [#275](https://github.com/ahliweb/omes/issues/275)) and not a record of what any run actually did.

Per-task model tiering (a high-capability coordinator with cheaper workers) remains an **upstream
gap** (G8): `delegate_task` has no per-task model parameter and the global `delegation.model` pin
applies to every child. The resolution order stays `DELEGATE_UPSTREAM -> ADAPT -> SPECIALIZED_SERVICE
-> DEFER`. The recommended ADAPT path is separate Hermes profiles per worker tier, each with its
own `delegation.model`/`delegation.provider`, selected by the operator through Hermes itself;
`omes health agent-runtime --profile-home <profile home>` reviews each profile. OMES does not
route models, choose providers or write `delegation.model` (ADR-0029, ADR-0032 rule 2), and no
OMES-side tier mapping exists. A request for per-task tiering belongs upstream.

### 3.2 ACP tool-surface posture check (issue #273)

Implemented: `omes health acp` ([docs/cli.md §4.13](cli.md#413-omes-health-layered-healthreadiness-checks),
[docs/hermes-integration.md §17.2](hermes-integration.md#172-acp-tool-surface-posture-issue-273)).
Inbound ACP is `DELEGATE` to Hermes `hermes acp` (an on-demand stdio JSON-RPC server launched by an
editor or a bridge; no daemon, no enable/disable key); outbound ACP is `DEFER` to upstream
[hermes-agent#5257](https://github.com/NousResearch/hermes-agent/issues/5257). The check reads
`platform_toolsets.acp` and `agent.disabled_toolsets` through the shared `hermes config get <key> --json`
reader and reports `ok`, `warn` or `unknown`: it warns when the key is absent (the curated `hermes-acp`
default toolset includes terminal and `execute_code`) or lists execution toolsets that
`agent.disabled_toolsets` does not disable. What it does not do:

- It reports **configured** values only (`scope: "configured"`). An explicit toolset list can still gain
  enabled plugin toolsets upstream, so even `ok` is not proof of the runtime tool surface.
- It cannot see whether an ACP session is live or exposed through a bridge (some bridges auto-approve
  `allow_once`): no supported interface exposes that, so `session_exposure` is always `unknown` and an
  `acp.session_exposure_unobservable` finding says so.
- `hermes acp --version` is run only as an installability hint (exit 0 is `installed`); its output is not
  parsed and it is never exposure evidence. `hermes acp --check` is not used because its output format is
  undocumented.
- OMES is not an ACP server, client or proxy and adds no listener; ACP traffic never reaches OMES host
  mutation, which stays the typed, allowlisted `operation-request` boundary (ADR-0032 rule 6, R9). OMES
  does not write Hermes configuration or approve ACP prompts. Outbound ACP is not implemented (deferred
  upstream).

## 4. Verified gaps

Evidence is from the repository audit of 2026-10-03. Line numbers refer to
that tree and may move; the file and symbol names are the stable reference.

| ID | Gap | Evidence | Child |
|---|---|---|---|
| G1 | Resolved by [#271](https://github.com/ahliweb/omes/issues/271) (implemented). The job runner persists `running` before executing the operation, and previously nothing reconciled a `running` record whose process died (`expire_stale_jobs` only handles `queued`/`approved`). Now the runner records executor identity `runner = {pid, boot_id, started_at}` (plus `pid_start_ticks`, so a recycled pid is not mistaken for the runner), and `omes job reconcile` proves an orphan by changed boot id, dead pid or reused pid, then reports only what the operation's read-back proves: `succeeded`, `failed`/`reconciliation_mismatch`, or `failed`/`outcome_unknown`. Remaining gap: `cancel` still rejects `running` and there is no confirmed cancellation (Not implemented yet, tracked in #271), and nothing invokes `reconcile` automatically. | `lib/omes/py/jobs/identity.py`, `lib/omes/py/jobs/reconcile.py`, `lib/omes/py/jobs/runner.py` (`run`); `lib/omes/py/jobs/store.py` (`cancel`) | [#271](https://github.com/ahliweb/omes/issues/271) |
| G2 | No per-node `STALE` state. Staleness only affects tree-level freshness (`live`/`stale`/`unknown`, 300 s), although ADR-0028 (Decision 4) describes transitioning unconfirmed running subagents to `STALE`. This change adds an implementation note to ADR-0028 and corrects the matching sentence in `docs/control-center-and-integrations.md` §4; per-node `STALE` is not claimed. Hermes `UNKNOWN` is now consumed as terminal non-success (#271) but is a separate, upstream-reported state, not a staleness state. | `lib/omes/py/agent/orchestration.py:24` (`DEFAULT_STALE_THRESHOLD_SECONDS = 300`), `:285-293` (`is_any_stale` set at tree level) | [#271](https://github.com/ahliweb/omes/issues/271) |
| G3 | `ingest_event` has no production caller or transport in this repository, so the orchestration projection has no live event source. The unused `HERMES_BASELINE` constant was removed by #271. | `lib/omes/py/agent/orchestration.py:115` (`ingest_event`), `:23` (`HERMES_BASELINE`, since removed); repository-wide search found no non-test caller | [#271](https://github.com/ahliweb/omes/issues/271), [#272](https://github.com/ahliweb/omes/issues/272) |
| G4 | No cross-plane causation or trace identifiers; no OpenTelemetry. **Partly closed:** the envelope contract (with `causation_id`, W3C-shaped `trace_id` and `span_id`) and a deterministic convergence reducer now exist (§4a). **Still open:** no producer emits the envelope, and nothing propagates the identifiers across AWCMS, job and worker flows. Not implemented yet (tracked in [#272](https://github.com/ahliweb/omes/issues/272)). | At audit time, a search of `lib/`, `modules/`, `contracts/` for `traceparent`, `trace_id`, `span_id`, `causation_id`, `opentelemetry` returned nothing. Now: `contracts/observability/v1/correlation-envelope.schema.json`, `lib/omes/py/observability/envelope.py`; a repository search still finds no producer or propagation caller outside tests and fixtures | [#272](https://github.com/ahliweb/omes/issues/272) |
| G5 | No unified policy/capability decision envelope; policy facts are spread across `operation-request.permission`, AI-egress decisions and job approval sets. | `lib/omes/py/jobs/store.py:112-114` (`DESTRUCTIVE_OPERATIONS`, `DEFAULT_AUTO_APPROVE`); `lib/omes/py/privacy/egress_policy.py` (`AI_EGRESS_*`) | [#274](https://github.com/ahliweb/omes/issues/274) |
| G6 | No token, usage or spend concept; OMES documents that it sets no LLM-spend ceiling. The governance model is now defined (§4.1) and the configured run limits are reported (`omes health budget`), but token, spend and cost usage stay unobservable through any supported Hermes interface and are reported as unknown, never zero. No metering or enforcement exists in OMES by design. | `docs/security.md` (non-guarantees list); `lib/omes/py/jobs/entitlement.py:25-32` (`RESOURCE_KEYS` has no budget key); `lib/omes/py/health/budget_posture.py` | [#275](https://github.com/ahliweb/omes/issues/275) |
| G7 | Partly closed by #276. Originally the compose network was always a plain bridge with `internal: false` and nothing compared declared with running state. Implemented now: `compose.egress` (`"open"` default, unchanged; `"none"` renders `internal: true`) and the read-only `omes agent isolation-drift` command, both shim-tested. The drift check compares `cap_drop`, `no-new-privileges`, read-only rootfs, user, image, limits, mounts and the network `internal` flag, plus the hardening fields the renderer leaves at Docker's default: any `HostConfig.CapAdd`, any `Devices` mapping, a seccomp or AppArmor override or other undeclared `SecurityOpt` entry, a non-private `IpcMode`, and `Privileged`. A missing or unparseable field is `unknown`, never `ok`; the default seccomp and AppArmor profiles cannot be proven active from `docker inspect` and are reported as not declared. The isolation profile matrix is in [docs/agent-deployment.md section 9.7](agent-deployment.md#97-declared-versus-running-isolation-drift-check-issue-276). Still Not implemented yet (tracked in [#276](https://github.com/ahliweb/omes/issues/276)): a per-destination egress allowlist (Docker `internal: true` blocks all external egress and has no allowlist), a runtime egress probe, evidence from a real rootless daemon, cleanup and retention of orphaned compose projects, containers and networks, and wiring drift into `omes agent doctor` or a schedule. | `lib/omes/py/agent/compose.py` (`EGRESS_MODES`, `render_compose_yaml`); `lib/omes/py/agent/isolation_drift.py`; compare opt-in `modules/hermes-restricted/module.sh:467` (`IPAddressDeny=any`) | [#276](https://github.com/ahliweb/omes/issues/276) |
| G8 | Hermes per-task model selection does not exist upstream at the pinned baseline. The posture check (§3.1) can only report that no delegation model is pinned; the gap itself is unresolved and stays an upstream request, with separate Hermes profiles as the ADAPT workaround. | Delegation page, quote in §3 | [#270](https://github.com/ahliweb/omes/issues/270) |

### 4.1 Budget and resource governance model (issue #275)

This section defines the model; it adds no code path that meters or enforces anything. The rule is
[ADR-0032](adr/0032-multi-agent-control-patterns-boundary.md) rule 8: budget **policy** is AWCMS's,
**enforcement** is Hermes's (run, loop and delegation limits, provider limits) and infrastructure's
(cgroup limits), and **unknown usage is not zero**. OMES is not a second LLM router and never meters
model calls ([ADR-0029](adr/0029-ai-data-boundary-and-private-inference.md)); it sets no LLM-spend
ceiling ([docs/security.md](security.md)). The implemented evidence is `omes health budget`
([docs/cli.md §4.13](cli.md#413-omes-health-layered-healthreadiness-checks),
[docs/hermes-integration.md §17.3](hermes-integration.md#173-budget-and-run-limit-posture-issue-275)),
which reads configured values only.

Vocabulary: **soft-warn** means a finding or notice that does not stop work (an OMES posture finding,
or an AWCMS alert); **hard-deny** means the enforcing party stops or refuses the work. "Observable
today" is limited to what a supported interface returns: `yes (configured)` means the configured value
is readable through `hermes config get <key> --json` or an OMES-owned file, not that a running process
obeys it; `no` means no supported interface exposes it; `unknown` means nothing verified says either way.

| Limit dimension | Deciding authority | Enforcing authority | Observable today | Soft-warn versus hard-deny |
|---|---|---|---|---|
| Money or spend per tenant, project or user (daily or monthly quota) | AWCMS (policy, plan) | Not defined in this repository; provider billing controls and any Hermes provider limits are outside OMES | no: no Hermes config key defines a spend or cost ceiling. Not implemented yet (tracked in [#275](https://github.com/ahliweb/omes/issues/275)) | None implemented. OMES reports `usage.cost: "unknown"` and the finding `budget.token_spend_unobservable`; it never enforces a spend ceiling |
| Tokens per run, tenant or user (daily or monthly quota) | AWCMS (policy) | Not defined in this repository; no Hermes enforcement is verified | no: no config key; `hermes insights` has no JSON output; `hermes usage --json` returns provider rate-limit windows through a network call that uses provider credentials and is never run by OMES. Not implemented yet (tracked in [#275](https://github.com/ahliweb/omes/issues/275)) | None implemented. OMES reports `usage.tokens: "unknown"` with `source: "none"` and `confidence: "none"`, never 0 |
| Turns per run | AWCMS (desired value) | Hermes | yes (configured): `agent.max_turns`; an explicit null means unlimited | Hermes stops the run at the limit (upstream behavior, not exercised by OMES). OMES soft-warns with `budget.unlimited_turns` when it is null; an absent key is `unknown` |
| Wall-clock time per run | AWCMS (desired value) | Hermes | yes (configured): `agent.run_budget_seconds`; an explicit null means no budget | Hermes stops the run at the budget (upstream behavior, not exercised by OMES). OMES soft-warns with `budget.no_run_time_budget` when it is null; an absent key is `unknown` |
| Child count and concurrency per coordinator | AWCMS (desired value) | Hermes | yes (configured): `delegation.max_concurrent_children` (`omes health agent-runtime`, §3.1); per-turn `agent.loop_caps.max_subagents` | Hermes limits the fan-out. OMES reports the values and does not warn on them here |
| Child depth per coordinator | AWCMS (desired value) | Hermes | yes (configured): `delegation.max_spawn_depth` (`omes health agent-runtime`) | Hermes limits nesting; `omes health agent-runtime` soft-warns above the documented depth |
| Child iterations and child timeout | AWCMS (desired value) | Hermes | yes (configured): `delegation.max_iterations`, `delegation.child_timeout_seconds` (`omes health agent-runtime`) | Hermes enforces; `omes health agent-runtime` soft-warns on no child timeout |
| Tool and browser call counts | AWCMS (desired value) | Hermes | web searches per turn: yes (configured), `agent.loop_caps.max_web_searches`. Browser, terminal and other tool counts: no verified key. Not implemented yet (tracked in [#275](https://github.com/ahliweb/omes/issues/275)) | Hermes enforces the web-search cap. OMES reports the value as information only |
| Per worker class (Opus, Sonnet or Haiku tier) | AWCMS (policy per tier) | Hermes, through separate profiles per tier (G8); no per-task model parameter exists upstream | partly: each profile's own limits and `delegation.model` pin are readable per profile with `--profile-home`. No per-class budget exists. Not implemented yet (tracked in [#275](https://github.com/ahliweb/omes/issues/275)) | None implemented. OMES does not map tiers to budgets and does not route models |
| Host CPU, memory and process count of the gateway unit | OMES (hardening defaults) and the operator; AWCMS entitlements where a plan applies | Infrastructure: systemd cgroup limits (`MemoryMax`, `MemoryHigh`, `CPUQuota`, `TasksMax`) rendered by OMES in a drop-in | partly: `omes doctor` prints the unit's `MemoryMax` and `TasksMax` from `systemctl show`. `omes health budget` does not collect host limits and reports `host_limits: "not_collected"` | Hard limit by the kernel (cgroup) once the drop-in is applied; OMES does not warn on the values in this check |
| Provisioned counts (servers, logical and specialist agents, isolated workers, storage in GB, backup retention days) | AWCMS (entitlement and plan) | OMES entitlement gate at job time (`evaluate()` in `lib/omes/py/jobs/entitlement.py`, `RESOURCE_KEYS`) | yes for the limits an entitlement states; this check reports no usage against them | Hard-deny of the requested provisioning action by the gate, with a reason; not applied to model calls or spend |
| Emergency stop | The operator, or AWCMS acting through an allowlisted OMES job | Scoped to one target: a Hermes session (the Hermes `/stop` or interrupt mechanism, owned and run by Hermes), or the gateway service lifecycle (OMES, through the existing allowlisted lifecycle operations) | no: no OMES view of a live Hermes run. Not implemented yet (tracked in [#275](https://github.com/ahliweb/omes/issues/275)) | Hard stop of one session or one service. OMES has no global kill switch and does not mediate Hermes-native tool calls (architecture §18.2) |

Rules that follow from the table:

1. Budget policy is not defined or stored in OMES. A machine-readable AWCMS budget-policy contract does not
   exist in this repository: Not implemented yet (tracked in [#275](https://github.com/ahliweb/omes/issues/275)).
2. OMES hardcodes no Hermes default. A key that Hermes reports as unset is `absent`, not "unlimited", and
   makes the overall status `unknown` for the two required keys; only an explicit null is reported as
   configured unlimited.
3. Unknown usage is never rendered as zero, never as "ok", and never inferred from limits: a finite
   turn or time limit does not bound spend, so `ok` from `omes health budget` means only that the two
   run limits were read and are finite.
4. Environment-variable overrides and runtime state are not observed; every value is labelled
   `scope: "configured"`.
5. Delegation limits are read once, by `omes health agent-runtime`; `omes health budget` points to that
   command and does not duplicate the reads.

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

## 5. Child issue plan

All children were **Not implemented yet** as of 2026-10-03, except the read-only configured-limits
posture check of #270 (`omes health agent-runtime`, §3.1; #270's per-task tiering gap stays open upstream),
[#271](https://github.com/ahliweb/omes/issues/271), which is implemented (deliverable below; confirmed cancellation of a running job remains not implemented),
the contract, validator and convergence reducer of [#272](https://github.com/ahliweb/omes/issues/272) (§4a; propagation and producer wiring remain not implemented yet, tracked in #272),
the read-only inbound ACP posture check of #273 (`omes health acp`, §3.2; outbound ACP is deferred upstream),
the budget governance model plus read-only configured run-limit posture of #275 (`omes health budget`, §4.1; no metering, no enforcement, usage unknown)
and the parts of #276 recorded in its row below.

| Issue | Title (summary) | Authority | Depends on | Deliverable |
|---|---|---|---|---|
| [#270](https://github.com/ahliweb/omes/issues/270) | Coordinator policy and Hermes capability audit | Hermes | This ADR | Documented coordinator/worker policy, per-task model gap resolution order, optional OMES posture check of delegation limits (posture check implemented, §3.1; per-task tiering unresolved upstream) |
| [#271](https://github.com/ahliweb/omes/issues/271) | Restart/orphan reconciliation and consumption of Hermes `unknown` | OMES | None | Implemented: executor identity (boot id, pid, start time) in place of a lease or heartbeat, plus `omes job reconcile` of orphaned `running` host jobs; observer treats Hermes `UNKNOWN` as terminal non-success. Not implemented: confirmed cancellation |
| [#272](https://github.com/ahliweb/omes/issues/272) | Cross-plane correlation/event envelope and trace propagation | OMES contract (observability plane) | None | Schema with causation/trace identifiers, redaction state and convergence rules; evidence only. **Implemented:** schema, fixtures, validator, convergence reducer, registry entry. **Not implemented yet (tracked in #272):** propagation and producer wiring |
| [#273](https://github.com/ahliweb/omes/issues/273) | ACP interoperability | Hermes | #270 | Posture and evidence for inbound `hermes acp` (implemented, §3.2: configured tool surface, installability hint, exposure reported unknown); outbound deferred to upstream |
| [#274](https://github.com/ahliweb/omes/issues/274) | Policy/capability decision envelope | OMES envelope; decisions by AWCMS, OMES, Hermes, infrastructure | #272 | Composition schema; deny/unavailable wins; no new policy engine |
| [#275](https://github.com/ahliweb/omes/issues/275) | Budget, token and cost governance | AWCMS policy; Hermes and infrastructure enforce | #270 | Governance model and configured run-limit evidence (implemented, §4.1: `omes health budget`; usage always `unknown`, never zero); a machine-readable AWCMS budget policy contract and any usage metering are Not implemented yet (tracked in [#275](https://github.com/ahliweb/omes/issues/275)) |
| [#276](https://github.com/ahliweb/omes/issues/276) | Workload isolation profile and verification | OMES/infrastructure (logical isolation: Hermes) | None | Declared-versus-running drift verification (capabilities added or dropped, `no-new-privileges`, seccomp/AppArmor overrides, devices, IPC mode, privileged, mounts, network `internal`) and per-deployment egress mode: implemented for the compose backend (shim-tested). Per-destination egress allowlist, runtime egress probe, real rootless-daemon evidence, orphan cleanup and retention, and `omes agent doctor` or schedule wiring: Not implemented yet (tracked in #276) |
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
| UNKNOWN (agent attempt) | Hermes cannot prove which side effects happened | Upstream (Hermes); OMES observer consumes it | Implemented (#271): terminal, non-success, counted in `failed_count`, sticky against later active or `SUCCEEDED` events, never downgrades a definite terminal state (`lib/omes/py/agent/orchestration.py`) |
| Lease / heartbeat | Proof that a `running` job still has a live executor | Replaced by executor identity (#271) | A single-host job is one synchronous subprocess of one `omes job run` process, so `runner = {pid, boot_id, started_at}` (+ `pid_start_ticks`) identifies the executor exactly; there is no lease, heartbeat or timer, and none is planned |
| Orphan | A `running` record with no live executor | Yes (OMES jobs) | `reconcile.assess` in `lib/omes/py/jobs/reconcile.py`; reconciled by `omes job reconcile` (read-back only; `outcome_unknown` when unverifiable) |
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
