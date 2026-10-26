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
| `running` | `succeeded` / `rolled_back` / `failed` | `omes job reconcile`, only for an orphaned `running` record (executor proven gone) — see §7.1; same targets as above, no new state |
| `failed` | `running` | `omes job run` called again, only when `error.retryable` is true and `attempts < max_attempts` (default 3) |

`succeeded`, `cancelled`, `expired`, and `rolled_back` are always
terminal. `failed` is terminal unless the failure was classified
retryable and attempts remain — see §6.

A `running` record whose executor has died is not left in `running`
forever: `omes job reconcile` (§7.1) moves it to a terminal state using only
the transitions already in this table. No new state exists.

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
| Orphaned `running` job whose read-back mismatched (`omes job reconcile`, §7.1) | `reconciliation_mismatch` | `false` |
| Orphaned `running` job whose outcome could not be verified (`omes job reconcile`, §7.1) | `outcome_unknown` | `false` |

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

### 7.1 Orphaned `running` jobs (issue #271)

`runner.run()` persists `running` before it executes the operation. If the
`omes job run` process then dies (crash, `kill -9`, OOM, reboot), nothing else
would ever move the record. `omes job reconcile` closes that gap without
adding a state, a scheduler or a daemon. Status: **Implemented**
(`lib/omes/py/jobs/identity.py`, `lib/omes/py/jobs/reconcile.py`).

**Executor identity (replaces a lease or heartbeat).** When a job enters
`running` the runner records, on the job record:

```json
"runner": {"pid": 4242, "boot_id": "<kernel boot id or null>",
           "started_at": "2026-10-03T00:00:00Z", "pid_start_ticks": 1234567}
```

`boot_id` comes from `/proc/sys/kernel/random/boot_id` (`null` if
unreadable); `pid_start_ticks` is the process start time from
`/proc/<pid>/stat` (`null` if unreadable) and exists only so a recycled pid is
not mistaken for the original runner. `runner` is internal to the local job
record: it is not part of `job-status.response` (whose `additionalProperties`
is `false`; its `state` enum is unchanged), is not in `omes job list --json`
summaries, and is never sent to the Control Center. `omes job status --json`
prints the raw local record, which already carries non-wire fields such as
`attempts` and `history`, so it now includes `runner` too.

**Orphan detection (`reconcile.assess`)** is deterministic and conservative:
if an orphan cannot be proven, the job is left alone.

| Evidence | Verdict |
|---|---|
| Recorded `boot_id` differs from the current one (both known) | orphan (`boot_id_changed`): the host rebooted |
| Recorded pid does not exist (`os.kill(pid, 0)` raises `ProcessLookupError`) | orphan (`pid_not_alive`) |
| Pid exists but its start time differs from `pid_start_ticks` | orphan (`pid_reused`): not this job's runner |
| Pid exists and start time matches or cannot be compared | **not** orphaned (`runner_alive`) |
| Liveness cannot be determined (other `OSError`) | **not** orphaned (`indeterminate`) |
| No usable `runner` (record written before #271) | orphan candidate (`legacy_record_stale`) only if `updated_at` is older than `OMES_JOBS_ORPHAN_LEGACY_SECONDS` (default `3600`); otherwise **not** orphaned |

A process that is gone cannot write to the record, so a proven orphan's own
runner cannot overwrite a reconciliation; a live runner is never touched.
The verdict is only as fresh as the record it was computed from, so
`reconcile_job` does not trust the batch snapshot: it re-reads the record from
disk before assessing it and returns `unchanged` with reason
`no_longer_running` if the state is no longer `running`; it then runs the
read-back (which may take up to `OMES_JOBS_TIMEOUT_SECONDS`) and re-reads the
record once more immediately before writing. If the on-disk state is no longer
`running`, or its `runner` identity differs from the one that was assessed
(for example a retry started a new run), nothing is written and no audit
entry is appended, so a terminal state saved by the real runner (or by a
concurrent `omes job reconcile`) is never overwritten. The transition is
applied to that freshly loaded record. This is a re-check, not a lock: the job
store has no file locking, so a save landing in the short interval between the
final re-read and the write is not excluded.

**Reconciliation (`omes job reconcile [--job <id>]`).** For every orphaned
`running` job the operation's existing read-back command is re-run
(`runner.readback_argv()`, the same `status --json` used after `backup`,
`restore` and `rollback`) and only what it proves is reported:

| Read-back result | Terminal state | `error` |
|---|---|---|
| Complete `desired`/`observed` pair, equal | `succeeded` (`rolled_back` for a `rollback` job, as in a normal run), `evidence.readback` attached, history note `reconciled after orphaned run` | `null` |
| Pair present and unequal, or `ok: false` | `failed` | `reconciliation_mismatch`, `retryable: false` |
| Read-back unavailable, unsupported (`preflight`/`status`/unmapped operations have no read-back), timed out, failed to execute, unparseable, or without a complete `desired`/`observed` pair | `failed` | `outcome_unknown`, `retryable: false`, message ends `manual review required` |

Reconciliation is deliberately **stricter** than a normal run: after a normal
run an exit code of 0 plus a bare `ok: true` status is accepted (§7), but for
an orphan there is no exit code and no proof the mutation ever finished, so a
bare `ok: true` cannot prove anything about this job and yields
`outcome_unknown`. A job is never reported `succeeded` without read-back
evidence, and time passing is never evidence. A reconciled `failed` job is
not retryable: an operator resubmits (new idempotency key) after reviewing it.

Every reconciled job transitions via `store.apply_transition()`, is saved
with `evidence.reconciliation` (orphan reason, classification, previous
runner identity), and appends one hash-chained `reconciled_orphan` audit
entry (`from: running`). Reconciliation is idempotent: a second run finds no
`running` job, executes no command, writes nothing and appends no audit
entry.

Tenant scope is default-deny and identical to `submit` (issue #318, see §9).
`reconcile` resolves the local tenant through the same
`store.resolve_local_tenant()` as `store.submit()`, **before** it loads or
lists any job. If no local tenant is configured (`OMES_JOBS_TENANT_ID` unset
or empty, and no enrolled tenant passed by an in-process caller), both the
batch run and `--job <id>` raise `TenantNotConfiguredError`, and
`omes job reconcile` exits 1 with `{"error": "tenant_not_configured",
"reason": ...}` under `--json` (the reason on stderr otherwise). Nothing is
read back, written, transitioned or audited, and `--job` does not reveal
whether the id exists (the tenant check comes first). Before #318 an unset
`OMES_JOBS_TENANT_ID` made a batch run reconcile every `running` job of every
tenant. With a tenant configured, a batch run skips (and counts in
`skipped_out_of_scope`) any job whose `tenant_id` differs, and `--job <id>` on
such a job is refused with `{"error": "cross_tenant_rejected", ...}` and
exit 1; a record with a missing `tenant_id` never matches.
`OMES_JOBS_SERVER_ID` stays optional and, when set, applies the same
skip/refuse rule to `target.server_id`.

Confirmed cancellation of a `running` job (intent versus verified stop) is
Not implemented yet (tracked in [#271](https://github.com/ahliweb/omes/issues/271));
`omes job cancel` still rejects `running`. `reconcile` is operator-invoked:
nothing runs it automatically (no timer, boot hook or daemon ships with it),
and no issue currently owns adding one.

## 8. Idempotency and audit

- Every request must carry `idempotency_key` (enforced by the
  `deployment.request` schema, #89). `store.submit()` looks the key up in
  `<state-dir>/jobs/idempotency.json` before creating anything; a hit
  returns the original job record unchanged and appends a `replayed`
  audit entry — the operation is never executed twice for the same key.
- Every transition (`submitted`, `replayed`, `approved`, `run_started`,
  `succeeded`, `failed`, `rolled_back`, `cancelled`, `expired`,
  `reconciled_orphan`) is
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

### 8.1 Correlation and trace IDs (issue #356)

Evidence, not authority (ADR-0032): these identifiers line up what AWCMS asked
for, what the job store recorded and what the worker reported. They are never
read by the operation allowlist, approval policy, tenant check or argv builder,
and grant nothing. They are opaque tokens with a pattern and a length bound; they
never carry free text or secrets, and a malformed value is rejected, never
sanitised, truncated or echoed back.

| Field | Shape | Origin |
| --- | --- | --- |
| `correlation_id` | `^[A-Za-z0-9_.:-]{1,128}$`, required | Caller (AWCMS). Flows unchanged. |
| `causation_id` | `^[A-Za-z0-9_.:-]{8,128}$`, optional | Caller only (the `event_id` that caused the job). Never generated: absent means a root job. |
| `trace_id` | W3C trace-id, 32 lowercase hex, not all zero, optional | Caller, else generated locally. |
| `span_id` | W3C span-id, 16 lowercase hex, not all zero, optional | Caller, else generated locally. |

- **Contracts.** `causation_id`, `trace_id` and `span_id` are optional, additive
  properties on `operation-request`, `deployment.request`,
  `worker-poll.response` (`job`), `worker-result.request` and
  `job-status.response`; the last two also carry optional `generated_ids`
  (`trace_id` and/or `span_id`) naming the IDs OMES generated locally. No required
  field changed, no command, shell or argv key was added, and every existing
  fixture still validates.
- **Submit.** `store.submit()` validates the IDs before anything is written
  (`InvalidCorrelationError`; `omes job submit --json` reports
  `invalid_correlation_id`). Supplied `trace_id`/`span_id` are stored unchanged;
  missing ones are generated and listed in the record's `generated_ids`. An
  idempotent replay returns the original record and its original IDs.
- **Audit.** Every job audit line carries `correlation_id` and, when present,
  `causation_id`, `trace_id` and `span_id` as top-level fields. The hash chain
  covers them. Records written before #356 simply lack the optional ones.
- **Worker.** `poll_and_dispatch_once()` forwards the caller's IDs into the local
  submit and echoes `correlation_id`, `causation_id`, `trace_id`, `span_id` and
  `generated_ids` in the `worker-result.request`. A malformed ID in a poll
  response fails the poll-response contract check, so nothing is stored or
  executed. A job that is rejected locally (wrong tenant, unsupported operation)
  echoes only the IDs the caller supplied; nothing is generated for it.
- **Envelopes.** `jobs.correlation.envelopes_for_job(record)` is a pure,
  rebuildable projection of a job record's state history into
  `contracts/observability/v1/correlation-envelope` events (`source_authority`
  `omes`, `classification` `INTERNAL`, `redaction_state` `none_required`). The
  `event_id` is `<job_id>.<index>`, so re-projecting yields identical events and
  `observability.envelope.reduce_events()` dedupes and orders them regardless of
  delivery order. Each event's `causation_id` is the previous event, the first
  one's is the job's caller-supplied `causation_id`. Envelopes carry identifiers
  and closed enums only: no parameters, output, error text or actor. If an
  identifier cannot be expressed in the envelope contract (for example a
  `correlation_id` shorter than the envelope's 8-character minimum) the projection
  raises `CorrelationError` rather than rewriting it. Nothing persists envelopes
  and no CLI command emits them.
- **OpenTelemetry: deferred.** The IDs are W3C Trace Context shaped so a later
  exporter can use them, but OMES adds no OpenTelemetry SDK, exporter or
  collector. Revisit when an operator actually runs a collector.
- Not implemented yet (tracked in #356): an envelope store or `omes job`
  subcommand that emits envelopes, producers other than the job record, the AWCMS
  side (it must re-vendor the changed contracts), and propagation across the
  Hermes delegation and ACP boundaries (only where upstream exposes IDs).

## 9. Tenant and target scope

`OMES_JOBS_TENANT_ID` names the tenant this OMES instance is enrolled
under. The check is default-deny (issue #279):

- If it is unset (or empty), `store.submit()` raises
  `TenantNotConfiguredError` and `omes job submit` refuses **every**
  request, emitting `{"error": "tenant_not_configured", "reason": ...}`
  with `--json` and exiting 1 — there is no implicit "any tenant is fine"
  default. Hosts that previously submitted jobs without the variable must
  now set it.
- If it is set, a request whose `tenant_id` does not match is rejected as
  `CrossTenantError` (`{"error": "cross_tenant_rejected", ...}`).
  `TenantNotConfiguredError` subclasses `CrossTenantError`, so any
  handler of the latter stays fail-closed.

Both rejections happen before the idempotency lookup and before anything
is written: no job record, idempotency index entry, or audit line is
created. The rejection itself is therefore not written to the job audit
log (there is no job to attach it to) — a Control Center-side audit of
the rejected call is the Control Center's own responsibility (see "What
remains in awcms-one").

The pull worker (`omes worker`, issue #192) does not read
`OMES_JOBS_TENANT_ID`: it passes the tenant it was enrolled under
(`omes worker enroll --tenant`, stored in its credentials) to
`store.submit(enrolled_tenant_id=...)`, so the local check is against the
enrolled tenant and works on hosts without the variable.

**Effective-tenant precedence (`store.resolve_local_tenant()`).** There is
one resolver, used by `store.submit()` and by orphan reconciliation
(`store.assert_record_in_local_scope()` / `reconcile.reconcile()`, issue #318):

1. `enrolled_tenant_id`, when the caller supplies a non-empty one, **wins**
   over the environment: if both are set and differ, the enrolled tenant is
   the one enforced and `OMES_JOBS_TENANT_ID` is ignored;
2. otherwise `OMES_JOBS_TENANT_ID` (an empty string counts as unset);
3. otherwise `TenantNotConfiguredError` (default-deny), never "any tenant".

`omes job submit` and `omes job reconcile` run in the operator's shell and
pass no enrolled tenant, so for them the effective tenant is
`OMES_JOBS_TENANT_ID` alone. The `enrolled_tenant_id` keyword is used by the
pull worker for `submit` and is accepted by `reconcile.reconcile()` for
in-process callers that hold enrolled credentials; the CLI has no flag for it.

`OMES_JOBS_SERVER_ID` stays optional: if set, it applies the same
mismatch check to `target.server_id`; if unset, the target server is not
checked.

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
- a transport connecting a Control Center process to this CLI — implemented as an outbound-only pull-worker client via `omes worker` / `lib/omes/py/jobs/worker.py` (issue #192, ADR-0027);
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
