"""lib/omes/py/architecture/registry.py - Architecture boundary and capability
registry validation (ADR-0017, issue #171).

Provides machine-checkable enforcement of:
  - Upstream-first precedence: DELEGATE -> PORT -> ADAPT -> DEFER -> REJECT
  - Explicit authority and capability registry in architecture/capabilities.json
  - Mandatory ADR reference and removal trigger for any duplicate capability
  - Exclusion of unreleased/upstream-main features from released_supported maturity
  - Layer boundaries forbidding core modules from importing commercial/domain code
  - Strict decoupling forbidding OMES from implementing Hermes agent reasoning/runtime
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
PY_ROOT = REPO_ROOT / "lib" / "omes" / "py"
if str(PY_ROOT) not in sys.path:
    sys.path.insert(0, str(PY_ROOT))

from jobs import schema as schema_mod  # noqa: E402

STANDARD_PRECEDENCE = ("delegate", "port", "adapt", "defer", "reject")

CORE_MODULES = frozenset({
    "agent",
    "jobs",
    "health",
    "provenance",
    "architecture",
})

COMMERCIAL_DOMAIN_MODULES = frozenset({
    "content",
    "domains",
})

DEFAULT_SCHEMA_PATH = REPO_ROOT / "contracts" / "architecture" / "v1" / "capabilities.schema.json"
DEFAULT_REGISTRY_PATH = REPO_ROOT / "architecture" / "capabilities.json"


def load_json(path: Path) -> dict[str, Any]:
    return schema_mod.load_json(path)


def load_registry(path: Path | None = None) -> dict[str, Any]:
    target = path or DEFAULT_REGISTRY_PATH
    return load_json(target)


def validate_capability(cap: dict[str, Any]) -> list[str]:
    """Semantic validation rules for a single capability entry."""
    errors: list[str] = []
    cap_id = cap.get("capability_id", "<missing>")

    # 1. Duplication rules
    duplication_allowed = cap.get("duplication_allowed")
    if duplication_allowed is True:
        adr = cap.get("adr_reference")
        if not adr or not str(adr).strip():
            errors.append(
                f"capability '{cap_id}': duplication_allowed=true requires a non-empty 'adr_reference'"
            )
        trigger = cap.get("removal_trigger")
        if not trigger or not str(trigger).strip():
            errors.append(
                f"capability '{cap_id}': duplication_allowed=true requires a non-empty 'removal_trigger'"
            )

    # 2. Main-only upstream features cannot be classified as released_supported
    observed_rev = cap.get("observed_upstream_revision")
    maturity = cap.get("maturity")
    disposition = cap.get("disposition")

    if observed_rev in ("main", "master", "upstream-main", "upstream/main"):
        if maturity == "released_supported":
            errors.append(
                f"capability '{cap_id}': upstream feature observed only on main cannot be classified as 'released_supported'"
            )

    if maturity == "upstream_main_candidate" and disposition == "released_supported":
        errors.append(
            f"capability '{cap_id}': upstream_main_candidate cannot be classified as 'released_supported'"
        )

    # 3. Evidence URLs
    urls = cap.get("evidence_urls", [])
    if not isinstance(urls, list) or len(urls) == 0:
        errors.append(f"capability '{cap_id}': requires at least one evidence URL")
    else:
        for u in urls:
            if not isinstance(u, str) or not (u.startswith("http://") or u.startswith("https://")):
                errors.append(f"capability '{cap_id}': invalid evidence URL: {u!r}")

    # 4. Precedence validation
    if disposition not in STANDARD_PRECEDENCE:
        errors.append(
            f"capability '{cap_id}': invalid disposition '{disposition}'; must be one of {STANDARD_PRECEDENCE}"
        )

    return errors


def validate_registry_data(
    data: dict[str, Any], schema_path: Path | None = None
) -> list[str]:
    """Validates full capability registry data against JSON Schema and semantic rules."""
    errors: list[str] = []
    schema_file = schema_path or DEFAULT_SCHEMA_PATH

    if schema_file.is_file():
        try:
            schema_data = schema_mod.load_json(schema_file)
            schema_mod.validate_schema(schema_data)
            schema_errors = schema_mod.validate(data, schema_data)
            if schema_errors:
                errors.extend(schema_errors)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"schema validation failed: {exc}")

    # Precedence order check
    precedence = data.get("precedence")
    if precedence != list(STANDARD_PRECEDENCE):
        errors.append(
            f"precedence must be exactly {list(STANDARD_PRECEDENCE)}, got {precedence}"
        )

    capabilities = data.get("capabilities", [])
    if not isinstance(capabilities, list):
        errors.append("capabilities must be a list")
        return errors

    seen_ids: set[str] = set()
    for cap in capabilities:
        if not isinstance(cap, dict):
            errors.append(f"capability entry must be an object, got {type(cap).__name__}")
            continue
        cap_id = cap.get("capability_id")
        if cap_id in seen_ids:
            errors.append(f"duplicate capability_id: '{cap_id}'")
        if cap_id:
            seen_ids.add(cap_id)
        errors.extend(validate_capability(cap))

    return errors


def check_module_coverage(
    repo_root: Path, registry_data: dict[str, Any]
) -> list[str]:
    """Ensures every Python submodule under lib/omes/py is classified by the registry."""
    errors: list[str] = []
    py_lib = repo_root / "lib" / "omes" / "py"
    if not py_lib.is_dir():
        return errors

    registered_modules = set()
    for cap in registry_data.get("capabilities", []):
        mod = cap.get("omes_module")
        if mod:
            registered_modules.add(mod)

    for item in sorted(py_lib.iterdir()):
        if item.is_dir() and item.name != "__pycache__":
            if any(item.glob("*.py")):
                if item.name not in registered_modules:
                    errors.append(
                        f"unclassified module: '{item.name}' under lib/omes/py is not mapped in architecture/capabilities.json"
                    )

    return errors


def check_layer_boundaries(repo_root: Path) -> list[str]:
    """Ensures OMES core modules do not import commercial or domain workflow modules."""
    errors: list[str] = []
    py_lib = repo_root / "lib" / "omes" / "py"
    if not py_lib.is_dir():
        return errors

    for core_mod_name in sorted(CORE_MODULES):
        core_dir = py_lib / core_mod_name
        if not core_dir.is_dir():
            continue
        for py_file in sorted(core_dir.glob("*.py")):
            try:
                tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
            except SyntaxError as exc:
                errors.append(f"{py_file}: syntax error parsing AST: {exc}")
                continue

            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        top_pkg = alias.name.split(".")[0]
                        if top_pkg in COMMERCIAL_DOMAIN_MODULES:
                            errors.append(
                                f"{py_file}:{node.lineno}: prohibited layer boundary violation: "
                                f"core module '{core_mod_name}' imports commercial/domain module '{top_pkg}'"
                            )
                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        top_pkg = node.module.split(".")[0]
                        if top_pkg in COMMERCIAL_DOMAIN_MODULES:
                            errors.append(
                                f"{py_file}:{node.lineno}: prohibited layer boundary violation: "
                                f"core module '{core_mod_name}' imports from commercial/domain module '{top_pkg}'"
                            )

    return errors


def check_runtime_coupling(repo_root: Path) -> list[str]:
    """Ensures OMES does not attempt direct SQLite connections to internal Hermes database paths."""
    errors: list[str] = []
    agent_dir = repo_root / "lib" / "omes" / "py" / "agent"
    if not agent_dir.is_dir():
        return errors

    for py_file in sorted(agent_dir.glob("*.py")):
        content = py_file.read_text(encoding="utf-8")
        if "messages.db" in content and "sqlite3" in content:
            errors.append(
                f"{py_file}: prohibited runtime coupling: direct sqlite access to internal Hermes messages.db"
            )
        if "hermes.db" in content and "sqlite3" in content:
            errors.append(
                f"{py_file}: prohibited runtime coupling: direct sqlite access to internal Hermes database"
            )

    return errors


def check_all(repo_root: Path | None = None) -> list[str]:
    root = repo_root or REPO_ROOT
    all_errors: list[str] = []

    reg_path = root / "architecture" / "capabilities.json"
    schema_path = root / "contracts" / "architecture" / "v1" / "capabilities.schema.json"

    if not reg_path.is_file():
        return [f"missing capability registry at {reg_path}"]

    try:
        reg_data = load_json(reg_path)
    except Exception as exc:  # noqa: BLE001
        return [f"failed to load capability registry: {exc}"]

    all_errors.extend(validate_registry_data(reg_data, schema_path=schema_path))
    all_errors.extend(check_module_coverage(root, reg_data))
    all_errors.extend(check_layer_boundaries(root))
    all_errors.extend(check_runtime_coupling(root))

    return all_errors
