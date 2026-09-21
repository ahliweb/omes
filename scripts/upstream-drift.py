#!/usr/bin/env python3
"""scripts/upstream-drift.py - Automated upstream drift review and deprecation tracking.

Inspects public upstream releases (Hermes, Omarchy, Graphify, Coolify) against
architecture/capabilities.json and evaluates baseline updates, candidates,
and opportunities to delegate or deprecate OMES wrappers (ADR-0026, issue #181).

Exit codes:
  0: Clean, no actionable drift or --check passed.
  1: Actionable drift findings detected (under --check).
  2: Upstream inspection blocked (network or API error) or usage error.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PY_ROOT = REPO_ROOT / "lib" / "omes" / "py"
sys.path.insert(0, str(PY_ROOT))

from architecture import drift, registry  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Inspect upstream project releases against OMES capability registry (ADR-0026)."
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit structured JSON output to stdout.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit 1 if actionable drift findings exist (for release gates / CI).",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Run in offline mode without making remote network calls.",
    )
    parser.add_argument(
        "--fixtures",
        type=str,
        default=None,
        help="Path to JSON file containing mocked upstream metadata fixtures.",
    )
    parser.add_argument(
        "--update-issue",
        action="store_true",
        help="Update or create the deduplicated GitHub tracking issue if drift is actionable.",
    )
    parser.add_argument(
        "--repo",
        type=str,
        default="ahliweb/omes",
        help="GitHub repository name (default: ahliweb/omes).",
    )
    parser.add_argument(
        "--token",
        type=str,
        default=None,
        help="GitHub API token (default: reads GITHUB_TOKEN or GH_TOKEN env vars).",
    )
    parser.add_argument(
        "--registry",
        type=str,
        default=None,
        help="Path to capabilities.json registry file.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    reg_path = Path(args.registry) if args.registry else (REPO_ROOT / "architecture" / "capabilities.json")
    if not reg_path.is_file():
        print(f"error: capabilities registry not found at {reg_path}", file=sys.stderr)
        return 2

    try:
        reg_data = registry.load_json(reg_path)
    except Exception as exc:
        print(f"error: failed to load {reg_path}: {exc}", file=sys.stderr)
        return 2

    # Load fixtures if specified
    fixtures_data: dict[str, Any] = {}
    if args.fixtures:
        fixture_path = Path(args.fixtures)
        if fixture_path.is_file():
            try:
                fixtures_data = registry.load_json(fixture_path)
            except Exception as exc:
                print(f"error: failed to load fixtures from {fixture_path}: {exc}", file=sys.stderr)
                return 2
        else:
            print(f"error: fixture file not found at {fixture_path}", file=sys.stderr)
            return 2

    token = args.token or os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
    client = drift.UpstreamClient(
        token=token,
        fixtures=fixtures_data,
        offline=args.offline,
    )

    report = drift.evaluate_upstream_drift(
        registry_data=reg_data,
        client=client,
        check_main_candidate=True,
    )

    if args.update_issue:
        try:
            drift.reconcile_github_issue(
                report=report,
                repo=args.repo,
                token=token,
                dry_run=False,
            )
        except Exception as exc:
            print(f"warning: failed to reconcile GitHub issue: {exc}", file=sys.stderr)

    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(drift.format_markdown_report(report))

    if report.status == drift.STATUS_BLOCKED:
        return 2

    if args.check and report.has_actionable_drift:
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
