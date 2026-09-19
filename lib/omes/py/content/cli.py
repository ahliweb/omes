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
import sys
from pathlib import Path

from . import inbox, jobs, paths

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

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
