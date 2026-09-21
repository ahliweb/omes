# ADR-0024: Content Distribution Workflow Boundary and Migration to AWCMS and Hermes

- **Status**: Accepted
- **Date**: 2026-09-21
- **Author**: OMES Architecture Team
- **Deciders**: OMES Core, Hermes Integration, AWCMS Architecture
- **Consulted**: Security, Operations, Agent Ecosystem
- **Informed**: Contributors, Operators
- **Supersedes**: ADR-0015 (Content Distribution Workflow)
- **Related Issues**: #63, #64, #65, #66, #67, #68, #69, #70, #171, #179, #195, #199

---

## 1. Context and Problem Statement

In issues #63–#70, OMES implemented an experimental content distribution workflow under `lib/omes/py/content/` (`omes content`). This included file inbox scanning, multi-platform media/caption planning and validation, browser profile/session management, browser automation drivers (`generic_browser`, `manual_stub`), direct outbound Telegram API notifications, and hash-chained audit logging.

Under ADR-0017 (*Upstream-First Architecture Precedence: DELEGATE -> PORT -> ADAPT -> DEFER -> REJECT*) and the core repository rules in `AGENTS.md`:
- **OMES** owns host compatibility, preflight, installation, service lifecycle, hardening, health, backup, restore, rollback, compatibility evidence, provenance, and host/deployment state.
- **Hermes Agent** owns agent runtime behavior: reasoning, messaging, channels, sessions, memory, skills, delegation, cron, browser automation, and model/provider routing.
- **AWCMS / Control Center** owns tenant, customer, catalog, subscription, invoice, payment, entitlement, approval, support, content records, publishing intent, business approval, platform policy, durable job/reconciliation state, and business audit/reporting.

Having OMES directly manage social media platform publishing logic, browser cookie storage for third-party platforms, Telegram bot messaging, and media content pipelines is an architectural boundary violation. It couples host deployment infrastructure to social media domain policies and introduces credential management risks into the host runtime.

Issue #179 requires refactoring content workflows out of OMES core to the AWCMS and Hermes boundaries without deleting user data or losing existing workflow evidence.

---

## 2. 11-Criteria Architectural Evaluation

To determine the target boundary and migration strategy, the architectural options were evaluated across 11 standard criteria:

### Option A: Retain Full Content Engine in OMES Core (Status Quo)
- **Advantages**: No immediate code relocation required; existing test suite passes unchanged.
- **Disadvantages**: Blurs product authorities; violates ADR-0017; mixes host infrastructure with commercial social-media policy; burdens OMES with fast-moving social network API changes and browser session security.
- **Security**: Poor. Storing third-party browser session cookies on the host elevates the host privilege domain.
- **Performance**: Heavy host footprint for browser drivers and media processing.
- **Maintainability**: Low. Every platform UI/API change forces host tool updates.
- **Scalability**: Low. Single-host file-based inbox cannot scale across multi-server fleets or multi-tenant organizations.
- **Accessibility**: N/A for host CLI, but no accessible multi-user management web interface.
- **SEO Impact**: None directly, but distorts OMES focus as a web server orchestrator.
- **UI/UX Implications**: CLI and Telegram only; lacks multi-tenant web dashboard.
- **Compatibility**: High for existing local scripts, but incompatible with centralized Control Center management.
- **Operational Complexity**: High. Operators must supervise local browser profiles and cookies on every host.
- **Long-term Technical Implications**: Severe architectural debt, conflicting release cycles between host tooling and domain publishing.

### Option B: Migrate Content Domain Directly into Hermes Agent Runtime
- **Advantages**: Delegates browser automation and LLM captioning directly to Hermes.
- **Disadvantages**: Forces Hermes into durable business database management, financial/tenant audit logging, and customer approval workflows, which violates Hermes's agentic design.
- **Security**: Moderate. Hermes owns agent sessions, but business approvals should not be purely agent-internal.
- **Performance**: High for agent reasoning; poor for long-term durable audit and media storage.
- **Maintainability**: Moderate. Keeps agent logic close to agent runtime.
- **Scalability**: Limited to individual Hermes agent instances.
- **Accessibility**: Dependent on chat/agent UI.
- **SEO Impact**: None.
- **UI/UX Implications**: Chat-only interaction model.
- **Compatibility**: Requires migrating all existing content state into Hermes memory/sessions.
- **Operational Complexity**: Moderate.
- **Long-term Technical Implications**: Conflates agent reasoning state with business records of truth.

### Option C: Decoupled Multi-Authority Architecture (Selected / Recommended)
- **Hermes**: Owns reasoning, caption generation, platform adaptation, browser automation execution, and chat messaging.
- **AWCMS / Control Center**: Owns content records, business approvals, tenant policies, durable job state, and audit reporting.
- **OMES**: Retains host execution/process isolation, compatibility shims for `omes content`, migration export utilities, and generic job primitives (`lib/omes/py/jobs/`).
- **Advantages**: Strictly adheres to ADR-0017 and `AGENTS.md`; cleanly separates concerns; preserves all existing audit and media evidence; eliminates credential risks on the host.
- **Disadvantages**: Requires coordination between AWCMS pull-worker jobs and Hermes agent skills.
- **Security**: Superior. Eliminates raw browser session cookies from OMES host storage; leverages centralized RBAC and least-privilege pull-worker tokens; enforces cryptographic read-back for publishing side effects.
- **Performance**: High. Host resources are freed from unmanaged browser automation; jobs execute asynchronously via pull workers.
- **Maintainability**: Superior. Platform API/UI changes are isolated to Hermes skills or AWCMS adapters without rebuilding host packages.
- **Scalability**: High. Pull-worker model enables distributed multi-server fleet execution and centralized management.
- **Accessibility**: Full web accessibility via AWCMS Control Center UI (WCAG 2.1 AA compliant).
- **SEO Impact**: Clean separation allows AWCMS to manage CMS metadata, canonical URLs, and social OpenGraph tags properly.
- **UI/UX Implications**: Modern Control Center dashboard with visual approval queues, audit trails, and command palette integration.
- **Compatibility**: Full backward compatibility via non-breaking CLI shims and a structured export format (`awcms-content-v1`).
- **Operational Complexity**: Low to Moderate. Clear operational boundaries and automated migration tooling.
- **Long-term Technical Implications**: Stable, maintainable architecture with clear upgrade paths and zero technical debt accumulation.

---

## 3. Decision

We adopt **Option C** and classify all existing content components according to the 5 standard migration dispositions:

### 3.1 Responsibility and Module Classification Matrix

| Component / Subsystem | Path / Reference | Classification | Successor / Target Authority | Removal Trigger / Timeline |
|---|---|---|---|---|
| **Inbox & Media Ingestion** | `lib/omes/py/content/inbox.py` | `KEEP_OMES_HOST_GLUE` / `COMPATIBILITY_SHIM` | OMES host glue monitors local inbox directories; durable job creation transitions to Control Center pull-worker job submission (#199). | Keep in OMES as optional local file watcher. |
| **Content Job State Machine** | `lib/omes/py/content/jobs.py` | `MOVE_TO_CONTROL_PLANE` | AWCMS Control Center (`omes_control` module, issue #196–#198). Consolidated onto generic state transition & approval patterns matching `lib/omes/py/jobs/`. | OMES v2.0 deprecation. |
| **Platform Validation** | `lib/omes/py/content/validation.py` | `MOVE_TO_HERMES_ADAPTER` / `MOVE_TO_CONTROL_PLANE` | Hermes skills validate captions before proposal; Control Center validates forms before approval. | Retire in OMES after AWCMS integration. |
| **Telegram Integration** | `lib/omes/py/content/telegram.py` | `RETIRE` | Direct outbound Telegram calls retired. Replaced by Hermes agent gateway channels (#183) and Control Center webhook notifications. | OMES v2.0 deprecation. |
| **Hermes Chat Skill** | `skills/content/` | `MOVE_TO_HERMES_ADAPTER` | Upstream Hermes agent skill communicating via Control Center REST/pull-worker API instead of direct CLI mutations. | Transferred to upstream Hermes skills catalog. |
| **Browser Workers & Drivers** | `lib/omes/py/content/workers/` | `MOVE_TO_HERMES_ADAPTER` | Upstream Hermes browser agent / computer-use tools or containerized worker tasks. | Retire from OMES host core. |
| **Session & Credential Storage** | `lib/omes/py/content/paths.py` (`sessions/`), `cli.py` (`session login/revoke`) | `RETIRE` | Host cookie/session storage retired. Replaced by encrypted vault / Hermes secret manager. | Deprecation notice in v1.x; removal in v2.0. |
| **Audit & Reports** | `lib/omes/py/content/reports.py` | `COMPATIBILITY_SHIM` / `MOVE_TO_CONTROL_PLANE` | Hash-chained audit logging consolidated with generic `lib/omes/py/jobs/audit.py`. Content reports exported to successor schema. | Retain export and archive reading. |
| **Content CLI Wrapper** | `lib/omes/cmd/content.sh`, `lib/omes/py/content/cli.py` | `COMPATIBILITY_SHIM` | Retained as compatibility shims with clear deprecation notices and export capabilities (`omes content export --format awcms-v1`). | Deprecated in OMES v1.x; removed in v2.0. |

---

## 4. Consolidation with Generic OMES Jobs Subsystem

The content job state machine in `lib/omes/py/content/jobs.py` and `reports.py` was evaluated against the generic jobs subsystem in `lib/omes/py/jobs/`:
1. **Hash-Chained Audit Log**: Both subsystems implemented SHA-256 hash-chained append-only logs (`state/audit.jsonl`). Going forward, generic host operations standardize on `lib/omes/py/jobs/audit.py`. Content audit history is preserved and exportable to the successor without modification.
2. **Idempotency & Deduplication**: Both subsystems enforce SHA-256 artifact deduplication and idempotency keys. The generic primitives in `lib/omes/py/jobs/store.py` are domain-neutral; content-specific fields (`caption`, `targets`, `platforms`, `hashtags`) are strictly kept out of generic host jobs.
3. **No Third State Engine**: Neither Hermes nor OMES will create a third state engine. Business state resides exclusively in AWCMS Control Center; host execution state resides in `lib/omes/py/jobs/`.

---

## 5. Migration and Export Specification (`awcms-content-v1`)

To guarantee safe, non-destructive migration without secret leakage:
1. **Export Utility**: `omes content export --format awcms-v1 --out <dir> [--since <date>]` exports all job history, approval decisions, media hashes, publication verification evidence, and audit logs.
2. **Secret Redaction**:
   - Browser profiles, cookies, tokens, and credentials in `content/sessions/` are **strictly excluded**.
   - All exported JSON structures pass through recursive secret pattern scrubbing (`TOKEN|KEY|SECRET|PASSWORD|COOKIE`).
3. **Integrity & Deduplication**:
   - Export includes SHA-256 hashes of original and processed media artifacts.
   - Preserves publication status and external URLs to prevent duplicate publication upon import into AWCMS.
4. **Manual Review Enforcement**:
   - Any job in `manual-review`, `retryable-failure`, or with partial platform failures is marked with `requires_manual_review: true`. The successor system must never auto-publish imported jobs without explicit operator approval.
5. **Reversibility**:
   - No original media, archived jobs, or audit logs are deleted during migration. Migration is entirely read-only with respect to existing data.

---

## 6. Deprecation and Sunset Timeline

1. **OMES v1.x (Current)**:
   - Feature freeze on `omes content`.
   - Deprecation notices printed on `omes content` CLI invocations.
   - Export tool (`--format awcms-v1`) available for migration.
   - Existing workflows remain functional for backward compatibility.
2. **OMES v1.9 / AWCMS Control Center GA**:
   - Control Center natively ingests migrated content records (#199).
   - Hermes agent skills connect directly to Control Center API.
3. **OMES v2.0**:
   - `lib/omes/py/content/` retired from OMES core distribution.
   - `omes content` replaced with guidance pointing to Control Center.

---

## 7. Consequences

### Positive
- Strict alignment with ADR-0017, `AGENTS.md`, and least-privilege security principles.
- Host security is hardened by removing browser cookies and social media session tokens.
- Generic jobs subsystem (`lib/omes/py/jobs/`) remains clean and domain-neutral.
- Full provenance and audit chain preserved without data loss.

### Negative / Trade-offs
- Operators running standalone local `omes content` pipelines must transition to AWCMS Control Center or Hermes agent skills before OMES v2.0.
