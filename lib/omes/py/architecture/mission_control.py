"""lib/omes/py/architecture/mission_control.py - Mission Control source-map
consistency guards MC1-MC9 (issue #264, epic #263, ADR-0031).

The AWCMS 3D Mission Control workspace is a derived, read-only compositional
projection. Three contracts under contracts/control-center/v1/ define it:

  - mission-control-source-map.json          (kind -> authority/source/state map)
  - mission-control-scene-view.schema.json   (live/historical scene wire shape)
  - mission-control-replay-window.schema.json (bounded replay evidence page)

This module keeps those three files, the source contracts the map references,
the operation-request allowlist, and the checked-in valid fixtures consistent,
so the map can never silently drift from the contracts it summarises. It is
stdlib-only (ADR-0012) and fail-closed: any file that cannot be read or parsed
is reported as an error, never skipped.

Guards (each error string is prefixed "MCn <path>: "):
  MC1 source-map shape (required keys, kind keys, state_map values, replay
      basis, source/authority references, read_only kind actions).
  MC2 kinds/sources/relations/visual-state/version bijection between the source
      map and both JSON schemas.
  MC3 every referenced source contract exists under contracts/control-center/v1.
  MC4 state coverage: every enum value of every referenced contract state field
      is a state_map key (a new upstream state fails CI until it is mapped).
  MC5 routes: workspace route is additive, detail routes are canonical screens,
      canonical inventory is exactly the 14 /admin/omes routes plus
      /admin/approvals.
  MC6 relation endpoints reference known kinds.
  MC7 actions: candidate actions exist, `operation.*` actions are in the
      operation-request allowlist (no operation may be invented here, see
      AGENTS.md section 5), shape of each action entry.
  MC8 fixture semantics for every valid-*.json scene-view and replay-window
      fixture (derived visual_state, detail routes, relations, uniqueness,
      ordering, live-mode rules).
  MC9 forbidden field terms (prompt, transcript, tool_args, ...) never appear
      in allowed_fields / label_field / summary_field.

`derive_visual_state` is the single deterministic implementation of the
freshness rule described in the source map; AWCMS vendors the same table.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent

CONTRACT_DIR = Path("contracts") / "control-center" / "v1"
SOURCE_MAP_NAME = "mission-control-source-map.json"
SCENE_SCHEMA_NAME = "mission-control-scene-view.schema.json"
REPLAY_SCHEMA_NAME = "mission-control-replay-window.schema.json"
OPERATION_SCHEMA_NAME = "operation-request.schema.json"
SCENE_FIXTURE_DIR = CONTRACT_DIR / "fixtures" / "mission-control-scene-view"
REPLAY_FIXTURE_DIR = CONTRACT_DIR / "fixtures" / "mission-control-replay-window"

REQUIRED_TOP_KEYS = (
    "source_map_version",
    "workspace",
    "canonical_screens",
    "visual_states",
    "freshness_rule",
    "sources",
    "kinds",
    "relations",
    "actions",
    "replay_bases",
    "forbidden_field_terms",
)

REQUIRED_KIND_KEYS = (
    "zone",
    "source",
    "source_id_field",
    "label_field",
    "summary_field",
    "state_field",
    "allowed_fields",
    "detail_route",
    "interaction",
    "candidate_actions",
    "replay_basis",
    "contract_state_enums",
    "projection_state_values",
    "state_map",
)

INTERACTIONS = frozenset({"read_only", "shortcut_to_existing_actions"})
AUTHORITIES = frozenset({"omes", "hermes", "awcms", "provider"})
OPEN_DETAILS = "open_details"

EXPECTED_CANONICAL_ROUTES = frozenset({
    "/admin/omes",
    "/admin/omes/servers",
    "/admin/omes/deployments",
    "/admin/omes/operations",
    "/admin/omes/jobs",
    "/admin/omes/health",
    "/admin/omes/backups",
    "/admin/omes/audit",
    "/admin/omes/enrollments",
    "/admin/omes/ai-privacy",
    "/admin/omes/orkestrasi-langsung",
    "/admin/omes/hermes",
    "/admin/omes/progres-hermes",
    "/admin/omes/arsitektur",
    "/admin/approvals",
})
NON_OMES_ALLOWED_ROUTE = "/admin/approvals"
OMES_ROUTE_PREFIX = "/admin/omes/"


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def derive_visual_state(
    source_map: dict[str, Any], kind: str, source_state: str, freshness: str
) -> str:
    """Return the deterministic visual_state for (kind, source_state, freshness).

    base = kinds[kind].state_map.get(source_state, freshness_rule.unmapped_state)
    freshness "unknown" -> freshness_rule.unknown
    freshness "stale"   -> base if base in stale_keeps else stale_otherwise
    otherwise           -> base

    Fail-closed: an unknown `kind` returns freshness_rule.unmapped_state
    ("unknown") rather than raising; callers that need to *report* an unknown
    kind (MC8) check membership in source_map["kinds"] separately. An
    unrecognised freshness value is treated like "live" (the schemas constrain
    it to live/stale/unknown).
    """
    rule = source_map["freshness_rule"]
    kinds = source_map.get("kinds", {})
    kind_entry = kinds.get(kind)
    if kind_entry is None:
        base = rule["unmapped_state"]
    else:
        base = kind_entry["state_map"].get(source_state, rule["unmapped_state"])
    if freshness == "unknown":
        return rule["unknown"]
    if freshness == "stale":
        return base if base in rule["stale_keeps"] else rule["stale_otherwise"]
    return base


def _field_tokens(name: str) -> list[str]:
    """Lower-cased tokens of a field path, split on '.', '_' and camelCase."""
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name)
    return [t for t in re.split(r"[._]+", spaced.lower()) if t]


def field_is_forbidden(field: str, forbidden_terms: list[str]) -> str | None:
    """Return the first forbidden term matching `field`, else None.

    A field matches when a forbidden term equals its leaf name, or the term's
    underscore-split tokens appear as a contiguous token sequence in the
    field's '.'/'_'-split tokens. Hence "active_tool" is allowed while
    "tool_args", "prompt_text" and "payload.raw" are not.
    """
    tokens = _field_tokens(field)
    leaf = field.rsplit(".", 1)[-1].lower()
    for term in forbidden_terms:
        term_l = str(term).lower()
        if leaf == term_l:
            return str(term)
        term_tokens = [t for t in term_l.split("_") if t]
        n = len(term_tokens)
        if n == 0:
            continue
        for i in range(len(tokens) - n + 1):
            if tokens[i:i + n] == term_tokens:
                return str(term)
    return None


def _dig(obj: Any, path: list[str]) -> Any:
    """Walk `path` through nested dicts; raise KeyError describing a miss."""
    cur = obj
    for i, key in enumerate(path):
        if not isinstance(cur, dict) or key not in cur:
            raise KeyError("/".join(path[: i + 1]))
        cur = cur[key]
    return cur


def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _rel(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


# ---------------------------------------------------------------------------
# MC1 - source-map shape
# ---------------------------------------------------------------------------


def check_source_map_shape(smap: dict[str, Any], label: str) -> list[str]:
    errors: list[str] = []

    def err(msg: str) -> None:
        errors.append(f"MC1 {label}: {msg}")

    for key in REQUIRED_TOP_KEYS:
        if key not in smap:
            err(f"missing required top-level key '{key}'")
    if errors:
        return errors

    visual_states = smap["visual_states"]
    sources = smap["sources"]
    kinds = smap["kinds"]
    replay_bases = smap["replay_bases"]
    if not isinstance(visual_states, dict) or not visual_states:
        err("'visual_states' must be a non-empty object")
        return errors
    if not isinstance(sources, dict) or not isinstance(kinds, dict) or not kinds:
        err("'sources' and 'kinds' must be objects (kinds non-empty)")
        return errors
    if not isinstance(replay_bases, dict):
        err("'replay_bases' must be an object")
        return errors

    rule = smap["freshness_rule"]
    if not isinstance(rule, dict):
        err("'freshness_rule' must be an object")
    else:
        for key in ("unknown", "stale_keeps", "stale_otherwise", "unmapped_state"):
            if key not in rule:
                err(f"freshness_rule missing '{key}'")
        if not errors:
            for key in ("unknown", "stale_otherwise", "unmapped_state"):
                if rule[key] not in visual_states:
                    err(f"freshness_rule.{key}='{rule[key]}' is not a visual_states key")
            keeps = rule["stale_keeps"]
            if not isinstance(keeps, list) or any(k not in visual_states for k in keeps):
                err("freshness_rule.stale_keeps must list visual_states keys")

    for sname, src in sources.items():
        if not isinstance(src, dict):
            err(f"source '{sname}' must be an object")
            continue
        if src.get("authority") not in AUTHORITIES:
            err(f"source '{sname}' authority must be one of {sorted(AUTHORITIES)}")
        if not isinstance(src.get("contracts"), list):
            err(f"source '{sname}' must declare a 'contracts' list")

    for kname, kind in kinds.items():
        if not isinstance(kind, dict):
            err(f"kind '{kname}' must be an object")
            continue
        missing = [k for k in REQUIRED_KIND_KEYS if k not in kind]
        if missing:
            err(f"kind '{kname}' missing keys {missing}")
            continue
        if kind["source"] not in sources:
            err(f"kind '{kname}' source '{kind['source']}' is not in sources")
        if kind["interaction"] not in INTERACTIONS:
            err(f"kind '{kname}' interaction must be one of {sorted(INTERACTIONS)}")
        if kind["replay_basis"] not in replay_bases:
            err(f"kind '{kname}' replay_basis '{kind['replay_basis']}' is not in replay_bases")
        actions = kind["candidate_actions"]
        if not isinstance(actions, list):
            err(f"kind '{kname}' candidate_actions must be a list")
        else:
            if OPEN_DETAILS not in actions:
                err(f"kind '{kname}' candidate_actions must include '{OPEN_DETAILS}'")
            if kind["interaction"] == "read_only" and actions != [OPEN_DETAILS]:
                err(f"read_only kind '{kname}' candidate_actions must be exactly ['{OPEN_DETAILS}']")
        if not isinstance(kind["allowed_fields"], list):
            err(f"kind '{kname}' allowed_fields must be a list")
        if not isinstance(kind["projection_state_values"], list):
            err(f"kind '{kname}' projection_state_values must be a list")
        enums = kind["contract_state_enums"]
        if not isinstance(enums, list):
            err(f"kind '{kname}' contract_state_enums must be a list")
        else:
            for i, entry in enumerate(enums):
                if (
                    not isinstance(entry, dict)
                    or not isinstance(entry.get("contract"), str)
                    or not isinstance(entry.get("path"), list)
                ):
                    err(f"kind '{kname}' contract_state_enums[{i}] needs 'contract' string and 'path' list")
        state_map = kind["state_map"]
        if not isinstance(state_map, dict) or not state_map:
            err(f"kind '{kname}' state_map must be a non-empty object")
        else:
            for sval, vstate in state_map.items():
                if vstate not in visual_states:
                    err(f"kind '{kname}' state_map['{sval}']='{vstate}' is not a visual_states key")
    return errors


# ---------------------------------------------------------------------------
# MC2 - bijection with schemas
# ---------------------------------------------------------------------------


def _enum_at(schema: dict[str, Any], path: list[str], label: str, errors: list[str]) -> list[str] | None:
    try:
        node = _dig(schema, path)
    except KeyError as exc:
        errors.append(f"MC2 {label}: cannot resolve schema path {exc}")
        return None
    enum = node.get("enum") if isinstance(node, dict) else None
    if not isinstance(enum, list):
        errors.append(f"MC2 {label}: schema path {'/'.join(path)} has no enum")
        return None
    return enum


def check_schema_bijection(
    smap: dict[str, Any],
    scene: dict[str, Any],
    replay: dict[str, Any],
    scene_label: str = SCENE_SCHEMA_NAME,
    replay_label: str = REPLAY_SCHEMA_NAME,
) -> list[str]:
    errors: list[str] = []
    kind_keys = set(smap["kinds"])
    visual_keys = set(smap["visual_states"])

    def compare(what: str, expected: set[str], enum: list[str] | None, label: str) -> None:
        if enum is None:
            return
        actual = set(enum)
        if actual != expected:
            only_map = sorted(expected - actual)
            only_schema = sorted(actual - expected)
            errors.append(
                f"MC2 {label}: {what} differs from source map "
                f"(only in source map: {only_map}; only in schema: {only_schema})"
            )

    scene_node = ["properties", "nodes", "items", "properties"]
    compare("node kind enum", kind_keys,
            _enum_at(scene, scene_node + ["kind"], scene_label, errors), scene_label)
    compare("node visual_state enum", visual_keys,
            _enum_at(scene, scene_node + ["visual_state"], scene_label, errors), scene_label)
    compare("sources[].source_kind enum", set(smap["sources"]),
            _enum_at(scene, ["properties", "sources", "items", "properties", "source_kind"],
                     scene_label, errors), scene_label)
    compare("relations[].relation enum", set(smap["relations"]),
            _enum_at(scene, ["properties", "relations", "items", "properties", "relation"],
                     scene_label, errors), scene_label)

    replay_ev = ["properties", "events", "items", "properties"]
    compare("event kind enum", kind_keys,
            _enum_at(replay, replay_ev + ["kind"], replay_label, errors), replay_label)
    compare("event visual_state enum", visual_keys,
            _enum_at(replay, replay_ev + ["visual_state"], replay_label, errors), replay_label)

    for schema, label in ((scene, scene_label), (replay, replay_label)):
        enum = _enum_at(schema, ["properties", "source_map_version"], label, errors)
        if enum is not None and enum != [smap["source_map_version"]]:
            errors.append(
                f"MC2 {label}: source_map_version enum {enum} must be exactly "
                f"['{smap['source_map_version']}'] (bump schema and map together)"
            )
    return errors


# ---------------------------------------------------------------------------
# MC3 / MC4 - source contracts and state coverage
# ---------------------------------------------------------------------------


def _contract_path(root: Path, name: str) -> Path:
    return root / CONTRACT_DIR / f"{name}.schema.json"


def check_source_contracts(smap: dict[str, Any], root: Path, label: str) -> list[str]:
    errors: list[str] = []
    for sname, src in smap["sources"].items():
        names = list(src.get("contracts", []))
        if not names and src.get("authority") != "awcms":
            errors.append(
                f"MC3 {label}: source '{sname}' (authority {src.get('authority')}) "
                f"must list at least one source contract"
            )
        for name in names:
            if not _contract_path(root, name).is_file():
                errors.append(
                    f"MC3 {label}: source '{sname}' contract '{name}' not found at "
                    f"{(CONTRACT_DIR / (name + '.schema.json')).as_posix()}"
                )
    for kname, kind in smap["kinds"].items():
        for entry in kind.get("contract_state_enums", []):
            name = entry.get("contract")
            if not _contract_path(root, str(name)).is_file():
                errors.append(
                    f"MC3 {label}: kind '{kname}' contract_state_enums contract '{name}' "
                    f"not found under {CONTRACT_DIR.as_posix()}"
                )
    return errors


def check_state_coverage(smap: dict[str, Any], root: Path, label: str) -> list[str]:
    errors: list[str] = []
    for kname, kind in smap["kinds"].items():
        state_map = kind["state_map"]
        for entry in kind.get("contract_state_enums", []):
            name = entry["contract"]
            path = entry["path"]
            cpath = _contract_path(root, name)
            try:
                schema = _load_json(cpath)
                node = _dig(schema, path)
            except (OSError, ValueError, KeyError) as exc:
                errors.append(
                    f"MC4 {label}: kind '{kname}' cannot resolve {name} path "
                    f"{'/'.join(path)}: {type(exc).__name__}: {exc}"
                )
                continue
            enum = node.get("enum") if isinstance(node, dict) else None
            if not isinstance(enum, list):
                errors.append(
                    f"MC4 {label}: kind '{kname}' {name} path {'/'.join(path)} "
                    f"does not resolve to an enum"
                )
                continue
            for value in enum:
                if value not in state_map:
                    errors.append(
                        f"MC4 {label}: kind '{kname}' state '{value}' from {name} "
                        f"{'/'.join(path)} is not in state_map (map every upstream state)"
                    )
        for value in kind.get("projection_state_values", []):
            if value not in state_map:
                errors.append(
                    f"MC4 {label}: kind '{kname}' projection_state_values '{value}' "
                    f"is not in state_map"
                )
    return errors


# ---------------------------------------------------------------------------
# MC5 - routes
# ---------------------------------------------------------------------------


def check_routes(smap: dict[str, Any], label: str) -> list[str]:
    errors: list[str] = []
    screens = smap["canonical_screens"]
    routes: list[str] = []
    for i, screen in enumerate(screens if isinstance(screens, list) else []):
        route = screen.get("route") if isinstance(screen, dict) else None
        if not isinstance(route, str):
            errors.append(f"MC5 {label}: canonical_screens[{i}] has no route string")
            continue
        routes.append(route)
    dupes = sorted({r for r in routes if routes.count(r) > 1})
    if dupes:
        errors.append(f"MC5 {label}: duplicate canonical_screens routes {dupes}")
    route_set = set(routes)
    if route_set != EXPECTED_CANONICAL_ROUTES:
        errors.append(
            f"MC5 {label}: canonical_screens must be exactly the 14 /admin/omes routes "
            f"plus {NON_OMES_ALLOWED_ROUTE} (missing: "
            f"{sorted(EXPECTED_CANONICAL_ROUTES - route_set)}; unexpected: "
            f"{sorted(route_set - EXPECTED_CANONICAL_ROUTES)})"
        )

    workspace = smap["workspace"]
    wroute = workspace.get("route") if isinstance(workspace, dict) else None
    if not isinstance(wroute, str) or not wroute.startswith(OMES_ROUTE_PREFIX):
        errors.append(f"MC5 {label}: workspace.route must start with '{OMES_ROUTE_PREFIX}'")
    elif wroute in route_set:
        errors.append(f"MC5 {label}: workspace.route '{wroute}' collides with a canonical screen")
    if not isinstance(workspace, dict) or workspace.get("replaces_screens") != []:
        errors.append(f"MC5 {label}: workspace.replaces_screens must be an empty list")

    for kname, kind in smap["kinds"].items():
        if kind.get("detail_route") not in route_set:
            errors.append(
                f"MC5 {label}: kind '{kname}' detail_route '{kind.get('detail_route')}' "
                f"is not a canonical_screens route"
            )
    return errors


# ---------------------------------------------------------------------------
# MC6 - relations
# ---------------------------------------------------------------------------


def check_relations(smap: dict[str, Any], label: str) -> list[str]:
    errors: list[str] = []
    known = set(smap["kinds"])
    for rname, rel in smap["relations"].items():
        if not isinstance(rel, dict):
            errors.append(f"MC6 {label}: relation '{rname}' must be an object")
            continue
        for side in ("from", "to"):
            kinds = rel.get(side)
            if not isinstance(kinds, list) or not kinds:
                errors.append(f"MC6 {label}: relation '{rname}' '{side}' must be a non-empty list")
                continue
            for k in kinds:
                if k not in known:
                    errors.append(f"MC6 {label}: relation '{rname}' {side} kind '{k}' is not a known kind")
    return errors


# ---------------------------------------------------------------------------
# MC7 - actions
# ---------------------------------------------------------------------------


def check_actions(smap: dict[str, Any], operations: list[str], label: str) -> list[str]:
    errors: list[str] = []
    actions = smap["actions"]
    for kname, kind in smap["kinds"].items():
        for action in kind.get("candidate_actions", []):
            if action not in actions:
                errors.append(f"MC7 {label}: kind '{kname}' candidate action '{action}' is not in actions")
    for aname, action in actions.items():
        if aname.startswith("operation."):
            op = aname[len("operation."):]
            if op not in operations:
                errors.append(
                    f"MC7 {label}: action '{aname}' operation '{op}' is not in the "
                    f"operation-request allowlist {operations}; Mission Control cannot invent "
                    f"operations (AGENTS.md section 5: update operation-request.schema.json, "
                    f"fixtures, and the upstream enum test together)"
                )
        if not isinstance(action, dict):
            errors.append(f"MC7 {label}: action '{aname}' must be an object")
            continue
        mutating = action.get("mutating")
        path = action.get("existing_path")
        if not isinstance(mutating, bool):
            errors.append(f"MC7 {label}: action '{aname}' needs boolean 'mutating'")
        if not isinstance(path, str) or not path:
            errors.append(f"MC7 {label}: action '{aname}' needs string 'existing_path'")
        elif mutating is False and path.strip().upper().startswith("POST"):
            errors.append(f"MC7 {label}: non-mutating action '{aname}' must not use a POST path")
    return errors


# ---------------------------------------------------------------------------
# MC9 - forbidden fields
# ---------------------------------------------------------------------------


def check_forbidden_fields(smap: dict[str, Any], label: str) -> list[str]:
    errors: list[str] = []
    terms = smap["forbidden_field_terms"]
    if not isinstance(terms, list) or not terms:
        return [f"MC9 {label}: forbidden_field_terms must be a non-empty list"]
    for kname, kind in smap["kinds"].items():
        candidates: list[tuple[str, str]] = [
            ("allowed_fields", f) for f in kind.get("allowed_fields", [])
        ]
        for key in ("label_field", "summary_field"):
            value = kind.get(key)
            if value is not None:
                candidates.append((key, str(value)))
        for where, field in candidates:
            hit = field_is_forbidden(str(field), terms)
            if hit is not None:
                errors.append(
                    f"MC9 {label}: kind '{kname}' {where} '{field}' matches forbidden term '{hit}'"
                )
    return errors


# ---------------------------------------------------------------------------
# MC8 - fixture semantics
# ---------------------------------------------------------------------------


def check_scene_fixture(smap: dict[str, Any], doc: Any, label: str) -> list[str]:
    """MC8 semantic checks for one valid scene-view fixture."""
    errors: list[str] = []

    def err(msg: str) -> None:
        errors.append(f"MC8 {label}: {msg}")

    if not isinstance(doc, dict):
        return [f"MC8 {label}: fixture must be a JSON object"]
    nodes = doc.get("nodes")
    relations = doc.get("relations")
    sources = doc.get("sources")
    if not isinstance(nodes, list) or not isinstance(relations, list) or not isinstance(sources, list):
        return [f"MC8 {label}: nodes, relations and sources must be arrays"]

    by_id: dict[str, dict[str, Any]] = {}
    seen_pairs: set[tuple[Any, Any]] = set()
    for i, node in enumerate(nodes):
        if not isinstance(node, dict):
            err(f"nodes[{i}] must be an object")
            continue
        nid = node.get("node_id")
        kind = node.get("kind")
        if nid in by_id:
            err(f"duplicate node_id '{nid}'")
        else:
            by_id[nid] = node
        pair = (kind, node.get("source_id"))
        if pair in seen_pairs:
            err(f"duplicate (kind, source_id) {pair}")
        seen_pairs.add(pair)
        if kind not in smap["kinds"]:
            err(f"node '{nid}' has unknown kind '{kind}'")
            continue
        expected = derive_visual_state(
            smap, kind, str(node.get("source_state")), str(node.get("freshness"))
        )
        if node.get("visual_state") != expected:
            err(
                f"node '{nid}' ({kind}) visual_state '{node.get('visual_state')}' != "
                f"derived '{expected}' from source_state '{node.get('source_state')}' "
                f"and freshness '{node.get('freshness')}'"
            )
        route = str(node.get("detail_route", "")).split("?", 1)[0]
        if route != smap["kinds"][kind]["detail_route"]:
            err(
                f"node '{nid}' ({kind}) detail_route path '{route}' != kind detail_route "
                f"'{smap['kinds'][kind]['detail_route']}'"
            )

    for i, rel in enumerate(relations):
        if not isinstance(rel, dict):
            err(f"relations[{i}] must be an object")
            continue
        src, dst, rname = rel.get("from"), rel.get("to"), rel.get("relation")
        if src not in by_id or dst not in by_id:
            err(f"relations[{i}] references missing node(s) from='{src}' to='{dst}'")
            continue
        rdef = smap["relations"].get(rname)
        if rdef is None:
            err(f"relations[{i}] unknown relation '{rname}'")
            continue
        fk, tk = by_id[src].get("kind"), by_id[dst].get("kind")
        if fk not in rdef["from"] or tk not in rdef["to"]:
            err(f"relations[{i}] '{rname}' does not allow {fk} -> {tk}")

    source_kinds = [s.get("source_kind") if isinstance(s, dict) else None for s in sources]
    for sk in sorted({str(s) for s in source_kinds if source_kinds.count(s) > 1}):
        err(f"duplicate sources[] source_kind '{sk}'")

    if doc.get("mode") == "live":
        if doc.get("as_of") != doc.get("generated_at"):
            err("live mode requires as_of == generated_at")
        if doc.get("evidence_gaps"):
            err("live mode must not carry evidence_gaps entries")
    return errors


def check_replay_fixture(smap: dict[str, Any], doc: Any, label: str) -> list[str]:
    """MC8 semantic checks for one valid replay-window fixture."""
    errors: list[str] = []

    def err(msg: str) -> None:
        errors.append(f"MC8 {label}: {msg}")

    if not isinstance(doc, dict):
        return [f"MC8 {label}: fixture must be a JSON object"]
    events = doc.get("events")
    window = doc.get("window")
    if not isinstance(events, list) or not isinstance(window, dict):
        return [f"MC8 {label}: events must be an array and window an object"]
    w_from, w_to = window.get("from"), window.get("to")

    keys: list[tuple[Any, Any, Any]] = []
    seen: set[tuple[Any, Any]] = set()
    for i, ev in enumerate(events):
        if not isinstance(ev, dict):
            err(f"events[{i}] must be an object")
            continue
        at, ek, eid = ev.get("at"), ev.get("evidence_kind"), ev.get("evidence_id")
        keys.append((at, ek, eid))
        if (ek, eid) in seen:
            err(f"duplicate evidence key ({ek}, {eid})")
        seen.add((ek, eid))
        kind = ev.get("kind")
        if kind not in smap["kinds"]:
            err(f"events[{i}] unknown kind '{kind}'")
        else:
            expected = derive_visual_state(smap, kind, str(ev.get("source_state")), "live")
            if ev.get("visual_state") != expected:
                err(
                    f"events[{i}] ({kind}) visual_state '{ev.get('visual_state')}' != derived "
                    f"'{expected}' from source_state '{ev.get('source_state')}'"
                )
        if kind != "hermes_subagent" and ev.get("parent_source_id") is not None:
            err(f"events[{i}] parent_source_id is only allowed on hermes_subagent events")
        if not (isinstance(at, str) and isinstance(w_from, str) and isinstance(w_to, str)
                and w_from <= at <= w_to):
            err(f"events[{i}] at '{at}' is outside window [{w_from}, {w_to}]")
    if keys != sorted(keys, key=lambda k: tuple(str(x) for x in k)):
        err("events must be sorted by (at, evidence_kind, evidence_id)")
    return errors


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def _guard(code: str, label: str, fn: Callable[[], list[str]]) -> list[str]:
    """Run a check; a crash on malformed input is itself an error (fail closed)."""
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001
        return [f"{code} {label}: check could not run on malformed input: {type(exc).__name__}: {exc}"]


def _check_fixture_dir(
    root: Path, rel_dir: Path, fn: Callable[[dict[str, Any], Any, str], list[str]], smap: dict[str, Any]
) -> list[str]:
    errors: list[str] = []
    directory = root / rel_dir
    if not directory.is_dir():
        return errors
    for path in sorted(directory.glob("valid-*.json")):
        label = _rel(path, root)
        try:
            doc = _load_json(path)
        except (OSError, ValueError) as exc:
            errors.append(f"MC8 {label}: cannot load fixture: {type(exc).__name__}: {exc}")
            continue
        errors.extend(_guard("MC8", label, lambda d=doc, lb=label: fn(smap, d, lb)))
    return errors


def check_mission_control(repo_root: Path) -> list[str]:
    """Run guards MC1-MC9 against `repo_root`; return a list of error strings."""
    root = Path(repo_root)
    map_path = root / CONTRACT_DIR / SOURCE_MAP_NAME
    map_label = _rel(map_path, root)
    try:
        smap = _load_json(map_path)
    except (OSError, ValueError) as exc:
        return [f"MC1 {map_label}: cannot load source map: {type(exc).__name__}: {exc}"]
    if not isinstance(smap, dict):
        return [f"MC1 {map_label}: source map must be a JSON object"]

    errors = _guard("MC1", map_label, lambda: check_source_map_shape(smap, map_label))
    if errors:
        # Later guards index into the validated shape; stop at the shape errors.
        return errors

    scene_path = root / CONTRACT_DIR / SCENE_SCHEMA_NAME
    replay_path = root / CONTRACT_DIR / REPLAY_SCHEMA_NAME
    op_path = root / CONTRACT_DIR / OPERATION_SCHEMA_NAME
    try:
        scene = _load_json(scene_path)
        replay = _load_json(replay_path)
    except (OSError, ValueError) as exc:
        errors.append(f"MC2 {map_label}: cannot load scene/replay schema: {type(exc).__name__}: {exc}")
    else:
        errors.extend(_guard("MC2", map_label, lambda: check_schema_bijection(
            smap, scene, replay, _rel(scene_path, root), _rel(replay_path, root))))

    errors.extend(_guard("MC3", map_label, lambda: check_source_contracts(smap, root, map_label)))
    errors.extend(_guard("MC4", map_label, lambda: check_state_coverage(smap, root, map_label)))
    errors.extend(_guard("MC5", map_label, lambda: check_routes(smap, map_label)))
    errors.extend(_guard("MC6", map_label, lambda: check_relations(smap, map_label)))

    try:
        operations = _dig(_load_json(op_path), ["properties", "operation", "enum"])
        if not isinstance(operations, list):
            raise ValueError("operation enum is not a list")
    except (OSError, ValueError, KeyError) as exc:
        errors.append(f"MC7 {_rel(op_path, root)}: cannot read operation allowlist: "
                      f"{type(exc).__name__}: {exc}")
    else:
        errors.extend(_guard("MC7", map_label, lambda: check_actions(smap, operations, map_label)))

    errors.extend(_check_fixture_dir(root, SCENE_FIXTURE_DIR, check_scene_fixture, smap))
    errors.extend(_check_fixture_dir(root, REPLAY_FIXTURE_DIR, check_replay_fixture, smap))
    errors.extend(_guard("MC9", map_label, lambda: check_forbidden_fields(smap, map_label)))
    return errors
