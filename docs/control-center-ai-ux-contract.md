# OMES Control Center and Hermes AI Management — shipped/planned UX contract v1.0

**Status: proposed UI/UX supplement, 24 October 2026.** [Issue #348](https://github.com/ahliweb/omes/issues/348). Reuses the **accepted** [ADR-0023](adr/0023-control-center-ui-ux-design-system.md), [UI/UX design system](ui-ux-design-system.md), [Control Center integration contracts](control-center-and-integrations.md), and [Bun/AWCMS runtime boundary](awcms-bun-channel-realtime-boundary.md). **AWCMS Astro SYSTEM admin implements web screens; OMES owns native host lifecycle and contracts; Hermes Agent owns model reasoning, sessions, skills and agent execution.** This is not a new management framework.

## 1. Existing evidence vs proposed work

| UX ID | Screen / path | State based on code/issue | Owner |
| --- | --- | --- | --- |
| OM-01 | Fleet overview /admin/omes | IMPLEMENTED in AWCMS | OMES #200/#201 |
| OM-02 | Servers, configuration/drift /admin/omes/servers | IMPLEMENTED | AWCMS OMES Control |
| OM-03 | Deployment and observed state /admin/omes/deployments | IMPLEMENTED | AWCMS OMES Control |
| OM-04 | Jobs and operations /admin/omes/jobs and /operations | IMPLEMENTED, typed allowlist | OMES Control |
| OM-05 | Health/Doctor /admin/omes/health | IMPLEMENTED, read-first | OMES Control |
| OM-06 | Backups/restores /admin/omes/backups | IMPLEMENTED within currently authorized contract | OMES Control |
| OM-07 | Audit /admin/omes/audit | IMPLEMENTED | OMES Control |
| OM-08 | Worker enrollment /admin/omes/enrollments | IMPLEMENTED | OMES Control |
| OM-09 | Architecture /admin/omes/arsitektur, Mission Control /mission-control | Implemented route; **release snapshots/read-only observation**, not unrestricted live controls | OMES Control |
| OM-10 | AI agent management workspace with typed action approval | PLANNED | [OMES #343](https://github.com/ahliweb/omes/issues/343)/[#344](https://github.com/ahliweb/omes/issues/344) |
| OM-11 | 3D organizational digital twin, multi-agent coordinator/worker | PLANNED | [#263](https://github.com/ahliweb/omes/issues/263), [#264](https://github.com/ahliweb/omes/issues/264), [#277](https://github.com/ahliweb/omes/issues/277) |
| OM-12 | Browser-transfer workflows, checkpoints and operator intervention | PLANNED | [#294](https://github.com/ahliweb/omes/issues/294), [#300–#306](https://github.com/ahliweb/omes/issues/300) |
| OM-13 | Bun SSE/WS CRM progress projection in Control Center | PLANNED: upstream ADR not accepted | [AWCMS #1168](https://github.com/ahliweb/awcms/issues/1168), [#1171](https://github.com/ahliweb/awcms/issues/1171) |
| OM-14 | Runtime/dependency health and compatibility dashboard | PLANNED, not auto-upgrade action | [OMES #347](https://github.com/ahliweb/omes/issues/347) |

## 2. Retain authoritative OMES design tokens and shell

Existing OMES dark-first tokens defined in the accepted system: background #0E1216, cards #151A20, sidebar #11161B, border #242C35, text #E6EDF3, primary cyan #5FC8D6, green #6FD08C for verified healthy, amber #E8B44A for drift, red #E86D5B for failures, violet #8B9CF7 for control-plane approvals. These are **existing reference values**, not a proposed rival palette. High-density typography plus monospace for server IDs, hashes, versions and correlation IDs; responsive display and WCAG contrast. No color-only status.

Navigation: Fleet → Servers & Drift → Deployments → Operations & Jobs → Hermes Orchestration (read-only initially) → Health/Doctor → Backup & Restore → Audit → Enrollment, with command palette only for **allowlisted** actions. Operator can propose jobs and view telemetry; Owner or explicitly authorized approver can approve sensitive operations. UI role switches do not grant privileges: server enforces capability. Do not duplicate the AWCMS /admin owner shell; this is one nested Control Center.

## 3. Detailed screen contracts

### OM-01 Fleet overview
Risk-sort hosts by name, OS version, observed last heartbeat, online/stale/unknown, role, resource pressure, deployment drift and pending approvals. KPIs total enrolled, healthy, stale, failed operations, last backup, unknown posture. Click row to an authorized server detail. Unknown host health must never display green based on a stale cached observation. On mobile, critical status and last-seen timestamp remain visible.

### OM-02 Servers & Drift
Host detail: OS/kernel/arch, systemd vs container mode, deployment correlation, last verified state, drift diff, approved action eligibility. Separate *desired* from *observed*, never allow operator arbitrary SSH command. Remediation previews and typed job request show permissions/risk/rollback; validation blocks privilege escalation. Sensitive hostname/IP masked according to role and policy.

### OM-03 Deployments
List job/deployment ID, target host, image digest/version, desired/current release, started/updated, health and rollback readiness. Show topology shared/dedicated and source-of-truth. Re-deploy is a typed job with approval, not arbitrary user text. Prior operation status, failure evidence and independent target verification must remain accessible.

### OM-04 Operations & Jobs
Tabs Queued/Planning/Awaiting Approval/Applying/Succeeded/Failed/Unknown with filter by host/operation/actor/correlation. Each row opens typed plan/effects/diff, idempotency key, proposal/approver, audit and work progress; staleness is distinct from completion. Operator request→validation→owner approval→signed outbound OMES host poll→result reconciliation→audit. Cancel is effective only before dispatch when contract permits, never falsely promise cancelling an already-running OS action.

### OM-05 Health / Doctor
Layered evidence from host/service, agent runtime, model/provider and AWCMS control plane. Each check shows source authority, last collected timestamp, expected ranges, discrepancy and safe remedy. A model endpoint reported by provider does not mean all AI features are running; unavailable measurements are Unknown. No remote shell debug action from browser.

### OM-06 Backups / Restore
Show last verified backup artifact, checksum, retention, restore rehearsal and recovery point; **backup requested** vs **backup verified** separately. Restore must include severe confirmation, selected target, dry-run/approval evidence, downtime and rollback plan. No secret paths exposed to unauthorized viewers.

### OM-07 Audit
Immutable timeline of who requested/approved/denied/applied operations, correlation ID, typed target, result and evidence pointer, redacted actor-sensitive details. Export only policy-limited records, scrub commands/secrets/PII. Query filters and pagination survive reload; roles do not see other tenant data.

### OM-08 Enrollment
Guided enrollment via short-lived token and signed key exchange that preserves OMES **outbound-only HTTPS pull-worker**. UI shows enrollment requested/enrolled/offline/revoked, token expiry and last heartbeat without exposing stored key material. No button to open inbound privileged WebSocket/gRPC/SSH port.

### OM-09 Architecture and Mission Control (current read-only)
Architecture release view is a **pinned snapshot**; tree/graph use bounded read-only states with labels “Observed” and “Snapshot”, timestamps and provenance. Mission Control may show session/subagent activity tree and events that current AWCMS contracts support. 3D twin (#263) is a **planned addition**, not a currently operating company/org digital twin. Provide keyboard-accessible 2D alternative and “data stale” overlays.

### OM-10 AI Management (planned)
Workspace tabs **Agents / Sessions / Policies / Knowledge Egress / Approvals / Budgets / Audit**. Cards Agent ID, model/provider, supported capability, risk posture, connected scope, last observed, cost estimate **only if reliable**, failures and linked task. Proposal detail includes goal, exact typed allowable tools, data classification, estimated budget, human checkpoint, provenance and expiry. Approve/Deny requires policy and separate capability; no direct access to Hermes databases, global “run any prompt as root”, arbitrary shell or credentials. “Assist” remains default for CRM; Hermes owns actual execution.

### OM-11 Mission Control advanced (planned)
Read-only/default digital-twin map of coordinator, worker, delegated tasks, budgets, isolation/recovery state as **projections from approved sources** (OMES/AWCMS/Hermes). Interactive selection opens existing 2D detail route, not duplicate business task engine; list fallback, keyboard navigation, screen reader descriptions, reduced-motion mode, 3D progressive enhancement and no extraneous animations hiding issues.

### OM-12 Browser transfer workflow (planned)
Wizard Source/Destination connection → schema mapping review → data privacy & approvals → dry-run sample → human login/MFA checkpoint if necessary → submit durable job → progress with per-record checkpoint → independent destination read-back → final reconciled report. Status pending/blocked-for-MFA/running/uncertain-write/verified/failed/cancelled, with redacted errors and a clear safe retry/rollback state. Browser automation executed by allowlisted Hermes/worker adapters; never new unbounded OMES native browser command authority.

### OM-13 Bun realtime projection (planned)
The AWCMS Astro control-plane application may use **Bun SSE first**, native Bun WS for proven duplex only after AWCMS #1168, to display sanitized progress. OMES host worker remains native Python/Bash/systemd and **does not expose inbound privileged WebSocket**. Signed outbound HTTPS pull/enroll/result/heartbeat remains authoritative. Upgrade clients verify Origin/audience/ticket; roles and tenant/server must be checked. Frame does not carry arbitrary host commands or private prompts.

### OM-14 Dependencies and release diagnostics (planned)
Operator view reports per host/agent/runtime **installed**, **latest available**, **latest supported**, **held back**, **vulnerability/compatibility** and **evidence refreshed at**. Bun 1.4.4 may apply to AWCMS and in-repo Bun tooling, not as a mandate to run OMES Python host services under Bun. OS-specific Python, systemd, browser dependencies and Hermes release require compatibility tests on Ubuntu 22.04/24.04/26.04 and Linux Mint 22. Latest with breaking changes is a **candidate**, not an instant production upgrade. Actions link to reviewed atomic PR/change plan; do not expose “upgrade all hosts” shell button.

## 4. Interaction states and role constraints

Each view must handle Loading, Empty, No Permission, Stale, Unknown, Offline, Error, Approval Required, Running and Verified Success; color + textual labels, source freshness and bounded retry. A dashboard cannot infer healthy host from any single successful heartbeat if other evidence is old. Destructive operations require approval and confirmation; operator cannot bypass via frontend role switch. Network retries are idempotent; no duplicate risky job. Focus restoration, keyboard nav, readable telemetry at 360px/mobile, table pagination and accessible 2D diagrams. No leak of secrets, device IDs, private sessions or cross-tenant PII in charts/export.

## 5. Cross-repo traceability and release
Full CRM/UI target [AWCMS-One #472](https://github.com/ahliweb/awcms-one/issues/472); upstream generic screens [AWCMS #1174](https://github.com/ahliweb/awcms/issues/1174); public Astro [#127](https://github.com/ahliweb/awcms-astro/issues/127); OMES AI [#343](https://github.com/ahliweb/omes/issues/343)/[#344](https://github.com/ahliweb/omes/issues/344), Mission Control #263/#264/#277 and browser transfer #294/#300–#306. Security/host boundary [ADR-0027](adr/0027-control-center-pull-worker-transport.md), runtime [#345](https://github.com/ahliweb/omes/issues/345), dependency inventory [#347](https://github.com/ahliweb/omes/issues/347).

Acceptance: compare UI against active AWCMS routes and OMES API contracts; no fake action buttons, no second Hermes runtime, no inbound host listener, cross-tenant negative tests, approval/idempotency/audit, mobile/keyboard/WCAG, status freshness and recovery. Run repo-defined docs, security and host-matrix gates before merging. This document makes **no** runtime change.
