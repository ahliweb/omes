"""lib/omes/py/jobs/reconcile.py - reconciliation of orphaned `running` host
jobs after a crash, kill or reboot (issue #271, ADR-0032 rule 3).

A job record is persisted as `running` BEFORE its operation executes
(runner.py). If the executing process dies, nothing else moves the record, so
it would claim `running` forever. This module:

1. decides, deterministically and conservatively, whether a `running` record
   has lost its executor (`assess`): "if it cannot be proven orphaned, it is
   NOT orphaned";
2. for an orphan, re-runs the operation's EXISTING read-back command
   (`runner.readback_argv` + `runner._run_argv`) and reports only what the
   read-back proves:
     - desired == observed -> `succeeded` (`rolled_back` for a rollback job,
       exactly as `runner.run` does), with the read-back as evidence;
     - desired != observed (or the read-back reports ok=false) -> `failed`,
       `reconciliation_mismatch`, not retryable;
     - read-back unavailable, unsupported, timed out, errored, or unable to
       supply a complete desired/observed pair -> `failed`, `outcome_unknown`,
       not retryable, manual review required.
   It NEVER reports success from the passage of time or from a bare `ok`.

No new job state exists: transitions are the already-allowed
`running -> succeeded|failed|rolled_back` through `store.apply_transition`,
and every transition appends a hash-chained `reconciled_orphan` audit entry.
Reconciling twice changes nothing the second time (the record is no longer
`running`). Stdlib only (ADR-0012).
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import audit, identity, runner, store

DEFAULT_LEGACY_ORPHAN_SECONDS = 3600

# Orphan verdict reasons (assess()["reason"]).
REASON_BOOT_ID_CHANGED = "boot_id_changed"
REASON_PID_NOT_ALIVE = "pid_not_alive"
REASON_PID_REUSED = "pid_reused"
REASON_LEGACY_STALE = "legacy_record_stale"
REASON_RUNNER_ALIVE = "runner_alive"
REASON_INDETERMINATE = "indeterminate"
REASON_LEGACY_RECENT = "legacy_record_recent"
REASON_NOT_RUNNING = "not_running"

NOTE_RECONCILED = "reconciled after orphaned run"


def legacy_orphan_seconds() -> int:
    """Minimum `updated_at` age before a `running` record WITHOUT executor
    identity (written before #271) may be treated as an orphan candidate.
    Configurable via OMES_JOBS_ORPHAN_LEGACY_SECONDS (default 3600)."""
    raw = os.environ.get("OMES_JOBS_ORPHAN_LEGACY_SECONDS")
    if raw:
        try:
            value = int(raw)
            if value >= 0:
                return value
        except ValueError:
            pass
    return DEFAULT_LEGACY_ORPHAN_SECONDS


def _usable_runner(info: Any) -> bool:
    pid = info.get("pid") if isinstance(info, dict) else None
    return isinstance(pid, int) and not isinstance(pid, bool) and pid > 0


def assess(record: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    """Returns {"orphaned": bool, "reason": str} for `record`.

    Only a `running` record can be orphaned. Order of proof:
      1. recorded boot_id differs from the current boot_id -> the host
         rebooted, so the executor is certainly gone;
      2. the recorded pid does not exist (ProcessLookupError) -> gone;
      3. the pid exists but its process start time differs from the recorded
         one -> the pid was reused by an unrelated process, runner is gone;
      4. the pid exists and (start time matches, or cannot be compared) ->
         NOT orphaned: a live runner, or an unprovable case, is left alone.
    A record with no usable `runner` (legacy) is an orphan candidate only
    when `updated_at` is older than `legacy_orphan_seconds()`.
    """
    if record.get("state") != "running":
        return {"orphaned": False, "reason": REASON_NOT_RUNNING}

    info = record.get("runner")
    if not _usable_runner(info):
        return _assess_legacy(record, now)

    recorded_boot = info.get("boot_id")
    current_boot = identity.current_boot_id()
    if isinstance(recorded_boot, str) and recorded_boot and current_boot and recorded_boot != current_boot:
        return {"orphaned": True, "reason": REASON_BOOT_ID_CHANGED}

    liveness = identity.pid_liveness(info["pid"])
    if liveness == identity.DEAD:
        return {"orphaned": True, "reason": REASON_PID_NOT_ALIVE}
    if liveness != identity.ALIVE:
        return {"orphaned": False, "reason": REASON_INDETERMINATE}

    recorded_ticks = info.get("pid_start_ticks")
    if isinstance(recorded_ticks, int) and not isinstance(recorded_ticks, bool):
        current_ticks = identity.process_start_ticks(info["pid"])
        if current_ticks is not None and current_ticks != recorded_ticks:
            return {"orphaned": True, "reason": REASON_PID_REUSED}
    return {"orphaned": False, "reason": REASON_RUNNER_ALIVE}


def _assess_legacy(record: dict[str, Any], now: datetime | None) -> dict[str, Any]:
    try:
        updated = datetime.strptime(record["updated_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (KeyError, TypeError, ValueError):
        return {"orphaned": False, "reason": REASON_INDETERMINATE}
    current = now or datetime.now(timezone.utc)
    if (current - updated).total_seconds() > legacy_orphan_seconds():
        return {"orphaned": True, "reason": REASON_LEGACY_STALE}
    return {"orphaned": False, "reason": REASON_LEGACY_RECENT}


def _classify_readback(record: dict[str, Any], readback_result: dict[str, Any]) -> tuple[str, str]:
    """Returns ("match" | "mismatch" | "unknown", reason).

    Stricter than a normal run on purpose: after a normal run, exit code 0
    plus a read-back `ok` is accepted (legacy contract). For an orphan there
    is no exit code and no knowledge that the mutation ever finished, so a
    bare `ok: true` proves nothing about THIS job; success needs the
    read-back to supply a complete desired/observed pair that compare equal.
    """
    if readback_result.get("timed_out"):
        return "unknown", "read-back timed out; a timeout is never treated as success"
    if not readback_result.get("ok"):
        return "unknown", f"read-back command failed: {readback_result.get('error')}"
    parsed = readback_result.get("parsed")
    if not isinstance(parsed, dict):
        return "unknown", "read-back produced no machine-readable result"
    if parsed.get("ok") is False:
        return "mismatch", "read-back reports ok=false"
    desired = parsed.get("desired")
    observed = parsed.get("observed")
    if desired is None and observed is None:
        return "unknown", "read-back supplied no desired/observed pair; an ok-only status cannot prove an orphaned mutation completed"
    if not isinstance(desired, dict) or not isinstance(observed, dict):
        return "unknown", "read-back has an incomplete desired/observed state pair"
    matches, reason = runner._compare_desired_observed(record, readback_result)
    return ("match" if matches else "mismatch"), reason


def _decide(record: dict[str, Any]) -> tuple[str, str | None, dict[str, Any]]:
    """Runs the read-back and returns (classification, reason, evidence)
    where classification is "match" | "mismatch" | "unknown"."""
    rb_argv = runner.readback_argv(record)
    if rb_argv is None:
        return (
            "unknown",
            f"operation {record['operation']!r} has no read-back command, so the outcome of the orphaned run cannot be verified",
            {},
        )
    try:
        result = runner._run_argv(rb_argv)
    except Exception as exc:  # noqa: BLE001 - any failure to read back is "unknown", never success
        return "unknown", f"read-back could not be executed: {type(exc).__name__}", {"argv": rb_argv, "ok": False}
    evidence = {"argv": rb_argv, "ok": result.get("ok"), "output_tail": result.get("output_tail")}
    classification, reason = _classify_readback(record, result)
    return classification, reason, evidence


def reconcile_job(record: dict[str, Any], root: Path | None = None, actor: str = "system") -> dict[str, Any]:
    """Reconciles one job record in place. Returns a result dict:
    {"job_id", "outcome": "reconciled" | "unchanged", "state", "reason", ...}.
    """
    job_id = record["job_id"]
    verdict = assess(record)
    if not verdict["orphaned"]:
        return {"job_id": job_id, "outcome": "unchanged", "state": record["state"], "reason": verdict["reason"]}

    classification, reason, readback = _decide(record)
    previous_runner = record.get("runner")

    if classification == "match":
        to_state = "rolled_back" if record["operation"] == "rollback" else "succeeded"
        record["error"] = None
        transition_note = NOTE_RECONCILED
    elif classification == "mismatch":
        to_state = "failed"
        record["error"] = {"code": "reconciliation_mismatch", "message": reason, "retryable": False}
        transition_note = "reconciliation_mismatch (orphaned run)"
    else:
        to_state = "failed"
        record["error"] = {
            "code": "outcome_unknown",
            "message": (
                "the runner of this job is gone and its outcome could not be verified by read-back "
                f"({reason}); manual review required"
            ),
            "retryable": False,
        }
        transition_note = "outcome_unknown (orphaned run)"

    evidence = record.setdefault("evidence", {})
    if readback:
        evidence["readback"] = readback
    evidence["reconciliation"] = {
        "note": NOTE_RECONCILED,
        "orphan_reason": verdict["reason"],
        "classification": classification,
        "reconciled_at": store.now_iso(),
        "previous_runner": previous_runner if isinstance(previous_runner, dict) else None,
    }

    store.apply_transition(record, to_state, actor=actor, note=transition_note)
    store.save_job(record, root)
    audit.append(
        root,
        actor=actor,
        job_id=job_id,
        event="reconciled_orphan",
        from_state="running",
        to_state=to_state,
        detail={
            "orphan_reason": verdict["reason"],
            "classification": classification,
            "error": record["error"],
            "evidence": evidence,
        },
    )
    return {
        "job_id": job_id,
        "outcome": "reconciled",
        "state": to_state,
        "reason": verdict["reason"],
        "classification": classification,
        "error": record["error"],
    }


def reconcile(root: Path | None = None, job_id: str | None = None, actor: str = "system") -> dict[str, Any]:
    """Reconciles one job (`job_id`) or every `running` job in local scope.

    With `job_id`: raises store.UnknownJobError for an unknown job and
    store.CrossTenantError for a job outside OMES_JOBS_TENANT_ID /
    OMES_JOBS_SERVER_ID. Without it, out-of-scope `running` jobs are left
    untouched and only counted.
    """
    reconciled: list[dict[str, Any]] = []
    unchanged: list[dict[str, Any]] = []
    skipped_out_of_scope = 0

    if job_id is not None:
        record = store.load_job(job_id, root)
        store.assert_record_in_local_scope(record)
        records = [record]
    else:
        records = [r for r in store.list_jobs(root) if r.get("state") == "running"]

    for record in records:
        if job_id is None:
            try:
                store.assert_record_in_local_scope(record)
            except store.CrossTenantError:
                skipped_out_of_scope += 1
                continue
        result = reconcile_job(record, root, actor=actor)
        (reconciled if result["outcome"] == "reconciled" else unchanged).append(result)

    return {"reconciled": reconciled, "unchanged": unchanged, "skipped_out_of_scope": skipped_out_of_scope}
