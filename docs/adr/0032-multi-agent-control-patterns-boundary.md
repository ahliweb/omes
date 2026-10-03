# ADR-0032 — Multi-agent control patterns adopted without a second agent runtime

- **Status:** Accepted
- **Date:** 2026-10-03
- **Decision maker:** @ahliweb
- **Related:** [ADR-0017](0017-upstream-first-ownership-and-boundary-enforcement.md) (upstream-first ownership), [ADR-0027](0027-control-center-pull-worker-transport.md) (pull-worker transport), [ADR-0028](0028-hermes-orchestration-visualization.md) (Hermes orchestration observer), [ADR-0029](0029-ai-data-boundary-and-private-inference.md) (AI data boundary), ADR-0031 (Mission Control; pending in PR [#268](https://github.com/ahliweb/omes/pull/268)), [Epic #269](https://github.com/ahliweb/omes/issues/269), [docs/multi-agent-control-patterns.md](../multi-agent-control-patterns.md), [docs/threat-model.md](../threat-model.md) (§5.z, `MA-01`..`MA-11`)
- **Supersedes / Amends:** None. Adds a decision record, two registry entries, and a wider R9 reserved-term list. It changes no runtime behavior. Number 0031 is reserved for the Mission Control ADR in PR #268.
- **Implementation:** Phase 0 only (this record, the registry/linter guard, the threat delta, and the decomposition into [#270](https://github.com/ahliweb/omes/issues/270)–[#277](https://github.com/ahliweb/omes/issues/277)). Every child issue is **Not implemented yet** unless its own PR lands. Child [#272](https://github.com/ahliweb/omes/issues/272) has landed its envelope contract, validator, convergence reducer and registry entry (`omes.observability.correlation_envelope`); its propagation through AWCMS, job and worker flows is Not implemented yet (tracked in #272).

---

## Context

Epic [#269](https://github.com/ahliweb/omes/issues/269) (validation date 2026-10-03) asks which multi-agent control patterns OMES should adopt after studying [TencentCloud/Octop](https://github.com/TencentCloud/Octop) as a reference-only project. The patterns are a team host that only schedules, parallel asynchronous dispatch to specialist members, per-agent workspace and checkpoint isolation, bidirectional ACP, a unified channel gateway, and a Mission-Control-like UI. The epic also asks about coordinator-to-worker model tiering (a high-capability coordinator model delegating to cheaper worker models), durable orchestration, tracing, policy, budget, and isolation.

Verified evidence behind this decision (full tables are in [docs/multi-agent-control-patterns.md](../multi-agent-control-patterns.md)):

- **Octop** documents that its team host only schedules and dispatches professional work asynchronously to members ([expert-teams.md](https://github.com/TencentCloud/Octop/blob/main/docs/expert-teams.md)). It also documents that members run in parallel across members but serially per member. In-flight team-job tracking is in-process (`TeamJobTracker`), and "out-of-process inbox persistence" is a listed v1 non-target whose consequence is "restart loses in-flight tasks". Its [architecture](https://github.com/TencentCloud/Octop/blob/main/docs/architecture.md) is "one process. There is no separate worker, no external queue". Its [ACP document](https://github.com/TencentCloud/Octop/blob/main/docs/acp.md) describes an inbound server plus outbound runners (OpenCode, CodeBuddy, Claude Code, Codex, Kimi Code, Cursor CLI, Pi) that are flagged `"trusted": true`.
- **Hermes Agent** (pinned OMES baseline `v2026.9.24`, `lib/omes/versions.sh`) already ships [delegation](https://github.com/NousResearch/hermes-agent/blob/v2026.9.24/website/docs/user-guide/features/delegation.md) with parallel children (`delegation.max_concurrent_children`, default 10), `max_spawn_depth` (default 1, up to 3), `leaf`/`orchestrator` roles, `max_iterations`, `child_timeout_seconds`, `worktree_isolation`, and a global delegation `model`/`provider` pin. The upstream text states that "the pin is global: `delegate_task` has no per-task model parameter, so every child in a batch runs on the configured delegation model". It also states that "A Hermes process restart does not resume a running child. Its attempt becomes `unknown` because Hermes cannot prove which side effects happened." Hermes also ships an [ACP server](https://github.com/NousResearch/hermes-agent/blob/v2026.9.24/website/docs/user-guide/features/acp.md) (`hermes acp`) that routes dangerous terminal commands to editor approval prompts. A generalized outbound ACP client exists only as an open proposal ([hermes-agent#5257](https://github.com/NousResearch/hermes-agent/issues/5257), P4).
- **OMES repository audit (2026-10-03)** found real gaps, each recorded with file evidence in the companion document: no reconciliation of an orphaned `running` host job after a restart (#271), no cross-plane `causation_id`/`trace_id`/`traceparent` (#272), no token/usage/spend concept (#275), and no per-container egress or declared-versus-running isolation drift detection (#276). The Hermes observer ingest function has no production caller or transport in this repository.

### Constraints

- ADR-0017 precedence applies: `DELEGATE -> PORT -> ADAPT -> DEFER -> REJECT`. Hermes owns delegation, sessions, memory, model/provider routing and ACP (AGENTS.md §2).
- Registry invariants R1, R8 and R9 already prevent OMES from owning agent-runtime behavior or silently adopting a second agent framework.
- **OMES does not mediate all Hermes-native tool execution** ([docs/architecture.md §18.2](../architecture.md#182-limitations--read-this-before-assuming-omes-mediates-agent-behavior)). Nothing in this ADR makes OMES an interception point for Hermes tool calls, an ACP proxy, or an agent scheduler.
- AWCMS owns tenants, approvals and Mission Control presentation ([epic #263](https://github.com/ahliweb/omes/issues/263); ADR-0031 pending in PR #268). The pull worker (ADR-0027, #192) distributes host-control jobs; it is not a distributed AI worker.
- No claim of compliance or certification follows from this ADR. No credentials, prompts, transcripts, or chain-of-thought may appear in any artifact this decision produces (ADR-0029).

---

## Evaluation of Options

- **Option A — Docs-only.** Write the analysis, add no registry entries, no guard, no threat delta, and no child issues.
- **Option B — Decision record, registry/linter guard, threat delta and atomic children (chosen).** Record the boundary in this ADR. Register the two existing upstream capabilities. Widen R9. Add the threat delta. Decompose the work into eight atomic, authority-labelled child issues (#270–#277).
- **Option C — Implement the envelope, policy and budget contracts now.** Build the cross-plane envelope, the policy decision envelope and budget governance inside this epic, before the owning upstream behavior is audited.
- **Option D — Embed or fork Octop or a LangGraph-style second runtime.** Run an additional agent runtime or team host beside Hermes to obtain coordinator/worker behavior, durable inboxes and ACP runners.

| Criterion | A — Docs-only | **B — Decision record + guard + threat delta + atomic children (chosen)** | C — Implement contracts now | D — Embed/fork Octop or a second runtime |
|---|---|---|---|---|
| **1. Advantages & disadvantages** | Pro: lowest effort. Con: prose drifts; ADR-0028 already carried a per-node `STALE` claim that code never implemented, which shows prose without a guard decays. | Pro: the boundary is machine-checked, gaps become owned work, no behavior change. Con: delivers no new runtime capability itself; value arrives only as the children land. | Pro: faster visible capability. Con: contracts written before the Hermes capability audit (#270) risk encoding assumptions upstream later falsifies. | Pro: coordinator/worker, durable inbox and ACP runners "for free". Con: duplicates Hermes (R8, ADR-0017), a second memory and approval surface, large unreviewed dependency. |
| **2. Security** | Weak: nothing stops a later change from registering an OMES-owned coordinator or ACP proxy. | Strong: R9 reserves `acp`, `agent_client_protocol`, `coordinator`, `subagent`, `subagents`; MA-01..MA-11 enumerate controls and honest status; no new privileged surface. | Medium: new contract surfaces, each a possible confused-deputy path if designed before the policy owners are fixed. | Weak: Octop runners are flagged `"trusted": true` and installed as host CLIs; embedding would import that trust model next to OMES host mutation. |
| **3. Performance** | None. | None (documentation, registry and CI-time check only). | Added envelope fields, bounded and optional. | A second process tree and model-call path; Octop itself is one process. |
| **4. Maintainability** | Low: no enforcement. | High: one ADR, one companion matrix, one linter extension, eight small issues. | Medium: three contracts maintained before consumers exist. | Low: tracking a second runtime's releases, security fixes and license. |
| **5. Scalability** | Not addressed. | Addresses it honestly: no broker or workflow engine without measured requirements (rule 4); durable state follows its authority. | Premature distribution risk. | Octop's own single-process design does not scale out; adopting it imports that limit. |
| **6. Accessibility** | N/A. | N/A. Mission Control accessibility is owned by AWCMS (#277). | N/A. | N/A. |
| **7. SEO impact** | None. | None (admin-only surfaces). | None. | None. |
| **8. UI/UX implications** | No guidance. | Mission Control (#277) may only project data from sources that exist; no empty or fabricated views. | Risk of a UI built on unaudited sources. | A second UI and session model competing with Hermes channels and AWCMS. |
| **9. Compatibility** | Compatible. | Compatible with the pinned Hermes baseline `v2026.9.24`; per-task model and outbound ACP deferred until upstream ships them. | May constrain upgrade paths if Hermes later adds a native equivalent. | Octop and Hermes disagree on sessions, memory and channel semantics. |
| **10. Operational complexity** | None added. | Low: no new service. | Medium: new evidence streams to operate. | High: extra daemon, workspace store, upgrade and incident surface. |
| **11. Long-term implications** | Boundary erodes under pressure. | Durable boundary with explicit revisit triggers; clean path to adopt upstream features when released. | Contracts may need a breaking v2 after #270 findings. | Permanent second-runtime ownership; contradicts ADR-0017; reversible only at high cost. |

---

## Decision

We adopt **Option B**. The following rules bind OMES contributors and the epic #269 children.

1. **Patterns, not products.** OMES adopts control patterns only where they fit its authority. It does not ship or embed a second agent runtime, second agent memory, IM/channel gateway, MCP gateway, approval engine, agent registry, Mission Control, or universal event store. Octop is a reference project and is not a dependency. (R8 and R9 enforce part of this.)
2. **Coordinator/worker tiering is Hermes configuration, not OMES code.** A coordinator on a high-capability model delegating to worker models (for example Opus to Sonnet or Haiku) is expressed through Hermes delegation configuration and skills. Because Hermes `v2026.9.24` has only a global delegation `model`/`provider` pin and no per-task model parameter, per-task tiering is a verified upstream gap. The resolution order is `DELEGATE_UPSTREAM -> ADAPT (separate Hermes profiles) -> SPECIALIZED_SERVICE -> DEFER`, tracked in [#270](https://github.com/ahliweb/omes/issues/270). OMES may only validate posture (for example that depth and concurrency limits are within policy) and must not route models (ADR-0029).
3. **Durability follows the authority.** Agent-run durability is Hermes authority. OMES consumes Hermes `unknown` and never converts it to success. Host-job orphan reconciliation (an orphaned `running` job after a restart) is OMES authority ([#271](https://github.com/ahliweb/omes/issues/271)). Missing or stale evidence yields `unknown` or manual review, never `succeeded`.
4. **No broker or workflow engine without evidence.** Kafka, NATS, RabbitMQ, Temporal or similar are not introduced without measured throughput, ordering, duration and high-availability requirements and a new ADR. Octop itself runs as one process with no external queue; that is evidence the pattern does not require one.
5. **The cross-plane envelope is evidence, not authority.** A correlation/event envelope with causation and trace identifiers ([#272](https://github.com/ahliweb/omes/issues/272)) carries identifiers and redaction state. It never carries prompts, transcripts or chain-of-thought, and never grants permission.
6. **ACP.** Inbound ACP is `DELEGATE` to Hermes `hermes acp`. Outbound ACP is `DEFER` to upstream [hermes-agent#5257](https://github.com/NousResearch/hermes-agent/issues/5257). ACP traffic must never reach OMES host mutation; the typed, allowlisted `operation-request` remains the only mutation boundary ([#273](https://github.com/ahliweb/omes/issues/273) covers posture evidence only).
7. **Policy decision envelope.** A decision envelope ([#274](https://github.com/ahliweb/omes/issues/274)) composes existing authorities (AWCMS approval, OMES permission re-derivation, Hermes tool approval, infrastructure controls) and adds no policy engine. Deny or unavailable wins. Stale evidence yields `unavailable`.
8. **Budget governance.** Budget policy is AWCMS's; enforcement is by Hermes (iteration, timeout, depth, concurrency limits and provider limits) and infrastructure (cgroup limits). Unknown usage is not zero ([#275](https://github.com/ahliweb/omes/issues/275)).
9. **Isolation split.** OS and host isolation (systemd hardening, rootless Compose, egress restriction) is OMES/infrastructure. Logical agent isolation (workspaces, sessions, memory, worktrees) is Hermes. Verifying declared isolation against running state is OMES work that is not implemented yet (tracked in [#276](https://github.com/ahliweb/omes/issues/276)); OMES does not claim to enforce logical isolation.
10. **Registry.** Register the existing upstream capabilities `hermes.agent.delegation` and `hermes.agent.acp_server` (authority `hermes`, `delegated_upstream`, `released_supported`, `adr_reference` ADR-0032). Extend R9's reserved terms with `acp`, `agent_client_protocol`, `coordinator`, `subagent`, `subagents` (existing: reasoning, model_routing, memory, delegation, sessions). Add no other registry entries until a child issue lands its implementation.
11. **Mission Control projects only.** The Mission Control view ([#277](https://github.com/ahliweb/omes/issues/277), AWCMS presentation) renders only data whose source exists, and is blocked by #270–#276 and by PR [#268](https://github.com/ahliweb/omes/pull/268) (ADR-0031, pending). It is not an authority for any state it displays.

---

## Security and privacy rationale

- The decision keeps model-driven (probabilistic) behavior in the Hermes plane and deterministic host mutation in OMES (docs/architecture.md §18), preserving the property that model output is data and not authorization (ADR-0029).
- Threats `MA-01`..`MA-11` ([docs/threat-model.md §5.z](../threat-model.md)) record controls and honest status, including coordinator privilege escalation, ACP compromise, confused-deputy host mutation, event poisoning, orphaned-task fabricated success, fan-out budget exhaustion, workload escape and leakage through projections.
- No new listener, credential, or privileged surface is introduced by this decision. Every deferred item is explicit rather than silently substituted.

### Standards alignment

This is **alignment only**. OMES asserts no compliance, certification or audit attestation.

- ISO/IEC 27001 and 27002 (least privilege, logging), ISO/IEC 27005 (risk register via `MA-xx`), ISO/IEC 27017 and 27018 (cloud and PII considerations), ISO/IEC 27034 (application security: allowlisted, fixed-argv job boundary), ISO/IEC 27701 (privacy governance).
- ISO/IEC 20000-1 (service management evidence), ISO 22301 (continuity: restart and orphan handling), ISO/IEC 42001 (AI governance: authority split).
- ISO/IEC 15408 (Common Criteria) is **not claimed**: there is no scoped Target of Evaluation.
- NIST SP 800-207 (no implicit trust between planes), NIST AI RMF (govern and map agent autonomy limits), OWASP ASVS, OWASP API Security Top 10 and OWASP Top 10 for LLM Applications (excessive agency, prompt injection), SLSA/OpenSSF (provenance for pinned upstream baselines).
- Indonesia UU No. 27/2022 (PDP) and PP No. 71/2019 (PSTE) as governance context for personal data handling and electronic system operation.

---

## Consequences

### Positive

- The authority boundary for multi-agent patterns is reviewable and CI-checked (R8, R9) rather than prose-only.
- Real gaps (orphaned host jobs, trace identifiers, budget, isolation drift) become atomic, owned issues instead of implied scope.
- No second runtime, no new service, and no new privileged surface.
- The two existing Hermes capabilities are now in the registry with released-baseline evidence.

### Negative / cost

- This phase delivers no new runtime capability; value depends on #270–#277 landing, in dependency order.
- Operators who want per-task model tiering today must use separate Hermes profiles or wait for upstream; OMES will not paper over the gap.
- Wider R9 terms may reject a legitimately named OMES capability; such a case needs an ADR rather than a rename.

### Residual risk

- Users and operators can reach Hermes directly through Hermes-owned channels, bypassing OMES and AWCMS. OMES evidence therefore cannot be complete for agent activity.
- Upstream Hermes behavior (delegation limits, ACP approval prompts) may change; posture checks must follow the pinned baseline.
- The observer ingest function exists but has no production caller or transport in this repository; until that is closed, orchestration projections have no live source.
- Standards alignment is informational and does not substitute for an audit.

---

## Revisit triggers

Revisit this ADR when:

- Hermes ships a per-task model parameter or a generalized outbound ACP client (upstream [#5257](https://github.com/NousResearch/hermes-agent/issues/5257) or successor);
- a measured throughput, ordering, duration or high-availability requirement justifies a broker or workflow engine;
- PR [#268](https://github.com/ahliweb/omes/pull/268) merges (ADR-0031) or changes the Mission Control boundary;
- a child issue (#270–#277) finds that an assumption recorded here is false;
- the pinned Hermes baseline changes in a way that alters delegation, restart (`unknown`) or ACP semantics.
