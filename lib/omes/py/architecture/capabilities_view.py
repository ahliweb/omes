"""lib/omes/py/architecture/capabilities_view.py - builds the
`architecture-capabilities-view` Control Center read projection (issue
#246, part 3) from `architecture/capabilities.json`.

This is the SINGLE source of truth for turning the internal capability
registry (schema 1.1.0, `contracts/architecture/v1/capabilities.schema.json`)
into the sanitized, read-only projection shape a future AWCMS Architecture
view consumes (`contracts/control-center/v1/architecture-capabilities-view.schema.json`).
Both `scripts/generate-architecture-capabilities-view.py` (the fixture
generator) and `lib/omes/py/architecture/registry.py` (the staleness guard
run by `scripts/check-architecture.py`) import `build_view()` from here so
the two can never drift apart from each other - only from the registry
they both read.

The projection intentionally carries far less than the full registry
entry: no `adr_reference`, `removal_trigger`, `duplication_allowed`,
`evidence_urls`, `maturity`, `disposition`, `supported_baseline`, or
`observed_upstream_revision` - none of those are needed to render a plane
lane of capability cards with an implementation-status badge, and leaving
them out keeps this projection small and stable rather than mirroring the
whole internal registry across a wire boundary.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

# View contract's own schema version (independent of the registry's
# schema_version 1.1.0 - this projection can evolve on its own timeline).
VIEW_SCHEMA_VERSION = "1.0.0"

# The plane table from docs/architecture.md §18.3 ("Plane table"). This is
# the canonical enumeration of the six reference-architecture planes; keep
# this in sync with that table by hand (both are small and rarely change),
# and prefer editing docs/architecture.md first since it is the narrative
# source of truth for the reference architecture (issue #247, D2 guard).
PLANE_TABLE: tuple[dict[str, str], ...] = (
    {
        "id": "business_control",
        "name": "Business & governance",
        "execution_semantics": "external_authority",
    },
    {
        "id": "host_control",
        "name": "Host control",
        "execution_semantics": "deterministic",
    },
    {
        "id": "agent_runtime",
        "name": "Agent runtime",
        "execution_semantics": "probabilistic",
    },
    {
        "id": "tool_data",
        "name": "Tool / data",
        "execution_semantics": "probabilistic",
    },
    {
        "id": "infrastructure",
        "name": "Infrastructure",
        "execution_semantics": "deterministic",
    },
    {
        "id": "observability",
        "name": "Observability & evidence",
        "execution_semantics": "observational",
    },
)

# Fixed, illustrative provenance values used only for the checked-in
# fixture (contracts/control-center/v1/fixtures/architecture-capabilities-view/
# valid-01-generated.json). A real production projection sets `generated_at`
# and `omes_commit` from the live host at request time (see
# lib/omes/py/agent/provenance.py's `git_ref()`); the fixture instead uses
# fixed values so `--check` is reproducible in CI regardless of wall-clock
# time or working-tree commit.
FIXTURE_GENERATED_AT = "2026-01-01T00:00:00Z"
FIXTURE_OMES_COMMIT = "0" * 40


def build_capability_entry(cap: dict[str, Any]) -> dict[str, Any]:
    """Projects one architecture/capabilities.json capability entry down to
    the fields the read-only view needs."""
    return {
        "id": cap.get("capability_id"),
        "name": cap.get("title"),
        "plane": cap.get("plane"),
        "authority": cap.get("authority"),
        "execution_semantics": cap.get("execution_semantics"),
        "implementation_status": cap.get("implementation_status"),
        "upstream_project": cap.get("upstream_project") or None,
        "omes_module": cap.get("omes_module"),
    }


def build_view(
    registry_data: dict[str, Any],
    *,
    generated_at: str,
    omes_version: str,
    omes_commit: str,
) -> dict[str, Any]:
    """Builds the full architecture-capabilities-view projection from
    `registry_data` (the loaded contents of architecture/capabilities.json).

    Capabilities are sorted by `capability_id` so the projection - and the
    fixture generated from it - is deterministic regardless of the source
    registry's on-disk ordering.
    """
    capabilities = [
        build_capability_entry(cap)
        for cap in sorted(
            registry_data.get("capabilities", []),
            key=lambda c: c.get("capability_id", ""),
        )
    ]
    planes = [dict(plane) for plane in PLANE_TABLE]

    return {
        "schema_version": VIEW_SCHEMA_VERSION,
        "omes_version": omes_version,
        "omes_commit": omes_commit,
        "generated_at": generated_at,
        "planes": planes,
        "capabilities": capabilities,
    }


def read_omes_version(repo_root: Path) -> str:
    version_file = Path(repo_root) / "VERSION"
    try:
        return version_file.read_text(encoding="utf-8").strip()
    except OSError:
        return "unknown"


def build_fixture_view(repo_root: Path, registry_data: dict[str, Any]) -> dict[str, Any]:
    """Builds the deterministic view used for the checked-in fixture (fixed
    `generated_at`/`omes_commit`, live `omes_version` read from VERSION)."""
    return build_view(
        registry_data,
        generated_at=FIXTURE_GENERATED_AT,
        omes_version=read_omes_version(repo_root),
        omes_commit=FIXTURE_OMES_COMMIT,
    )
