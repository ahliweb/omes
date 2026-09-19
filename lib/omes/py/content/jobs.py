"""Job record schema, storage, and (from #67) state machine transitions.

See docs/content-distribution.md sections 4-5 for the schema and state
machine this module implements. Stdlib only (ADR-0012).
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import paths

SCHEMA_VERSION = 1

# All states in the design (docs/content-distribution.md section 5).
STATES = (
    "queued",
    "planning",
    "approval-required",
    "approved",
    "publishing",
    "verifying",
    "succeeded",
    "retryable-failure",
    "manual-review",
    "failed",
    "cancelled",
    "archived",
)

TERMINAL_STATES = frozenset({"succeeded", "failed", "cancelled", "archived"})


class ContentJobsError(Exception):
    """Base class for content-jobs errors."""


class UnknownSchemaVersionError(ContentJobsError):
    pass


class UnknownJobError(ContentJobsError):
    pass


class InvalidTransitionError(ContentJobsError):
    def __init__(self, from_state: str, to_state: str):
        super().__init__(f"invalid transition: {from_state!r} -> {to_state!r}")
        self.from_state = from_state
        self.to_state = to_state


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def now_stamp() -> str:
    """Compact UTC timestamp used in job ids and backup dirs."""
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def make_job_id(sha256_hex: str, stamp: str | None = None) -> str:
    return f"{sha256_hex[:12]}-{stamp or now_stamp()}"


def atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    """Write `data` as JSON to `path` atomically (temp file + os.replace) so
    a crash never leaves a partially-written job record."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".tmp-", suffix=".json", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, sort_keys=True)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def new_job_record(
    job_id: str,
    original_path: str,
    processing_path: str,
    sha256_hex: str,
    size_bytes: int,
    mime_guess: str | None,
    duplicate_of: str | None = None,
) -> dict[str, Any]:
    ts = now_iso()
    state = "queued" if duplicate_of is None else "cancelled"
    record: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "job_id": job_id,
        "state": state,
        "source": {
            "original_path": original_path,
            "processing_path": processing_path,
            "sha256": sha256_hex,
            "size_bytes": size_bytes,
            "mime_guess": mime_guess,
            "duplicate_of": duplicate_of,
        },
        "platform": None,
        "plan": {"caption": None, "caption_source": None, "targets": []},
        "approvals": [],
        "publish": {
            "worker_version": None,
            "attempts": 0,
            "max_attempts": 5,
            "last_result": None,
            "resulting_url": None,
        },
        "created_at": ts,
        "updated_at": ts,
        "history": [
            {
                "ts": ts,
                "from": None,
                "to": state,
                "actor": "system",
                "note": "duplicate" if duplicate_of else "scan",
            }
        ],
    }
    return record


def save_job(record: dict[str, Any], root: Path | None = None) -> Path:
    path = paths.job_record_path(record["job_id"], root)
    atomic_write_json(path, record)
    os.chmod(path, 0o600)
    return path


def load_job(job_id: str, root: Path | None = None) -> dict[str, Any]:
    path = paths.job_record_path(job_id, root)
    if not path.is_file():
        raise UnknownJobError(job_id)
    with path.open("r", encoding="utf-8") as fh:
        record = json.load(fh)
    if record.get("schema_version") != SCHEMA_VERSION:
        raise UnknownSchemaVersionError(
            f"job {job_id} has schema_version={record.get('schema_version')!r}, "
            f"expected {SCHEMA_VERSION}"
        )
    return record


def list_jobs(root: Path | None = None) -> list[dict[str, Any]]:
    jobs_dir = paths.jobs_dir(root)
    if not jobs_dir.is_dir():
        return []
    records = []
    for entry in sorted(jobs_dir.glob("*.json")):
        try:
            with entry.open("r", encoding="utf-8") as fh:
                records.append(json.load(fh))
        except (json.JSONDecodeError, OSError):
            continue
    return records


def record_history(record: dict[str, Any], to_state: str, actor: str, note: str = "") -> None:
    record["history"].append(
        {
            "ts": now_iso(),
            "from": record.get("state"),
            "to": to_state,
            "actor": actor,
            "note": note,
        }
    )
    record["state"] = to_state
    record["updated_at"] = now_iso()


# ---------------------------------------------------------------------------
# State machine (issue #67) - docs/content-distribution.md section 5.
# ---------------------------------------------------------------------------

TRANSITIONS: dict[str, frozenset[str]] = {
    "queued": frozenset({"planning", "cancelled"}),
    "planning": frozenset({"approval-required", "cancelled"}),
    "approval-required": frozenset({"approved", "cancelled"}),
    "approved": frozenset({"publishing", "cancelled"}),
    "publishing": frozenset({"verifying", "manual-review", "retryable-failure", "failed", "cancelled"}),
    "verifying": frozenset({"succeeded", "manual-review", "retryable-failure", "cancelled"}),
    "retryable-failure": frozenset({"publishing", "failed", "cancelled"}),
    "manual-review": frozenset({"publishing", "cancelled"}),
    "succeeded": frozenset({"archived"}),
    "failed": frozenset({"archived"}),
    "cancelled": frozenset({"archived"}),
    "archived": frozenset(),
}

CANCELLABLE_STATES = frozenset(
    s for s, targets in TRANSITIONS.items() if "cancelled" in targets
)

# Default per-platform retry policy (docs/content-distribution.md section 5.2).
DEFAULT_BASE_SECONDS = 30
DEFAULT_MAX_SECONDS = 1800
DEFAULT_MAX_ATTEMPTS = 5
DEFAULT_APPROVAL_TTL_SECONDS = 3600


def validate_transition(from_state: str, to_state: str) -> None:
    allowed = TRANSITIONS.get(from_state)
    if allowed is None or to_state not in allowed:
        raise InvalidTransitionError(from_state, to_state)


def apply_transition(record: dict[str, Any], to_state: str, actor: str, note: str = "") -> None:
    """Validates and applies a state transition, appending a history
    entry. Raises InvalidTransitionError without mutating `record` when
    the transition is not in TRANSITIONS."""
    validate_transition(record["state"], to_state)
    record_history(record, to_state, actor, note)


def compute_backoff_seconds(
    attempt: int,
    base_seconds: int = DEFAULT_BASE_SECONDS,
    max_seconds: int = DEFAULT_MAX_SECONDS,
) -> int:
    """Bounded exponential backoff: base * 2**attempt, capped at
    max_seconds. `attempt` is 0-based (the number of prior attempts)."""
    if attempt < 0:
        attempt = 0
    return min(base_seconds * (2**attempt), max_seconds)


def classify_worker_result(result: dict[str, Any]) -> str:
    """Maps a worker's publish/verify JSON result (docs/content-
    distribution.md section 6) to the next job state, given the job is
    currently in `publishing` or `verifying`.

    - status "ok" with a URL -> the caller decides "verifying" (from
      publishing) or "succeeded" (from verifying); this function returns
      the outcome kind instead: "ok", "uncertain", or one of
      "retryable"/"nonretryable" for errors, so callers can combine it
      with their own current-state-specific target.
    """
    status = result.get("status")
    if status == "ok":
        return "ok"
    if status == "uncertain":
        return "uncertain"
    if status == "error":
        return "retryable" if result.get("retryable") else "nonretryable"
    # Anything else (missing/garbled status) is treated as uncertain, never
    # as a silent success or a silent failure.
    return "uncertain"


def next_state_after_publish(result: dict[str, Any]) -> str:
    outcome = classify_worker_result(result)
    return {
        "ok": "verifying",
        "uncertain": "manual-review",
        "retryable": "retryable-failure",
        "nonretryable": "failed",
    }[outcome]


def next_state_after_verify(result: dict[str, Any]) -> str:
    outcome = classify_worker_result(result)
    return {
        "ok": "succeeded",
        "uncertain": "manual-review",
        "retryable": "retryable-failure",
        "nonretryable": "failed",
    }[outcome]


# ---------------------------------------------------------------------------
# Approvals (docs/content-distribution.md section 7)
# ---------------------------------------------------------------------------


def approve_job(
    record: dict[str, Any],
    actor: str,
    channel: str = "cli",
    ttl_seconds: int = DEFAULT_APPROVAL_TTL_SECONDS,
) -> None:
    from datetime import timedelta

    ts = datetime.now(timezone.utc)
    approval = {
        "actor": actor,
        "decision": "approved",
        "artifact_hash": record["source"]["sha256"],
        "approved_at": ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "expires_at": (ts + timedelta(seconds=ttl_seconds)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "channel": channel,
    }
    record["approvals"].append(approval)
    apply_transition(record, "approved", actor, note=f"approved via {channel}")


def reject_job(record: dict[str, Any], actor: str, channel: str = "cli") -> None:
    approval = {
        "actor": actor,
        "decision": "rejected",
        "artifact_hash": record["source"]["sha256"],
        "approved_at": now_iso(),
        "expires_at": now_iso(),
        "channel": channel,
    }
    record["approvals"].append(approval)
    apply_transition(record, "cancelled", actor, note=f"rejected via {channel}")


def latest_approval(record: dict[str, Any]) -> dict[str, Any] | None:
    approvals = record.get("approvals") or []
    return approvals[-1] if approvals else None


def is_approval_valid(record: dict[str, Any]) -> tuple[bool, str]:
    """Returns (valid, reason). A valid approval must be the most recent
    approval, decided "approved", bound to the job's current artifact
    hash, and not expired."""
    approval = latest_approval(record)
    if approval is None:
        return False, "no approval on record"
    if approval["decision"] != "approved":
        return False, f"latest decision is {approval['decision']!r}"
    if approval["artifact_hash"] != record["source"]["sha256"]:
        return False, "approval artifact hash does not match current job artifact hash"
    expires_at = datetime.strptime(approval["expires_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) > expires_at:
        return False, "approval has expired"
    return True, "ok"


# ---------------------------------------------------------------------------
# Retry / cancel (issue #67)
# ---------------------------------------------------------------------------


class RetryTooSoonError(ContentJobsError):
    pass


class ApprovalError(ContentJobsError):
    pass


def seconds_since_last_history_entry(record: dict[str, Any]) -> float:
    last = record["history"][-1]
    last_ts = datetime.strptime(last["ts"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - last_ts).total_seconds()


def retry_job(record: dict[str, Any], actor: str, force: bool = False) -> None:
    """Moves a retryable-failure or manual-review job back to
    `publishing`. Never re-invokes a worker itself - the caller
    (cli.py's `retry` command) does that after this transition succeeds.
    Enforces the bounded-backoff delay unless `force`."""
    if record["state"] not in ("retryable-failure", "manual-review"):
        raise InvalidTransitionError(record["state"], "publishing")

    attempts = record["publish"]["attempts"]
    max_attempts = record["publish"].get("max_attempts", DEFAULT_MAX_ATTEMPTS)
    if attempts >= max_attempts:
        raise ContentJobsError(
            f"job {record['job_id']} has exhausted max_attempts={max_attempts}"
        )

    if not force and record["state"] == "retryable-failure":
        delay = compute_backoff_seconds(attempts)
        elapsed = seconds_since_last_history_entry(record)
        if elapsed < delay:
            raise RetryTooSoonError(
                f"retry available in {delay - elapsed:.0f}s (backoff {delay}s since last attempt)"
            )

    record["publish"]["attempts"] = attempts + 1
    apply_transition(record, "publishing", actor, note="retry")


def cancel_job(record: dict[str, Any], actor: str, note: str = "cancelled by operator") -> None:
    if record["state"] not in CANCELLABLE_STATES:
        raise InvalidTransitionError(record["state"], "cancelled")
    apply_transition(record, "cancelled", actor, note=note)


def plan_job(record: dict[str, Any], actor: str = "system", caption: str | None = None, targets: list[str] | None = None) -> None:
    """queued -> planning -> approval-required. Caption generation itself
    (optionally via Hermes) is out of scope here (#69); this only records
    whatever caption/targets the caller already produced (or none, for a
    plan an operator will fill in by hand before approving)."""
    apply_transition(record, "planning", actor, note="plan started")
    record["plan"]["caption"] = caption
    record["plan"]["caption_source"] = "operator" if caption else None
    record["plan"]["targets"] = targets or []
    apply_transition(record, "approval-required", actor, note="plan complete")


# ---------------------------------------------------------------------------
# Publish / verify orchestration (issue #67; real workers land in #66)
# ---------------------------------------------------------------------------


def publish_job(record: dict[str, Any], worker_executable: str, worker_version: str | None = None) -> dict[str, Any]:
    """Runs `worker_executable`'s `publish` operation for `record`,
    classifies the result, and applies the resulting transition. Refuses
    to run without a current, valid approval (approval-gated by default -
    docs/content-distribution.md section 8)."""
    from . import worker as worker_mod

    if record["state"] != "approved":
        raise InvalidTransitionError(record["state"], "publishing")

    valid, reason = is_approval_valid(record)
    if not valid:
        apply_transition(record, "manual-review", actor="system", note=f"approval invalid: {reason}")
        raise ApprovalError(reason)

    apply_transition(record, "publishing", actor="system", note="publish started")
    record["publish"]["worker_version"] = worker_version
    record["publish"]["worker_executable"] = worker_executable

    payload = {
        "job_id": record["job_id"],
        "source_path": record["source"]["processing_path"],
        "caption": record["plan"].get("caption"),
        "targets": record["plan"].get("targets", []),
        "session_dir": None,
    }
    result = worker_mod.run_worker(worker_executable, "publish", payload)
    record["publish"]["attempts"] += 1
    record["publish"]["last_result"] = result
    if result.get("url"):
        record["publish"]["resulting_url"] = result["url"]

    to_state = next_state_after_publish(result)
    apply_transition(record, to_state, actor="system", note=result.get("note", ""))
    return result


def verify_job(record: dict[str, Any], worker_executable: str) -> dict[str, Any]:
    """Runs `worker_executable`'s `verify` operation for `record` (must be
    in `verifying`) and applies the resulting transition."""
    from . import worker as worker_mod

    if record["state"] != "verifying":
        raise InvalidTransitionError(record["state"], "succeeded")

    payload = {
        "job_id": record["job_id"],
        "url": record["publish"].get("resulting_url"),
        "session_dir": None,
    }
    result = worker_mod.run_worker(worker_executable, "verify", payload)
    record["publish"]["last_result"] = result
    if result.get("url"):
        record["publish"]["resulting_url"] = result["url"]

    to_state = next_state_after_verify(result)
    apply_transition(record, to_state, actor="system", note=result.get("note", ""))
    return result
