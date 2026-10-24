# AI-assisted browser data transfer — proposed integration architecture

> **Status: DESIGN ONLY — Not implemented yet.** Parent [#294](https://github.com/ahliweb/omes/issues/294); architecture task [#295](https://github.com/ahliweb/omes/issues/295). Implementation requires separately reviewed child issues and cross-repository changes. This design is not evidence of a deployed browser worker, configured model, or working UI.
>
> Read [AGENTS.md](../AGENTS.md), [scope](scope.md), [architecture](architecture.md), [jobs](jobs.md), [Ollama](ollama.md), [AI privacy](ai-data-privacy-and-model-security.md), [ADR-0017](adr/0017-upstream-first-ownership-and-boundary-enforcement.md), [ADR-0024](adr/0024-content-workflow-boundary-and-migration.md), [ADR-0027](adr/0027-control-center-pull-worker-transport.md), and [ADR-0029](adr/0029-ai-data-boundary-and-private-inference.md).

## 1. Purpose and guardrails

Enable an authorized operator to request: "Read up to 100 eligible records from source website A, map selected fields, enter them in destination website B, confirm each persisted record, and report exceptions." Supports sites that expose no API, but prefers supported APIs when available.

**Separation of concerns (normative):**

| Capability | Authority | Location / disposition |
|---|---|---|
| Business instruction, workflow approval, site registry, mapping versions, tenant/RBAC/ABAC/RLS | AWCMS | `ahliweb/awcms` canonical `/admin/omes/*` area; `awcms-one` is template/reference, not canonical source |
| Durable transfer job, per-record ledger, scheduling, report, reconciliation queue, business audit | AWCMS or dedicated application under its governance | Not OMES host state; reuse existing AWCMS job/approval patterns |
| Natural-language interpretation, model/provider selection, memory, agent workflow, browser invocation | Hermes | Supported Hermes runtime/skills/MCP; do not create second OMES agent runtime |
| Deterministic browser interactions and browser session management | Hermes-owned adapter or dedicated external worker | Playwright SDK; an MCP adapter may aid discovery, but neither implementation becomes OMES business logic |
| Host install/preflight/hardening/health, sandbox, resource ceilings, provenance, sanitized host evidence | OMES | Extend existing lifecycle, exposure audit (#80), Ollama health (#71), isolation (#276), pull-worker (#192), privacy (#214–#216) |
| External record truth and submit outcomes | Origin websites | Source and destination websites remain authoritative |
| Consolidated operator UI / Mission Control projection | AWCMS | Reuse existing Control Center and #263; no separate dashboard |

**Critical caveat:** OMES does not intercept every Hermes-native action. Any Hermes entrypoint not wired to this controlled workflow must not be described as governed by this policy. The approved workflow runs in a separately constrained profile, browser session, and tool adapter; a general unrestricted Hermes agent remains outside its enforcement scope.

## 2. Upstream evaluation and choices

- Prefer **Playwright SDK** for a repeatable, reviewed action graph: stable role/label/test-id locators, event-aware waits, typed errors, and deterministic retries. It does not need an LLM for every field.
- Prefer Hermes' **existing native browser tool** if it provides equivalent required guarantees on the released/pinned version. Inspect installed Hermes behavior first; do not assume all upstream `main` features ship in the pinned release. If a specific contract gap exists, use a **Hermes-owned Playwright tool/skill or supported MCP connection** rather than importing a second browser engine into OMES.
- **Playwright MCP** may be an opt-in discovery/recovery interface; it is *not a security boundary*. Do not expose a shared unauthenticated browser endpoint. Authorize each action outside MCP.
- **Ollama** is optional local inference. Candidate `qwen3.5:9b` (agent planning/tool calling) and comparator `ministral-3:8b`. Use `qwen3.5:4b` only if a measured synthetic evaluation shows sufficient accuracy for mapping. Models are configurable/pinned; no model is declared "best" without field tests. AI model routing remains Hermes-owned.
- For `RESTRICTED` tasks, enforce verified local-only model egress using ADR-0029. If local inference fails, **pause**; never silently fall back to a cloud model.

Upstream references: [Playwright locators](https://playwright.dev/docs/locators), [Playwright authentication](https://playwright.dev/docs/auth), [Playwright tracing](https://playwright.dev/docs/trace-viewer), [Microsoft Playwright MCP](https://github.com/microsoft/playwright-mcp), [Hermes browser tools](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/browser.md), [Hermes MCP tools](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/mcp.md), [Ollama tools](https://docs.ollama.com/capabilities/tool-calling). Upstream `main` is a research reference, not proof of the deployed version.

## 3. User journey (proposed)

1. Authorized user creates source and target **site profiles**, selects a pre-approved transfer template, data class, maximum records, permitted fields and an explicit run window.
2. Operator supplies *opaque session secret references* or completes interactive login; credentials never pass into an LLM or AWCMS logs.
3. Workflow creates immutable `plan_version`, `mapping_version`, `policy_version`, `model_digest`, `browser_adapter_version` and a preview/dry run against synthetic or read-only source data.
4. Policy resolves approval requirements (especially writes, sensitive records, financial/health data, new site origins).
5. Approved job leases an isolated browser worker under tenant/job scope. A task lease expires; orphaned sessions are killed.
6. Worker extracts one source record with stable locators and computes a canonical source key or fingerprint; schema validation and duplicate lookup happen **before** any mutation.
7. For a known mapping, use deterministic transform. For ambiguity, invoke local LLM on the **minimum necessary sanitized fields/structure**, requiring typed JSON and confidence/uncertainty metadata; reject unsupported categories.
8. Deterministic executor revalidates action type, allowed locator identifier, target origin, field type, input bounds and authorization *outside the model*. Free-form JS/shell/fetch is disallowed.
9. Fill target form, validate the preview, submit at most once, then read back destination through a separate view/search/record ID and compare normalized values. A success toast does not equal persistence.
10. Store audit outcome and sanitized evidence; continue, pause for manual review, or reconcile uncertain writes. Do not automatically repeat ambiguous submissions.
11. Stream redacted progress to existing AWCMS Jobs/Operations screens and Mission Control projection. Final report reconciles counts and links each exception to a permitted operator action.

## 4. Two execution modes

| Step | Deterministic | AI-assisted |
|---|---|---|
| Discover fields | Signed or reviewed template selectors | Read-only DOM/accessibility observation + model proposes candidate mapping |
| Choose destination | Registered profile + fixed origin | Same — model may not introduce new domains |
| Fill/select/click | Typed Playwright actions | Model suggests typed action from fixed registry; validator decides |
| Write | Policy/approval-backed executor | **Same executor and policy**, no model direct write |
| Verify | Read-after-write and fingerprint compare | Same; AI may summarize exception but never declare success |
| Recover | Known selector alternatives; bounded retry | Propose a mapping fix; require review/version bump before next write |

Proposed allowed action types: `navigate_registered`, `read_text`, `read_table_page`, `get_field_value`, `select_option`, `fill_registered_field`, `click_registered_action`, `wait_for_condition`, `verify_destination_record`, `pause_for_human`. **No** `evaluate_js`, `run_shell`, `send_http`, `execute_sql`, `read_local_file`, arbitrary selector/XPath, or arbitrary URL supplied by the model. Upload/download requires a **separate** explicit capability design.

Do not rely on Playwright-MCP flags as an authorization or security boundary. The trusted executable adapter must enforce the policy independently.

## 5. Example typed action contract (illustrative; not published schema)

```json
{
  "schema_version": "browser_action.v1",
  "job_id": "job-fixture-001",
  "record_key": "source-0001",
  "tenant_scope": "tenant-fixture",
  "target_site_profile": "target-fixture",
  "action": "fill_registered_field",
  "field_id": "customer_name",
  "value_ref": "validated_record.customer_name",
  "mapping_version": "mapping-v1",
  "step_id": "step-0003",
  "correlation_id": "trace-fixture-001",
  "approval_ref": "approval-fixture"
}
```

`value_ref` resolves to a validated in-memory value, not raw sensitive content embedded in an audit command. Schema needs `additionalProperties: false`, bounded lengths, strict enums, and exact version checks. Browser adapter receives the real value only in its privileged per-job execution context. Model output is not trusted until all validation checks pass.

**Ownership of contracts**:
- AWCMS: workflow definition, mappings, tenant authorization, durable record ledger, approval, reporting and user-facing projections.
- Hermes/adapter: model intent -> typed browser tool invocation, locator registry, session-scoped progress and terminal adapter result.
- OMES: host capability/health/exposure/isolation metadata and sanitized execution evidence compatible with the existing pull-worker and Control Center contracts.
- New cross-boundary OMES schema files require their own issue and consumers pinned to the exact version; don't extend the host safe-operation allowlist to arbitrary `browser_click` / `browser_script`.

## 6. Durable job and record state (proposal)

**Job**: `draft -> validated -> approval_required? -> queued -> leased -> running -> reconciling? -> completed | completed_with_exceptions | failed | cancelled`. `paused_needs_login`, `paused_needs_review`, `paused_policy` are explicit non-terminal states. Terminal reports are immutable; a rerun is a new run linked to the original job.

**Record**: `discovered -> extracted -> validated -> duplicate_skipped | ready -> pre_submit_checked -> submitted_unknown -> verified_committed | reconciliation_required | failed_nonretryable | paused_manual`.

A submit response, tool timeout, browser crash, HTTP 2xx, DOM toast, or successful model tool call results initially in **`submitted_unknown`** until a separate authoritative destination lookup confirms persistence. `reconciliation_required` never automatically transitions to a fresh submit without proof the previous attempt did not persist.

**Delivery and concurrency**:
- At-least-once task delivery is expected; exactly-once side effects are not assumed possible on UI-only systems.
- Uniqueness key proposal: `(tenant_id, workflow_id, destination_profile_id, source_record_key, mapping_version)`; define reprocessing/version semantics explicitly in AWCMS.
- Locks/leases serialize writes per destination record, with fencing tokens to prevent late workers committing after lease loss.
- Retry navigation, reads, model inference and validation if safely retryable; writes are handled by idempotency+reconciliation and never blind retry.
- When the source site does not expose a durable key, require an approved deterministic fingerprint and document collisions.
- Failed/paused records keep checkpoints; cancellation never assumes an in-flight commit was reverted.

## 7. Monitoring and reports: sanitized, reconcilable

**No second dashboard.** Add a task type and projection to existing AWCMS `/admin/omes/*` Jobs/Operations/Audit and #263 Mission Control; the latter is visualization only.

Recommended event envelope metadata: `event_id`, `schema_version`, `tenant_id`, `workflow_id`, `run_id`, `job_id`, `record_key_hash`, `step_id`, `state_before`, `state_after`, `action_type`, `site_profile_id`, `worker_id`, `model_id`, `adapter_version`, `correlation_id`, `idempotency_key_hash`, `reason_code`, `duration_ms`, `observed_at`, `evidence_ref`, `approval_ref`. Any trace or screenshot is privacy-classified, masked and short-lived. No raw prompts, page HTML, customer values, cookie jars, bearer tokens, or full browser sessions in the event stream.

**Invariants**:
- `total_discovered = verified_committed + duplicate_skipped + rejected + paused + reconciliation_required + failed + in_progress`, with mutually exclusive latest-record states.
- A failed job must not be reported as a successful complete transfer.
- Dashboard staleness / unreachable agent must be visible. Never infer zero failures from missing evidence.
- Monitor counts, approved-vs-denied actions, record throughput, latency p50/p95, browser crash rate, auth interruptions, ambiguous mapping rate, reconciliation age, GPU/RAM footprint and model-call volume. A 5-minute summary is a *notification/reporting view*, not the primary data store.

Report example: `run_id; started_at; finished_at; source_profile; target_profile; model_version; mapping_version; discovered; verified_committed; duplicates; rejected; paused; uncertain; failed; operator_approvals; redacted_errors; evidence_refs`. Export subject to RBAC, retention and masking.

## 8. Threats and safeguards

| Threat / failure | Control and required test |
|---|---|
| Indirect prompt injection in website DOM or accessibility labels | Treat every observation as untrusted data; policy tool whitelist; hostile-page test tries to override instructions and exfiltrate data |
| Browser cookie/session theft or cross-tenant reuse | Unique context/profile per job; encrypted secret refs; no shared browser; isolate user ID, filesystem and network; destroy on completion |
| Ollama or CDP/MCP listening publicly | Default loopback/unix socket, host audit #80, test exposed listener is blocked; private service auth where remote use is approved |
| Cloud-model data egress or fallback | Reuse #214-#216, local-only/egress-denied for RESTRICTED, negative outbound traffic test |
| SSRF, file://, local metadata endpoints, redirects, popup | Origin allowlist checked on every navigation/redirect/request; network egress sandbox; reject forbidden protocols and private hosts |
| Cross-tenant job or stale approval | AWCMS RLS/RBAC/ABAC; signed/scoped short-lived lease; approver authorization and version binding |
| Browser timeout after submit | Mark unknown; independent destination lookup before any retry |
| Race/concurrent workers duplicating form writes | Per-record lease/fencing and idempotent ledger; restart/duplicate-message test |
| MFA/CAPTCHA/login expired | Manual intervention; do not bypass challenges or collect credentials through model |
| Evidence leaks through screenshots, traces and logs | Mask/disable by default for sensitive workflows; opt-in scoped evidence, encrypted short retention, deletion/hold policy |
| Untrusted HTML/template or arbitrary JS actions | Strict action schema, no arbitrary code execution, sanitized structured locator ID registry |
| Inconsistent source/destination format or wrong mapping | Validated mapping version, schema/enum checks, preview and field-level read-back comparison |
| Worker restart, cancel and orphan browser | Durable checkpoint, supervisor cleanup, lease expiry, heartbeat and reconciliation |

Regulatory/standards references: Indonesian [UU 27/2022 PDP](https://peraturan.bpk.go.id/Details/229798/uu-no-27-tahun-2022), [PP 71/2019 PSTE](https://peraturan.bpk.go.id/Details/122030/pp-no-71-tahun-2019), and [Permenkes 24/2022](https://peraturan.bpk.go.id/Details/245550/permenkes-no-24-tahun-2022) for relevant health records; ISO/IEC 27001, 27002, 27005, 27034, 27701, ISO 22301 and ISO/IEC 42001 as risk-based design references. Regulatory applicability is workload-specific and must be assessed; referencing a standard does not imply certification.

## 9. Synthetic pilot and acceptance tests

Use two owned, fake, local websites:
- **Source**: paginated list, details, 100 unique test records, malformed inputs, extra whitespace, Indonesian text, delayed render, dynamic labels, session expiry, 429/5xx.
- **Destination**: multi-step form, dropdown, unique ID check, duplicate warning, delayed commit, success toast without persistence, server-side validation, update search/detail for independent read-back.

Run at least these cases:
1. 100 deterministic records: exact normalized field match, no duplicate persisted.
2. AI-assisted mapping of differently named fields: schema-valid bounded action proposals and manual review on ambiguity.
3. Prompt injection in page content: zero unauthorized navigation/tool use.
4. Wrong origin, private-network redirect, `file://`, unknown action: blocked by executor.
5. Crash immediately before, during, after submit: reconciliation before retry; no silent double entry.
6. Source duplicate/changed record with replayed queue item and two workers: one correct destination commit.
7. Expired login and MFA: pause with actionable operator status; no bypass.
8. Cross-tenant, unapproved write, expired approval and revoked worker: denied.
9. Secrets and PII in page content: sanitized logs and reports; no cloud egress for restricted profiles.
10. Missing/late heartbeat and model service outage: observable degraded/paused state and resumable recovery.

Test budget should track *record-level verified correctness* separately from *browser-step completion*. At minimum, no unverified record is ever counted as transferred; exactly-once side effects are **not** promised by the underlying UI.

## 10. Detailed implementation order and release gates

| Phase | Code owner | Deliverable | Release gate |
|---|---|---|---|
| P0 | OMES architecture | ADR, PRD, threat model, contracts plan, child issues | Reviewed, no ownership overlap |
| P1 | OMES host | Browser isolation/provenance/readiness extension **only**, reuse #276 and #80 | Safe deployment/preflight and failure tests |
| P1 | Hermes integration | One pinned baseline/adapter, AI tool contract, selector registry | Sandbox tests, no arbitrary actions |
| P2 | AWCMS | Tenant-scoped workflows, mapping approvals, durable record ledger | Auth/RLS, leases, retries, replay tests |
| P3 | AWCMS + Hermes | Verification/reconciliation, manual intervention | Zero blind resubmit on unknown writes |
| P4 | AWCMS | Existing admin UI, live status, report and audit projection | Reconciled counts, no second dashboard |
| P5 | Integration QA | Fake-site end-to-end, performance, chaos, threat testing | Signed test evidence & go/no-go |

**OMES host-readiness scope (P1, [#298](https://github.com/ahliweb/omes/issues/298)).** The OMES-owned P1 host deliverable is `omes health browser-worker` ([docs/browser-worker-readiness.md](browser-worker-readiness.md)): read-only, metadata-only host evidence emitted as the `browser-worker-readiness` document of [`contracts/browser-transfer/v1`](../contracts/browser-transfer/v1/README.md) (browser runtime provenance by version label, CDP/MCP/Ollama listener binding, model readiness through the existing Ollama probe, model-egress class, resource floors, orphan sessions and leftover profiles, and heartbeat freshness). It is never `ready` on a real host until per-job isolation evidence from [#276](https://github.com/ahliweb/omes/issues/276) lands: until then `session_isolation` is `unknown` with `ISOLATION_EVIDENCE_NOT_INTEGRATED`. A `ready` document is a producer claim about the host at `observed_at`, not authorization to run a job. It adds no executor, worker, adapter or listener, and OMES still does not run browsers; the worker, adapter and supervisor remain Hermes-owned or external ([#299](https://github.com/ahliweb/omes/issues/299)) and job approval, leases and the record ledger remain AWCMS-owned.

**Not implemented yet**: reusable browser-data-transfer templates, Hermes Playwright adapter, AWCMS transfer UI/ledger, record-level reporting. Tracked by #294 and its children. Existing OMES `omes job`, Ollama health, exposure and Control Center contracts are reusable foundations but do not by themselves deliver this workflow. The `contracts/browser-transfer/v1` wire contracts (#296) and the read-only `omes health browser-worker` evidence producer (#298) now exist; neither executes a browser.

Per-job browser isolation evidence (`session_isolation: per_job`, per-job egress allowlists and cgroup ceilings) and cryptographic binary hash pinning of the browser executable are Not implemented yet (tracked in #298; isolation depends on #276). Only the browser version label is compared today.

## 11. Operational decision rubric

- If a stable source/target API exists and is authorized, prefer API integration.
- If both sites are fixed, build a deterministic Playwright workflow and use AI only for mapping ambiguity or error triage.
- If pages are varied, use a supervised Hermes agent with a typed action registry and constrained Playwright/MCP observation.
- If data classification, origin, field mapping, approval or destination state is unknown, **pause**; do not hallucinate an action.
- If the job affects financial, identity or medical records, default to stricter approval, masking, isolation and independent read-back.

This design requires compatibility checks against the **actual pinned Hermes/Ollama/Playwright versions** and applicable licenses before implementation.

Pinned version evidence, the Hermes native browser and Playwright MCP capability inventory, the A/B/C approach comparison with ADR-0017 classification, the adapter gap analysis, and the (currently BLOCKED) local-model benchmark protocol are recorded in [browser-transfer-compatibility-matrix.md](browser-transfer-compatibility-matrix.md) (#297); this design's compatibility requirement is satisfied only to the extent that document's open items are closed.
