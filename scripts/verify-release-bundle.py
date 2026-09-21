#!/usr/bin/env python3
"""scripts/verify-release-bundle.py - verify release SLSA provenance, SBOM,
and evidence bundle integrity (ADR-0010, issues #168, #173).
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
    parser = argparse.ArgumentParser(description="Verify OMES release evidence bundle.")
    parser.add_argument("--bundle-dir", required=True, help="Directory containing release evidence bundle")
    parser.add_argument("--version", help="Expected release version (e.g. 0.3.0)")
    parser.add_argument("--commit", help="Expected release commit 40-character SHA")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    bundle_dir = Path(args.bundle_dir).resolve()
    if not bundle_dir.is_dir():
        print(f"verify-release-bundle: ERROR bundle directory does not exist: {bundle_dir}", file=sys.stderr)
        return 1

    errors = release_bundle.verify_bundle(
        bundle_dir=bundle_dir,
        expected_version=args.version,
        expected_commit=args.commit,
    )

    if errors:
        print(f"verify-release-bundle: FAILED ({len(errors)} error(s)):", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    print(f"verify-release-bundle: OK verified evidence bundle at {bundle_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
