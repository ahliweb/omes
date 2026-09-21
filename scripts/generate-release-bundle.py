#!/usr/bin/env python3
"""scripts/generate-release-bundle.py - generate release SLSA provenance, SBOM,
and evidence bundle artifacts (ADR-0010, issues #168, #173).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PY_ROOT = REPO_ROOT / "lib" / "omes" / "py"
sys.path.insert(0, str(PY_ROOT))

from provenance import release_bundle  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate OMES release evidence bundle.")
    parser.add_argument("--version", required=True, help="Release version (e.g. 0.3.0)")
    parser.add_argument("--tag", required=True, help="Release tag (e.g. v0.3.0)")
    parser.add_argument("--commit", required=True, help="Release commit 40-character SHA")
    parser.add_argument("--output", required=True, help="Output directory for evidence bundle")
    parser.add_argument("--date", help="Release date (YYYY-MM-DD)")
    parser.add_argument("--ci-ref", help="CI run reference or invocation ID")
    parser.add_argument("--no-vm-evidence", action="store_true", help="Simulate missing VM evidence")
    parser.add_argument("--no-ci-evidence", action="store_true", help="Simulate missing CI evidence")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    output_dir = Path(args.output).resolve()
    try:
        files = release_bundle.generate_bundle(
            repo_root=REPO_ROOT,
            version=args.version,
            tag=args.tag,
            commit_sha=args.commit,
            output_dir=output_dir,
            date_str=args.date,
            ci_run_ref=args.ci_ref,
            has_ci_evidence=not args.no_ci_evidence,
            has_vm_evidence=not args.no_vm_evidence,
        )
        print(f"generate-release-bundle: OK generated {len(files)} files in {output_dir}")
        for fname in sorted(files.keys()):
            print(f"  - {fname}")
        return 0
    except Exception as exc:
        print(f"generate-release-bundle: ERROR {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
