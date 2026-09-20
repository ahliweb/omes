# OMES control jobs (issue #90)

> Status: implemented in this repository as `omes job` (`lib/omes/cmd/job.sh`,
> `lib/omes/py/jobs/`). This is the OMES-side half of the boundary defined
> in [docs/control-center-contracts.md](control-center-contracts.md) (#89):
> it validates, stores, approves, executes, and audits jobs. It does not
> implement a Control Center, an HTTP API, a transport, or a tenant
> database — see "What remains in awcms-one" below.

## 1. The job boundary

```text
   deployment.request (contracts/control-center/v1/deployment.request.schema.json)
             │
             ▼
   omes job submit --file request.json
     - schema validation (lib/omes/py/jobs/schema.py)
     - idempotency check (replay -> original job, audited)
     - tenant/target scope check (OMES_JOBS_TENANT_ID / OMES_JOBS_SERVER_ID)
             │
             ▼
        job record (queued)  <state-dir>/jobs/<job-id>.json, mode 0600
             │
             ▼
   omes job approve <id> --actor <id>     (destructive operations only,
             │                             or auto-approved per policy)
             ▼
        job record (approved)
             │
             ▼
   omes job run <id>
     - map operation -> fixed bin/omes argv (never a shell, never a
       request field)
     - execute with a bounded timeout
     - read-back verification (re-run the corresponding status/health
       command; timeout != success)
             │
             ▼
   succeeded | failed | rolled_back   (audited, evidence attached)
```

Every state transition is appended to `<state-dir>/jobs/audit.jsonl` as
one redacted, hash-chained JSON line (`lib/omes/py/jobs/audit.py`, the
same pattern as `lib/omes/py/content/reports.py`'s audit log).

## 2. Job store layout

```
<state-dir>/jobs/                  mode 0700
<state-dir>/jobs/<job-id>.json     one job record, mode 0600
<state-dir>/jobs/idempotency.json  idempotency_key -> job_id index, mode 0600
<state-dir>/jobs/audit.jsonl       append-only, hash-chained, mode 0600
```

`<state-dir>` is `lib/omes/core.sh`'s `omes_state_dir()` (root:
`/var/lib/omes`; user: `${XDG_STATE_HOME:-$HOME/.local/state}/omes`;
override: `OMES_STATE_DIR`). `lib/omes/py/jobs/paths.py` reimplements
this same resolution in Python (it does not shell out to read it,
per ADR-0012).

A job record's `job_id` is derived from a hash of its `idempotency_key`
plus a timestamp (`job-<sha256[:12]>-<UTC stamp>`), so a duplicate
request never overwrites the original file even before the idempotency
index is consulted.

## 3. State machine

```
queued -> approved -> running -> succeeded
                                -> failed  (-> running, if error.retryable
                                             and attempts < max_attempts:
                                             a bounded retry re-entry,
                                             not a generic reopening)
                                -> rolled_back  (operation == rollback only)
queued -> cancelled
queued -> expired
approved -> cancelled
approved -> expired
```

| From | To | Trigger |
|---|---|---|
| `queued` | `approved` | `omes job approve` (destructive ops), or automatically inside `omes job run` (non-destructive ops in `OMES_JOBS_AUTO_APPROVE`) |
| `queued` | `cancelled` | `omes job cancel` |
| `queued` | `expired` | `omes job expire`, `created_at` older than `OMES_JOBS_TTL_SECONDS` |
| `approved` | `running` | `omes job run` |
| `approved` | `cancelled` | `omes job cancel` |
| `approved` | `expired` | `omes job expire` |
| `running` | `succeeded` | operation executed and (if applicable) read-back confirmed the desired state |
| `running` | `failed` | operation failed (any classification), or read-back mismatched/timed out |
| `running` | `rolled_back` | operation was `rollback` AND read-back confirmed the desired state |
| `failed` | `running` | `omes job run` called again, only when `error.retryable` is true and `attempts < max_attempts` (default 3) |

`succeeded`, `cancelled`, `expired`, and `rolled_back` are always
terminal. `failed` is terminal unless the failure was classified
retryable and attempts remain — see §6.

`omes job cancel` never accepts a `running` job: a synchronous
`bin/omes` subprocess call cannot be safely interrupted mid-mutation
without risking a half-applied state, so cancellation is only offered
before execution starts. This is a documented limitation, not an
oversight.

## 4. Approval policy

Destructive operations — `restore`, `rollback`, `stop`, `configure` —
require `omes job approve <id> --actor <id>` before `run`. `configure` is
included conservatively: OMES cannot yet distinguish a benign
configuration change from an "uninstall-like" one, so every `configure`
job requires approval until that distinction exists.

`OMES_JOBS_AUTO_APPROVE` (default `preflight,status,backup`) is an
explicit, documented allowlist of operation names `omes job run` may
auto-approve directly from `queued`. An operation in the destructive set
is **never** auto-approvable regardless of this variable's contents —
the allowlist can only widen which *non-destructive* operations skip an
explicit approval step, never bypass the destructive-operation gate.

Approving `restore` or `rollback` requires the job to already carry a
`backup_id` or `rollback_ref` (`store.approve()` raises
`ApprovalRequiredError` otherwise) — the wire contract
(`rollback.request.schema.json`) already requires `rollback_ref`; this is
belt-and-suspenders for `restore`, where a backup reference is optional
at the schema level but mandatory before OMES will actually approve
destroying/overwriting current state.

Every approval records the deciding `actor` in the audit log
(`lib/omes/py/jobs/audit.py`'s `append(event="approved", ...)`).

## 5. Operation → command table

`lib/omes/py/jobs/runner.py`'s `build_argv()` is the **only** place a
job's operation becomes an actual `bin/omes` invocation. It is a Python
literal mapping, never built from request fields:

| Operation | `bin/omes` argv | Read-back after execution |
|---|---|---|
| `preflight` | `check --json` | none (read-only) |
| `status` | `status --json` | none (read-only) |
| `backup` | `backup --json --yes` | `status --json` |
| `restore` | `restore --json --yes [--from <backup_id>]` | `status --json` |
| `rollback` | `restore --json --yes --from <rollback_ref>` | `status --json` |
| `install`, `configure`, `update`, `start`, `stop`, `restart` | **not_implemented** (typed failure, no shell fallback) | n/a |

`rollback` reuses `omes restore --from <timestamp>` because that already
**is** this repository's rollback mechanism (docs/rollback.md); there is
no separate `omes rollback` verb to invent, and inventing a parallel path
to the same backup/restore engine would be the kind of duplicated
authority AGENTS.md §2 warns against.

`install`, `configure`, `update`, `start`, `stop`, and `restart` have no
existing `bin/omes` command that targets a remote/logical deployment the
way a Control Center would mean them (as opposed to, say, `configure`
meaning "edit a module's local config", which is not what this contract
models). Per issue #90's instruction, these are implemented as a typed
`not_implemented` failure — `error.code == "not_implemented"`,
`retryable: false` — **never** as an arbitrary shell command standing in
for the missing verb.

## 6. Retry classification

`runner._run_argv()` classifies every execution outcome:

| Outcome | `error.code` | `retryable` |
|---|---|---|
| Subprocess exceeded `OMES_JOBS_TIMEOUT_SECONDS` | `timeout` | `true` |
| `bin/omes` not found / OS-level exec failure | `environment_error` | `false` |
| `bin/omes` exited `8` (network required but unavailable, docs/cli.md) | `transport_error` | `true` |
| `bin/omes` exited any other non-zero code | `operation_failed` | `false` |
| Read-back did not confirm desired state | `reconciliation_mismatch` | `false` |
| Operation has no command mapping | `not_implemented` | `false` |

A retryable failure may be retried by calling `omes job run <id>` again
while `attempts < max_attempts` (default 3, `store.DEFAULT_MAX_ATTEMPTS`).
A non-retryable failure is terminal; `run` raises `InvalidTransitionError`
if called again.

## 7. Read-back verification and reconciliation

For any operation with a read-back command (`backup`, `restore`,
`rollback`), `runner.run()` re-runs that command after execution and
validates the result (`_compare_desired_observed()`):

- a timed-out read-back is **never** treated as success;
- a read-back reporting `ok: false` is treated as a mismatch;
- when the backend supplies both `desired` and `observed` objects, they must
  match exactly; an incomplete pair or mismatch is rejected;
- legacy status output that supplies only `ok: true` remains supported for
  backwards compatibility;
- a mismatch reports `failed` with `error.code ==
  "reconciliation_mismatch"` and both the execute and read-back evidence
  attached under `record["evidence"]`, so an operator/Control Center can
  see exactly what was observed rather than only "it failed";
- `rollback` only reaches the terminal `rolled_back` state when its
  read-back confirms success — a `rollback` job whose read-back
  mismatches reports `failed`, never a false `rolled_back`.

## 8. Idempotency and audit

- Every request must carry `idempotency_key` (enforced by the
  `deployment.request` schema, #89). `store.submit()` looks the key up in
  `<state-dir>/jobs/idempotency.json` before creating anything; a hit
  returns the original job record unchanged and appends a `replayed`
  audit entry — the operation is never executed twice for the same key.
- Every transition (`submitted`, `replayed`, `approved`, `run_started`,
  `succeeded`, `failed`, `rolled_back`, `cancelled`, `expired`) is
  appended to `audit.jsonl` as one hash-chained line
  (`entry["line_hash"] = sha256(json.dumps(entry_without_hash,
  sort_keys=True) + prev_hash)`), so any rewrite of an earlier line is
  detectable by recomputing the chain (`audit.verify_chain()`).
- Every audit entry and every captured command-output tail is passed
  through `audit.redact_structure()`/`audit.capped_redacted_tail()`
  before being written: any field whose name matches
  `token|password|secret|credential|api_key|passphrase|cookie`
  (case-insensitive) is replaced with `[REDACTED]`, and command output is
  capped to the last 4096 characters (a failure reason is usually at the
  end, not the beginning) with the same redaction applied.

## 9. Tenant and target scope

`OMES_JOBS_TENANT_ID` names the tenant this OMES instance is enrolled
under. If it is unset, `omes job submit` refuses **every** request —
there is no implicit "any tenant is fine" default. If it is set, a
request whose `tenant_id` does not match is rejected as
`CrossTenantError` before any job record is created, and the rejection
itself is not currently written to the job audit log (there is no job to
attach it to) — a Control Center-side audit of the rejected call is the
Control Center's own responsibility (see "What remains in awcms-one").
`OMES_JOBS_SERVER_ID`, if set, applies the same check to
`target.server_id`.

## 10. What remains in awcms-one

This repository ships:

- the versioned wire contract (#89, `contracts/control-center/v1/`);
- the local, stdlib-only job store, state machine, approval gate,
  execution mapping, retry classification, read-back reconciliation, and
  hash-chained audit log (`lib/omes/py/jobs/`);
- the `omes job` CLI as the only way to drive any of the above.

It does **not** ship, and issue #90 does not require it to ship:

- an HTTP/API server, or any network listener;
- a tenant database, RBAC/ABAC, or multi-tenant identity;
- a transport (local socket / mTLS / pull worker) connecting a Control
  Center process to this CLI — `omes job submit --file ...` is the
  integration point a future transport would call;
- a UI, approval workflow beyond the CLI actor string, or notification
  system;
- `install`/`configure`/`update`/`start`/`stop`/`restart` execution
  against a target deployment — these remain `not_implemented` until a
  concrete deployment target abstraction exists (tracked by the
  agent-deployment work, issue #87, and any future OMES issue that adds
  them, not by #90).

Per AGENTS.md §4 and the issue-#90 acceptance criteria: everything listed
as implemented above is genuinely implemented and tested in this
repository (see `tests/py/jobs/`); everything in this section is
explicitly left for `awcms-one` or a future issue, not silently assumed.

## 11. Related documents

- [docs/control-center-contracts.md](control-center-contracts.md) (#89) — the wire contract `omes job submit` validates against.
- [docs/control-center-threat-model.md](control-center-threat-model.md) (#89) — STRIDE analysis referencing this design throughout its mitigation column.
- [docs/rollback.md](rollback.md) — the `omes restore` mechanism `rollback`/`restore` operations reuse.
- [docs/security.md](security.md), [docs/threat-model.md](threat-model.md) — the rows this document's controls are cross-referenced from.
