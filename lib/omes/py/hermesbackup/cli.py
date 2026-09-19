#!/usr/bin/env python3
"""omes agent-backup <subcommand> - CLI entry point for Hermes data-class
backup/restore (invoked by lib/omes/cmd/agent-backup.sh). Stdlib only
(ADR-0012).

Exit codes follow docs/cli.md's stable exit-code contract: 0 ok, 1
general/backup error, 2 usage error, 9 backup/restore failed (mirrors
lib/omes/backup.sh's/lib/omes/restore.sh's convention for the equivalent
bash-side commands).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import backup, classes as classes_mod, paths

EX_OK = 0
EX_ERROR = 1
EX_USAGE = 2
EX_BACKUP = 9


def _print_json(obj) -> None:
    print(json.dumps(obj, indent=2, sort_keys=True))


def _confirm(args: argparse.Namespace, prompt: str) -> bool:
    if getattr(args, "yes", False):
        return True
    if sys.stdin.isatty():
        answer = input(f"{prompt} [y/N] ").strip().lower()
        return answer in ("y", "yes")
    print("error: this is a mutating operation; pass --yes to confirm", file=sys.stderr)
    return False


def cmd_create(args: argparse.Namespace) -> int:
    hermes_home = Path(args.hermes_home) if args.hermes_home else paths.hermes_home()

    if args.include_secrets:
        print(
            "WARNING: --include-secrets was passed. Secret files (.env, auth.json) will be "
            "included in this backup's archive.tar. Treat this backup session as sensitive.",
            file=sys.stderr,
        )

    try:
        result = backup.create(
            hermes_home,
            args.class_ or None,
            include_secrets=args.include_secrets,
            dry_run=args.dry_run,
        )
    except backup.BackupError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EX_BACKUP
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EX_USAGE

    if not args.dry_run:
        backup.prune()

    if args.json:
        _print_json(result)
    else:
        if result.get("dry_run"):
            print(f"dry-run: {result['entry_count']} file(s) would be backed up (classes: {', '.join(result['classes'])})")
            for e in result["entries"]:
                print(f"  [{e['category']}] {e['path']} ({e['size']} bytes)")
        else:
            print(f"created backup {result['timestamp']} at {result['path']} ({result['entry_count']} file(s))")
    return EX_OK


def cmd_list(args: argparse.Namespace) -> int:
    sessions = backup.list_sessions()
    if args.json:
        _print_json({"backups": sessions})
    else:
        if not sessions:
            print("no backups found")
        for s in sessions:
            if "error" in s:
                print(f"{s['timestamp']}: ERROR {s['error']}")
                continue
            secrets_note = " (includes secrets)" if s.get("include_secrets") else ""
            print(f"{s['timestamp']}  classes={','.join(s['classes'])}{secrets_note}")
    return EX_OK


def cmd_verify(args: argparse.Namespace) -> int:
    try:
        result = backup.verify(args.timestamp)
    except backup.BackupError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EX_BACKUP

    if args.json:
        _print_json(result)
    else:
        if result["ok"]:
            print(f"{args.timestamp}: OK ({result['entry_count']} file(s) verified)")
        else:
            print(f"{args.timestamp}: CORRUPT")
            for p in result["problems"]:
                print(f"  ! {p}")
    return EX_OK if result["ok"] else EX_BACKUP


def cmd_restore(args: argparse.Namespace) -> int:
    target_home = Path(args.hermes_home) if args.hermes_home else paths.hermes_home()

    if args.restore_secrets:
        if not _confirm(
            args,
            "This will restore SECRET files (.env, auth.json) over any that currently exist. Continue?",
        ):
            print("error: restore of secrets was not confirmed", file=sys.stderr)
            return EX_ERROR
    elif not args.dry_run and not _confirm(args, f"Restore backup {args.timestamp} into {target_home}?"):
        return EX_ERROR

    try:
        result = backup.restore(
            args.timestamp,
            target_home,
            args.class_ or None,
            dry_run=args.dry_run,
            restore_secrets=args.restore_secrets,
            force_home=args.force_home,
        )
    except backup.BackupError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EX_BACKUP

    if args.json:
        _print_json(result)
    else:
        if result.get("dry_run"):
            print(f"dry-run: would restore {len(result.get('would_restore', []))} file(s) from {args.timestamp}")
        else:
            print(f"restored {len(result.get('restored', []))} file(s) from {args.timestamp}")
            if result.get("pre_restore_backup"):
                print(f"pre-restore backup: {result['pre_restore_backup']}")
    return EX_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="omes agent-backup")
    sub = parser.add_subparsers(dest="subcommand")

    common_json = dict(action="store_true", help="emit a single JSON object on stdout")

    p_create = sub.add_parser("create", help="create a new backup session")
    p_create.add_argument("--class", dest="class_", action="append", default=[], choices=classes_mod.CLASS_NAMES)
    p_create.add_argument("--include-secrets", action="store_true")
    p_create.add_argument("--dry-run", action="store_true")
    p_create.add_argument("--hermes-home")
    p_create.add_argument("--json", **common_json)
    # --yes is accepted (a no-op here; create never prompts) so the bash
    # wrapper (lib/omes/cmd/agent-backup.sh) can pass OMES_NONINTERACTIVE
    # through uniformly to every subcommand.
    p_create.add_argument("--yes", action="store_true")
    p_create.set_defaults(func=cmd_create)

    p_list = sub.add_parser("list", help="list backup sessions")
    p_list.add_argument("--json", **common_json)
    p_list.add_argument("--yes", action="store_true")
    p_list.set_defaults(func=cmd_list)

    p_verify = sub.add_parser("verify", help="verify a backup session's checksums")
    p_verify.add_argument("timestamp")
    p_verify.add_argument("--json", **common_json)
    p_verify.add_argument("--yes", action="store_true")
    p_verify.set_defaults(func=cmd_verify)

    p_restore = sub.add_parser("restore", help="restore a backup session")
    p_restore.add_argument("timestamp")
    p_restore.add_argument("--class", dest="class_", action="append", default=[], choices=classes_mod.CLASS_NAMES)
    p_restore.add_argument("--dry-run", action="store_true")
    p_restore.add_argument("--yes", action="store_true")
    p_restore.add_argument("--restore-secrets", action="store_true")
    p_restore.add_argument("--force-home", action="store_true")
    p_restore.add_argument("--hermes-home")
    p_restore.add_argument("--json", **common_json)
    p_restore.set_defaults(func=cmd_restore)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "subcommand", None):
        parser.print_usage(sys.stderr)
        return EX_USAGE
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
