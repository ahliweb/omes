# OMES Control Center foundation (issue #91)

> Status: OMES-side contract deliverable only. Issue #91 ("Build AWCMS-based
> OMES Control Center foundation") is a web GUI issue. Per
> [AGENTS.md](../AGENTS.md) §2 and the AWCMS boundary rule, the web GUI,
> tenant/RLS tables, RBAC/ABAC enforcement, browser/API tests, and the
> authentication/audit/workflow-approval/reporting primitives it uses are
> implemented in the **awcms-one** repository, not here. This document
> states precisely what belongs where so neither repository re-implements
> the other's responsibility, and lists which of issue #91's acceptance
> criteria this repository does and does not satisfy.

## 1. What this repository (OMES) provides for issue #91

- **`contracts/control-center/v1/operation-request.schema.json`** — the
  wire shape a Control Center screen sends OMES for a safe lifecycle
  operation (`status, preflight, start, stop, restart, update, backup,
  rollback`), carrying the actor's already-decided `permission`
  (`granted`, `policy_id`, optional `reason`/`requires_approval`).
- **`contracts/control-center/v1/deployment-view.schema.json`** — the read
  shape for a desired-vs-observed deployment view, with `last_reconciled_at`
  and a required (possibly `null`) `error_evidence` field.
- The existing **`deployment.request`**, **`preflight.request/response`**,
  **`server-registration.request/response`**, **`job-status.response`**,
  **`health-readiness.response`**, **`backup-status.response`**, and
  **`rollback.request`** contracts (issue #89), unchanged by this issue.
- The existing **`omes job` runner** (`lib/omes/py/jobs/`, issue #90):
  idempotent submission, an allowlisted operation → command mapping with no
  arbitrary shell, approval gating for destructive operations, an
  append-only hash-chained audit log, and read-back verification before a
  job is reported as succeeded.
- Fixtures under `contracts/control-center/v1/fixtures/operation-request/`
  and `contracts/control-center/v1/fixtures/deployment-view/` demonstrating
  both a tenant-isolation-shaped rejection (a second tenant identifier
  smuggled into `target`, a denied-permission request that still tries to
  carry a free-form `command` field) and a raw-secret rejection (a
  bearer-token-shaped string embedded in an otherwise schema-legal string
  field), alongside the valid shapes.
- This document, cross-referenced from
  [docs/control-center-contracts.md](control-center-contracts.md) §2.5.

## 2. What awcms-one must provide (not this repository)

Issue #91's acceptance criteria that are **entirely out of scope** for
this repository and must be satisfied in awcms-one:

- "Provide tenant-scoped screens for servers, logical agents, deployments,
  health/readiness, jobs, backups, and audit activity." — a web GUI;
  OMES has no UI.
- "Support server registration and preflight request without accepting raw
  SSH/API/provider secrets in the browser or database." — the browser
  form, its client/server validation, and the database that must never
  store a raw secret are all awcms-one components. OMES's contribution is
  only that `server-registration.request.schema.json` and every other
  contract reject a raw secret value if one is ever produced
  (`lib/omes/py/jobs/schema.py`'s `scan_for_raw_secrets()`).
- "Use AWCMS authentication, RBAC/ABAC, RLS, audit, workflow approval, and
  reporting primitives rather than parallel implementations." — OMES does
  not implement authentication, row-level security, or a workflow-approval
  engine. `operation-request.schema.json`'s `permission` field is a
  self-describing record of a decision AWCMS already made; it is not an
  authorization system.
- "Customer users cannot access arbitrary shell, host filesystem, Hermes
  secrets, or cross-tenant records." — enforced by AWCMS's RBAC/ABAC/RLS
  layer at the browser/API boundary. OMES's contribution is the schema-level
  backstop (no `command`/shell field exists in any contract; a request
  cannot carry two tenant identifiers) plus the job runner's own
  tenant/target check (`docs/control-center-contracts.md` §1), which is a
  second, independent check — not a substitute for AWCMS's own enforcement.
- "Provide responsive operator and customer views with clear
  degraded/offline states." — a UI concern. OMES's contribution is that
  `deployment-view.schema.json` gives a UI the data it needs to render a
  degraded/offline state truthfully (`observed_state.status` includes
  `degraded`/`unknown`; `error_evidence` is required, not optional).
- "Add browser/API tests for tenant isolation, authorization, job
  submission, duplicate submission, and failed deployment display." —
  browser and API tests against a running Control Center do not exist in
  this repository. This repository's test contribution is
  `tests/py/contracts/` validating the contracts themselves (schema
  conformance, tenant-isolation-shaped and raw-secret fixtures) and
  `tests/py/jobs/` validating idempotent submission/duplicate rejection at
  the job-runner layer those screens would call into.

## 3. How awcms-one is expected to consume this repository

1. A Control Center screen builds an `operation-request` (this repository's
   schema) after its own RBAC/ABAC/RLS layer has decided `permission`.
2. awcms-one translates an approved `operation-request` into a
   `deployment.request` (issue #89's broader, job-runner-facing contract)
   and calls `omes job submit` (issue #90) over whatever transport
   [docs/control-center-contracts.md](control-center-contracts.md) §5
   specifies (local socket, mTLS, or a pull worker — never a public
   unauthenticated listener).
3. The job runner independently re-derives its own allowlist/approval
   decision; it never trusts `operation-request.permission.granted` alone.
4. A Control Center screen renders `deployment-view` records built by
   reconciling `job-status.response` and `health-readiness.response`
   against the last-known desired state. This repository does not
   implement that reconciliation loop; it fixes the shape it must produce.

## 4. Relationship to other documents

This document does not redefine
[docs/control-center-and-integrations.md](control-center-and-integrations.md)
(§1–§4, authoritative design) or
[docs/control-center-contracts.md](control-center-contracts.md) (§1
ownership matrix, §2 contract catalog). It exists because issue #91 is
specifically the web-GUI issue, and the "what belongs in awcms-one" split
needed a single, issue-scoped answer rather than being inferred from the
general documents above.
