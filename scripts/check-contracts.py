#!/usr/bin/env python3
"""scripts/check-contracts.py - validate every contract fixture against its
JSON Schema.

Walks `contracts/<area>/v<major>/fixtures/<schema-name>/*.json` and
validates each fixture file against
`contracts/<area>/v<major>/<schema-name>.schema.json`, using the stdlib-only
validator in `lib/omes/py/jobs/schema.py` (ADR-0012; no PyPI deps).

Naming convention (see contracts/README.md):
  contracts/<area>/v<major>/<schema-name>.schema.json
  contracts/<area>/v<major>/fixtures/<schema-name>/valid-*.json    (must pass)
  contracts/<area>/v<major>/fixtures/<schema-name>/invalid-*.json  (must fail)

An `invalid-*.json` fixture may carry a sibling `<name>.reason.txt` file
naming the substring that must appear in the produced validation error, so
this script (and its tests, tests/py/contracts/) can assert an invalid
fixture fails for the *intended* reason rather than an unrelated one.

Exit codes: 0 all fixtures validated as expected; 1 one or more fixtures
did not.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PY_ROOT = REPO_ROOT / "lib" / "omes" / "py"
sys.path.insert(0, str(PY_ROOT))

from jobs import schema as schema_mod  # noqa: E402


def find_contract_dirs(contracts_root: Path) -> list[Path]:
    """Every directory directly containing at least one *.schema.json file."""
    dirs = set()
    for schema_path in contracts_root.rglob("*.schema.json"):
        dirs.add(schema_path.parent)
    return sorted(dirs)


def check_fixture(fixture_path: Path, schema_path: Path) -> tuple[bool, str]:
    try:
        instance = schema_mod.load_json(fixture_path)
        schema = schema_mod.load_json(schema_path)
    except Exception as exc:  # noqa: BLE001 - report, don't crash the whole run
        return False, f"could not load: {exc}"
    errors = schema_mod.validate(instance, schema)
    if errors:
        return False, "; ".join(errors)
    return True, ""


def check_contract_dir(contract_dir: Path) -> list[str]:
    """Validates every fixture under contract_dir/fixtures/<schema-name>/
    against contract_dir/<schema-name>.schema.json. Returns a list of
    failure descriptions (empty means everything in this dir is correct)."""
    failures: list[str] = []
    fixtures_root = contract_dir / "fixtures"
    for schema_path in sorted(contract_dir.glob("*.schema.json")):
        try:
            schema_data = schema_mod.load_json(schema_path)
            schema_mod.validate_schema(schema_data)
        except Exception as exc:
            failures.append(f"{schema_path}: invalid schema: {exc}")
            continue

        schema_name = schema_path.name[: -len(".schema.json")]
        fixture_dir = fixtures_root / schema_name
        if not fixture_dir.is_dir():
            failures.append(f"{schema_path}: no fixtures directory at {fixture_dir}")
            continue
        fixture_files = sorted(fixture_dir.glob("*.json"))
        valid_files = [f for f in fixture_files if f.name.startswith("valid")]
        invalid_files = [f for f in fixture_files if f.name.startswith("invalid")]
        if not valid_files:
            failures.append(f"{schema_path}: no valid-*.json fixture found in {fixture_dir}")
        if not invalid_files:
            failures.append(f"{schema_path}: no invalid-*.json fixture found in {fixture_dir}")

        for fixture in valid_files:
            ok, reason = check_fixture(fixture, schema_path)
            if not ok:
                failures.append(f"{fixture}: expected VALID against {schema_path.name}, got errors: {reason}")

        for fixture in invalid_files:
            ok, reason = check_fixture(fixture, schema_path)
            if ok:
                failures.append(f"{fixture}: expected INVALID against {schema_path.name}, but it validated")
                continue
            reason_file = fixture.with_suffix("").with_suffix(".reason.txt")
            if reason_file.is_file():
                expected_substring = reason_file.read_text(encoding="utf-8").strip()
                if expected_substring not in reason:
                    failures.append(
                        f"{fixture}: failed, but not for the expected reason "
                        f"(expected substring {expected_substring!r} in: {reason})"
                    )
    return failures


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    contracts_root = REPO_ROOT / "contracts"
    if argv:
        contracts_root = Path(argv[0])

    if not contracts_root.is_dir():
        print(f"check-contracts: no contracts directory at {contracts_root}", file=sys.stderr)
        return 1

    all_failures: list[str] = []
    checked = 0
    for contract_dir in find_contract_dirs(contracts_root):
        checked += len(list(contract_dir.glob("*.schema.json")))
        all_failures.extend(check_contract_dir(contract_dir))

    if all_failures:
        print(f"check-contracts: {len(all_failures)} failure(s) across {checked} schema(s):", file=sys.stderr)
        for failure in all_failures:
            print(f"  - {failure}", file=sys.stderr)
        return 1

    print(f"check-contracts: OK ({checked} schema(s) validated)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
