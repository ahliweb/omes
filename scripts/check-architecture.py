#!/usr/bin/env python3
"""scripts/check-architecture.py - validate capability registry and enforce architecture boundaries.

Checks:
  1. contracts/architecture/v1/capabilities.schema.json schema conformance.
  2. architecture/capabilities.json semantic correctness (precedence, triggers, ADRs).
  3. Module coverage: all lib/omes/py modules classified.
  4. Layer boundaries: core modules cannot depend on commercial/domain modules.
  5. Decoupling: forbidden runtime coupling with internal Hermes database files.

Exit codes:
  0: all architecture boundaries and registry checks passed.
  1: one or more violations detected.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PY_ROOT = REPO_ROOT / "lib" / "omes" / "py"
sys.path.insert(0, str(PY_ROOT))

from architecture import registry  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    repo_root = REPO_ROOT
    if argv:
        repo_root = Path(argv[0]).resolve()

    errors = registry.check_all(repo_root)
    if errors:
        print(f"check-architecture: {len(errors)} violation(s) found:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    print("check-architecture: OK (all architecture boundaries and capabilities verified)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
