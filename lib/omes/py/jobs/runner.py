"""lib/omes/py/jobs/runner.py - executes an approved job by mapping its
allowlisted operation to a FIXED argv of `bin/omes` itself (issue #90).

This module never builds a command from free-form request fields, never
invokes a shell, and never accepts a "command" field from anywhere - the
argv for each operation is a Python literal in this file. See
docs/jobs.md "Operation -> command table" for the authoritative mapping
and docs/control-center-contracts.md section 2.2 for why the wire
contract already forbids a free-form command field one layer up.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

from . import audit, store

DEFAULT_TIMEOUT_SECONDS = 120


def _omes_root() -> Path:
    env = os.environ.get("OMES_ROOT")
    if env:
        return Path(env)
    # lib/omes/py/jobs/runner.py -> jobs -> py -> omes -> lib -> <repo root>
    return Path(__file__).resolve().parents[4]


def _bin_omes() -> Path:
    return _omes_root() / "bin" / "omes"


class NotImplementedOperationError(Exception):
    pass


def build_argv(record: dict[str, Any]) -> list[str] | None:
    """Returns the fixed argv (without the `bin/omes` prefix) for
    `record`'s operation, or None if the operation has no existing OMES
    command yet (issue #90: "implement it as not_implemented, never as
    shell")."""
    operation = record["operation"]
    if operation == "preflight":
        return ["check", "--json"]
    if operation == "status":
        return ["status", "--json"]
    if operation == "backup":
        return ["backup", "--json", "--yes"]
    if operation == "restore":
        argv = ["restore", "--json", "--yes"]
        if record.get("backup_id"):
            argv += ["--from", store.require_safe_argv_value("backup_id", record["backup_id"])]
        return argv
    if operation == "rollback":
        # docs/rollback.md: `omes restore --from <timestamp>` IS the
        # rollback mechanism this repository ships; there is no separate
        # `omes rollback` verb to invent.
        return [
            "restore",
            "--json",
            "--yes",
            "--from",
            store.require_safe_argv_value("rollback_ref", record["rollback_ref"]),
        ]
    # install, configure, update, start, stop, restart: no existing OMES
    # command targets a remote/logical deployment yet (docs/jobs.md
    # "Left for awcms-one" / future OMES work).
    return None


def readback_argv(record: dict[str, Any]) -> list[str] | None:
    """The command to re-run after a mutation to verify desired vs.
    observed state. Returns None when the operation is itself read-only
    (no mutation to verify)."""
    operation = record["operation"]
    if operation in ("backup", "restore", "rollback"):
        return ["status", "--json"]
    return None


def _test_argv_override() -> list[str] | None:
    """Test-only hook (issue #90 acceptance criterion: "timeout (fake
    slow operation via a test hook env)"). Honored ONLY when
    OMES_JOBS_TEST_MODE=1 is also set, so a stray environment variable
    can never silently redirect a real job's execution in production."""
    if os.environ.get("OMES_JOBS_TEST_MODE") != "1":
        return None
    raw = os.environ.get("OMES_JOBS_TEST_ARGV_OVERRIDE")
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def _timeout_seconds() -> float:
    raw = os.environ.get("OMES_JOBS_TIMEOUT_SECONDS")
    if raw:
        try:
            return float(raw)
        except ValueError:
            pass
    return DEFAULT_TIMEOUT_SECONDS


# Every literal token build_argv() ever emits as a fixed flag/subcommand
# (never a record-derived value). _run_argv()'s blanket check below
# treats anything NOT in this set as record-derived, so any current or
# future value smuggled into argv without going through
# store.require_safe_argv_value() is still caught here as a second,
# independent gate - not just at the two call sites in build_argv().
_KNOWN_FIXED_ARGV_TOKENS = frozenset({"check", "status", "backup", "restore", "--json", "--yes", "--from"})


class UnsafeArgvError(Exception):
    """Raised by _run_argv() when an argv element that is not one of the
    fixed literal tokens build_argv() emits looks like a CLI option
    (starts with "-"). This is the blanket, second-layer version of
    store.require_safe_argv_value()'s per-field check."""


def _reject_option_like_argv(argv: list[str]) -> None:
    for element in argv:
        if element in _KNOWN_FIXED_ARGV_TOKENS:
            continue
        if element.startswith("-"):
            raise UnsafeArgvError(f"argv element {element!r} looks like a CLI option; refusing to execute")


def _run_argv(argv: list[str]) -> dict[str, Any]:
    """Runs `argv` (a bin/omes subcommand, or a test-hook override argv)
    and returns a classified, redacted result. Never raises for a
    subprocess-level failure - always returns a result dict so callers
    have a single code path. Exception: UnsafeArgvError, from the
    defensive check below, which callers must never swallow into a
    generic "operation_failed" - see run()'s handling."""
    _reject_option_like_argv(argv)
    override = _test_argv_override()
    full_argv = override if override is not None else [str(_bin_omes())] + argv
    started = time.monotonic()
    try:
        completed = subprocess.run(
            full_argv,
            capture_output=True,
            text=True,
            timeout=_timeout_seconds(),
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "timed_out": True,
            "duration_seconds": time.monotonic() - started,
            "error": {
                "code": "timeout",
                "message": f"operation did not complete within {_timeout_seconds()}s",
                "retryable": True,
            },
            "output_tail": audit.capped_redacted_tail((exc.stdout or "") + (exc.stderr or "")),
        }
    except (OSError, FileNotFoundError) as exc:
        return {
            "ok": False,
            "timed_out": False,
            "duration_seconds": time.monotonic() - started,
            "error": {"code": "environment_error", "message": str(exc), "retryable": False},
            "output_tail": "",
        }

    duration = time.monotonic() - started
    output_tail = audit.capped_redacted_tail((completed.stdout or "") + (completed.stderr or ""))
    parsed_stdout: dict[str, Any] | None = None
    try:
        parsed_stdout = json.loads(completed.stdout) if completed.stdout else None
    except json.JSONDecodeError:
        parsed_stdout = None

    if completed.returncode == 0:
        return {
            "ok": True,
            "timed_out": False,
            "duration_seconds": duration,
            "exit_code": completed.returncode,
            "parsed": parsed_stdout,
            "output_tail": output_tail,
        }

    # Non-zero exit: classify using the stable OMES exit-code contract
    # (docs/cli.md). 8 = network required but unavailable -> retryable
    # transport failure. Everything else is treated as non-retryable
    # (validation/policy/module failure) per issue #90's retry
    # classification requirement.
    retryable = completed.returncode == 8
    return {
        "ok": False,
        "timed_out": False,
        "duration_seconds": duration,
        "exit_code": completed.returncode,
        "parsed": parsed_stdout,
        "error": {
            "code": "transport_error" if retryable else "operation_failed",
            "message": f"bin/omes exited {completed.returncode}",
            "retryable": retryable,
        },
        "output_tail": output_tail,
    }


def _compare_desired_observed(record: dict[str, Any], readback_result: dict[str, Any]) -> tuple[bool, str]:
    """Compares the desired outcome of a mutation to the observed
    read-back result. A timeout is NEVER treated as success (issue #90's
    explicit requirement)."""
    if readback_result.get("timed_out"):
        return False, "read-back timed out; timeout is never treated as success"
    if not readback_result.get("ok"):
        return False, f"read-back command failed: {readback_result.get('error')}"
    parsed = readback_result.get("parsed") or {}
    if parsed.get("ok") is False:
        return False, "read-back reports ok=false"

    # Older OMES status commands expose only `ok`; preserve that contract.
    # When a backend supplies the stronger desired/observed pair, require
    # both sides and compare them exactly so a partial observation cannot be
    # reported as a successful mutation.
    desired = parsed.get("desired")
    observed = parsed.get("observed")
    if desired is not None or observed is not None:
        if not isinstance(desired, dict) or not isinstance(observed, dict):
            return False, "read-back has an incomplete desired/observed state pair"
        if desired != observed:
            return False, "read-back observed state does not match desired state"
    return True, "read-back confirms observed state matches desired outcome"


def _fail(
    record: dict[str, Any],
    root: Path | None,
    *,
    code: str,
    message: str,
    retryable: bool = False,
    note: str | None = None,
) -> dict[str, Any]:
    """Shared terminal-failure path: sets record["error"], transitions to
    `failed`, saves, and appends one audit entry (including whatever
    execute/readback evidence has already been recorded on `record` by
    the caller). Every failure branch in run() goes through this so the
    audit/evidence shape is consistent."""
    record["error"] = {"code": code, "message": message, "retryable": retryable}
    store.apply_transition(record, "failed", actor="system", note=note or code)
    store.save_job(record, root)
    audit.append(
        root,
        actor="system",
        job_id=record["job_id"],
        event="failed",
        to_state="failed",
        detail={"error": record["error"], "evidence": record.get("evidence") or {}},
    )
    return record


def run(record: dict[str, Any], actor: str, root: Path | None = None) -> dict[str, Any]:
    """Executes `record` (which must be `approved`, or `failed` with a
    retryable error and remaining attempts - see docs/jobs.md's retry
    policy) and returns the updated record. Every branch ends in a
    `save_job` + audit entry; this function never leaves a job in a state
    that does not match what was persisted."""
    state = record["state"]
    if state == "failed":
        error = record.get("error") or {}
        if not error.get("retryable"):
            raise store.InvalidTransitionError(state, "running")
        if record["attempts"] >= record.get("max_attempts", store.DEFAULT_MAX_ATTEMPTS):
            raise store.JobsError(f"job {record['job_id']} has exhausted max_attempts")
        store.apply_transition(record, "running", actor, note="retry")
    elif state == "queued":
        if not store.can_auto_approve(record["operation"]):
            raise store.ApprovalRequiredError(
                f"operation {record['operation']!r} requires `omes job approve` before it can run"
            )
        store.apply_transition(record, "approved", actor="system", note="auto-approved (policy)")
        audit.append(root, actor="system", job_id=record["job_id"], event="approved", from_state="queued", to_state="approved", detail={"auto": True})
        store.apply_transition(record, "running", actor, note="run started")
    elif state == "approved":
        store.apply_transition(record, "running", actor, note="run started")
    else:
        raise store.InvalidTransitionError(state, "running")

    store.save_job(record, root)
    audit.append(root, actor=actor, job_id=record["job_id"], event="run_started", to_state="running")

    record["attempts"] += 1

    # build_argv() re-validates backup_id/rollback_ref against the exact
    # same pattern the #89 contract enforces at submit time
    # (store.require_safe_argv_value()), so a job record is never
    # trusted just because it is already sitting in the store - this is
    # the second, independent gate the schema's stricter pattern alone
    # cannot guarantee for a record however it reached disk.
    try:
        argv = build_argv(record)
    except store.UnsafeArgvValueError as exc:
        return _fail(record, root, code="invalid_argument", message=str(exc), note="invalid_argument")

    if argv is None:
        return _fail(
            record,
            root,
            code="not_implemented",
            message=f"operation {record['operation']!r} has no existing OMES command yet",
            note="not_implemented",
        )

    try:
        result = _run_argv(argv)
    except UnsafeArgvError as exc:
        # Blanket second-layer catch (see _reject_option_like_argv): this
        # should be unreachable given the per-field check above, but a
        # future build_argv() branch that forgets to call
        # require_safe_argv_value() still fails safe here instead of
        # executing.
        return _fail(record, root, code="invalid_argument", message=str(exc), note="invalid_argument")

    record["evidence"]["execute"] = {
        "argv": argv,
        "ok": result.get("ok"),
        "duration_seconds": result.get("duration_seconds"),
        "output_tail": result.get("output_tail"),
    }

    if not result.get("ok"):
        error = result["error"]
        return _fail(record, root, code=error["code"], message=error["message"], retryable=error.get("retryable", False), note=error["code"])

    rb_argv = readback_argv(record)
    if rb_argv is None:
        record["error"] = None
        store.apply_transition(record, "succeeded", actor="system", note="no read-back required")
        store.save_job(record, root)
        audit.append(root, actor="system", job_id=record["job_id"], event="succeeded", to_state="succeeded")
        return record

    try:
        readback_result = _run_argv(rb_argv)
    except UnsafeArgvError as exc:
        return _fail(record, root, code="invalid_argument", message=str(exc), note="invalid_argument")

    record["evidence"]["readback"] = {
        "argv": rb_argv,
        "ok": readback_result.get("ok"),
        "output_tail": readback_result.get("output_tail"),
    }
    matches, reason = _compare_desired_observed(record, readback_result)
    if not matches:
        # A rollback whose read-back mismatches did not actually reach the
        # desired rolled-back state - report `failed` with evidence, never
        # a false `rolled_back`. `rolled_back` is reserved for a rollback
        # job whose read-back DID confirm success (see the success branch
        # below).
        return _fail(record, root, code="reconciliation_mismatch", message=reason, note="reconciliation_mismatch")

    record["error"] = None
    to_state = "rolled_back" if record["operation"] == "rollback" else "succeeded"
    store.apply_transition(record, to_state, actor="system", note="read-back confirmed")
    store.save_job(record, root)
    audit.append(root, actor="system", job_id=record["job_id"], event=to_state, to_state=to_state)
    return record
