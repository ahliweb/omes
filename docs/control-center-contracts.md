# OMES Control Center contracts (issue #89)

> Status: design/contract deliverable. This document defines the versioned
> boundary between an AWCMS/awcms-one-based Control Center, OMES, and
> Hermes. It does not claim the Control Center, its API server, or its
> database exist in this repository — see
> [docs/control-center-and-integrations.md](control-center-and-integrations.md)
> section 11 for delivery status. The one piece of this document that
> **is** implemented in this repository is the OMES-side job runner
> (`omes job ...`, issue #90, `lib/omes/py/jobs/`), which validates
> requests against the `deployment.request` schema before executing
> anything.

This document is concrete, not a re-statement of
[docs/control-center-and-integrations.md](control-center-and-integrations.md)
(§1–§4 remain the authoritative design source; this document makes it
concrete per entity, with actual JSON Schemas and fixtures under
[`contracts/control-center/v1/`](../contracts/control-center/v1/)).

## 1. Ownership matrix

| Entity / state | AWCMS (Control Center) | OMES | Hermes |
|---|---|---|---|
| Tenant, user, permission (RBAC/ABAC) | **owns** | reads tenant/actor identity on incoming requests only | no knowledge |
| Service catalog, subscription, invoice, entitlement | **owns** | consumes `entitlement.changed` events to decide whether a deployment stays enabled; never stores billing state | no knowledge |
| Server/deployment inventory (which hosts exist, which tenant owns them) | stores a copy for UI/billing, reconciled from OMES | **owns the live truth** (`omes status`, state file) | no knowledge |
| Host preflight/compatibility evidence | displays it | **owns** (`omes check`) | no knowledge |
| Install/configure/start/stop/restart/update execution | requests it via a job | **owns** (executes; the only party that runs host commands) | Hermes-specific config only, applied by OMES |
| Job queue, state machine, audit log | reads job status via API | **owns** (`lib/omes/py/jobs/`, issue #90) | no knowledge |
| Backup/restore/rollback | requests and displays status | **owns** (`omes backup`/`restore`, `lib/omes/backup.sh`) | no knowledge |
| Health/readiness evidence | displays it | **owns** (reuses `lib/omes/py/health/model.py`, issue #79) | Hermes reports its own runtime signals which OMES's health layer reads, never the reverse |
| Agent reasoning, sessions, memory, skills, channels, model/provider routing | no knowledge | no knowledge (never reimplements this) | **owns** |
| Approvals for destructive operations | records the approval decision and actor | enforces the approval gate before executing (issue #90) | no knowledge |
| Secrets (tokens, passwords, credentials) | never stores raw values; stores `secret_ref` pointers only | resolves `secret_ref` locally (env/file/vault/os-keyring); never returns raw values | resolves its own secrets from its own `.env`, unrelated to Control Center secrets |
| AI privacy posture / egress policy-decision evidence (issue #217) | displays the projection; owns which tenant/actor may read it and who may approve an approval_required decision (RBAC/ABAC) | **owns** the evidence and the pure evaluation/projection logic (`lib/omes/py/privacy/`); never selects or calls a model provider | **owns** agent reasoning and model/provider routing; OMES only reads bounded facts through supported interfaces, never Hermes's private databases |

This matrix is the concrete instantiation of
[docs/control-center-and-integrations.md](control-center-and-integrations.md)
§1's authority table and [ADR-0011](adr/0011-control-center-and-provider-boundaries.md)
decision 1, per entity rather than per concern.

## 2. Versioned API contracts (v1)

All schemas live under
[`contracts/control-center/v1/`](../contracts/control-center/v1/) as JSON
Schema (draft 2020-12 subset with fail-closed keyword enforcement — see
[`contracts/README.md`](../contracts/README.md) "Validator subset and fail-closed keyword enforcement").
Each has at least one valid and one invalid fixture under
`contracts/control-center/v1/fixtures/<schema-name>/`.

| Contract | Schema file | Direction |
|---|---|---|
| Server registration request | `server-registration.request.schema.json` | Control Center → OMES |
| Server registration response | `server-registration.response.schema.json` | OMES → Control Center |
| Preflight request | `preflight.request.schema.json` | Control Center → OMES |
| Preflight response | `preflight.response.schema.json` | OMES → Control Center |
| Deployment request | `deployment.request.schema.json` | Control Center → OMES |
| Job status response | `job-status.response.schema.json` | OMES → Control Center |
| Health/readiness response | `health-readiness.response.schema.json` | OMES → Control Center |
| Backup status response | `backup-status.response.schema.json` | OMES → Control Center |
| Rollback request | `rollback.request.schema.json` | Control Center → OMES |
| Operation request (issue #91) | `operation-request.schema.json` | Control Center → OMES |
| Deployment view (issue #91) | `deployment-view.schema.json` | OMES → Control Center |
| Worker enrollment request (issue #192, ADR-0027) | `worker-enrollment.request.schema.json` | OMES Worker → Control Center |
| Worker enrollment response (issue #192, ADR-0027) | `worker-enrollment.response.schema.json` | Control Center → OMES Worker |
| Worker poll request (issue #192, ADR-0027) | `worker-poll.request.schema.json` | OMES Worker → Control Center |
| Worker poll response (issue #192, ADR-0027) | `worker-poll.response.schema.json` | Control Center → OMES Worker |
| Worker heartbeat request (issue #192, ADR-0027) | `worker-heartbeat.request.schema.json` | OMES Worker → Control Center |
| Worker heartbeat response (issue #192, ADR-0027) | `worker-heartbeat.response.schema.json` | Control Center → OMES Worker |
| Worker result request (issue #192, ADR-0027) | `worker-result.request.schema.json` | OMES Worker → Control Center |
| Worker result response (issue #192, ADR-0027) | `worker-result.response.schema.json` | Control Center → OMES Worker |
| Hermes orchestration event (issue #183, ADR-0028) | `hermes-orchestration-event.schema.json` | Hermes Observer → OMES |
| Hermes orchestration tree (issue #183, ADR-0028) | `hermes-orchestration-tree.schema.json` | OMES → Control Center |

### 2.1 Mutating-request common fields

Every mutating request (`server-registration.request`, `preflight.request`,
`deployment.request`, `rollback.request`) requires:

- `tenant_id` — the tenant this request is scoped to.
- `correlation_id` — ties a request to its response and to related events.
- `idempotency_key` — replaying the same key must return the original
  result (issue #90 job runner behavior), never re-execute.
- `actor` — `{"type": "user"|"service", "id": "..."}`; every mutation is
  attributable.
- `operation` — an explicit operation name (a `const` for single-purpose
  requests, an `enum` for `deployment.request`). There is no free-form
  command field anywhere in this contract set.

### 2.2 `deployment.request` — the allowlisted operation contract

`deployment.request.schema.json` is the single shape a Control Center may
use to ask OMES to do anything to a host or deployment. `operation` is a
closed enum:

```
preflight, install, configure, start, stop, restart,
update, status, backup, restore, rollback
```

`additionalProperties: false` at the top level means a `command`, `args`,
`shell`, or any other unrecognized field is rejected by the schema itself
— see `contracts/control-center/v1/fixtures/deployment.request/invalid-free-form-command.json`,
which the test suite (`tests/py/contracts/`) asserts fails specifically
with "additional properties not allowed", not some unrelated reason. This
is the schema-level backstop for AGENTS.md §3 ("Never add arbitrary shell
execution to a web/API path, webhook, job payload, or agent tool") and
issue #90's "declared operations only" acceptance criterion.

Destructive operations (`restore`, `rollback`) carry optional `backup_id`
and `rollback_ref` fields; the job runner (issue #90, §4 below) — not this
schema — enforces that they are present and valid *when the operation
requires them*, because "required only if X" is outside this validator's
subset (no conditional `if`/`then`).

### 2.3 `job-status.response` states

```
queued, approved, running, succeeded, failed, cancelled, expired, rolled_back
```

See [`docs/jobs.md`](jobs.md) (issue #90) for the implemented OMES-side
state machine and transition table; this contract only fixes the wire shape
of a status read. The HTTP/API transport, tenant database, web RBAC/ABAC,
and Control Center UI remain AWCMS/awcms-one responsibilities.

### 2.4 `health-readiness.response`

Reuses the layers/signals shape of `lib/omes/py/health/model.py` (issue
#79, branch `origin/feat/79-health-model`): `layers` is a map from layer
name (`host`, `runtime`, `gateway`, `provider`, `channel`, ...) to
`{"status": "pass"|"fail"|"not_applicable", "signals": {...}, "proves":
"...", "remediation": "..."|null, "detail": "..."}`, plus top-level
`ready`/`connected` booleans computed by that module's `aggregate()`. This
contract does not redefine issue #79's aggregation rules; it only fixes
the JSON shape those rules already produce.

### 2.5 `operation-request` and `deployment-view` (issue #91)

Issue #91 asks awcms-one to build the first Control Center web GUI over this
job/contract boundary; see
[docs/control-center-foundation.md](control-center-foundation.md) for the
full split of what is implemented in awcms-one versus this repository. The
two contracts this repository adds for it:

- **`operation-request.schema.json`** — the shape a tenant-scoped screen
  sends when a user asks for a safe lifecycle operation. `operation` is a
  narrower, closed enum than `deployment.request`'s
  (`status, preflight, start, stop, restart, update, backup, rollback` —
  no `install`/`configure`/`restore`, which stay job-runner-only, direct
  operations) because these are the eight operations issue #91's
  acceptance criteria name explicitly. It additionally requires a
  `permission` object (`granted`, `policy_id`, optional `reason` and
  `requires_approval`) carrying the RBAC/ABAC decision AWCMS already made
  for this actor/operation/target. **OMES does not trust `granted: true`
  on its own** — the job runner (issue #90) re-derives its own
  allowlist/approval decision from the translated `deployment.request`;
  `permission` exists so the end-to-end request is self-describing and
  auditable, and so a denied request that still tries to smuggle a
  `command` field is rejected by the schema itself
  (`fixtures/operation-request/invalid-permission-denied-with-command-field.json`).
  `additionalProperties: false` on `target` also rejects an attempt to
  carry a second, conflicting tenant scope inside the target object
  (`fixtures/operation-request/invalid-cross-tenant-scope-in-target.json`)
  — real cross-tenant authorization is AWCMS's row-level-security
  responsibility (§1), this is only the schema-level backstop that a
  smuggled second tenant identifier cannot ride along silently.
- **`deployment-view.schema.json`** — the read shape behind issue #91's
  "show desired state separately from observed state, including last
  reconciliation and error evidence" acceptance criterion. `desired_state`
  and `observed_state` are separate required objects (never merged into
  one "current state" blob a UI could misrender as consistent when it is
  not); `last_reconciled_at` is required so a screen can show staleness;
  `error_evidence` is a required field (not merely optional) that must be
  either `null` or `{"message", "occurred_at", "code"?}` — a producer
  cannot omit it while a reconciliation is failing.
  `fixtures/deployment-view/valid-02-drift-with-error.json` is a worked
  example of desired/observed drift with non-null `error_evidence`.

Neither contract introduces a new secret field; both are covered by the
same unconditional `scan_for_raw_secrets()` pass as every other contract
in this set (see `fixtures/deployment-view/invalid-raw-secret-in-error-evidence.json`,
which is rejected for a bearer-token-shaped string inside the otherwise
schema-legal `error_evidence.message` field — the pattern-based secret-value
check, not the key-name check, catches it).

### 2.5a Pull-worker job correlation: no `job_id` (issue #221, ADR-0027)

`worker-poll.response.job` (when `status` is `job_available`) now carries
the same closed shape as `operation-request.schema.json` — `tenant_id`,
`correlation_id`, `idempotency_key`, `actor`, `operation`, `target`,
`permission`, and the optional `backup_id`/`rollback_ref`/`parameters` —
duplicated inline (this repository's validator has no `$ref` support; see
`contracts/README.md`) rather than left as the untyped `{"type": "object"}`
it previously was. `lib/omes/py/jobs/worker.py`'s `poll_and_dispatch_once`
already validated the `job` payload against `operation-request.schema.json`
at runtime before this change; the schema now says so explicitly instead
of leaving the poll response's most execution-relevant field unspecified.

`worker-result.request.schema.json` no longer has a `job_id` field at all
(previously required, both in `required` and `properties`). Nothing in
this contract set ever gave the worker a server-known job id to echo
back — `worker-poll.response.job` had no shape, and
`operation-request.schema.json` has no `job_id` property under its own
`additionalProperties: false` — so the field was in practice a
client-invented opaque string
(`f"job_auto_{secrets.token_hex(8)}"` in `lib/omes/py/jobs/worker.py`)
that the Control Center could never validate. Correlation between a
`worker-poll.response.job` and the matching `worker-result.request` binds
on `idempotency_key` (plus `correlation_id`, `tenant_id`, `server_id`, and
the leasing `worker_id`) only; `idempotency_key` is minted by the Control
Center at job-promotion time and is the sole correlation handle.

This was a breaking change to `v1` (removes a required property, adds
`additionalProperties: false` to a previously-untyped object), amended in
place rather than released as `v2` — see
`contracts/README.md` "Documented exception: issue #221" for why that call
was proportionate here (exactly one known consumer, pinned by commit hash
with a CI drift gate) and what a consumer must do to re-pin.

### 2.6 Catalog, subscription, and entitlement contracts (issue #92)

Issue #92 asks OMES to model service plans and enforce explicit
entitlements. The catalog itself (product/plan/pricing decisions) lives in
AWCMS; what this repository fixes is the wire shape and the pure
evaluation logic OMES needs to enforce those entitlements at request/policy
boundaries "never rely only on UI hiding" (issue #92 acceptance criteria).

| Contract | Schema file |
|---|---|
| Product | `catalog-product.schema.json` |
| Plan | `catalog-plan.schema.json` |
| Add-on | `catalog-addon.schema.json` |
| Version (immutable dated revision) | `catalog-version.schema.json` |
| Price | `catalog-price.schema.json` |
| Billing cycle | `catalog-billing-cycle.schema.json` |
| Resource policy (suspension behavior) | `catalog-resource-policy.schema.json` |
| Backend eligibility | `catalog-backend-eligibility.schema.json` |
| Subscription | `subscription.schema.json` |
| Entitlement | `entitlement.schema.json` |

`catalog-plan.limits` and `entitlement.limits` share the same shape:
`servers, logical_agents, specialist_agents, isolated_workers, storage_gb,
backup_retention_days`, plus an optional `backends` array. `catalog-addon`
only ever ADDS to a plan's limits via `limits_delta` (never negative -
enforced by `minimum: 0` on every field); revoking capacity is a plan
change, not an add-on.

`subscription.state` / `entitlement.state` are the same closed enum:
`trialing, active, past_due, grace_period, suspended, cancelled, expired`.
The allowed transitions between them are encoded as data, not code, in
[`contracts/control-center/v1/subscription.states.json`](../contracts/control-center/v1/subscription.states.json)
and interpreted by the generic, reusable `lib/omes/py/jobs/states.py`
(`StateMachine.is_valid_transition()` / `assert_transition()`) - the same
module issue #93's invoice state machine reuses rather than
re-implementing transition checking.

`lib/omes/py/jobs/entitlement.py`'s `evaluate(entitlement, action,
resource_policy)` is the single pure function anything (OMES or, per
`docs/control-center-foundation.md`, awcms-one) must call before allowing
`provision_new`, `upgrade`, `start_optional_worker`, or
`keep_existing_running`. It never trusts a client-supplied "granted" flag
(issue #92's "no client-supplied entitlement" security requirement); it
recomputes allow/deny from `entitlement.limits` and, for a
non-active/trialing subscription state, from a `catalog-resource-policy`.
When `entitlement.limits.backends` is present, provisioning, upgrades, and
optional-worker starts must include a backend listed there; an unlisted or
missing backend is denied. Entitlements created before backend eligibility was
introduced remain compatible when that optional field is absent.
The suspension-policy default is `existing_healthy_deployments_action:
keep_running` — a suspended/cancelled/expired subscription never stops a
currently-healthy deployment unless a resource policy explicitly says
`stop` or `degrade`, matching AGENTS.md §3's "do not silently stop a
healthy existing deployment". `new_provisioning_action`,
`upgrade_action`, and `optional_workers_action` are controlled
independently, so a policy can (for example) block new provisioning while
still allowing an already-approved upgrade.

`lib/omes/py/jobs/entitlement.py`'s `apply_subscription_event()` applies a
subscription-lifecycle event to build the next entitlement/subscription
record, and is idempotent by `event_id`: replaying an already-applied
event id returns the existing record unchanged rather than re-applying a
(possibly now-invalid) transition — see
`tests/py/jobs/test_entitlement.py::TestSubscriptionEventReplay`.

Cross-tenant enforcement: `evaluate()` rejects any action whose
`tenant_id` does not match the entitlement's own `tenant_id`,
unconditionally and before any limit/policy logic runs
(`tests/py/jobs/test_entitlement.py::TestCrossTenant`) — this is a second,
independent check behind AWCMS's own RLS enforcement (§1), not a
replacement for it.

### 2.7 Manual invoicing and billing ledger contracts (issue #93)

Issue #93 adds a minimal, auditable manual-billing foundation before any
live payment gateway integration (issue #94). It never sends email or
renders a downloadable document itself — both are AWCMS's provider-neutral
email boundary and document rendering, respectively.

| Contract | Schema file |
|---|---|
| Billing profile | `billing-profile.schema.json` |
| Invoice | `invoice.schema.json` |
| Invoice line | `invoice-line.schema.json` |
| Credit | `credit.schema.json` |
| Adjustment | `adjustment.schema.json` |
| Payment record | `payment-record.schema.json` |
| Refund record | `refund-record.schema.json` |
| Tax metadata | `tax-metadata.schema.json` |
| Currency | `currency.schema.json` |

**Money precision rule**: every amount anywhere in this contract set is
`amount_minor`/`unit_amount_minor` — an **integer** count of the
currency's smallest unit (cents for USD/EUR, no minor unit for JPY, ...).
No schema in this set has a `number` (float) money field; `currency` is
an ISO 4217 alphabetic code (`^[A-Z]{3}$`). Mixing currencies within one
invoice's lines/credits/adjustments/payments is always an error —
`lib/omes/py/jobs/ledger.py` raises `CurrencyMismatchError` rather than
converting.

**Immutable price snapshots**: `invoice.lines[].price_snapshot` (same
shape as `invoice-line.price_snapshot`) copies `price_id`,
`catalog_version_id`, `currency`, and `unit_amount_minor` at generation
time. A later catalog price or plan change never rewrites an
already-issued invoice's lines — this is what "generate manual invoices
from subscription/catalog snapshots without changing historical prices
when a plan changes" (issue #93 acceptance criteria) means concretely.

**`invoice.state`** is `draft, issued, partially_paid, paid, overdue,
void, refunded, disputed`, with allowed transitions encoded as data in
[`contracts/control-center/v1/invoice.states.json`](../contracts/control-center/v1/invoice.states.json)
and checked by the SAME `lib/omes/py/jobs/states.py` module issue #92's
subscription state machine uses — no second transition-checking
implementation. `void` and `refunded` are terminal.

**`lib/omes/py/jobs/ledger.py`** is the stdlib reconciliation helper:

- `compute_totals()` — recomputes `subtotal_minor`/`tax_minor`/
  `credit_minor`/`adjustment_minor`/`total_minor`/`paid_minor` from
  `lines`/`credits`/`adjustments`/`payments` in pure integer arithmetic.
  Tax is computed per line as `floor(line_subtotal * rate_bps / 10000)`
  and summed (deterministic; no accumulated rounding drift across lines).
  `total_minor` is clamped at 0 (an over-credited/over-adjusted invoice
  never reports a negative total).
- `record_payment()` / `record_refund()` — append-only; both reject a
  currency mismatch against the invoice/payment, and both reject a
  **duplicate confirmation** by `idempotency_key` (issue #93: "reject
  duplicate confirmations") rather than double-counting. `record_refund()`
  additionally rejects refunding more than a payment's own amount.
- `state_after_payment()` — recommends `paid`/`partially_paid`/unchanged
  from a recomputed `paid_minor`; it does NOT itself apply the
  transition — the caller must still validate the recommendation against
  `states.py` before applying it, so a `void`/`refunded`/`disputed`
  invoice is never silently overwritten by a stale payment recomputation
  (`tests/py/jobs/test_ledger.py::test_caller_must_still_validate_the_recommended_transition`).

**Relationship to entitlement (issue #92) and payment gateways (issue
#94)**: this contract set does not mutate OMES deployment state or
entitlement records directly. AWCMS is expected to read an invoice's
`state` (e.g. `paid`) and independently decide whether to emit a
`subscription`-lifecycle event (issue #92's `apply_subscription_event()`)
— the two are linked by cross-reference (`invoice.subscription_id`), not
by one writing the other. Issue #94 will add the live payment-gateway
webhook contract that would eventually produce a `payment-record`/
`refund-record` automatically instead of manually; this issue's records
carry a `manual confirmation` shape (`actor`, `evidence_ref`) precisely
because no gateway integration exists yet.

### 2.8 Payment gateway, webhook, and proration contracts (issue #94)

Issue #94 extends the manual billing foundation (issue #93) with
provider-backed automation contracts. No live provider integration, no
webhook HTTP endpoint, and no automated dunning/suspension pipeline exists
in this repository — this section fixes the shapes and the pure
verification/calculation logic such a pipeline must use.

| Contract | Schema file |
|---|---|
| Payment gateway adapter | `payment-gateway-adapter.schema.json` |
| Webhook envelope | `webhook-envelope.schema.json` |
| Proration | `proration.schema.json` |
| Suspension automation policy | `suspension-automation-policy.schema.json` |
| Outbox event | `outbox-event.schema.json` |

**Provider neutrality**: `payment-gateway-adapter.provider_id` is an
opaque, operator-assigned identifier, never a specific commercial
provider name; `capabilities` is a closed enum
(`charge, refund, recurring, webhook, dispute_handling, proration`) an
adapter declares rather than the contract assuming every provider
supports everything.

**Webhook verification order (issue #94: "verify webhook signatures,
timestamps, event IDs, and replay/idempotency before applying state
changes")**: `lib/omes/py/jobs/payments.py`'s `verify_webhook()` runs, in
this fixed order, for every inbound event: (1) `assert_valid_signature()`
— HMAC-SHA256, constant-time comparison (`hmac.compare_digest`), so an
unverifiable event is rejected before any of its other fields are even
trusted enough to read; (2) `check_freshness()` — rejects a timestamp
outside `replay_window_seconds` of receipt, in either direction; (3)
`check_not_replayed()` — rejects a previously-seen `event_id`. Fixture
tests never use a literal secret string; `tests/py/jobs/test_payments.py`
generates a throwaway HMAC key with `secrets.token_bytes()` at test run
time.

**Event types**: `payment.succeeded, payment.failed, invoice.recurring,
dunning.retry, refund.completed, dispute.opened, cancellation.requested`
— the same closed enum on both `webhook-envelope.event_type` and
`outbox-event.event_type`.

**Deterministic rounding rule**: proration uses **HALF-UP** rounding in
integer minor units (`proration.rounding_rule` is the fixed const
`half_up_minor_unit`), not banker's rounding (round-half-to-even).
Banker's rounding exists to cancel bias across a very large number of
automated transactions; a subscription proration is a single, often
support-reviewed, human-facing number, where HALF-UP is the rounding
behavior most people already expect (the same intuition as cash
rounding). `lib/omes/py/jobs/payments.py`'s `round_half_up()` is pure
integer division with a manual remainder check — it never uses
`float`/`Decimal`.

**Outbox pattern (issue #94: "no network payment call occurs inside a
database transaction")**: a webhook handler's own transaction only
verifies the envelope and writes one `outbox-event` row; every downstream
effect (an entitlement update, a deployment action, a notification) is a
separate, retryable dispatch of that row. This repository does not
implement a dispatcher; `outbox-event.schema.json` fixes the row shape
(`status: pending|dispatched|failed`, `attempts`, `next_attempt_at`) a
dispatcher must use.

**Suspension automation gating (issue #94: "Require approval or explicit
documented automation policy before destructive deployment actions
caused by non-payment")**: `suspension-automation-policy.schema.json`
references a `catalog-resource-policy` (issue #92, "what happens") and
adds `requires_approval_for_destructive_action` ("whether it may happen
unattended"). `lib/omes/py/jobs/payments.py`'s `decide_automation()` is
the pure function that reads this: a non-destructive event type
(`payment.succeeded`, `invoice.recurring`) never requires approval; a
destructive event type (`payment.failed`, `dunning.retry`,
`dispute.opened`, `cancellation.requested`) requires approval **by
default** — an automation policy that skips approval is an explicit,
reviewable opt-in (`requires_approval_for_destructive_action: false`),
never the default when the field is absent.

### 2.9 Reporting projection contracts (issue #95)

Issue #95 provides operational and business reports without turning a
reporting table into a second source of truth. No projection dispatcher,
report API, or export UI exists in this repository; this section fixes
the shapes and the pure, fixture-based builder those must use.

| Contract | Schema file |
|---|---|
| Projection state (cursor/freshness/rebuild/reconciliation) | `projection-state.schema.json` |
| Billing report (subscriptions, invoices, MRR, overdue, credits/refunds, entitlements) | `report-billing.schema.json` |
| Operations report (server/agent/deployment counts, health duration, failed jobs, provisioning duration, backup compliance, resource usage) | `report-operations.schema.json` |

**Never a second source of truth**: every report embeds a `projection`
block (the same shape as standalone `projection-state.schema.json`) with
`cursor` (how far it has consumed its source event stream),
`freshness` (`as_of`, `lag_seconds`, `stale`), `rebuild` (whether it is
currently being recomputed from scratch), and `reconciliation`
(`last_reconciled_at`, `discrepancies_found` — always against the
authoritative source records, never against another projection).
`lib/omes/py/jobs/projections.py`'s `reconcile_measurement()` enforces
this: it raises unless the caller supplies a value re-derived directly
from source records.

**Estimate vs. provider-confirmed**: every `measurements[]` entry on
both report schemas carries a required `label` of `estimate` or
`provider_confirmed` — issue #95's "explicitly label estimates versus
provider-confirmed amounts" is a schema-level requirement, not a
documentation convention (`fixtures/report-billing/invalid-unlabeled-measurement.json`
is rejected for exactly this). `build_mrr_report()`/`build_overdue_report()`/
`build_deployment_counts_report()`/`build_failed_jobs_report()` in
`lib/omes/py/jobs/projections.py` always emit `provider_confirmed`
because they read directly from the authoritative subscription/invoice/
deployment/job shapes this repository already defines (issues #90, #92,
#93); a resource-usage measurement not metered with provider-grade
precision would be labeled `estimate` instead (see
`fixtures/report-operations/valid-01.json`'s `resource_cpu_percent`).

**Tenant/legal-entity scope**: `scope.tenant_id` is `null` only for a
platform-level aggregate; `additionalProperties: false` on `scope`
rejects a second, conflicting scope identifier riding alongside it
(`fixtures/report-operations/invalid-cross-tenant-scope-fields.json`).
Which roles may request a `tenant_id: null` aggregate is an AWCMS-side
RBAC/ABAC decision (§1), not something this schema enforces.

**Export redaction**: `lib/omes/py/jobs/projections.py`'s
`redact_for_export()` replaces `scope.tenant_id`/`scope.legal_entity_id`
with `"[REDACTED]"` (never removes the field, so the exported shape
still matches the schema) when the exporting role is not authorized to
see which tenant a report covers; it never touches `measurements` — a
redacted report still functions as a report, it just cannot be traced to
one tenant.

**Retention**: this repository does not implement retention enforcement
for the underlying job/audit/billing event streams a projection is built
from. A projection whose source events have been retention-purged before
`last_reconciled_at` cannot be exactly rebuilt from scratch — only
incrementally maintained from the point retention began. Any consumer of
`rebuild.status=rebuilt` must therefore treat a rebuild as authoritative
only back to the oldest retained source event, a limitation this
document records rather than resolves (no retention policy or purge job
exists in this repository yet).

### 2.10 AI privacy posture and policy-decision projection (issue #217)

Issue #217 extends this contract set so a Control Center screen can
display and govern AI privacy posture and AI egress policy decisions
using only SANITIZED metadata from issue #214
(`lib/omes/py/privacy/egress_policy.py`) and issue #216
(`lib/omes/py/privacy/posture_evidence.py`) - never raw prompts,
transcripts, restricted data, or provider credentials. No AWCMS-side
screen, API, or database consumes these contracts yet: **not implemented
yet (tracked in #217)** on the AWCMS side; this repository only fixes
the wire shapes and the pure OMES-side evaluation logic.

**Authority split** (this is this issue's documentation acceptance
criterion, stated explicitly, not merely implied by the ownership
matrix in §1):

- **Hermes** remains authoritative for agent runtime, reasoning, and
  model/provider routing. Nothing in this section, or in
  `lib/omes/py/privacy/`, selects, calls, or routes to a model provider.
- **OMES** owns host/deployment evidence collection and the pure
  evaluation/projection logic: `egress_policy.py` (issue #214, one
  decision), `posture_evidence.py` (issue #216, ongoing posture), and
  `posture_projection.py` (issue #217, the Control Center-scoped view
  and the owner-approval authorization gate). OMES does not read or
  modify Hermes's private databases; it only reads bounded facts through
  supported interfaces (`hermes --version`, `hermes config get`,
  `hermes doctor`, OMES's own host/network classification).
- **AWCMS** owns business/tenant policy: which tenant/actor may read a
  given `ai-privacy-posture-view`, which actor holds the owner-approval
  role, and the RBAC/ABAC decision behind every read and every approval
  request. **UI hiding of a field is never a substitute for that
  server-side check** - `lib/omes/py/privacy/posture_projection.py`'s
  `project_posture_view()`/`authorize_approval()` cross-tenant checks are
  a second, independent backstop behind AWCMS's own authorization, the
  same pattern §2.6's `entitlement.evaluate()` uses
  (`tests/py/privacy/test_posture_projection.py::TestCrossTenant`).

| Contract | Schema file |
|---|---|
| Tenant-scoped posture + latest-decision read projection | `ai-privacy-posture-view.schema.json` |
| Owner-approval request (issue #90 job-runner-adjacent, not a job) | `ai-egress-approval.request.schema.json` |
| Owner-approval response | `ai-egress-approval.response.schema.json` |

`ai-privacy-posture-view` surfaces exactly what issue #217's acceptance
criteria name: `classification_mode`/`destination_class` (posture),
`latest_decision.decision`/`latest_decision.reason_codes` (the most
recent egress decision for this target, or `null` if none has been
recorded), `evidence_freshness` (`fresh`/`stale`/`unknown`, computed at
projection time from `last_verified_at` against
`posture_evidence.DEFAULT_MAX_EVIDENCE_AGE_SECONDS`), and `authority`
(which system asserted the fact - never inferred from the reader).
**Stale or unknown evidence is never rendered as healthy**:
`evidence_freshness: unknown` on a missing/unparsable timestamp is
structural (never `fresh`), and `status` falls back to `BLOCKED` whenever
no recognized `AI_PRIVACY_POSTURE_*` reason code survives projection
(`tests/py/privacy/test_posture_projection.py::TestFreshness`).

**Owner-approval path is intentionally narrow.** `ai-egress-approval.request`
can only reference one of the three decisions `egress_policy.py` already
marks `approval_required`
(`AI_EGRESS_APPROVAL_REQUIRED_CONFIDENTIAL_PRIVATE_ENDPOINT`,
`AI_EGRESS_APPROVAL_REQUIRED_CONFIDENTIAL_CLOUD_SANITIZED`,
`AI_EGRESS_APPROVAL_REQUIRED_RESTRICTED_PRIVATE_ENDPOINT`) -
`decision_ref.reason_code` is a closed 3-value enum. **`RESTRICTED` ->
`cloud_sanitized` has no approval path and stays denied, full stop**,
matching `egress_policy.py`'s own unconditional
`AI_EGRESS_DENY_RESTRICTED_CLOUD_SANITIZED` deny; this repository's
fail-closed stdlib schema validator (issue #172) validates fields
independently and has no cross-field keyword, so the actual unconditional
block on that combination is enforced by value, independent of
`reason_code`, in `posture_projection.py`'s `authorize_approval()`
(`tests/py/privacy/test_posture_projection.py::TestRestrictedCloudNeverApprovable`)
- not merely by the schema's enum. Changing this would require a future
ADR and a lawful policy change (issue #217 scope), not a code change
alone.

**No raw prompt/transcript/credential field exists in any of these
schemas - structurally, not merely unpopulated.** Every object schema in
this section sets `additionalProperties: false`, and
`tests/py/privacy/test_posture_projection.py::TestSchemasHaveNoPromptTranscriptCredentialFields`
walks each schema's declared property names and asserts none of them
matches a raw-content-shaped name (`prompt`, `transcript`,
`response_text`, `chain_of_thought`, `raw_provider_response`);
`fixtures/ai-privacy-posture-view/invalid-additional-property-raw-prompt.json`
and `fixtures/ai-egress-approval.request/invalid-credential-field-added.json`
are the schema-level fixtures proving `additionalProperties: false`
itself rejects an attempted `prompt`/`credential` field. `justification`
on the approval request is a bounded (`maxLength: 500`) short operator
note only; the length bound is a structural discouragement against
pasting a transcript, not a content filter - reviewer discipline and
this document remain the actual control on what an operator types there.

Two events (§3) accompany this section:
`ai-privacy-posture.changed` (drift or freshness change) and
`ai-egress-approval.recorded` (an approval decision was recorded - never
carries the request's `justification` text). Both are delivered over the
existing pull-worker/outbox transport (§5, ADR-0027, issue #192); neither
introduces a new privileged inbound listener.

## 3. Versioned events (v1)

Every event uses a common envelope (`contracts/control-center/v1/events/*.schema.json`):

```
event_id, event_version, event_type, occurred_at,
tenant_id, correlation_id, idempotency_key, actor, source, data
```

`event_version` is `"v1"` for every event in this contract set;
`event_type` is a per-event `const` (e.g. `"deployment.requested"`) so a
consumer can dispatch on it without inspecting `data`. `source` names
which system emitted the event (`omes`, `hermes`, or `control-center`).

| Event | `event_type` | Emitted by | Meaning |
|---|---|---|---|
| Deployment requested | `deployment.requested` | OMES (job accepted) | A job entered `queued`. |
| Deployment applied | `deployment.applied` | OMES (job runner) | The operation executed and its immediate read-back succeeded. |
| Deployment failed | `deployment.failed` | OMES (job runner) | The job reached `failed` (non-retryable, or retries exhausted). |
| Deployment healthy | `deployment.healthy` | OMES (health layer) | A post-mutation reconciliation pass observed `ready=true`. |
| Backup completed | `backup.completed` | OMES (backup) | A backup finished with a recorded manifest hash. |
| Entitlement changed | `entitlement.changed` | Control Center (AWCMS) | An entitlement's state changed; OMES only *consumes* this — see the ownership matrix. |
| AI privacy posture changed | `ai-privacy-posture.changed` | OMES (health/posture layer) | An `ai-privacy-posture-view` projection's `status` or `evidence_freshness` changed (issue #217) — see §2.10. |
| AI egress approval recorded | `ai-egress-approval.recorded` | Control Center (AWCMS) | An owner-approval decision on an approval_required AI egress decision was recorded (issue #217) — see §2.10. |

## 4. Authentication and secret-reference rules

- **No raw secrets in any contract instance, ever.** Every schema in this
  contract set treats fields named like `*token*`, `*password*`,
  `*secret*`, `*credential*`, `*api_key*`, `*passphrase*`, `*cookie*`,
  `*authorization*` (case-insensitive) as forbidden to carry a scalar
  value. The only allowed shape is a `secret_ref` object:
  `{"store": "env"|"file"|"vault"|"os-keyring", "key": "..."}`,
  `null`, or a list of secret *names* (short identifiers such as
  `["provider-primary"]`, the shape the agent-deployment manifest's
  `spec.secrets` uses — see [docs/agent-deployment.md](agent-deployment.md));
  a list containing anything that is not a plain identifier is rejected.
  As defense in depth against a secret placed under a
  misleadingly generic field name, any string value anywhere in the
  instance that matches a well-known secret-value shape
  (Stripe/GitHub/AWS/Slack/bearer-token prefixes) is rejected too, even
  under a field name that does not match the pattern above. Both checks
  are enforced by `lib/omes/py/jobs/schema.py`'s `scan_for_raw_secrets()`,
  run unconditionally on every `validate()` call — a schema author
  cannot accidentally weaken this by omitting a check.
- Actor identity (`actor.id`) is an opaque identifier, never a credential.
- Enrollment/registration flows reference a secret via
  `enrollment_secret_ref`, never an inline token
  (`server-registration.request.schema.json`).

## 5. Transport recommendation

Per [docs/control-center-and-integrations.md](control-center-and-integrations.md)
§2 and [ADR-0011](adr/0011-control-center-and-provider-boundaries.md)
decision 2: the OMES side of this boundary must be reached over a local
UNIX domain socket, an authenticated mTLS channel, or a pull-based worker
that polls the Control Center rather than accepting inbound connections.
**A public, unauthenticated, privileged OMES listener is never the
default.** This document does not implement a transport (no listener
exists in this repository); it only fixes the payload shapes any future
transport must carry.

## 6. Capability allowlist

The only operations any contract in this set can request are the 11
values in `deployment.request`'s `operation` enum (§2.2). There is no
schema, event, or field anywhere in `contracts/control-center/v1/` that
carries a shell command, script body, or arbitrary argv. Any future
operation must be added to the enum explicitly (a compatibility-visible,
reviewable change — see §7), never accepted as free text.

## 7. Compatibility rules

See [`contracts/README.md`](../contracts/README.md) "Compatibility rules".
In summary: additive-only within `v1` (new optional fields, new enum
values that old consumers can ignore); anything that could break an
existing valid message requires a new `v2` directory.

## 8. Relationship to other issues

This document defines the contract; it does not redefine or duplicate:

- **#79** (health/readiness model) — `health-readiness.response` reuses
  its shape; the aggregation logic stays owned by `lib/omes/py/health/`.
- **#82** (Hermes backup classes) — `backup-status.response` reports
  outcomes; backup mechanics stay owned by `lib/omes/backup.sh`.
- **#83** (compatibility evidence) — `preflight.response` reports outcomes;
  compatibility logic stays owned by the preflight/check implementation.
- **#84** (provenance/audit) — the job audit log (issue #90,
  `lib/omes/py/jobs/`) follows the same append-only, hash-chained pattern
  as `lib/omes/py/content/reports.py`'s audit log (issue #68/#84 lineage);
  it does not redefine #84's provenance contract, it applies the same
  pattern to a new subsystem.
- **#87** (agent deployment MVP) — the `target.deployment_id` field
  identifies an `AgentDeployment` (docs/agent-orchestration-roadmap.md);
  this document does not redefine that manifest's lifecycle states.
- **#213/#214/#216** (AI data-privacy boundary, egress policy, posture
  evidence) — §2.10's contracts project, and never redefine,
  `lib/omes/py/privacy/egress_policy.py`'s `AI_EGRESS_*` reason codes and
  `lib/omes/py/privacy/posture_evidence.py`'s `AI_PRIVACY_POSTURE_*`
  reason codes; see
  [docs/ai-data-privacy-and-model-security.md](ai-data-privacy-and-model-security.md)
  and [ADR-0029](adr/0029-ai-data-boundary-and-private-inference.md) for
  the underlying policy.

See [docs/control-center-threat-model.md](control-center-threat-model.md)
for the STRIDE analysis of this boundary.
