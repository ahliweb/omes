#!/usr/bin/env python3
"""omes job <subcommand> - CLI entry point for the OMES control job runner
(invoked by lib/omes/cmd/job.sh). Stdlib only (ADR-0012).

Exit codes follow docs/cli.md's stable exit-code contract (0 ok, 1
general error, 2 usage error).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import runner, schema, store

EX_OK = 0
EX_ERROR = 1
EX_USAGE = 2

_REPO_ROOT = Path(__file__).resolve().parents[4]
_DEPLOYMENT_REQUEST_SCHEMA = (
    _REPO_ROOT / "contracts" / "control-center" / "v1" / "deployment.request.schema.json"
)


def _print_json(obj) -> None:
    print(json.dumps(obj, indent=2, sort_keys=True))


def _job_summary(record: dict) -> dict:
    return {
        "job_id": record["job_id"],
        "tenant_id": record["tenant_id"],
        "operation": record["operation"],
        "state": record["state"],
        "target": record["target"],
        "created_at": record["created_at"],
        "updated_at": record["updated_at"],
        "error": record.get("error"),
    }


def cmd_submit(args: argparse.Namespace) -> int:
    try:
        with open(args.file, "r", encoding="utf-8") as fh:
            request = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"error: could not read/parse {args.file}: {exc}", file=sys.stderr)
        return EX_USAGE

    schema_doc = schema.load_json(_DEPLOYMENT_REQUEST_SCHEMA)
    errors = schema.validate(request, schema_doc)
    if errors:
        if args.json:
            _print_json({"error": "schema_validation_failed", "reasons": errors})
        else:
            print("error: request failed schema validation:", file=sys.stderr)
            for e in errors:
                print(f"  - {e}", file=sys.stderr)
        return EX_ERROR

    try:
        record, replayed = store.submit(request)
    except store.CrossTenantError as exc:
        if args.json:
            _print_json({"error": "cross_tenant_rejected", "reason": str(exc)})
        else:
            print(f"error: {exc}", file=sys.stderr)
        return EX_ERROR

    if args.json:
        _print_json({**_job_summary(record), "replayed": replayed})
    else:
        verb = "replayed (idempotent)" if replayed else "submitted"
        print(f"{record['job_id']}: {verb}, state={record['state']}")
    return EX_OK


def cmd_approve(args: argparse.Namespace) -> int:
    try:
        record = store.load_job(args.job_id)
        store.approve(record, actor=args.actor)
    except store.JobsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EX_ERROR
    if args.json:
        _print_json(_job_summary(record))
    else:
        print(f"{record['job_id']}: approved by {args.actor}")
    return EX_OK


def cmd_run(args: argparse.Namespace) -> int:
    try:
        record = store.load_job(args.job_id)
        record = runner.run(record, actor=args.actor)
    except store.JobsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EX_ERROR
    if args.json:
        _print_json(_job_summary(record))
    else:
        print(f"{record['job_id']}: {record['state']}")
    return EX_OK if record["state"] in ("succeeded", "rolled_back") else EX_ERROR


def cmd_status(args: argparse.Namespace) -> int:
    try:
        record = store.load_job(args.job_id)
    except store.UnknownJobError:
        print(f"error: unknown job {args.job_id}", file=sys.stderr)
        return EX_ERROR
    if args.json:
        _print_json(record)
    else:
        print(f"{record['job_id']}: {record['state']} (operation={record['operation']})")
    return EX_OK


def cmd_list(args: argparse.Namespace) -> int:
    records = store.list_jobs()
    if args.state:
        records = [r for r in records if r["state"] == args.state]
    if args.json:
        _print_json({"jobs": [_job_summary(r) for r in records]})
    else:
        for r in records:
            print(f"{r['job_id']}  {r['state']}  {r['operation']}")
    return EX_OK


def cmd_cancel(args: argparse.Namespace) -> int:
    try:
        record = store.load_job(args.job_id)
        store.cancel(record, actor=args.actor)
    except store.JobsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EX_ERROR
    if args.json:
        _print_json(_job_summary(record))
    else:
        print(f"{record['job_id']}: cancelled by {args.actor}")
    return EX_OK


def cmd_expire(args: argparse.Namespace) -> int:
    expired = store.expire_stale_jobs()
    if args.json:
        _print_json({"expired": expired})
    else:
        print(f"expired: {len(expired)}")
        for job_id in expired:
            print(f"  ! {job_id}")
    return EX_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="omes job")
    sub = parser.add_subparsers(dest="subcommand", required=True)

    p_submit = sub.add_parser("submit", help="submit a deployment.request job")
    p_submit.add_argument("--file", required=True, help="path to a deployment.request JSON document")
    p_submit.add_argument("--json", action="store_true")
    p_submit.set_defaults(func=cmd_submit)

    p_approve = sub.add_parser("approve", help="approve a queued destructive job")
    p_approve.add_argument("job_id")
    p_approve.add_argument("--actor", required=True)
    p_approve.add_argument("--json", action="store_true")
    p_approve.set_defaults(func=cmd_approve)

    p_run = sub.add_parser("run", help="execute an approved (or auto-approvable) job")
    p_run.add_argument("job_id")
    p_run.add_argument("--actor", default="system")
    p_run.add_argument("--json", action="store_true")
    p_run.set_defaults(func=cmd_run)

    p_status = sub.add_parser("status", help="show a job's full record")
    p_status.add_argument("job_id")
    p_status.add_argument("--json", action="store_true")
    p_status.set_defaults(func=cmd_status)

    p_list = sub.add_parser("list", help="list jobs")
    p_list.add_argument("--state", choices=store.STATES, default=None)
    p_list.add_argument("--json", action="store_true")
    p_list.set_defaults(func=cmd_list)

    p_cancel = sub.add_parser("cancel", help="cancel a queued/approved job")
    p_cancel.add_argument("job_id")
    p_cancel.add_argument("--actor", required=True)
    p_cancel.add_argument("--json", action="store_true")
    p_cancel.set_defaults(func=cmd_cancel)

    p_expire = sub.add_parser("expire", help="expire queued/approved jobs older than the TTL")
    p_expire.add_argument("--json", action="store_true")
    p_expire.set_defaults(func=cmd_expire)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
