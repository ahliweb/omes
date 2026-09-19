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

This matrix is the concrete instantiation of
[docs/control-center-and-integrations.md](control-center-and-integrations.md)
§1's authority table and [ADR-0011](adr/0011-control-center-and-provider-boundaries.md)
decision 1, per entity rather than per concern.

## 2. Versioned API contracts (v1)

All schemas live under
[`contracts/control-center/v1/`](../contracts/control-center/v1/) as JSON
Schema (draft 2020-12 subset — see
[`contracts/README.md`](../contracts/README.md) "Validator subset").
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

See `docs/jobs.md` (issue #90; landing in the stacked `feat/90-control-jobs` branch, not yet on this branch) for the full state machine and
transition table; this contract only fixes the wire shape of a status
read.

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

See [docs/control-center-threat-model.md](control-center-threat-model.md)
for the STRIDE analysis of this boundary.
