"""lib/omes/py/jobs/store.py - the durable job record store and state
machine for `omes job` (issue #90).

See docs/jobs.md for the design this module implements: the operation
allowlist, the state machine and its validation table, the approval
policy, idempotency, and tenant-scope enforcement. Stdlib only
(ADR-0012).
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import audit, paths

SCHEMA_VERSION = 1

# Mirrors contracts/control-center/v1/deployment.request.schema.json's
# `backup_id`/`rollback_ref` pattern exactly: must start with an
# alphanumeric character, so a value can never be mistaken for a CLI
# option (e.g. "--yes", "-rf") once it is placed into an argv list after
# a flag like `--from` (runner.py's build_argv). The schema already
# rejects this at `omes job submit` time; this constant lets runner.py
# re-check the SAME rule against a job record immediately before
# execution, so a record loaded from disk - however it got there - is
# never trusted just because it is sitting in the job store.
SAFE_ARGV_VALUE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


class UnsafeArgvValueError(Exception):
    """Raised when a job record field that will be placed into a
    `bin/omes` argv does not match SAFE_ARGV_VALUE_RE - most importantly,
    when it looks like a CLI option (starts with "-"). This must never
    happen for a record that passed schema validation at submit time;
    it exists as a second, independent gate directly at execution time."""

    def __init__(self, field: str, value: Any):
        super().__init__(f"{field}={value!r} does not look like a safe argv value (rejected before execution)")
        self.field = field
        self.value = value


def require_safe_argv_value(field: str, value: Any) -> str:
    """Returns `value` unchanged if it is a string matching
    SAFE_ARGV_VALUE_RE; raises UnsafeArgvValueError otherwise. Called by
    runner.py immediately before any record-derived value is appended to
    a `bin/omes` argv."""
    if not isinstance(value, str) or not SAFE_ARGV_VALUE_RE.match(value):
        raise UnsafeArgvValueError(field, value)
    return value

# The 11-operation allowlist. This MUST stay in sync with
# contracts/control-center/v1/deployment.request.schema.json's `operation`
# enum (tests/py/jobs/test_store.py asserts the two match) - #89 owns the
# wire contract, this module owns what OMES actually does with it.
OPERATIONS = (
    "preflight",
    "install",
    "configure",
    "start",
    "stop",
    "restart",
    "update",
    "status",
    "backup",
    "restore",
    "rollback",
)

STATES = (
    "queued",
    "approved",
    "running",
    "succeeded",
    "failed",
    "cancelled",
    "expired",
    "rolled_back",
)

TERMINAL_STATES = frozenset({"succeeded", "failed", "cancelled", "expired", "rolled_back"})

# Validation table (docs/jobs.md "State machine"): the only transitions
# `apply_transition` accepts, other than the special retry re-entry from
# `failed` handled separately in runner.py (a retryable failure may run
# again while attempts remain, which is not a generic "failed is not
# terminal" rule - it is retry policy, kept out of this table on purpose
# so the table stays a true description of *state* reachability, not of
# retry policy).
TRANSITIONS: dict[str, frozenset[str]] = {
    "queued": frozenset({"approved", "cancelled", "expired"}),
    "approved": frozenset({"running", "cancelled", "expired"}),
    "running": frozenset({"succeeded", "failed", "rolled_back"}),
    "succeeded": frozenset(),
    "failed": frozenset({"running"}),  # retry re-entry only; runner.py gates this on error.retryable and attempts
    "cancelled": frozenset(),
    "expired": frozenset(),
    "rolled_back": frozenset(),
}

# Operations that require an explicit `omes job approve` before `run`,
# unless the operation name is listed in OMES_JOBS_AUTO_APPROVE
# (docs/jobs.md "Approval policy"). `configure` is included because OMES
# cannot yet distinguish a benign configuration change from an
# uninstall-like one; the conservative default is to require approval for
# every `configure` job until that distinction exists.
DESTRUCTIVE_OPERATIONS = frozenset({"restore", "rollback", "stop", "configure"})

DEFAULT_AUTO_APPROVE = frozenset({"preflight", "status", "backup"})

DEFAULT_TTL_SECONDS = 3600
DEFAULT_MAX_ATTEMPTS = 3


class JobsError(Exception):
    """Base class for job-store errors."""


class UnknownJobError(JobsError):
    pass


class InvalidTransitionError(JobsError):
    def __init__(self, from_state: str, to_state: str):
        super().__init__(f"invalid transition: {from_state!r} -> {to_state!r}")
        self.from_state = from_state
        self.to_state = to_state


class ApprovalRequiredError(JobsError):
    pass


class CrossTenantError(JobsError):
    pass


class ValidationFailedError(JobsError):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def now_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def make_job_id(idempotency_key: str, stamp: str | None = None) -> str:
    import hashlib

    digest = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:12]
    return f"job-{digest}-{stamp or now_stamp()}"


def atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    """Copied (not imported) from lib/omes/py/content/jobs.py's helper of
    the same name - see audit.py's module docstring for why packages copy
    this small helper instead of importing across each other."""
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


def local_tenant_id() -> str | None:
    return os.environ.get("OMES_JOBS_TENANT_ID") or None


def local_server_id() -> str | None:
    return os.environ.get("OMES_JOBS_SERVER_ID") or None


def auto_approve_allowlist() -> frozenset[str]:
    raw = os.environ.get("OMES_JOBS_AUTO_APPROVE")
    if raw is None:
        return DEFAULT_AUTO_APPROVE
    return frozenset(x.strip() for x in raw.split(",") if x.strip())


def ttl_seconds() -> int:
    raw = os.environ.get("OMES_JOBS_TTL_SECONDS")
    if raw:
        try:
            return int(raw)
        except ValueError:
            pass
    return DEFAULT_TTL_SECONDS


# ---------------------------------------------------------------------------
# Idempotency index: idempotency_key -> job_id
# ---------------------------------------------------------------------------


def _load_idempotency_index(root: Path | None) -> dict[str, str]:
    path = paths.idempotency_index_path(root)
    if not path.is_file():
        return {}
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError):
        return {}


def _save_idempotency_index(root: Path | None, index: dict[str, str]) -> None:
    atomic_write_json(paths.idempotency_index_path(root), index)
    os.chmod(paths.idempotency_index_path(root), 0o600)


def find_job_by_idempotency_key(idempotency_key: str, root: Path | None = None) -> dict[str, Any] | None:
    index = _load_idempotency_index(root)
    job_id = index.get(idempotency_key)
    if job_id is None:
        return None
    try:
        return load_job(job_id, root)
    except UnknownJobError:
        return None


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


def new_job_record(request: dict[str, Any], job_id: str | None = None) -> dict[str, Any]:
    ts = now_iso()
    job_id = job_id or make_job_id(request["idempotency_key"])
    record: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "job_id": job_id,
        "state": "queued",
        "tenant_id": request["tenant_id"],
        "correlation_id": request["correlation_id"],
        "idempotency_key": request["idempotency_key"],
        "actor": request["actor"],
        "operation": request["operation"],
        "target": request["target"],
        "backup_id": request.get("backup_id"),
        "rollback_ref": request.get("rollback_ref"),
        "parameters": request.get("parameters", {}),
        "attempts": 0,
        "max_attempts": DEFAULT_MAX_ATTEMPTS,
        "error": None,
        "evidence": {},
        "created_at": ts,
        "updated_at": ts,
        "history": [{"ts": ts, "from": None, "to": "queued", "actor": request["actor"]["id"], "note": "submitted"}],
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
        return json.load(fh)


def list_jobs(root: Path | None = None) -> list[dict[str, Any]]:
    jobs_dir = paths.jobs_root(root)
    if not jobs_dir.is_dir():
        return []
    records = []
    for entry in sorted(jobs_dir.glob("job-*.json")):
        try:
            with entry.open("r", encoding="utf-8") as fh:
                records.append(json.load(fh))
        except (json.JSONDecodeError, OSError):
            continue
    return records


def record_history(record: dict[str, Any], to_state: str, actor: str, note: str = "") -> None:
    record["history"].append(
        {"ts": now_iso(), "from": record.get("state"), "to": to_state, "actor": actor, "note": note}
    )
    record["state"] = to_state
    record["updated_at"] = now_iso()


def apply_transition(record: dict[str, Any], to_state: str, actor: str, note: str = "") -> None:
    allowed = TRANSITIONS.get(record["state"])
    if allowed is None or to_state not in allowed:
        raise InvalidTransitionError(record["state"], to_state)
    record_history(record, to_state, actor, note)


# ---------------------------------------------------------------------------
# Submission (idempotency + tenant/target validation)
# ---------------------------------------------------------------------------


def submit(request: dict[str, Any], root: Path | None = None, actor_for_audit: str | None = None) -> tuple[dict[str, Any], bool]:
    """Creates (or replays) a job for `request` (already schema-validated
    by the caller). Returns (record, replayed). Enforces tenant scope
    against OMES_JOBS_TENANT_ID: if that env var is set and does not
    match the request's tenant_id, raises CrossTenantError (audited by
    the caller, which has the request context for the audit entry)."""
    paths.ensure_layout(root)
    tenant = local_tenant_id()
    if tenant is not None and request["tenant_id"] != tenant:
        raise CrossTenantError(
            f"request tenant_id={request['tenant_id']!r} does not match local tenant {tenant!r}"
        )
    server_id = local_server_id()
    if server_id is not None and request["target"].get("server_id") not in (None, server_id):
        raise CrossTenantError(
            f"request target.server_id={request['target'].get('server_id')!r} "
            f"does not match local server {server_id!r}"
        )

    existing = find_job_by_idempotency_key(request["idempotency_key"], root)
    if existing is not None:
        audit.append(
            root,
            actor=actor_for_audit or request["actor"]["id"],
            job_id=existing["job_id"],
            event="replayed",
            detail={"idempotency_key": request["idempotency_key"]},
        )
        return existing, True

    record = new_job_record(request)
    save_job(record, root)
    index = _load_idempotency_index(root)
    index[request["idempotency_key"]] = record["job_id"]
    _save_idempotency_index(root, index)
    audit.append(
        root,
        actor=actor_for_audit or request["actor"]["id"],
        job_id=record["job_id"],
        event="submitted",
        to_state="queued",
        detail={"operation": record["operation"], "target": record["target"]},
    )
    return record, False


# ---------------------------------------------------------------------------
# Approval
# ---------------------------------------------------------------------------


def is_destructive(operation: str) -> bool:
    return operation in DESTRUCTIVE_OPERATIONS


def can_auto_approve(operation: str) -> bool:
    if is_destructive(operation):
        return False
    return operation in auto_approve_allowlist()


def approve(record: dict[str, Any], actor: str, root: Path | None = None, note: str = "") -> None:
    if record["state"] != "queued":
        raise InvalidTransitionError(record["state"], "approved")
    if is_destructive(record["operation"]):
        if record["operation"] in ("restore", "rollback"):
            if not (record.get("backup_id") or record.get("rollback_ref")):
                raise ApprovalRequiredError(
                    f"{record['operation']} approval requires a backup_id or rollback_ref on the job"
                )
    apply_transition(record, "approved", actor, note=note or f"approved by {actor}")
    save_job(record, root)
    audit.append(root, actor=actor, job_id=record["job_id"], event="approved", from_state="queued", to_state="approved")


def cancel(record: dict[str, Any], actor: str, root: Path | None = None, note: str = "") -> None:
    if record["state"] not in ("queued", "approved"):
        raise InvalidTransitionError(record["state"], "cancelled")
    from_state = record["state"]
    apply_transition(record, "cancelled", actor, note=note or "cancelled by operator")
    save_job(record, root)
    audit.append(root, actor=actor, job_id=record["job_id"], event="cancelled", from_state=from_state, to_state="cancelled")


def expire_stale_jobs(root: Path | None = None, ttl: int | None = None) -> list[str]:
    """Transitions every queued/approved job older than `ttl` seconds
    (default OMES_JOBS_TTL_SECONDS) to `expired`. Returns the list of
    expired job ids."""
    ttl = ttl if ttl is not None else ttl_seconds()
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=ttl)
    expired: list[str] = []
    for record in list_jobs(root):
        if record["state"] not in ("queued", "approved"):
            continue
        created = datetime.strptime(record["created_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        if created < cutoff:
            from_state = record["state"]
            apply_transition(record, "expired", actor="system", note=f"TTL {ttl}s exceeded")
            save_job(record, root)
            audit.append(root, actor="system", job_id=record["job_id"], event="expired", from_state=from_state, to_state="expired")
            expired.append(record["job_id"])
    return expired
