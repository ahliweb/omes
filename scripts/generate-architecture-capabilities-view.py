#!/usr/bin/env python3
"""scripts/generate-architecture-capabilities-view.py - build the checked-in
`architecture-capabilities-view` fixture from `architecture/capabilities.json`
(issue #246, part 3).

`contracts/control-center/v1/architecture-capabilities-view.schema.json`
defines the READ-ONLY Control Center projection a future AWCMS Architecture
view consumes: planes as lanes, capabilities as cards with an
implementation_status badge, plus an OMES version/commit/generated_at
provenance stamp. The projection logic itself lives in
`lib/omes/py/architecture/capabilities_view.py` (`build_view()` /
`build_fixture_view()`) so this script and the staleness guard in
`lib/omes/py/architecture/registry.py` (`check_architecture_capabilities_view()`,
run by `scripts/check-architecture.py`) can never drift apart from each
other - only from `architecture/capabilities.json` they both read.

The checked-in fixture uses a fixed `generated_at` and `omes_commit` (see
`capabilities_view.FIXTURE_GENERATED_AT`/`FIXTURE_OMES_COMMIT`) so
`--check` is reproducible in CI regardless of wall-clock time or the
working tree's current commit; `omes_version` is read live from the
repository's `VERSION` file. A real production projection (produced by
the OMES pull-worker boundary, not this script) sets all three from the
live host at request time.

Usage:
  scripts/generate-architecture-capabilities-view.py            # write the fixture
  scripts/generate-architecture-capabilities-view.py --check     # exit 1 if stale
  scripts/generate-architecture-capabilities-view.py --out PATH  # write elsewhere (testing)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
PY_ROOT = REPO_ROOT / "lib" / "omes" / "py"
if str(PY_ROOT) not in sys.path:
    sys.path.insert(0, str(PY_ROOT))

from architecture import capabilities_view  # noqa: E402
from jobs import schema as schema_mod  # noqa: E402

REGISTRY_PATH = REPO_ROOT / "architecture" / "capabilities.json"
DEFAULT_OUT = (
    REPO_ROOT
    / "contracts"
    / "control-center"
    / "v1"
    / "fixtures"
    / "architecture-capabilities-view"
    / "valid-01-generated.json"
)


def render(view: dict[str, Any]) -> str:
    return json.dumps(view, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def build_fixture(repo_root: Path = REPO_ROOT) -> dict[str, Any]:
    registry_data = schema_mod.load_json(REGISTRY_PATH)
    return capabilities_view.build_fixture_view(repo_root, registry_data)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="exit 1 if the committed fixture is stale")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="output path (default: the checked-in fixture)")
    args = parser.parse_args(argv)

    rendered = render(build_fixture())

    if args.check:
        if not args.out.is_file():
            print(
                f"generate-architecture-capabilities-view: {args.out} does not exist; "
                "run without --check to create it",
                file=sys.stderr,
            )
            return 1
        current = args.out.read_text(encoding="utf-8")
        if current != rendered:
            print(
                f"generate-architecture-capabilities-view: {args.out} is stale relative to "
                "architecture/capabilities.json; run scripts/generate-architecture-capabilities-view.py",
                file=sys.stderr,
            )
            return 1
        print(f"generate-architecture-capabilities-view: OK ({args.out} is up to date)")
        return 0

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(rendered, encoding="utf-8")
    print(f"generate-architecture-capabilities-view: wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
