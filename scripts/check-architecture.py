#!/usr/bin/env python3
"""scripts/check-architecture.py - validate capability registry and enforce architecture boundaries.

Checks:
  1. contracts/architecture/v1/capabilities.schema.json schema conformance
     (schema_version 1.1.0: plane, execution_semantics, implementation_status
     are required per capability; authority adds platform/external).
  2. architecture/capabilities.json semantic correctness (precedence, triggers, ADRs).
  3. Module coverage: all lib/omes/py modules classified.
  4. Layer boundaries: core modules cannot depend on commercial/domain modules.
  5. Decoupling: forbidden runtime coupling with internal Hermes database files.
  6. Semantic plane/authority invariants R1-R9 (issue #247): OMES cannot claim
     agent-runtime/business-control/probabilistic capabilities or Hermes-reserved
     terms; agent_runtime plane outside Hermes requires an ADR; business_control
     plane is restricted to awcms/provider/external authority; logical_boundary
     capabilities carry no omes_module; any "implemented" OMES capability (or
     tool_data "gateway" claim) needs on-repo evidence; authority external is
     constrained (including SIEM-like capabilities); second-agent-framework
     references require an ADR and non-omes authority.
  7. Control Center contract safety C1-C2: no contracts/control-center/v1
     schema exposes a raw shell-command property, and operation-request.schema.json
     keeps additionalProperties:false, a non-empty operation allowlist, and its
     required safety fields.
  8. Canonical documentation invariants D1-D2: no unsupported-OS "support" claim
     in the six canonical docs, and docs/architecture.md carries the
     reference-architecture marker, a mermaid diagram, and the required phrase.

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
