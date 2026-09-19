#!/usr/bin/env python3
"""omes content <subcommand> - CLI entry point for the content distribution
workflow (invoked by lib/omes/cmd/content.sh). Stdlib only (ADR-0012).

Exit codes follow docs/cli.md's stable exit-code contract (0 ok, 1 general
error, 2 usage error); this extension does not currently need the more
specific codes bin/omes uses for module apply/verify/rollback.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import inbox, jobs, paths, reports


def _default_approval_ttl_seconds() -> int:
    raw = os.environ.get("OMES_CONTENT_APPROVAL_TTL_SECONDS")
    if raw:
        try:
            return int(raw)
        except ValueError:
            pass
    return jobs.DEFAULT_APPROVAL_TTL_SECONDS


def _confirm(args: argparse.Namespace, prompt: str) -> bool:
    if getattr(args, "yes", False):
        return True
    if sys.stdin.isatty():
        answer = input(f"{prompt} [y/N] ").strip().lower()
        return answer in ("y", "yes")
    print("error: this is a mutating operation; pass --yes to confirm", file=sys.stderr)
    return False

EX_OK = 0
EX_ERROR = 1
EX_USAGE = 2


def _print_json(obj) -> None:
    print(json.dumps(obj, indent=2, sort_keys=True))


def cmd_scan(args: argparse.Namespace) -> int:
    root = paths.ensure_layout()
    try:
        with inbox.scan_lock(root):
            result = inbox.scan(root, settle_seconds=args.settle_seconds)
    except inbox.LockContentionError as exc:
        if args.json:
            _print_json({"error": str(exc)})
        else:
            print(f"error: {exc}", file=sys.stderr)
        return EX_ERROR

    for job_id in result["created"] + result["duplicates"]:
        try:
            record = jobs.load_job(job_id, root)
            reports.audit_append_for_job(root, record, actor="system")
        except jobs.ContentJobsError:
            pass

    if args.json:
        _print_json(result)
    else:
        print(f"created: {len(result['created'])}")
        for job_id in result["created"]:
            print(f"  + {job_id}")
        print(f"duplicates: {len(result['duplicates'])}")
        for job_id in result["duplicates"]:
            print(f"  = {job_id}")
        print(f"pending (not yet settled): {len(result['pending'])}")
    return EX_OK


def cmd_rescan(args: argparse.Namespace) -> int:
    root = paths.ensure_layout()
    result = inbox.rescan(root)
    if args.json:
        _print_json(result)
    else:
        print(f"checked: {len(result['checked'])}")
        print(f"mismatches: {len(result['mismatches'])}")
        for job_id in result["mismatches"]:
            print(f"  ! {job_id}")
    return EX_OK if not result["mismatches"] else EX_ERROR


def cmd_list(args: argparse.Namespace) -> int:
    root = paths.ensure_layout()
    result = inbox.list_jobs_summary(root, state=args.state)
    if args.json:
        _print_json({"jobs": result})
    else:
        for j in result:
            dup = f" (duplicate_of={j['duplicate_of']})" if j["duplicate_of"] else ""
            print(f"{j['job_id']}  {j['state']}{dup}")
    return EX_OK


def _persist(record: dict, root: Path, actor: str) -> None:
    """Audits the job's latest transition, archives+reports it if it just
    reached a terminal outcome (succeeded/failed/cancelled), then saves."""
    reports.audit_append_for_job(root, record, actor)
    if record["state"] in ("succeeded", "failed", "cancelled"):
        reports.archive_job(record, root, actor="system")
        reports.audit_append_for_job(root, record, actor="system")
        reports.generate_report(record, root)
    jobs.save_job(record, root)


def cmd_approve(args: argparse.Namespace) -> int:
    root = paths.ensure_layout()
    try:
        record = jobs.load_job(args.job_id, root)
        jobs.approve_job(record, actor=args.actor, ttl_seconds=args.ttl_seconds)
        _persist(record, root, args.actor)
    except jobs.ContentJobsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EX_ERROR
    if args.json:
        _print_json({"job_id": record["job_id"], "state": record["state"]})
    else:
        print(f"{record['job_id']}: approved by {args.actor}")
    return EX_OK


def cmd_reject(args: argparse.Namespace) -> int:
    root = paths.ensure_layout()
    try:
        record = jobs.load_job(args.job_id, root)
        jobs.reject_job(record, actor=args.actor)
        _persist(record, root, args.actor)
    except jobs.ContentJobsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EX_ERROR
    if args.json:
        _print_json({"job_id": record["job_id"], "state": record["state"]})
    else:
        print(f"{record['job_id']}: rejected by {args.actor}")
    return EX_OK


def cmd_retry(args: argparse.Namespace) -> int:
    if not _confirm(args, f"Retry job {args.job_id}?"):
        return EX_ERROR
    root = paths.ensure_layout()
    try:
        record = jobs.load_job(args.job_id, root)
        jobs.retry_job(record, actor=args.actor, force=args.force)
        _persist(record, root, args.actor)
    except jobs.ContentJobsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EX_ERROR
    if args.json:
        _print_json({"job_id": record["job_id"], "state": record["state"]})
    else:
        print(f"{record['job_id']}: retry recorded by {args.actor} -> {record['state']}")
    return EX_OK


def cmd_cancel(args: argparse.Namespace) -> int:
    if not _confirm(args, f"Cancel job {args.job_id}?"):
        return EX_ERROR
    root = paths.ensure_layout()
    try:
        record = jobs.load_job(args.job_id, root)
        jobs.cancel_job(record, actor=args.actor)
        _persist(record, root, args.actor)
    except jobs.ContentJobsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EX_ERROR
    if args.json:
        _print_json({"job_id": record["job_id"], "state": record["state"]})
    else:
        print(f"{record['job_id']}: cancelled by {args.actor}")
    return EX_OK


def cmd_resume(args: argparse.Namespace) -> int:
    """Re-runs verify (never publish) for every job stuck in `publishing`
    or `verifying`, e.g. after a process restart. This is the load-bearing
    rule against duplicate publication (docs/content-distribution.md
    section 5.3)."""
    root = paths.ensure_layout()
    resumed = []
    skipped = []
    for record in jobs.list_jobs(root):
        if record["state"] not in ("publishing", "verifying"):
            continue
        worker_executable = record["publish"].get("worker_executable")
        if not worker_executable:
            jobs.apply_transition(
                record, "manual-review", actor="system", note="resume: no worker recorded for this job"
            )
            _persist(record, root, "system")
            skipped.append(record["job_id"])
            continue
        if record["state"] == "publishing":
            # A crash mid-publish is uncertain by definition: we do not
            # know whether the worker's publish call landed. Move to
            # manual-review rather than guessing either way.
            jobs.apply_transition(
                record, "manual-review", actor="system", note="resume: publish outcome unknown after restart"
            )
            _persist(record, root, "system")
            skipped.append(record["job_id"])
            continue
        # verifying: safe to re-run verify only.
        jobs.verify_job(record, worker_executable)
        _persist(record, root, "system")
        resumed.append(record["job_id"])
    result = {"resumed": resumed, "skipped_to_manual_review": skipped}
    if args.json:
        _print_json(result)
    else:
        print(f"resumed (re-verified): {len(resumed)}")
        print(f"skipped to manual-review: {len(skipped)}")
    return EX_OK


def cmd_reconcile(args: argparse.Namespace) -> int:
    root = paths.ensure_layout()
    needs_review = []
    for record in jobs.list_jobs(root):
        reasons = []
        if record["state"] == "manual-review":
            reasons.append("in manual-review")
        if record["state"] == "retryable-failure":
            attempts = record["publish"]["attempts"]
            max_attempts = record["publish"].get("max_attempts", jobs.DEFAULT_MAX_ATTEMPTS)
            if attempts >= max_attempts:
                reasons.append(f"exhausted max_attempts ({attempts}/{max_attempts})")
            else:
                delay = jobs.compute_backoff_seconds(attempts)
                elapsed = jobs.seconds_since_last_history_entry(record)
                if elapsed >= delay:
                    reasons.append("retry backoff elapsed; ready for `omes content retry`")
        if record["state"] == "approval-required":
            valid, reason = jobs.is_approval_valid(record) if record["approvals"] else (False, "not yet approved")
            if not valid:
                reasons.append(reason)
        if reasons:
            needs_review.append({"job_id": record["job_id"], "state": record["state"], "reasons": reasons})
    if args.json:
        _print_json({"jobs": needs_review})
    else:
        for j in needs_review:
            print(f"{j['job_id']} ({j['state']}): {'; '.join(j['reasons'])}")
    return EX_OK


def cmd_report(args: argparse.Namespace) -> int:
    root = paths.ensure_layout()
    try:
        record = jobs.load_job(args.job_id, root)
    except jobs.ContentJobsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EX_ERROR

    if record["state"] in ("succeeded", "failed", "cancelled"):
        reports.archive_job(record, root, actor="system")
        reports.audit_append_for_job(root, record, actor="system")
        jobs.save_job(record, root)

    report_path = reports.latest_report_json(record, root)
    if report_path is None:
        paths_written = reports.generate_report(record, root)
        report_path = Path(paths_written["json"])

    if args.md:
        md_path = report_path.with_suffix(".md")
        print(md_path.read_text(encoding="utf-8"))
    elif args.json:
        print(report_path.read_text(encoding="utf-8"))
    else:
        print(str(report_path))
    return EX_OK


def cmd_export(args: argparse.Namespace) -> int:
    root = paths.ensure_layout()
    result = reports.export_reports(root, args.since, Path(args.out))
    if args.json:
        _print_json(result)
    else:
        print(f"exported {len(result['exported_jobs'])} job report(s) and {result['audit_lines']} audit line(s) to {result['out_dir']}")
    return EX_OK


def cmd_prune(args: argparse.Namespace) -> int:
    root = paths.ensure_layout()
    dry_run = args.dry_run
    if not dry_run and not _confirm(args, f"Permanently delete archived jobs older than {args.older_than} day(s)?"):
        return EX_ERROR
    result = reports.prune(root, args.older_than, dry_run=dry_run)
    if args.json:
        _print_json(result)
    else:
        verb = "would delete" if dry_run else "deleted"
        print(f"{verb} {len(result['job_ids'])} job(s): {', '.join(result['job_ids']) or '(none)'}")
    return EX_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="omes content")
    sub = parser.add_subparsers(dest="subcommand", required=True)

    p_scan = sub.add_parser("scan", help="detect new inbox files and create jobs")
    p_scan.add_argument("--json", action="store_true")
    p_scan.add_argument("--settle-seconds", type=float, default=inbox.DEFAULT_SETTLE_SECONDS)
    p_scan.set_defaults(func=cmd_scan)

    p_rescan = sub.add_parser("rescan", help="re-hash processing/failed for consistency")
    p_rescan.add_argument("--json", action="store_true")
    p_rescan.set_defaults(func=cmd_rescan)

    p_list = sub.add_parser("list", help="list jobs")
    p_list.add_argument("--json", action="store_true")
    p_list.add_argument("--state", choices=jobs.STATES, default=None)
    p_list.set_defaults(func=cmd_list)

    p_approve = sub.add_parser("approve", help="record an approval decision")
    p_approve.add_argument("job_id")
    p_approve.add_argument("--actor", required=True)
    p_approve.add_argument("--ttl-seconds", type=int, default=_default_approval_ttl_seconds())
    p_approve.add_argument("--json", action="store_true")
    p_approve.set_defaults(func=cmd_approve)

    p_reject = sub.add_parser("reject", help="reject a pending approval")
    p_reject.add_argument("job_id")
    p_reject.add_argument("--actor", required=True)
    p_reject.add_argument("--json", action="store_true")
    p_reject.set_defaults(func=cmd_reject)

    p_retry = sub.add_parser("retry", help="retry a retryable-failure/manual-review job")
    p_retry.add_argument("job_id")
    p_retry.add_argument("--actor", required=True)
    p_retry.add_argument("--yes", action="store_true")
    p_retry.add_argument("--force", action="store_true", help="bypass the backoff delay")
    p_retry.add_argument("--json", action="store_true")
    p_retry.set_defaults(func=cmd_retry)

    p_cancel = sub.add_parser("cancel", help="cancel a non-terminal job")
    p_cancel.add_argument("job_id")
    p_cancel.add_argument("--actor", required=True)
    p_cancel.add_argument("--yes", action="store_true")
    p_cancel.add_argument("--json", action="store_true")
    p_cancel.set_defaults(func=cmd_cancel)

    p_resume = sub.add_parser("resume", help="re-verify jobs stuck in publishing/verifying after a restart")
    p_resume.add_argument("--json", action="store_true")
    p_resume.set_defaults(func=cmd_resume)

    p_reconcile = sub.add_parser("reconcile", help="list jobs needing operator review, with reasons")
    p_reconcile.add_argument("--json", action="store_true")
    p_reconcile.set_defaults(func=cmd_reconcile)

    p_report = sub.add_parser("report", help="print/generate a job's report")
    p_report.add_argument("job_id")
    p_report.add_argument("--md", action="store_true")
    p_report.add_argument("--json", action="store_true")
    p_report.set_defaults(func=cmd_report)

    p_export = sub.add_parser("export", help="redacted export of reports/audit")
    p_export.add_argument("--since", required=True, help="YYYY-MM-DD")
    p_export.add_argument("--out", required=True)
    p_export.add_argument("--json", action="store_true")
    p_export.set_defaults(func=cmd_export)

    p_prune = sub.add_parser("prune", help="delete archived media/reports older than N days")
    p_prune.add_argument("--older-than", type=int, required=True, dest="older_than")
    p_prune.add_argument("--dry-run", action="store_true")
    p_prune.add_argument("--yes", action="store_true")
    p_prune.add_argument("--json", action="store_true")
    p_prune.set_defaults(func=cmd_prune)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
