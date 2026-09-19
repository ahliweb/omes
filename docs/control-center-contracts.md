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
