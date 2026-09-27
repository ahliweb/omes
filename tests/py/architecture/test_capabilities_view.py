"""tests/py/architecture/test_capabilities_view.py - Unit tests for
lib/omes/py/architecture/capabilities_view.py, the shared builder behind
the architecture-capabilities-view Control Center projection (issue #246,
part 3) and its checked-in fixture generator,
scripts/generate-architecture-capabilities-view.py.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
PY_ROOT = REPO_ROOT / "lib" / "omes" / "py"
if str(PY_ROOT) not in sys.path:
    sys.path.insert(0, str(PY_ROOT))

from architecture import capabilities_view  # noqa: E402
from jobs import schema as schema_mod  # noqa: E402


def _cap(**overrides):
    cap = {
        "capability_id": "test.example.capability",
        "title": "Test Example Capability",
        "authority": "omes",
        "upstream_project": "",
        "plane": "host_control",
        "execution_semantics": "deterministic",
        "implementation_status": "implemented",
        "omes_module": "jobs",
    }
    cap.update(overrides)
    return cap


class BuildViewTests(unittest.TestCase):
    def test_planes_cover_all_six_reference_architecture_planes(self) -> None:
        plane_ids = {p["id"] for p in capabilities_view.PLANE_TABLE}
        self.assertEqual(
            plane_ids,
            {
                "business_control",
                "host_control",
                "agent_runtime",
                "tool_data",
                "infrastructure",
                "observability",
            },
        )

    def test_build_view_projects_expected_capability_fields(self) -> None:
        registry_data = {"capabilities": [_cap()]}
        view = capabilities_view.build_view(
            registry_data,
            generated_at="2026-01-01T00:00:00Z",
            omes_version="0.4.0",
            omes_commit="0" * 40,
        )
        self.assertEqual(len(view["capabilities"]), 1)
        entry = view["capabilities"][0]
        self.assertEqual(
            entry,
            {
                "id": "test.example.capability",
                "name": "Test Example Capability",
                "plane": "host_control",
                "authority": "omes",
                "execution_semantics": "deterministic",
                "implementation_status": "implemented",
                "upstream_project": None,
                "omes_module": "jobs",
            },
        )

    def test_build_view_sorts_capabilities_by_id(self) -> None:
        registry_data = {
            "capabilities": [
                _cap(capability_id="z.last", title="Z"),
                _cap(capability_id="a.first", title="A"),
            ]
        }
        view = capabilities_view.build_view(
            registry_data,
            generated_at="2026-01-01T00:00:00Z",
            omes_version="0.4.0",
            omes_commit="0" * 40,
        )
        ids = [c["id"] for c in view["capabilities"]]
        self.assertEqual(ids, ["a.first", "z.last"])

    def test_empty_upstream_project_becomes_null(self) -> None:
        registry_data = {"capabilities": [_cap(upstream_project="")]}
        view = capabilities_view.build_view(
            registry_data,
            generated_at="2026-01-01T00:00:00Z",
            omes_version="0.4.0",
            omes_commit="0" * 40,
        )
        self.assertIsNone(view["capabilities"][0]["upstream_project"])

    def test_build_view_validates_against_the_contract_schema(self) -> None:
        registry_data = {"capabilities": [_cap()]}
        view = capabilities_view.build_view(
            registry_data,
            generated_at="2026-01-01T00:00:00Z",
            omes_version="0.4.0",
            omes_commit="0" * 40,
        )
        schema = schema_mod.load_json(
            REPO_ROOT / "contracts" / "control-center" / "v1" / "architecture-capabilities-view.schema.json"
        )
        errs = schema_mod.validate(view, schema)
        self.assertEqual(errs, [])

    def test_build_fixture_view_reads_live_omes_version(self) -> None:
        registry_data = {"capabilities": [_cap()]}
        view = capabilities_view.build_fixture_view(REPO_ROOT, registry_data)
        expected_version = (REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip()
        self.assertEqual(view["omes_version"], expected_version)
        self.assertEqual(view["generated_at"], capabilities_view.FIXTURE_GENERATED_AT)
        self.assertEqual(view["omes_commit"], capabilities_view.FIXTURE_OMES_COMMIT)

    def test_current_registry_builds_a_schema_valid_view(self) -> None:
        registry_data = schema_mod.load_json(REPO_ROOT / "architecture" / "capabilities.json")
        view = capabilities_view.build_fixture_view(REPO_ROOT, registry_data)
        schema = schema_mod.load_json(
            REPO_ROOT / "contracts" / "control-center" / "v1" / "architecture-capabilities-view.schema.json"
        )
        errs = schema_mod.validate(view, schema)
        self.assertEqual(errs, [])


if __name__ == "__main__":
    unittest.main()
