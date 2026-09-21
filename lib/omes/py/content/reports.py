"""Provenance, append-only audit log, and archive reports (issue #68).

See docs/content-distribution.md sections 4, 7, 8 for the schema and
security rules this module implements: the audit log and reports never
read content/sessions/ (browser profiles/cookies), and every line/report
is redacted of anything matching TOKEN|KEY|SECRET|PASSWORD|COOKIE before
it is written. Stdlib only (ADR-0012).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import jobs, paths

_SECRET_KEY_RE = re.compile(r"(TOKEN|KEY|SECRET|PASSWORD|COOKIE)", re.IGNORECASE)
_SECRET_VALUE_RE = re.compile(
    r"([A-Za-z_]*(?:TOKEN|KEY|SECRET|PASSWORD|COOKIE)[A-Za-z_]*\s*[:=]\s*)\S+",
    re.IGNORECASE,
)

GENESIS_HASH = "0" * 64


def redact_value(value: Any) -> Any:
    if isinstance(value, str):
        return _SECRET_VALUE_RE.sub(r"\1[REDACTED]", value)
    if isinstance(value, dict):
        return redact_structure(value)
    if isinstance(value, list):
        return [redact_value(v) for v in value]
    return value


def redact_structure(obj: dict[str, Any]) -> dict[str, Any]:
    """Recursively redacts any key whose name matches
    TOKEN|KEY|SECRET|PASSWORD|COOKIE, and any string value that looks like
    `SOME_TOKEN=value`/`SOME_TOKEN: value`, anywhere in a nested
    dict/list structure."""
    out: dict[str, Any] = {}
    for k, v in obj.items():
        if _SECRET_KEY_RE.search(k):
            out[k] = "[REDACTED]"
        else:
            out[k] = redact_value(v)
    return out


# ---------------------------------------------------------------------------
# Append-only, hash-chained audit log
# ---------------------------------------------------------------------------


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


def audit_append(
    root: Path | None,
    *,
    actor: str,
    job_id: str,
    from_state: str | None,
    to_state: str,
    platform: str | None = None,
    artifact_hash: str | None = None,
    url: str | None = None,
    worker_version: str | None = None,
    note: str = "",
) -> dict[str, Any]:
    """Appends one redacted, hash-chained JSON line to state/audit.jsonl.
    Never reads or references content/sessions/."""
    audit_path = paths.audit_log_path(root)
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(audit_path.parent, 0o700)

    entry = {
        "ts": jobs.now_iso(),
        "actor": actor,
        "job": job_id,
        "from": from_state,
        "to": to_state,
        "platform": platform,
        "artifact_hash": artifact_hash,
        "url": url,
        "worker_version": worker_version,
        "note": note,
    }
    entry = redact_structure(entry)

    prev_hash = _last_line_hash(audit_path)
    entry["prev_hash"] = prev_hash
    entry["line_hash"] = _line_hash(entry, prev_hash)

    # Append-only: open in append mode, one JSON object per line. A
    # concurrent-writer race is out of scope for the MVP (single
    # operator/manager process), but the hash chain still lets a later
    # `omes content export`/audit-verify pass detect any rewrite.
    with audit_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, sort_keys=True) + "\n")
    os.chmod(audit_path, 0o600)
    return entry


def audit_append_for_job(root: Path | None, record: dict[str, Any], actor: str) -> dict[str, Any]:
    """Convenience wrapper: builds an audit entry from a job record's most
    recent history entry."""
    last = record["history"][-1]
    return audit_append(
        root,
        actor=actor,
        job_id=record["job_id"],
        from_state=last.get("from"),
        to_state=last.get("to"),
        platform=record.get("platform"),
        artifact_hash=record["source"]["sha256"],
        url=record["publish"].get("resulting_url"),
        worker_version=record["publish"].get("worker_version"),
        note=last.get("note", ""),
    )


class AuditTamperedError(Exception):
    def __init__(self, line_number: int):
        super().__init__(f"audit log hash chain broken at line {line_number}")
        self.line_number = line_number


def audit_verify(root: Path | None) -> tuple[bool, int | None]:
    """Verifies the audit log's hash chain. Returns (True, None) if
    intact, or (False, line_number) for the first line whose line_hash
    doesn't match its own content+prev_hash (append-only violation:
    covers both a rewritten line and an inserted/removed one, since every
    line commits to its predecessor's hash)."""
    audit_path = paths.audit_log_path(root)
    if not audit_path.is_file():
        return True, None
    prev_hash = GENESIS_HASH
    with audit_path.open("r", encoding="utf-8") as fh:
        for i, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                return False, i
            claimed_hash = obj.pop("line_hash", None)
            claimed_prev = obj.get("prev_hash")
            if claimed_prev != prev_hash:
                return False, i
            recomputed = _line_hash(obj, prev_hash)
            if recomputed != claimed_hash:
                return False, i
            prev_hash = claimed_hash
    return True, None


# ---------------------------------------------------------------------------
# Reports (JSON + Markdown), never overwritten
# ---------------------------------------------------------------------------


def _build_report_dict(record: dict[str, Any]) -> dict[str, Any]:
    report = {
        "job_id": record["job_id"],
        "state": record["state"],
        "source": {
            "sha256": record["source"]["sha256"],
            "size_bytes": record["source"]["size_bytes"],
            "mime_guess": record["source"]["mime_guess"],
            "duplicate_of": record["source"].get("duplicate_of"),
        },
        "platform": record.get("platform"),
        "caption": record["plan"].get("caption"),
        "targets": record["plan"].get("targets", []),
        "approvals": record.get("approvals", []),
        "publish": {
            "attempts": record["publish"]["attempts"],
            "worker_version": record["publish"].get("worker_version"),
            "resulting_url": record["publish"].get("resulting_url"),
            "last_result": record["publish"].get("last_result"),
        },
        "created_at": record["created_at"],
        "updated_at": record["updated_at"],
        "history": record["history"],
    }
    return redact_structure(report)


def _report_markdown(report: dict[str, Any]) -> str:
    lines = [
        f"# Content job report: {report['job_id']}",
        "",
        f"- **State:** {report['state']}",
        f"- **Artifact sha256:** `{report['source']['sha256']}`",
        f"- **Platform:** {report.get('platform') or '(none)'}",
        f"- **Resulting URL:** {report['publish'].get('resulting_url') or '(none)'}",
        f"- **Created:** {report['created_at']}",
        f"- **Updated:** {report['updated_at']}",
        "",
        "## History",
        "",
    ]
    for h in report["history"]:
        lines.append(f"- {h['ts']}: `{h['from']}` -> `{h['to']}` (actor: {h['actor']}) {h.get('note', '')}")
    lines.append("")
    lines.append("## Approvals")
    lines.append("")
    for a in report["approvals"]:
        lines.append(f"- {a['approved_at']}: {a['decision']} by {a['actor']} via {a['channel']} (expires {a['expires_at']})")
    lines.append("")
    return "\n".join(lines)


def _next_report_paths(job_reports_dir: Path) -> tuple[Path, Path, bool]:
    """Returns (json_path, md_path, is_first). report.json/report.md are
    the first version; a regeneration finds the next unused report-<n>
    suffix (n starting at 2) rather than overwriting anything."""
    json_path = job_reports_dir / "report.json"
    md_path = job_reports_dir / "report.md"
    if not json_path.exists() and not md_path.exists():
        return json_path, md_path, True
    n = 2
    while (job_reports_dir / f"report-{n}.json").exists() or (job_reports_dir / f"report-{n}.md").exists():
        n += 1
    return job_reports_dir / f"report-{n}.json", job_reports_dir / f"report-{n}.md", False


def generate_report(record: dict[str, Any], root: Path | None = None) -> dict[str, str]:
    root = root or paths.content_root()
    job_reports_dir = paths.reports_dir(root) / record["job_id"]
    job_reports_dir.mkdir(parents=True, exist_ok=True)

    report = _build_report_dict(record)
    json_path, md_path, _is_first = _next_report_paths(job_reports_dir)

    jobs.atomic_write_json(json_path, report)
    os.chmod(json_path, 0o600)
    md_path.write_text(_report_markdown(report), encoding="utf-8")
    os.chmod(md_path, 0o600)
    return {"json": str(json_path), "md": str(md_path)}


def latest_report_json(record: dict[str, Any], root: Path | None = None) -> Path | None:
    root = root or paths.content_root()
    job_reports_dir = paths.reports_dir(root) / record["job_id"]
    candidates = sorted(job_reports_dir.glob("report*.json"))
    return candidates[-1] if candidates else None


# ---------------------------------------------------------------------------
# Archive
# ---------------------------------------------------------------------------

_ARCHIVE_DIR_FOR_STATE = {
    "succeeded": "uploaded",
    "failed": "failed",
    "cancelled": "review",
    "manual-review": "review",
}


def archive_job(record: dict[str, Any], root: Path | None = None, actor: str = "system") -> Path | None:
    """Moves a terminal job's processing/ directory (evidence) into
    uploaded/<job-id>/, failed/<job-id>/, or review/<job-id>/ depending on
    outcome, then transitions the job to `archived`. Idempotent: if the
    destination already exists, evidence is left as-is (never
    overwritten) and only the state transition (if still needed) runs."""
    root = root or paths.content_root()
    if record["state"] not in ("succeeded", "failed", "cancelled", "manual-review"):
        raise jobs.InvalidTransitionError(record["state"], "archived")

    dest_top = _ARCHIVE_DIR_FOR_STATE[record["state"]]
    dest_dir = root / dest_top / record["job_id"]
    src_dir = paths.job_processing_dir(record["job_id"], root)

    if src_dir.is_dir() and not dest_dir.exists():
        dest_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src_dir), str(dest_dir))

    jobs.apply_transition(record, "archived", actor=actor, note=f"archived to {dest_top}/")
    return dest_dir if dest_dir.exists() else None


# ---------------------------------------------------------------------------
# Export (redacted; never touches sessions/)
# ---------------------------------------------------------------------------


def export_reports(root: Path | None, since: str, out_dir: Path) -> dict[str, Any]:
    """Copies reports/ (already redacted at generation time) and the
    portion of the audit log at/after `since` (ISO date, e.g.
    "2026-09-01") into `out_dir`. NEVER reads content/sessions/."""
    root = root or paths.content_root()
    out_dir.mkdir(parents=True, exist_ok=True)

    reports_src = paths.reports_dir(root)
    reports_dst = out_dir / "reports"
    exported_jobs = []
    if reports_src.is_dir():
        for job_dir in sorted(reports_src.iterdir()):
            if not job_dir.is_dir():
                continue
            shutil.copytree(job_dir, reports_dst / job_dir.name, dirs_exist_ok=True)
            exported_jobs.append(job_dir.name)

    since_dt = datetime.strptime(since, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    audit_src = paths.audit_log_path(root)
    audit_lines = []
    if audit_src.is_file():
        with audit_src.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts = datetime.strptime(obj["ts"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                if ts >= since_dt:
                    audit_lines.append(line)
    (out_dir / "audit.jsonl").write_text("\n".join(audit_lines) + ("\n" if audit_lines else ""), encoding="utf-8")

    return {"exported_jobs": exported_jobs, "audit_lines": len(audit_lines), "out_dir": str(out_dir)}


def export_awcms_v1(root: Path | None, since: str, out_dir: Path) -> dict[str, Any]:
    """Exports content jobs, media metadata, and audit logs to the AWCMS
    Control Center migration manifest format (schema: awcms-content-v1,
    ADR-0024, issue #179).

    Safety and Security Guarantees:
    - Strictly excludes content/sessions/ (zero cookies, tokens, or browser profiles).
    - Recursively redacts all secret patterns across job history and reports.
    - Retains non-secret job state, SHA-256 artifact hashes, and publication evidence.
    - Preserves duplicate publish protection (dedup references and published target records).
    - Flags uncertain or partial publications with requires_manual_review: true.
    - Completely non-destructive: existing job records, media, and audit trails remain untouched.
    """
    root = root or paths.content_root()
    out_dir.mkdir(parents=True, exist_ok=True)

    since_dt = datetime.strptime(since, "%Y-%m-%d").replace(tzinfo=timezone.utc)

    # 1. Export legacy reports and filtered audit log into out_dir
    legacy_res = export_reports(root, since, out_dir)

    # 2. Gather audit log lines indexed by job_id
    audit_src = paths.audit_log_path(root)
    job_audit_map: dict[str, list[dict[str, Any]]] = {}
    if audit_src.is_file():
        with audit_src.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                jid = entry.get("job") or entry.get("job_id")
                if jid:
                    job_audit_map.setdefault(jid, []).append(redact_structure(entry))

    # 3. Process jobs
    all_jobs = jobs.list_jobs(root)
    exported_records: list[dict[str, Any]] = []

    for record in all_jobs:
        ts_str = record.get("updated_at") or record.get("created_at")
        if ts_str:
            try:
                rec_dt = datetime.strptime(ts_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                if rec_dt < since_dt:
                    continue
            except ValueError:
                pass

        redacted = redact_structure(record)
        job_id = redacted["job_id"]
        state = redacted.get("state", "unknown")

        publish_info = redacted.get("publish", {})
        attempts = publish_info.get("attempts", 0)
        last_result = publish_info.get("last_result")

        requires_manual_review = False
        if state in ("manual-review", "retryable-failure", "failed", "cancelled"):
            requires_manual_review = True
        elif state == "succeeded":
            requires_manual_review = False
        elif attempts > 0 and last_result != "ok":
            requires_manual_review = True
        elif state in ("queued", "planning", "approval-required"):
            requires_manual_review = True

        publications = []
        resulting_url = publish_info.get("resulting_url")
        if resulting_url:
            publications.append({
                "platform": redacted.get("platform") or "unknown",
                "url": resulting_url,
                "verified": state in ("succeeded", "archived"),
                "worker_version": publish_info.get("worker_version"),
            })

        source_info = redacted.get("source", {})
        artifact_hash = source_info.get("sha256")
        original_path = source_info.get("original_path", "")
        original_filename = Path(original_path).name if original_path else ""

        exported_item = {
            "job_id": job_id,
            "state": state,
            "artifact_sha256": artifact_hash,
            "size_bytes": source_info.get("size_bytes"),
            "mime_guess": source_info.get("mime_guess"),
            "original_filename": original_filename,
            "created_at": redacted.get("created_at"),
            "updated_at": redacted.get("updated_at"),
            "duplicate_of": redacted.get("duplicate_of") or source_info.get("duplicate_of"),
            "plan": {
                "caption": redacted.get("plan", {}).get("caption", ""),
                "targets": redacted.get("plan", {}).get("targets", []),
            },
            "approvals": redacted.get("approvals", []),
            "publications": publications,
            "requires_manual_review": requires_manual_review,
            "audit_events": job_audit_map.get(job_id, []),
        }
        exported_records.append(exported_item)

    manifest = {
        "schema_version": "awcms-content-v1",
        "source_system": "omes-content-v1",
        "adr_reference": "ADR-0024",
        "exported_at": jobs.now_iso(),
        "since": since,
        "total_jobs": len(exported_records),
        "requires_manual_review_count": sum(1 for j in exported_records if j["requires_manual_review"]),
        "sessions_excluded": True,
        "secrets_redacted": True,
        "jobs": exported_records,
    }

    manifest_path = out_dir / "awcms-content-migration-manifest.json"
    jobs.atomic_write_json(manifest_path, manifest)
    os.chmod(manifest_path, 0o600)

    return {
        "format": "awcms-v1",
        "exported_jobs": [j["job_id"] for j in exported_records],
        "audit_lines": legacy_res["audit_lines"],
        "out_dir": str(out_dir),
        "manifest": str(manifest_path),
        "manifest_schema": "awcms-content-v1",
        "sessions_untouched": True,
        "requires_manual_review_count": manifest["requires_manual_review_count"],
    }



# ---------------------------------------------------------------------------
# Prune (retention; never touches sessions/ or audit.jsonl)
# ---------------------------------------------------------------------------

_PRUNABLE_TOP_DIRS = ("uploaded", "failed", "review")


def prune(root: Path | None, older_than_days: int, dry_run: bool = True) -> dict[str, Any]:
    """Deletes archived media/report directories for jobs whose
    `updated_at` is older than `older_than_days`. Only ever touches
    `uploaded/`, `failed/`, `review/`, and each such job's `reports/`
    directory - never `sessions/`, never `state/audit.jsonl`, and never a
    job that is not yet archived."""
    root = root or paths.content_root()
    cutoff = datetime.now(timezone.utc) - timedelta(days=older_than_days)
    to_delete: list[str] = []

    for record in jobs.list_jobs(root):
        if record["state"] != "archived":
            continue
        updated = datetime.strptime(record["updated_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        if updated >= cutoff:
            continue
        to_delete.append(record["job_id"])

    deleted_paths: list[str] = []
    if not dry_run:
        for job_id in to_delete:
            for top in _PRUNABLE_TOP_DIRS:
                candidate = root / top / job_id
                if candidate.is_dir():
                    shutil.rmtree(candidate)
                    deleted_paths.append(str(candidate))
            report_dir = paths.reports_dir(root) / job_id
            if report_dir.is_dir():
                shutil.rmtree(report_dir)
                deleted_paths.append(str(report_dir))

    return {
        "job_ids": to_delete,
        "deleted_paths": deleted_paths,
        "dry_run": dry_run,
        "sessions_untouched": True,
        "audit_untouched": True,
    }
