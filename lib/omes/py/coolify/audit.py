"""lib/omes/py/coolify/audit.py - append-only, hash-chained audit log and
secret redaction for the Coolify adapter (issue #97).

Deliberate copy (not an import) of lib/omes/py/jobs/audit.py, keeping the
exact same record shape (`ts`, `actor`, `job_id`, `event`, `from`, `to`,
`detail`, `prev_hash`, `line_hash`, chained by
`sha256(json.dumps(entry, sort_keys=True) + prev_hash)`) so every OMES
audit log - jobs, content, coolify - can be read, verified, and tooled the
same way, per the brief for issue #97 ("emits an audit event via the
existing jobs audit log shape"). `job_id` here holds the Coolify adapter's
own `correlation_id` (the field name is kept for shape parity across
packages, not because this is a job-runner job). Stdlib only (ADR-0012).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import paths

GENESIS_HASH = "0" * 64

_SECRET_KEY_RE = re.compile(
    r"(token|password|secret|credential|api[_-]?key|passphrase|cookie|authorization)", re.IGNORECASE
)
# Bounded on both sides for the same reason lib/omes/py/jobs/audit.py's
# equivalent pattern is bounded: an unbounded run of the same character
# class as the alternation it precedes is a catastrophic-backtracking
# (ReDoS) shape, and this function redacts attacker-reachable captured
# output (client.py error text, provider responses).
_SECRET_VALUE_RE = re.compile(
    r"([A-Za-z_]{0,32}(?:TOKEN|KEY|SECRET|PASSWORD|CREDENTIAL|PASSPHRASE|COOKIE|AUTHORIZATION)[A-Za-z_]{0,32}\s*[:=]\s*)\S+",
    re.IGNORECASE,
)
_BEARER_RE = re.compile(r"(Bearer\s+)\S+", re.IGNORECASE)


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def redact_value(value: Any) -> Any:
    if isinstance(value, str):
        return _BEARER_RE.sub(r"\1[REDACTED]", _SECRET_VALUE_RE.sub(r"\1[REDACTED]", value))
    if isinstance(value, dict):
        return redact_structure(value)
    if isinstance(value, list):
        return [redact_value(v) for v in value]
    return value


def redact_structure(obj: dict[str, Any]) -> dict[str, Any]:
    """Recursively redacts any key matching a secret-like name and any
    string value that looks like `SOME_TOKEN=value`/`Authorization:
    Bearer <token>`, anywhere in a nested dict/list structure."""
    out: dict[str, Any] = {}
    for k, v in obj.items():
        if _SECRET_KEY_RE.search(k):
            out[k] = "[REDACTED]"
        else:
            out[k] = redact_value(v)
    return out


def redact_text(text: str) -> str:
    """Redacts a plain-text blob (e.g. a client.py error message) the same
    way redact_structure redacts dict values - including a bare
    `Authorization: Bearer <token>` header line."""
    return _BEARER_RE.sub(r"\1[REDACTED]", _SECRET_VALUE_RE.sub(r"\1[REDACTED]", text))


MAX_OUTPUT_TAIL_CHARS = 4096


def capped_redacted_tail(text: str, limit: int = MAX_OUTPUT_TAIL_CHARS) -> str:
    """Never store or log raw provider output beyond a capped, redacted
    tail. Truncates BEFORE redacting so the regex work is bounded by
    `limit` regardless of input size (same rationale as
    lib/omes/py/jobs/audit.py's equivalent function)."""
    if len(text) <= limit:
        return redact_text(text)
    return "...[truncated]...\n" + redact_text(text[-limit:])


def _line_hash(entry_without_hash: dict[str, Any], prev_hash: str) -> str:
    canonical = json.dumps(entry_without_hash, sort_keys=True) + prev_hash
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _last_line_hash(audit_path: Path) -> str:
    if not audit_path.is_file():
        return GENESIS_HASH
    last_hash = GENESIS_HASH
    with audit_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            last_hash = obj.get("line_hash", last_hash)
    return last_hash


def append(
    root: Path | None,
    *,
    actor: str,
    job_id: str,
    event: str,
    from_state: str | None = None,
    to_state: str | None = None,
    detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Appends one redacted, hash-chained JSON line to
    <state-dir>/coolify/audit.jsonl. `job_id` carries the Coolify
    correlation_id (see module docstring)."""
    audit_path = paths.audit_log_path(root)
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(audit_path.parent, 0o700)

    entry: dict[str, Any] = {
        "ts": now_iso(),
        "actor": actor,
        "job_id": job_id,
        "event": event,
        "from": from_state,
        "to": to_state,
        "detail": detail or {},
    }
    entry = redact_structure(entry)

    prev_hash = _last_line_hash(audit_path)
    entry["prev_hash"] = prev_hash
    entry["line_hash"] = _line_hash(entry, prev_hash)

    with audit_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, sort_keys=True) + "\n")
    os.chmod(audit_path, 0o600)
    return entry


class AuditTamperedError(Exception):
    def __init__(self, line_number: int):
        super().__init__(f"coolify audit log hash chain broken at line {line_number}")
        self.line_number = line_number


def verify_chain(root: Path | None = None) -> None:
    """Raises AuditTamperedError if any line's recorded hash does not
    match what recomputing the chain produces. Silently returns if the
    log does not exist (nothing to verify yet)."""
    audit_path = paths.audit_log_path(root)
    if not audit_path.is_file():
        return
    prev_hash = GENESIS_HASH
    with audit_path.open("r", encoding="utf-8") as fh:
        for line_number, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            recorded_hash = obj.get("line_hash")
            recorded_prev = obj.get("prev_hash")
            if recorded_prev != prev_hash:
                raise AuditTamperedError(line_number)
            without_hash = {k: v for k, v in obj.items() if k != "line_hash"}
            expected = _line_hash(without_hash, prev_hash)
            if expected != recorded_hash:
                raise AuditTamperedError(line_number)
            prev_hash = recorded_hash
