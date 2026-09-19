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
