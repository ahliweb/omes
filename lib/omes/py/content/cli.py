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

from . import inbox, jobs, paths, reports, validation
from .workers import registry as worker_registry


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


def _authorize_channel_actor(args: argparse.Namespace) -> str | None:
    """Returns an error string (never raises) if `args.channel` ==
    "telegram" and `args.actor` is not in the effective authorized-
    approver set (issue #65: OMES_CONTENT_APPROVERS intersected with
    Hermes's TELEGRAM_ALLOWED_USERS). `channel` "cli" is never gated
    here - the always-available MVP approval path stays available
    without any Telegram configuration."""
    channel = getattr(args, "channel", "cli")
    if channel != "telegram":
        return None
    from . import telegram as telegram_mod

    if not telegram_mod.is_authorized_approver(args.actor):
        return (
            f"actor {args.actor!r} is not an authorized Telegram approver "
            "(must be numeric, in OMES_CONTENT_APPROVERS, and in TELEGRAM_ALLOWED_USERS)"
        )
    return None


def _check_expected_hash(args: argparse.Namespace, record: dict) -> str | None:
    expected = getattr(args, "expected_hash", None)
    if expected and expected != record["source"]["sha256"]:
        return (
            f"expected_hash {expected!r} does not match the job's current artifact hash "
            f"{record['source']['sha256']!r} (refusing - possible tamper/stale preview)"
        )
    return None


def cmd_approve(args: argparse.Namespace) -> int:
    root = paths.ensure_layout()
    channel = getattr(args, "channel", "cli")
    try:
        record = jobs.load_job(args.job_id, root)
        auth_error = _authorize_channel_actor(args)
        if auth_error:
            print(f"error: {auth_error}", file=sys.stderr)
            return EX_ERROR
        hash_error = _check_expected_hash(args, record)
        if hash_error:
            print(f"error: {hash_error}", file=sys.stderr)
            return EX_ERROR
        jobs.approve_job(record, actor=args.actor, channel=channel, ttl_seconds=args.ttl_seconds)
        _persist(record, root, args.actor)
    except jobs.ContentJobsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EX_ERROR
    if args.json:
        _print_json({"job_id": record["job_id"], "state": record["state"]})
    else:
        print(f"{record['job_id']}: approved by {args.actor} via {channel}")
    return EX_OK


def cmd_reject(args: argparse.Namespace) -> int:
    root = paths.ensure_layout()
    channel = getattr(args, "channel", "cli")
    try:
        record = jobs.load_job(args.job_id, root)
        auth_error = _authorize_channel_actor(args)
        if auth_error:
            print(f"error: {auth_error}", file=sys.stderr)
            return EX_ERROR
        jobs.reject_job(record, actor=args.actor, channel=channel)
        _persist(record, root, args.actor)
    except jobs.ContentJobsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EX_ERROR
    if args.json:
        _print_json({"job_id": record["job_id"], "state": record["state"]})
    else:
        print(f"{record['job_id']}: rejected by {args.actor} via {channel}")
    return EX_OK


def cmd_edit(args: argparse.Namespace) -> int:
    root = paths.ensure_layout()
    try:
        record = jobs.load_job(args.job_id, root)
        auth_error = _authorize_channel_actor(args)
        if auth_error:
            print(f"error: {auth_error}", file=sys.stderr)
            return EX_ERROR

        if args.platform and args.caption_file:
            # issue #69: a per-platform, versioned edit. Never touches
            # plan.caption (the generated/original draft) or job state -
            # it only appends a new caption.v<n> under this job's own
            # variants/<platform>/ directory.
            caption_text = Path(args.caption_file).read_text(encoding="utf-8")
            variant_path = jobs.save_caption_variant(record["job_id"], args.platform, caption_text, root)
            reports.audit_append(
                root,
                actor=args.actor,
                job_id=record["job_id"],
                from_state=record["state"],
                to_state=record["state"],
                platform=args.platform,
                note=f"caption variant saved: {variant_path.name}",
            )
            result = {"job_id": record["job_id"], "platform": args.platform, "variant": variant_path.name}
        else:
            jobs.edit_plan(record, actor=args.actor, caption=args.caption, targets=args.target)
            reports.audit_append_for_job(root, record, actor=args.actor)
            jobs.save_job(record, root)
            result = {
                "job_id": record["job_id"],
                "state": record["state"],
                "caption": record["plan"]["caption"],
                "targets": record["plan"]["targets"],
            }
    except jobs.ContentJobsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EX_ERROR

    if args.json:
        _print_json(result)
    else:
        print(f"{record['job_id']}: plan edited by {args.actor}")
    return EX_OK


def cmd_notify(args: argparse.Namespace) -> int:
    root = paths.ensure_layout()
    from . import telegram as telegram_mod

    try:
        record = jobs.load_job(args.job_id, root)
    except jobs.ContentJobsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EX_ERROR

    chat_id = args.chat_id or os.environ.get("OMES_CONTENT_TELEGRAM_CHAT_ID")
    if not chat_id:
        print("error: --chat-id or OMES_CONTENT_TELEGRAM_CHAT_ID is required", file=sys.stderr)
        return EX_ERROR

    try:
        result = telegram_mod.notify_job(record, root, chat_id)
    except telegram_mod.TelegramError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EX_ERROR

    reports.audit_append(
        root,
        actor="system",
        job_id=record["job_id"],
        from_state=record["state"],
        to_state=record["state"],
        platform=record.get("platform"),
        artifact_hash=record["source"]["sha256"],
        note=f"telegram notify sent (thumbnail={result.get('thumbnail_used')})",
    )
    if args.json:
        _print_json({"job_id": record["job_id"], "sent": result.get("sent", False)})
    else:
        print(f"{record['job_id']}: notification sent")
    return EX_OK


def cmd_status(args: argparse.Namespace) -> int:
    root = paths.ensure_layout()
    try:
        record = jobs.load_job(args.job_id, root)
    except jobs.ContentJobsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EX_ERROR

    if args.telegram:
        from . import telegram as telegram_mod

        chat_id = args.chat_id or os.environ.get("OMES_CONTENT_TELEGRAM_CHAT_ID")
        if not chat_id:
            print("error: --chat-id or OMES_CONTENT_TELEGRAM_CHAT_ID is required for --telegram", file=sys.stderr)
            return EX_ERROR
        try:
            telegram_mod.send_message(chat_id, telegram_mod.status_text(record))
        except telegram_mod.TelegramError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return EX_ERROR

    if args.json:
        _print_json(
            {
                "job_id": record["job_id"],
                "state": record["state"],
                "platform": record.get("platform"),
                "resulting_url": record["publish"].get("resulting_url"),
                "attempts": record["publish"].get("attempts", 0),
            }
        )
    else:
        print(f"{record['job_id']}: {record['state']}")
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


CONTENT_DEPRECATION_NOTICE = (
    "[omes] DEPRECATION: 'omes content' is deprecated in OMES v1.x and scheduled for retirement "
    "in OMES v2.0 (ADR-0024, issue #179). Content distribution workflows are transitioning to "
    "AWCMS Control Center and Hermes agent skills. Use 'omes content export --format awcms-v1' "
    "to export job records and audit logs to the successor format."
)


def cmd_export(args: argparse.Namespace) -> int:
    root = paths.ensure_layout()
    fmt = getattr(args, "format", "legacy")
    if fmt == "awcms-v1":
        result = reports.export_awcms_v1(root, args.since, Path(args.out))
    else:
        result = reports.export_reports(root, args.since, Path(args.out))
    if args.json:
        _print_json(result)
    else:
        if fmt == "awcms-v1":
            print(
                f"exported {len(result['exported_jobs'])} job(s) to AWCMS manifest at {result['manifest']} "
                f"(requires_manual_review={result['requires_manual_review_count']})"
            )
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


def _allowed_paths_for(root: Path, job_id: str, platform: str) -> dict[str, str]:
    """The explicit filesystem permission boundary (issue #66) passed to
    a worker: at most its job's processing dir, its own platform's
    session dir, and its job's evidence dir - never sessions/ for any
    other platform, never another job's processing dir, never state/ or
    reports/*.json directly."""
    return {
        "processing_dir": str(paths.job_processing_dir(job_id, root)),
        "session_dir": str(paths.session_platform_dir(platform, root)),
        "evidence_dir": str(paths.job_evidence_dir(job_id, root)),
    }


def _validation_preview_for_targets(record: dict, root: Path) -> dict[str, list[dict]]:
    """Issue #69: "plan shows the generated caption and cover plan" and
    its validation - computed per target platform, using whatever caption
    variant currently applies to that platform (falling back to the
    generic plan.caption). This never blocks `plan` itself; only
    `publish` (per-platform) acts on blocking issues."""
    preview: dict[str, list[dict]] = {}
    for platform in record["plan"].get("targets") or []:
        caption_text, _source = jobs.resolve_caption_for_platform(record, platform, root)
        profile = validation.load_platform_profile(platform)
        issues = validation.validate_platform(caption_text, profile)
        preview[platform] = [i.to_dict() for i in issues]
    return preview


def cmd_plan(args: argparse.Namespace) -> int:
    root = paths.ensure_layout()
    try:
        record = jobs.load_job(args.job_id, root)
        jobs.plan_job(record, actor=args.actor, caption=args.caption, targets=args.target or [])
        _persist(record, root, args.actor)
    except jobs.ContentJobsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EX_ERROR

    validation_preview = _validation_preview_for_targets(record, root)

    if args.json:
        _print_json(
            {
                "job_id": record["job_id"],
                "state": record["state"],
                "caption": record["plan"]["caption"],
                "targets": record["plan"]["targets"],
                "validation": validation_preview,
            }
        )
    else:
        targets = ", ".join(record["plan"]["targets"]) or "(none)"
        print(f"{record['job_id']}: planned -> {record['state']} (targets: {targets})")
        print(f"caption: {record['plan']['caption'] or '(none)'}")
        for platform, issues in validation_preview.items():
            if not issues:
                print(f"  [{platform}] no validation issues")
                continue
            for issue in issues:
                print(f"  [{platform}] {issue['severity']}: {issue['rule']}: {issue['message']}")
    return EX_OK


def cmd_publish(args: argparse.Namespace) -> int:
    root = paths.ensure_layout()
    try:
        record = jobs.load_job(args.job_id, root)
    except jobs.ContentJobsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EX_ERROR

    targets = record["plan"].get("targets") or []
    if targets and args.platform not in targets:
        print(
            f"error: platform {args.platform!r} is not one of this job's planned targets {targets}",
            file=sys.stderr,
        )
        return EX_ERROR

    if args.worker_executable:
        worker_executable = args.worker_executable
    else:
        try:
            worker_executable = str(worker_registry.worker_executable_for_platform(args.platform))
        except worker_registry.UnknownPlatformError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return EX_ERROR

    record["platform"] = args.platform

    caption_text, _caption_source = jobs.resolve_caption_for_platform(record, args.platform, root)
    profile = validation.load_platform_profile(args.platform)
    issues = validation.validate_platform(
        caption_text, profile, cover_path=args.cover, check_link_reachability=args.check_links
    )
    blocking = [i for i in issues if i.severity == "error"]
    if blocking and not args.force_validation:
        # approved -> publishing -> manual-review, same two-step pattern
        # as the "prepare not ready" case below: validation failing this
        # one platform's caption/cover is a publish-phase outcome, and
        # blocks only THIS platform's publish (issue #69) - other
        # targets on the same job are unaffected, since each `publish`
        # call is scoped to one --platform.
        summary = "; ".join(f"{i.rule}: {i.message}" for i in blocking)
        jobs.apply_transition(record, "publishing", actor="system", note="publish started")
        jobs.apply_transition(
            record, "manual-review", actor="system", note=f"validation blocked ({args.platform}): {summary}"
        )
        _persist(record, root, "system")
        if args.json:
            _print_json(
                {
                    "job_id": record["job_id"],
                    "state": record["state"],
                    "validation": [i.to_dict() for i in issues],
                }
            )
        else:
            print(f"{record['job_id']}: validation blocked publish to {args.platform!r} -> manual-review", file=sys.stderr)
            for issue in blocking:
                print(f"  error: {issue.rule}: {issue.message}", file=sys.stderr)
        return EX_ERROR

    allowed_paths = _allowed_paths_for(root, record["job_id"], args.platform)

    prepare_result = jobs.prepare_worker(record, worker_executable, allowed_paths=allowed_paths, root=root)
    if prepare_result.get("status") != "ok" or not prepare_result.get("ready"):
        # approved -> publishing -> manual-review: prepare failing is a
        # publish-phase outcome (docs/content-distribution.md section 5),
        # there is no direct approved -> manual-review transition.
        jobs.apply_transition(record, "publishing", actor="system", note="publish started")
        jobs.apply_transition(
            record,
            "manual-review",
            actor="system",
            note=f"prepare not ready: {prepare_result.get('note', prepare_result)}",
        )
        _persist(record, root, "system")
        if args.json:
            _print_json({"job_id": record["job_id"], "state": record["state"], "prepare_result": prepare_result})
        else:
            print(f"{record['job_id']}: prepare failed -> manual-review ({prepare_result.get('note', '')})", file=sys.stderr)
        return EX_ERROR

    try:
        publish_result = jobs.publish_job(
            record,
            worker_executable,
            worker_version=args.worker_version,
            allowed_paths=allowed_paths,
            root=root,
            caption_override=caption_text,
        )
    except jobs.ApprovalError as exc:
        _persist(record, root, "system")
        if args.json:
            _print_json({"job_id": record["job_id"], "state": record["state"], "error": str(exc)})
        else:
            print(f"error: {exc}", file=sys.stderr)
        return EX_ERROR
    except jobs.ContentJobsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EX_ERROR

    if record["state"] == "verifying":
        jobs.verify_job(record, worker_executable, allowed_paths=allowed_paths)

    # Capture the outcome BEFORE _persist() auto-archives a terminal
    # state (succeeded/failed/cancelled -> archived, issue #68) - the
    # exit code must reflect what actually happened (a failed publish is
    # still a failure once archived), even though the printed/JSON
    # `state` correctly shows the final `archived` state to the caller.
    outcome_state = record["state"]
    _persist(record, root, "system")

    if args.json:
        _print_json(
            {
                "job_id": record["job_id"],
                "state": record["state"],
                "publish_result": publish_result,
                "resulting_url": record["publish"].get("resulting_url"),
            }
        )
    else:
        url = record["publish"].get("resulting_url") or "(none)"
        print(f"{record['job_id']}: publish -> {record['state']} (url: {url})")
    return EX_OK if outcome_state not in ("failed", "manual-review", "retryable-failure") else EX_ERROR


def cmd_session_login(args: argparse.Namespace) -> int:
    root = paths.ensure_layout()
    try:
        worker_executable = str(worker_registry.worker_executable_for_platform(args.platform))
    except worker_registry.UnknownPlatformError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EX_ERROR

    from . import worker as worker_mod

    session_dir = paths.session_platform_dir(args.platform, root)
    allowed_paths = {"session_dir": str(session_dir)}
    print(f"[omes] content session login: launching bootstrap-session for platform '{args.platform}'.", file=sys.stderr)
    print("[omes] content session login: this is a manual login step ONLY - it will not publish anything.", file=sys.stderr)
    print(f"[omes] content session login: the session profile lives at {session_dir} (mode 0700, never backed up/committed).", file=sys.stderr)
    payload = {
        "job_id": f"session-login-{args.platform}",
        "platform": args.platform,
        "allowed_paths": allowed_paths,
        "session_dir": str(session_dir),
    }
    result = worker_mod.run_worker(worker_executable, "bootstrap-session", payload)
    reports.audit_append(
        root,
        actor=args.actor,
        job_id=f"session:{args.platform}",
        from_state=None,
        to_state="session-login",
        platform=args.platform,
        note=result.get("note", ""),
    )
    if args.json:
        _print_json(result)
    else:
        print(f"[omes] content session login: {result.get('status')} - {result.get('note', '')}")
    return EX_OK if result.get("status") == "ok" else EX_ERROR


def cmd_session_revoke(args: argparse.Namespace) -> int:
    root = paths.ensure_layout()
    try:
        worker_executable = str(worker_registry.worker_executable_for_platform(args.platform))
    except worker_registry.UnknownPlatformError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EX_ERROR
    if not _confirm(args, f"Revoke the '{args.platform}' session (deletes its browser profile)?"):
        return EX_ERROR

    from . import worker as worker_mod
    import shutil as _shutil

    session_dir = paths.session_platform_dir(args.platform, root)
    allowed_paths = {"session_dir": str(session_dir)}
    payload = {
        "job_id": f"session-revoke-{args.platform}",
        "platform": args.platform,
        "allowed_paths": allowed_paths,
        "session_dir": str(session_dir),
    }
    result = worker_mod.run_worker(worker_executable, "revoke-session", payload)
    if session_dir.is_dir():
        for child in session_dir.iterdir():
            if child.is_dir():
                _shutil.rmtree(child)
            else:
                child.unlink()
    reports.audit_append(
        root,
        actor=args.actor,
        job_id=f"session:{args.platform}",
        from_state=None,
        to_state="session-revoked",
        platform=args.platform,
        note=result.get("note", ""),
    )
    if args.json:
        _print_json(result)
    else:
        print(f"[omes] content session revoke: {result.get('status')}")
    return EX_OK if result.get("status") == "ok" else EX_ERROR


def cmd_session(args: argparse.Namespace) -> int:
    return args.session_func(args)


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
    p_approve.add_argument("--channel", choices=("cli", "telegram"), default="cli")
    p_approve.add_argument("--expected-hash", default=None, dest="expected_hash")
    p_approve.add_argument("--json", action="store_true")
    p_approve.set_defaults(func=cmd_approve)

    p_reject = sub.add_parser("reject", help="reject a pending approval")
    p_reject.add_argument("job_id")
    p_reject.add_argument("--actor", required=True)
    p_reject.add_argument("--channel", choices=("cli", "telegram"), default="cli")
    p_reject.add_argument("--json", action="store_true")
    p_reject.set_defaults(func=cmd_reject)

    p_edit = sub.add_parser("edit", help="edit a plan's caption/targets, or save a per-platform caption variant")
    p_edit.add_argument("job_id")
    p_edit.add_argument("--actor", required=True)
    p_edit.add_argument("--caption", default=None)
    p_edit.add_argument("--target", action="append", dest="target")
    p_edit.add_argument("--platform", default=None, help="with --caption-file, saves a versioned per-platform caption variant (#69)")
    p_edit.add_argument("--caption-file", default=None, dest="caption_file")
    p_edit.add_argument("--channel", choices=("cli", "telegram"), default="cli")
    p_edit.add_argument("--json", action="store_true")
    p_edit.set_defaults(func=cmd_edit)

    p_notify = sub.add_parser("notify", help="send the Telegram approval-request preview for a job")
    p_notify.add_argument("job_id")
    p_notify.add_argument("--chat-id", default=None, dest="chat_id")
    p_notify.add_argument("--json", action="store_true")
    p_notify.set_defaults(func=cmd_notify)

    p_status = sub.add_parser("status", help="print (and optionally send via Telegram) a job's status")
    p_status.add_argument("job_id")
    p_status.add_argument("--telegram", action="store_true")
    p_status.add_argument("--chat-id", default=None, dest="chat_id")
    p_status.add_argument("--json", action="store_true")
    p_status.set_defaults(func=cmd_status)

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

    p_export = sub.add_parser("export", help="redacted export of reports/audit or AWCMS migration manifest")
    p_export.add_argument("--since", required=True, help="YYYY-MM-DD")
    p_export.add_argument("--out", required=True)
    p_export.add_argument(
        "--format",
        choices=["legacy", "awcms-v1"],
        default="legacy",
        help="export format (legacy=reports/audit, awcms-v1=AWCMS migration manifest)",
    )
    p_export.add_argument("--json", action="store_true")
    p_export.set_defaults(func=cmd_export)

    p_prune = sub.add_parser("prune", help="delete archived media/reports older than N days")
    p_prune.add_argument("--older-than", type=int, required=True, dest="older_than")
    p_prune.add_argument("--dry-run", action="store_true")
    p_prune.add_argument("--yes", action="store_true")
    p_prune.add_argument("--json", action="store_true")
    p_prune.set_defaults(func=cmd_prune)

    p_plan = sub.add_parser("plan", help="record a caption/target plan and move to approval-required")
    p_plan.add_argument("job_id")
    p_plan.add_argument("--actor", default="system")
    p_plan.add_argument("--caption", default=None)
    p_plan.add_argument("--target", action="append", dest="target", help="platform name; repeatable")
    p_plan.add_argument("--json", action="store_true")
    p_plan.set_defaults(func=cmd_plan)

    p_publish = sub.add_parser("publish", help="run a platform worker's prepare/publish/verify (issue #66)")
    p_publish.add_argument("job_id")
    p_publish.add_argument("--platform", required=True, help="e.g. generic_browser")
    p_publish.add_argument("--worker-executable", default=None, help="override the registry lookup (mainly for tests)")
    p_publish.add_argument("--worker-version", default=None)
    p_publish.add_argument("--cover", default=None, help="cover/thumbnail image path, validated against the platform profile (#69)")
    p_publish.add_argument("--check-links", action="store_true", dest="check_links", help="opt-in HEAD reachability check on caption links (#69)")
    p_publish.add_argument("--force-validation", action="store_true", dest="force_validation", help="publish despite blocking validation errors (operator override)")
    p_publish.add_argument("--json", action="store_true")
    p_publish.set_defaults(func=cmd_publish)

    p_session = sub.add_parser("session", help="manual login/bootstrap and revocation for a platform's browser session")
    session_sub = p_session.add_subparsers(dest="session_subcommand", required=True)

    p_session_login = session_sub.add_parser("login", help="run the worker's bootstrap-session op (never publishes)")
    p_session_login.add_argument("platform")
    p_session_login.add_argument("--actor", required=True)
    p_session_login.add_argument("--json", action="store_true")
    p_session_login.set_defaults(session_func=cmd_session_login)

    p_session_revoke = session_sub.add_parser("revoke", help="run the worker's revoke-session op and clear the local profile")
    p_session_revoke.add_argument("platform")
    p_session_revoke.add_argument("--actor", required=True)
    p_session_revoke.add_argument("--yes", action="store_true")
    p_session_revoke.add_argument("--json", action="store_true")
    p_session_revoke.set_defaults(session_func=cmd_session_revoke)

    p_session.set_defaults(func=cmd_session)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
