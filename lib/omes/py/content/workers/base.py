"""Shared transport and filesystem-boundary helpers for platform worker
subprocesses (issue #66, docs/content-distribution.md section 6).

Deliberately self-contained (stdlib only, no relative imports, no
dependency on the rest of the `content` package): a worker is invoked as
a bare script (``python3 <worker-executable> <operation>``), never via
``python3 -m``, and must keep working regardless of PYTHONPATH/OMES_ROOT.
See `workers/__init__.py` for how this module is imported both ways.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Iterable

# Mirrors the audit-log/worker-result redaction rule in
# docs/content-distribution.md sections 6 and 8: a worker's JSON output
# must never contain a raw secret, even by accident.
SECRET_KEY_RE = re.compile(r"(TOKEN|KEY|SECRET|PASSWORD|COOKIE)", re.IGNORECASE)

# Typed failure states (issue #66 acceptance criteria). "ok" is not a
# failure state; it is the success case.
FAILURE_STATES = ("retryable", "nonretryable", "uncertain", "needs_login")


class PathBoundaryError(Exception):
    """Raised when a worker attempts to touch a path outside the explicit
    allowlist the manager passed it in the request. This is the
    filesystem permission boundary from issue #66: a worker gets only
    `processing/<job>/`, its own `sessions/<platform>/`, and
    `reports/<job>/evidence/` — nothing else, and this is enforced here
    rather than trusted to worker implementations."""


def resolve_strict(path: str | os.PathLike) -> Path:
    return Path(path).expanduser().resolve()


def enforce_path_boundary(path: str | os.PathLike, allowed_roots: Iterable[str | os.PathLike]) -> Path:
    """Resolves `path` and verifies it is inside at least one of
    `allowed_roots` (each resolved the same way, symlinks included, so a
    symlink escape cannot bypass this check). Raises PathBoundaryError
    otherwise. A worker must call this before every filesystem write (and,
    defensively, read) outside a path it was directly handed as an opaque
    input (e.g. `source_path`, which is itself validated the same way by
    the caller)."""
    resolved = resolve_strict(path)
    roots = [resolve_strict(r) for r in allowed_roots if r]
    for root in roots:
        try:
            resolved.relative_to(root)
            return resolved
        except ValueError:
            continue
    raise PathBoundaryError(
        f"path {resolved} is outside the allowed roots {[str(r) for r in roots]}"
    )


def read_request() -> dict[str, Any]:
    """Reads and parses the JSON request body from stdin. Never raises on
    malformed input - returns {} so the caller can fail closed with a
    typed error rather than crash (a crash would look like process
    failure, which the manager already treats conservatively, but an
    explicit typed response is more informative)."""
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    return payload


def redact(obj: Any) -> Any:
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if SECRET_KEY_RE.search(str(k)):
                out[k] = "[REDACTED]"
            else:
                out[k] = redact(v)
        return out
    if isinstance(obj, list):
        return [redact(v) for v in obj]
    return obj


def write_response(obj: dict[str, Any]) -> None:
    """Prints exactly one redacted JSON object on stdout, per the worker
    contract (docs/content-distribution.md section 6)."""
    print(json.dumps(redact(obj)))


def allowed_roots_from_request(payload: dict[str, Any]) -> list[str]:
    """Extracts the explicit path allowlist the manager put in the
    request under `allowed_paths` (`processing_dir`, `session_dir`,
    `evidence_dir`). A worker must never assume it may touch any path
    beyond what it was explicitly given here."""
    allowed = payload.get("allowed_paths") or {}
    roots = []
    for key in ("processing_dir", "session_dir", "evidence_dir"):
        v = allowed.get(key)
        if v:
            roots.append(v)
    return roots


def ensure_session_dir(session_dir: str | os.PathLike) -> Path:
    """Creates (if needed) a worker's own isolated browser profile
    directory and enforces mode 0700 on it (issue #66: "per-worker
    isolation: sessions/<platform>/ browser profile dir mode 0700 created
    by prepare"). Idempotent."""
    p = Path(session_dir)
    p.mkdir(parents=True, exist_ok=True)
    os.chmod(p, 0o700)
    return p


def ensure_evidence_dir(evidence_dir: str | os.PathLike) -> Path:
    p = Path(evidence_dir)
    p.mkdir(parents=True, exist_ok=True)
    return p


def typed_failure(kind: str, note: str) -> dict[str, Any]:
    """Builds the typed-failure JSON body for one of FAILURE_STATES.
    `retryable`/`nonretryable` map to the transport-level status "error"
    (with `retryable` set accordingly, so an unmodified manager/#67
    classify_worker_result still routes it correctly); `uncertain` and
    `needs_login` map to status "uncertain" (never auto-retried - routed
    to manual-review). `failure_state` always carries the specific typed
    reason so #65/#69 front ends and the report can show *why* a job is
    stuck rather than only that it is."""
    if kind not in FAILURE_STATES:
        raise ValueError(f"unknown failure kind {kind!r}; expected one of {FAILURE_STATES}")
    if kind == "retryable":
        status, retryable = "error", True
    elif kind == "nonretryable":
        status, retryable = "error", False
    else:  # uncertain, needs_login
        status, retryable = "uncertain", False
    return {
        "status": status,
        "url": None,
        "screenshot_ref": None,
        "retryable": retryable,
        "failure_state": kind,
        "note": note,
    }


def ok_result(url: str | None = None, screenshot_ref: str | None = None, note: str = "") -> dict[str, Any]:
    return {
        "status": "ok",
        "url": url,
        "screenshot_ref": screenshot_ref,
        "retryable": False,
        "failure_state": None,
        "note": note,
    }


def dispatch(operations: dict[str, Any], operation: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Runs `operations[operation](payload)`, catching PathBoundaryError
    (and any other unexpected exception) and turning it into a typed,
    non-retryable failure rather than a stack trace on stdout (the
    manager treats non-JSON stdout as "uncertain", which is safe but
    throws away the diagnostic; this keeps the diagnostic on stdout as
    structured JSON while stderr keeps the human-readable traceback for
    an operator running the worker by hand)."""
    handler = operations.get(operation)
    if handler is None:
        return typed_failure("nonretryable", f"unknown operation {operation!r}")
    try:
        return handler(payload)
    except PathBoundaryError as exc:
        print(f"path boundary violation: {exc}", file=sys.stderr)
        return typed_failure("nonretryable", f"path boundary violation: {exc}")
    except Exception as exc:  # noqa: BLE001 - deliberate: never let a worker crash unclassified
        print(f"unhandled worker error: {exc!r}", file=sys.stderr)
        return typed_failure("nonretryable", f"unhandled worker error: {exc}")


def main_with_operations(operations: dict[str, Any]) -> int:
    if len(sys.argv) < 2:
        write_response(typed_failure("nonretryable", "missing operation argument"))
        return 0
    operation = sys.argv[1]
    payload = read_request()
    result = dispatch(operations, operation, payload)
    write_response(result)
    return 0
