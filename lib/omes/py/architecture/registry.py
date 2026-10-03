"""lib/omes/py/architecture/registry.py - Architecture boundary and capability
registry validation (ADR-0017, issue #171; plane/semantics invariants and
Control Center/documentation contract checks added for issue #247).

Provides machine-checkable enforcement of:
  - Upstream-first precedence: DELEGATE -> PORT -> ADAPT -> DEFER -> REJECT
  - Explicit authority and capability registry in architecture/capabilities.json
  - Mandatory ADR reference and removal trigger for any duplicate capability
  - Exclusion of unreleased/upstream-main features from released_supported maturity
  - Layer boundaries forbidding core modules from importing commercial/domain code
  - Strict decoupling forbidding OMES from implementing Hermes agent reasoning/runtime
  - Semantic plane/authority/execution-semantics invariants R1-R9 (schema 1.1.0):
    R1 authority omes excludes agent_runtime/business_control plane and
       probabilistic execution_semantics; R2 non-hermes agent_runtime plane
       requires an adr_reference; R3 business_control plane requires
       authority in {awcms, provider, external}; R4 forbids OMES-owned RAG
       capabilities; R5 logical_boundary implies omes_module null and
       authority != omes; R6 requires omes_module + on-repo evidence for any
       authority-omes "implemented" capability or tool_data "gateway" claim;
       R7 constrains authority external's implementation_status and requires
       any SIEM-like id/title (any spelling of "SIEM"/"Wazuh"/"Splunk"/
       "Sentinel") to declare authority external regardless of the
       capability's own declared authority; R8 gates references to a second
       agent framework, matching common spelling variants (spaced,
       underscored, hyphenated, concatenated: e.g. "Semantic Kernel",
       "Llama Index", "Crew AI", "Auto Gen", "Lang Graph"); R9 reserves the
       hermes.* id namespace and Hermes runtime terms, matching a reserved
       term (e.g. "model_routing") across space/underscore/hyphen/
       concatenated spellings in both the capability_id and the title.
  - Control Center contract safety C1 (no raw shell-command escape hatch in
    any contracts/control-center/v1 schema) and C2 (operation-request.schema.json
    allowlist/required-field shape).
  - Canonical documentation invariants D1 (no unsupported-OS "support" claims)
    and D2 (docs/architecture.md reference-architecture marker/diagram/phrase).
  - AV1: the checked-in `architecture-capabilities-view` fixture (issue #246,
    part 3) is not stale relative to architecture/capabilities.json - it is
    regenerated in memory via lib/omes/py/architecture/capabilities_view.py
    and diffed against the fixture on disk.
  - Mission Control guards MC1-MC9 (issue #264, ADR-0031; implemented in
    lib/omes/py/architecture/mission_control.py): MC1 source-map shape; MC2
    kind/source/relation/visual-state/version bijection with the scene-view and
    replay-window schemas; MC3 referenced source contracts exist; MC4 every
    upstream state enum value is mapped (anti-drift); MC5 routes are additive
    and point at canonical screens; MC6 relation endpoints are known kinds; MC7
    candidate actions are known and every operation.* action is in the
    operation-request allowlist; MC8 valid fixture semantics (derived
    visual_state, detail routes, relations, ordering); MC9 no forbidden field
    terms (prompt, transcript, tool_args, ...) in allowed fields.
"""
from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
PY_ROOT = REPO_ROOT / "lib" / "omes" / "py"
if str(PY_ROOT) not in sys.path:
    sys.path.insert(0, str(PY_ROOT))

from jobs import schema as schema_mod  # noqa: E402
from architecture import capabilities_view  # noqa: E402
from architecture import mission_control  # noqa: E402

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

# ---------------------------------------------------------------------------
# Semantic-invariant support (issue #247): keyword sets and regexes shared by
# validate_semantic_invariants() below.
# ---------------------------------------------------------------------------

_RAG_TERMS = frozenset({
    "rag", "retrieval", "retriever", "vectorstore", "embedding", "embeddings",
})

_HERMES_RESERVED_TERMS = frozenset({
    "reasoning", "model_routing", "memory", "delegation", "sessions",
})

_SECOND_AGENT_FRAMEWORK_RE = re.compile(
    r"(lang[\s_-]?chain|lang[\s_-]?graph|auto[\s_-]?gen|crew[\s_-]?ai|"
    r"llama[\s_-]?index|semantic[\s_-]?kernel|haystack)",
    re.IGNORECASE,
)

_SIEM_RE = re.compile(r"(siem|wazuh|splunk|sentinel)", re.IGNORECASE)


def _id_segments(cap_id: str) -> list[str]:
    return [seg for seg in re.split(r"[._-]", cap_id or "") if seg]


def _title_words(title: str) -> list[str]:
    return [w for w in re.split(r"\W+", (title or "").lower()) if w]


def _reserved_term_match(cap_id: str, title: str, terms: frozenset[str]) -> str | None:
    """Returns the first reserved term (from `terms`, canonical underscore
    form, e.g. "model_routing") matched anywhere in `cap_id` or `title`,
    treating space, underscore, hyphen and no-separator ("concatenated") as
    equivalent word joins on BOTH sides of the comparison. This closes the
    bypass where a multi-word reserved term such as "model_routing" never
    matches a naturally-spaced title like "OMES Model Routing Helper"
    because the title is tokenised on `\\W+` (splitting on spaces) while the
    reserved term is only ever spelled with an underscore.
    """

    def _tokens(text: str) -> list[str]:
        return [t for t in re.split(r"[\s_.\-]+", (text or "").lower()) if t]

    for text in (cap_id, title):
        words = _tokens(text)
        if not words:
            continue
        for term in terms:
            term_words = term.split("_")
            term_concat = "".join(term_words)
            # underscore/hyphen/space forms all tokenise identically via
            # _tokens(), so a single-token exact match covers all of them;
            # the concatenated form (no separator at all) is checked
            # explicitly since _tokens() never re-joins split words.
            if term in words or term_concat in words:
                return term
            n = len(term_words)
            if n > 1:
                for i in range(len(words) - n + 1):
                    if words[i:i + n] == term_words:
                        return term
    return None


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


def validate_semantic_invariants(
    data: dict[str, Any], repo_root: Path | None = None
) -> list[str]:
    """Semantic plane/authority invariants (issue #247, ADR-0017): R1-R9.

    Each rule fails closed with a distinct, greppable error message prefix
    so a violation can never be silently swallowed by a broader check.
    """
    errors: list[str] = []
    root = repo_root or REPO_ROOT
    capabilities = data.get("capabilities", [])
    if not isinstance(capabilities, list):
        return errors

    for cap in capabilities:
        if not isinstance(cap, dict):
            continue
        cap_id = cap.get("capability_id", "<missing>")
        title = cap.get("title", "")
        authority = cap.get("authority")
        plane = cap.get("plane")
        execution_semantics = cap.get("execution_semantics")
        implementation_status = cap.get("implementation_status")
        omes_module = cap.get("omes_module")
        adr_reference = cap.get("adr_reference")
        upstream_project = cap.get("upstream_project", "")

        segments = {s.lower() for s in _id_segments(cap_id)}
        words = set(_title_words(title))
        haystack_terms = segments | words

        # R1: authority `omes` -> plane must not be agent_runtime/business_control;
        # execution_semantics must not be probabilistic.
        if authority == "omes":
            if plane in ("agent_runtime", "business_control"):
                errors.append(
                    f"R1 capability '{cap_id}': authority 'omes' must not declare plane '{plane}' "
                    "(OMES must not implement a second agent runtime or business-control plane)"
                )
            if execution_semantics == "probabilistic":
                errors.append(
                    f"R1 capability '{cap_id}': authority 'omes' must not declare execution_semantics "
                    "'probabilistic' (OMES is deterministic host tooling, not a reasoning engine)"
                )

        # R2: plane `agent_runtime` and authority != hermes -> requires non-empty
        # adr_reference (R1 still independently blocks authority `omes`).
        if plane == "agent_runtime" and authority != "hermes":
            if not adr_reference or not str(adr_reference).strip():
                errors.append(
                    f"R2 capability '{cap_id}': plane 'agent_runtime' with authority '{authority}' "
                    "(!= 'hermes') requires a non-empty 'adr_reference'"
                )

        # R3: plane `business_control` -> authority in {awcms, provider, external};
        # authority `awcms` -> plane != agent_runtime.
        if plane == "business_control" and authority not in ("awcms", "provider", "external"):
            errors.append(
                f"R3 capability '{cap_id}': plane 'business_control' requires authority in "
                f"{{'awcms', 'provider', 'external'}}, got '{authority}'"
            )
        if authority == "awcms" and plane == "agent_runtime":
            errors.append(
                f"R3 capability '{cap_id}': authority 'awcms' must not declare plane 'agent_runtime'"
            )

        # R4: RAG terms under authority `omes`.
        if authority == "omes" and haystack_terms & _RAG_TERMS:
            errors.append(
                f"R4 capability '{cap_id}': authority 'omes' must not own a RAG/retrieval "
                "capability (matched a RAG-related term in the id or title)"
            )

        # R5: implementation_status `logical_boundary` -> omes_module must be null
        # and authority must not be `omes`.
        if implementation_status == "logical_boundary":
            if omes_module is not None:
                errors.append(
                    f"R5 capability '{cap_id}': implementation_status 'logical_boundary' requires "
                    f"omes_module to be null, got {omes_module!r}"
                )
            if authority == "omes":
                errors.append(
                    f"R5 capability '{cap_id}': implementation_status 'logical_boundary' must not "
                    "declare authority 'omes' (a logical boundary is not a component OMES ships)"
                )

        # R6: authority `omes` and implementation_status `implemented` ->
        # omes_module non-null and implementation evidence exists in the repo.
        # Also: authority omes + plane tool_data + id/title containing
        # "gateway" must satisfy the same evidence requirement (no universal
        # OMES tool gateway without registered capability + implementation
        # evidence).
        is_gateway_claim = (
            authority == "omes" and plane == "tool_data" and "gateway" in haystack_terms
        )
        if (authority == "omes" and implementation_status == "implemented") or is_gateway_claim:
            if not omes_module:
                errors.append(
                    f"R6 capability '{cap_id}': authority 'omes' with implementation_status "
                    "'implemented' (or a tool_data 'gateway' claim) requires a non-null 'omes_module'"
                )
            else:
                if not _has_implementation_evidence(root, str(omes_module)):
                    errors.append(
                        f"R6 capability '{cap_id}': no implementation evidence found in the repo for "
                        f"omes_module '{omes_module}' (expected lib/omes/py/<module>/, or modules/ "
                        "containing it, or lib/omes/<module>*)"
                    )

        # R7: authority `external` -> implementation_status in
        # {optional_external, staged}; a SIEM-like id/title must never be
        # presented as OMES-shipped core (threat-model AR-05), so this check
        # runs regardless of `authority` -- it is NOT nested under the
        # `authority == "external"` branch, because a SIEM-like capability
        # declared under any other authority (e.g. "omes") is itself the
        # violation, not a formatting detail of the external branch.
        if authority == "external":
            if implementation_status not in ("optional_external", "staged"):
                errors.append(
                    f"R7 capability '{cap_id}': authority 'external' requires implementation_status "
                    f"in {{'optional_external', 'staged'}}, got '{implementation_status}'"
                )

        if _SIEM_RE.search(cap_id or "") or _SIEM_RE.search(title or ""):
            if authority != "external":
                errors.append(
                    f"R7 capability '{cap_id}': SIEM-like capability must declare authority "
                    f"'external', got '{authority}' (SIEM/external observability must never be "
                    "presented as OMES-shipped core)"
                )
            else:
                has_evidence = bool(omes_module) and _has_implementation_evidence(
                    root, str(omes_module)
                )
                if implementation_status != "optional_external" and not (
                    implementation_status == "implemented" and has_evidence
                ):
                    errors.append(
                        f"R7 capability '{cap_id}': SIEM-like capability under authority 'external' "
                        "must declare implementation_status 'optional_external' unless implemented "
                        "with repo evidence (R6)"
                    )

        # R8: second agent framework references require a non-empty
        # adr_reference AND authority != omes.
        if (
            _SECOND_AGENT_FRAMEWORK_RE.search(cap_id or "")
            or _SECOND_AGENT_FRAMEWORK_RE.search(title or "")
            or _SECOND_AGENT_FRAMEWORK_RE.search(upstream_project or "")
        ):
            if not adr_reference or not str(adr_reference).strip():
                errors.append(
                    f"R8 capability '{cap_id}': references a second agent framework and requires a "
                    "non-empty 'adr_reference'"
                )
            if authority == "omes":
                errors.append(
                    f"R8 capability '{cap_id}': references a second agent framework and must not "
                    "declare authority 'omes'"
                )

        # R9: Hermes-reserved namespace and terms.
        if (cap_id or "").startswith("hermes.") and authority != "hermes":
            errors.append(
                f"R9 capability '{cap_id}': capability_id in the reserved 'hermes.' namespace "
                f"requires authority 'hermes', got '{authority}'"
            )
        reserved_match = _reserved_term_match(cap_id, title, _HERMES_RESERVED_TERMS)
        if authority == "omes" and reserved_match:
            errors.append(
                f"R9 capability '{cap_id}': authority 'omes' must not own a Hermes-reserved runtime "
                f"concern (matched reserved term '{reserved_match}' in the id or title; reserved: "
                "reasoning/model_routing/memory/delegation/sessions)"
            )

    return errors


def _has_implementation_evidence(repo_root: Path, module: str) -> bool:
    """Returns True if `module` has on-disk evidence of implementation under
    lib/omes/py/<module>/, modules/ (a path containing <module>), or a
    lib/omes/<module>* sibling tree."""
    candidate = repo_root / "lib" / "omes" / "py" / module
    if candidate.is_dir():
        return True

    modules_dir = repo_root / "modules"
    if modules_dir.is_dir():
        for path in modules_dir.rglob("*"):
            if module in path.name:
                return True

    lib_omes_dir = repo_root / "lib" / "omes"
    if lib_omes_dir.is_dir():
        for path in lib_omes_dir.glob(f"{module}*"):
            return True

    return False


def check_control_center_contracts(repo_root: Path) -> list[str]:
    """C1-C2: Control Center contract safety (issue #247).

    C1: no *.schema.json under contracts/control-center/v1/ (recursively,
    excluding fixtures/) declares a property key that looks like a raw shell
    command escape hatch, at any nesting.
    C2: operation-request.schema.json declares additionalProperties: false
    at the top level, `operation` has a non-empty enum (allowlist,
    resolving $ref/$defs if used), and `required` includes tenant_id,
    correlation_id, idempotency_key, actor, operation, target, permission.
    """
    errors: list[str] = []
    cc_dir = repo_root / "contracts" / "control-center" / "v1"
    if not cc_dir.is_dir():
        return errors

    forbidden_keys = frozenset({
        "command", "cmd", "shell", "script", "exec", "argv",
        "shell_command", "raw_command",
    })

    def _walk_for_forbidden_keys(node: Any, path: Path) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key in forbidden_keys:
                    errors.append(
                        f"C1 {path}: declares forbidden property key '{key}' "
                        "(no schema.json may expose a raw shell-command escape hatch)"
                    )
                _walk_for_forbidden_keys(value, path)
        elif isinstance(node, list):
            for item in node:
                _walk_for_forbidden_keys(item, path)

    for schema_file in sorted(cc_dir.rglob("*.schema.json")):
        try:
            rel = schema_file.relative_to(cc_dir)
        except ValueError:
            rel = schema_file
        if rel.parts and rel.parts[0] == "fixtures":
            continue
        try:
            schema_data = json.loads(schema_file.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"C1 {schema_file}: failed to parse JSON: {exc}")
            continue
        _walk_for_forbidden_keys(schema_data, schema_file)

    op_request_path = cc_dir / "operation-request.schema.json"
    if op_request_path.is_file():
        try:
            op_schema = json.loads(op_request_path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"C2 {op_request_path}: failed to parse JSON: {exc}")
            op_schema = None

        if isinstance(op_schema, dict):
            if op_schema.get("additionalProperties") is not False:
                errors.append(
                    f"C2 {op_request_path}: top-level 'additionalProperties' must be false"
                )

            defs = op_schema.get("$defs") or op_schema.get("definitions") or {}

            def _resolve(node: Any) -> Any:
                if isinstance(node, dict) and "$ref" in node:
                    ref = node["$ref"]
                    if isinstance(ref, str) and ref.startswith("#/$defs/"):
                        return defs.get(ref[len("#/$defs/"):], {})
                    if isinstance(ref, str) and ref.startswith("#/definitions/"):
                        return defs.get(ref[len("#/definitions/"):], {})
                    return {}
                return node

            properties = op_schema.get("properties", {})
            operation_schema = _resolve(properties.get("operation", {}))
            enum = operation_schema.get("enum") if isinstance(operation_schema, dict) else None
            if not isinstance(enum, list) or len(enum) == 0:
                errors.append(
                    f"C2 {op_request_path}: 'operation' property must declare a non-empty enum "
                    "(allowlist)"
                )

            required = op_schema.get("required", [])
            required_fields = {
                "tenant_id", "correlation_id", "idempotency_key", "actor",
                "operation", "target", "permission",
            }
            missing = required_fields - set(required if isinstance(required, list) else [])
            if missing:
                errors.append(
                    f"C2 {op_request_path}: 'required' is missing {sorted(missing)}"
                )
        elif op_schema is not None:
            errors.append(f"C2 {op_request_path}: schema root must be an object")
    else:
        errors.append(
            f"C2 {op_request_path}: missing; the typed operation allowlist contract is required"
        )

    return errors


_CANONICAL_DOCS = (
    "docs/architecture.md",
    "docs/scope.md",
    "docs/security.md",
    "docs/threat-model.md",
    "docs/ai-data-privacy-and-model-security.md",
    "docs/control-center-and-integrations.md",
)

_UNSUPPORTED_OS_RE = re.compile(
    r"(Debian|Fedora|RHEL|Red Hat|CentOS|Rocky Linux|AlmaLinux|openSUSE|macOS|Windows)",
    re.IGNORECASE,
)
_SUPPORT_RE = re.compile(r"support", re.IGNORECASE)
_NEGATION_RE = re.compile(
    r"(\bnot\b|unsupported|\bno \b|non-goal|\bnever\b|out of scope|isn't|aren't|n't)",
    re.IGNORECASE,
)
_ARCH_LINUX_RE = re.compile(r"arch linux", re.IGNORECASE)

_REFERENCE_ARCHITECTURE_MARKER = "<!-- omes:reference-architecture:v1 -->"
_MEDIATE_PHRASE = "does not mediate all Hermes-native tool execution"


def check_canonical_documentation(repo_root: Path) -> list[str]:
    """D1-D2: canonical documentation invariants (issue #247).

    Gracefully skips any canonical doc that does not exist under
    `repo_root` (real repo: all six must exist; test tmp roots: only the
    files a test constructs are checked).
    """
    errors: list[str] = []

    for rel in _CANONICAL_DOCS:
        doc_path = repo_root / rel
        if not doc_path.is_file():
            continue
        try:
            text = doc_path.read_text(encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"D1 {doc_path}: failed to read: {exc}")
            continue

        for lineno, line in enumerate(text.splitlines(), start=1):
            if _ARCH_LINUX_RE.search(line):
                continue
            if _UNSUPPORTED_OS_RE.search(line) and _SUPPORT_RE.search(line):
                if not _NEGATION_RE.search(line):
                    errors.append(
                        f"D1 {doc_path}:{lineno}: line names an unsupported OS alongside "
                        "'support' with no negation - only Ubuntu Server 26.04/24.04/22.04 LTS "
                        f"and Linux Mint 22.x are supported: {line.strip()!r}"
                    )

    arch_doc = repo_root / "docs" / "architecture.md"
    if arch_doc.is_file():
        try:
            arch_text = arch_doc.read_text(encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"D2 {arch_doc}: failed to read: {exc}")
            arch_text = None

        if arch_text is not None:
            marker_idx = arch_text.find(_REFERENCE_ARCHITECTURE_MARKER)
            if marker_idx == -1:
                errors.append(
                    f"D2 {arch_doc}: missing required marker '{_REFERENCE_ARCHITECTURE_MARKER}'"
                )
            else:
                remainder = arch_text[marker_idx:]
                if "```mermaid" not in remainder:
                    errors.append(
                        f"D2 {arch_doc}: no ```mermaid block found after the "
                        "reference-architecture marker"
                    )
            if _MEDIATE_PHRASE not in arch_text:
                errors.append(
                    f"D2 {arch_doc}: missing required phrase {_MEDIATE_PHRASE!r}"
                )

    return errors


_ARCHITECTURE_CAPABILITIES_VIEW_FIXTURE = (
    Path("contracts")
    / "control-center"
    / "v1"
    / "fixtures"
    / "architecture-capabilities-view"
    / "valid-01-generated.json"
)


def check_architecture_capabilities_view(
    repo_root: Path, registry_data: dict[str, Any] | None = None
) -> list[str]:
    """AV1: the checked-in `architecture-capabilities-view` fixture (issue
    #246, part 3, `contracts/control-center/v1/architecture-capabilities-view.schema.json`)
    must not be stale relative to architecture/capabilities.json. Regenerates
    the projection in memory from the same builder the generator script uses
    (`lib/omes/py/architecture/capabilities_view.build_fixture_view()`) and
    diffs it, byte-for-byte, against the fixture on disk - the same
    signature `scripts/generate-architecture-capabilities-view.py --check`
    uses, so a capability added/changed/removed in the registry without
    regenerating the fixture fails this guard.

    Gracefully skips (returns no errors) when the fixture file does not
    exist under `repo_root` yet, so this check does not break test tmp
    roots that construct only a subset of the real repository tree.
    """
    errors: list[str] = []
    fixture_path = repo_root / _ARCHITECTURE_CAPABILITIES_VIEW_FIXTURE
    if not fixture_path.is_file():
        return errors

    try:
        data = registry_data if registry_data is not None else load_registry(repo_root / "architecture" / "capabilities.json")
        expected = capabilities_view.build_fixture_view(repo_root, data)
        expected_text = json.dumps(expected, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    except Exception as exc:  # noqa: BLE001
        return [f"AV1 {fixture_path}: failed to build expected projection: {exc}"]

    try:
        current_text = fixture_path.read_text(encoding="utf-8")
    except OSError as exc:
        return [f"AV1 {fixture_path}: failed to read: {exc}"]

    if current_text != expected_text:
        errors.append(
            f"AV1 {fixture_path}: stale relative to architecture/capabilities.json - "
            "run scripts/generate-architecture-capabilities-view.py"
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
    all_errors.extend(validate_semantic_invariants(reg_data, root))
    all_errors.extend(check_control_center_contracts(root))
    all_errors.extend(check_canonical_documentation(root))
    all_errors.extend(check_architecture_capabilities_view(root, reg_data))
    all_errors.extend(mission_control.check_mission_control(root))

    return all_errors
