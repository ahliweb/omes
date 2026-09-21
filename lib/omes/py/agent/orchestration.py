"""lib/omes/py/agent/orchestration.py - Hermes subagent process tree and
delegated-task orchestration projection (ADR-0028, issue #183).

Pure Python standard library only (ADR-0012). Ingests read-only observer events
emitted by `hermes.observer.v1`, normalizes and sanitizes payloads, reconstructs
nested parent-child hierarchies, and generates validated tree projections for the
OMES Control Center Screen 6.
"""
from __future__ import annotations

import html
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jobs import paths, schema as schema_mod

SCHEMA_VERSION = "1.0.0"
HERMES_BASELINE = "v2026.9.14"
DEFAULT_STALE_THRESHOLD_SECONDS = 300

ACTIVE_STATES = frozenset({"PENDING", "STARTING", "RUNNING"})
TERMINAL_SUCCESS_STATES = frozenset({"SUCCEEDED"})
TERMINAL_FAILURE_STATES = frozenset({"FAILED", "INTERRUPTED", "CANCELLED"})
TERMINAL_STATES = TERMINAL_SUCCESS_STATES | TERMINAL_FAILURE_STATES

_REPO_ROOT = Path(__file__).resolve().parents[4]
_SCHEMAS_DIR = _REPO_ROOT / "contracts" / "control-center" / "v1"


class OrchestrationError(Exception):
    """Base exception for orchestration projection errors."""


class SessionNotFoundError(OrchestrationError):
    """Raised when a requested session observation document does not exist."""


class TenantScopeError(OrchestrationError):
    """Raised when a request violates tenant scope boundaries."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(ts_str: str) -> datetime:
    try:
        # Normalize trailing 'Z' to UTC offset
        cleaned = ts_str.replace("Z", "+00:00")
        return datetime.fromisoformat(cleaned)
    except Exception:
        return datetime.now(timezone.utc)


def _load_schema(schema_name: str) -> dict[str, Any]:
    path = _SCHEMAS_DIR / f"{schema_name}.schema.json"
    if not path.is_file():
        raise OrchestrationError(f"schema file not found: {path}")
    data = schema_mod.load_json(path)
    schema_mod.validate_schema(data)
    return data


def _orchestration_dir(state_root: Path | None = None) -> Path:
    base = state_root or paths.state_root()
    d = base / "agent" / "orchestration"
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(d, 0o700)
    return d


def _session_file(session_id: str, state_root: Path | None = None) -> Path:
    safe_session = re.sub(r"[^A-Za-z0-9_.:-]", "_", session_id)
    return _orchestration_dir(state_root) / f"session-{safe_session}.json"


def sanitize_text(val: str | None, max_len: int = 512) -> str:
    """Sanitizes text fields to prevent XSS and secret leakage."""
    if not val:
        return ""
    # Strip dangerous HTML and escape entities
    escaped = html.escape(val[:max_len], quote=True)
    # Redact secret patterns if present in text
    secret_patterns = re.compile(
        r"(ghp_[A-Za-z0-9_]{30,}|gho_[A-Za-z0-9_]{30,}|eyJ[A-Za-z0-9_-]{20,}|ssh-ed25519\s+[A-Za-z0-9+/=]{30,})",
        re.IGNORECASE,
    )
    return secret_patterns.sub("[REDACTED]", escaped)


def atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".tmp-orch-", suffix=".json", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, sort_keys=True)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
        os.chmod(path, 0o600)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def ingest_event(
    event: dict[str, Any],
    state_root: Path | None = None,
) -> dict[str, Any]:
    """Ingests a read-only observer event emitted by hermes.observer.v1.

    Validates schema, sanitizes fields, merges state idempotently,
    and updates the session observation document on disk.
    """
    event_schema = _load_schema("hermes-orchestration-event")
    errors = schema_mod.validate(event, event_schema)
    if errors:
        raise OrchestrationError(f"invalid orchestration event: {'; '.join(errors)}")

    # Check secret scanner
    secret_errors = schema_mod.scan_for_raw_secrets(event)
    if secret_errors:
        raise OrchestrationError(f"secret detected in orchestration event: {'; '.join(secret_errors)}")

    session_id = event["session_id"]
    subagent_id = event["subagent_id"]
    now_str = event["timestamp"]

    doc_path = _session_file(session_id, state_root)
    if doc_path.is_file():
        try:
            doc = json.loads(doc_path.read_text(encoding="utf-8"))
        except Exception:
            doc = {}
    else:
        doc = {}

    if not doc:
        doc = {
            "schema_version": SCHEMA_VERSION,
            "tenant_id": event["tenant_id"],
            "server_id": event["server_id"],
            "session_id": session_id,
            "root_subagent_id": subagent_id if not event.get("parent_subagent_id") else None,
            "created_at": now_str,
            "updated_at": now_str,
            "nodes": {},
        }

    # Verify tenant consistency
    if doc.get("tenant_id") != event["tenant_id"]:
        raise TenantScopeError(
            f"event tenant '{event['tenant_id']}' mismatches session tenant '{doc.get('tenant_id')}'"
        )

    nodes = doc.setdefault("nodes", {})
    node = nodes.setdefault(subagent_id, {
        "subagent_id": subagent_id,
        "parent_subagent_id": event.get("parent_subagent_id"),
        "role": sanitize_text(event.get("role", "subagent"), max_len=64),
        "goal": sanitize_text(event.get("goal", ""), max_len=512),
        "state": "PENDING",
        "started_at": now_str,
        "completed_at": None,
        "duration_seconds": 0.0,
        "active_tool": sanitize_text(event.get("active_tool", ""), max_len=64),
        "step_count": 0,
        "summary": sanitize_text(event.get("summary", ""), max_len=512),
        "last_observed_at": now_str,
    })

    # Update role and goal if provided and not yet populated
    if event.get("role"):
        node["role"] = sanitize_text(event["role"], max_len=64)
    if event.get("goal"):
        node["goal"] = sanitize_text(event["goal"], max_len=512)
    if event.get("parent_subagent_id") is not None:
        node["parent_subagent_id"] = event["parent_subagent_id"]

    # Root subagent resolution
    if not node.get("parent_subagent_id") and not doc.get("root_subagent_id"):
        doc["root_subagent_id"] = subagent_id

    # State update handling (tolerate out-of-order completion)
    new_state = event["state"]
    current_state = node.get("state", "PENDING")

    # If already terminal, do not revert to active on late-arriving start event
    if current_state in TERMINAL_STATES and new_state in ACTIVE_STATES:
        # Keep terminal state, but update metadata if missing
        pass
    else:
        node["state"] = new_state

    # Tool and step update
    if event.get("active_tool"):
        node["active_tool"] = sanitize_text(event["active_tool"], max_len=64)
    if event.get("step_number") is not None:
        node["step_count"] = max(node.get("step_count", 0), int(event["step_number"]))
    if event.get("summary"):
        node["summary"] = sanitize_text(event["summary"], max_len=512)

    # Timing
    if event["event_type"] == "subagent_start":
        node["started_at"] = now_str
    elif event["event_type"] == "subagent_stop" or new_state in TERMINAL_STATES:
        if not node.get("completed_at"):
            node["completed_at"] = now_str

    if node.get("completed_at") and node.get("started_at"):
        t_start = _parse_iso(node["started_at"])
        t_end = _parse_iso(node["completed_at"])
        node["duration_seconds"] = max(0.0, (t_end - t_start).total_seconds())

    node["last_observed_at"] = now_str
    doc["updated_at"] = now_str

    atomic_write_json(doc_path, doc)
    return node


def build_tree(
    session_id: str,
    tenant_id: str,
    server_id: str,
    state_root: Path | None = None,
    now: datetime | None = None,
    stale_threshold_seconds: int = DEFAULT_STALE_THRESHOLD_SECONDS,
) -> dict[str, Any]:
    """Reconstructs the hierarchical process tree projection for a session.

    Validates tenant scope, calculates duration and staleness freshness,
    and returns a payload conforming to hermes-orchestration-tree.schema.json.
    """
    doc_path = _session_file(session_id, state_root)
    if not doc_path.is_file():
        raise SessionNotFoundError(f"session '{session_id}' not found")

    doc = json.loads(doc_path.read_text(encoding="utf-8"))

    if doc.get("tenant_id") != tenant_id:
        raise TenantScopeError(
            f"session tenant '{doc.get('tenant_id')}' mismatches requested tenant '{tenant_id}'"
        )
    if doc.get("server_id") != server_id:
        raise TenantScopeError(
            f"session server '{doc.get('server_id')}' mismatches requested server '{server_id}'"
        )

    current_time = now or datetime.now(timezone.utc)
    nodes_map: dict[str, dict[str, Any]] = doc.get("nodes", {})

    # Compute children lists
    children_map: dict[str, list[str]] = {nid: [] for nid in nodes_map}
    for nid, node in nodes_map.items():
        pid = node.get("parent_subagent_id")
        if pid and pid in children_map:
            children_map[pid].append(nid)

    # Resolve root
    root_id = doc.get("root_subagent_id")
    if not root_id or root_id not in nodes_map:
        # Fallback: pick node without parent, or first node
        candidates = [nid for nid, n in nodes_map.items() if not n.get("parent_subagent_id")]
        root_id = candidates[0] if candidates else next(iter(nodes_map), "root_unknown")

    active_count = 0
    completed_count = 0
    failed_count = 0
    is_any_stale = False

    projected_nodes: list[dict[str, Any]] = []

    for nid in sorted(nodes_map.keys()):
        raw = nodes_map[nid]
        state = raw.get("state", "UNKNOWN")

        # Staleness calculation for active nodes
        last_obs = _parse_iso(raw.get("last_observed_at", raw.get("started_at", _now_iso())))
        elapsed_since_obs = (current_time - last_obs).total_seconds()

        if state in ACTIVE_STATES:
            if elapsed_since_obs > stale_threshold_seconds:
                is_any_stale = True
            active_count += 1
            # In-progress duration
            t_start = _parse_iso(raw.get("started_at", _now_iso()))
            duration = max(0.0, (current_time - t_start).total_seconds())
        elif state in TERMINAL_SUCCESS_STATES:
            completed_count += 1
            duration = float(raw.get("duration_seconds", 0.0))
        else:
            failed_count += 1
            duration = float(raw.get("duration_seconds", 0.0))

        projected_nodes.append({
            "subagent_id": nid,
            "parent_subagent_id": raw.get("parent_subagent_id"),
            "role": raw.get("role", "subagent"),
            "goal": raw.get("goal", ""),
            "state": state,
            "started_at": raw.get("started_at", _now_iso()),
            "completed_at": raw.get("completed_at"),
            "duration_seconds": duration,
            "active_tool": raw.get("active_tool", ""),
            "step_count": int(raw.get("step_count", 0)),
            "summary": raw.get("summary", ""),
            "children": sorted(children_map.get(nid, [])),
        })

    # Freshness
    if not projected_nodes:
        freshness = "unknown"
    elif is_any_stale:
        freshness = "stale"
    else:
        freshness = "live"

    tree = {
        "schema_version": SCHEMA_VERSION,
        "tenant_id": tenant_id,
        "server_id": server_id,
        "session_id": session_id,
        "root_subagent_id": root_id,
        "generated_at": _now_iso(),
        "freshness": freshness,
        "active_count": active_count,
        "completed_count": completed_count,
        "failed_count": failed_count,
        "nodes": projected_nodes,
    }

    # Validate against tree schema
    tree_schema = _load_schema("hermes-orchestration-tree")
    errors = schema_mod.validate(tree, tree_schema)
    if errors:
        raise OrchestrationError(f"projected tree schema violation: {'; '.join(errors)}")

    return tree


def list_orchestration_sessions(
    tenant_id: str,
    server_id: str | None = None,
    state_root: Path | None = None,
) -> list[dict[str, Any]]:
    """Lists summary records of observed orchestration sessions for a tenant."""
    orch_dir = _orchestration_dir(state_root)
    results: list[dict[str, Any]] = []

    for p in sorted(orch_dir.glob("session-*.json")):
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
            if doc.get("tenant_id") != tenant_id:
                continue
            if server_id and doc.get("server_id") != server_id:
                continue
            nodes = doc.get("nodes", {})
            results.append({
                "session_id": doc.get("session_id"),
                "server_id": doc.get("server_id"),
                "tenant_id": doc.get("tenant_id"),
                "node_count": len(nodes),
                "created_at": doc.get("created_at"),
                "updated_at": doc.get("updated_at"),
            })
        except Exception:
            continue

    return results
