"""tests/py/architecture/test_mission_control.py - Unit tests for
lib/omes/py/architecture/mission_control.py, the MC1-MC9 guards that keep the
Mission Control source map, scene-view/replay-window schemas, source contracts
and fixtures consistent (issue #264, ADR-0031).
"""
from __future__ import annotations

import copy
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
PY_ROOT = REPO_ROOT / "lib" / "omes" / "py"
if str(PY_ROOT) not in sys.path:
    sys.path.insert(0, str(PY_ROOT))

from architecture import mission_control as mc  # noqa: E402

CONTRACTS = Path("contracts") / "control-center" / "v1"


def _load(path: Path):
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _real_map() -> dict:
    return _load(REPO_ROOT / CONTRACTS / mc.SOURCE_MAP_NAME)


class TempRepo:
    """A throwaway copy of contracts/control-center/v1 (without fixtures)."""

    def __init__(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        shutil.copytree(
            REPO_ROOT / CONTRACTS,
            self.root / CONTRACTS,
            ignore=shutil.ignore_patterns("fixtures"),
        )

    def path(self, name: str) -> Path:
        return self.root / CONTRACTS / name

    def edit(self, name: str, fn) -> None:
        p = self.path(name)
        data = _load(p)
        fn(data)
        p.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def write_fixture(self, subdir: str, name: str, doc: dict) -> None:
        d = self.root / CONTRACTS / "fixtures" / subdir
        d.mkdir(parents=True, exist_ok=True)
        (d / name).write_text(json.dumps(doc, indent=2), encoding="utf-8")

    def errors(self) -> list[str]:
        return mc.check_mission_control(self.root)

    def cleanup(self) -> None:
        self._tmp.cleanup()


class TempRepoCase(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = TempRepo()
        self.addCleanup(self.repo.cleanup)

    def assertHasError(self, errors: list[str], prefix: str, text: str = "") -> None:
        matches = [e for e in errors if e.startswith(prefix) and text in e]
        self.assertTrue(matches, f"expected {prefix!r} containing {text!r}, got {errors}")


class RealRepoTests(unittest.TestCase):
    def test_real_repo_passes_all_guards(self) -> None:
        self.assertEqual(mc.check_mission_control(REPO_ROOT), [])

    def test_clean_temp_copy_passes(self) -> None:
        repo = TempRepo()
        self.addCleanup(repo.cleanup)
        self.assertEqual(repo.errors(), [])

    def test_missing_source_map_fails_closed(self) -> None:
        repo = TempRepo()
        self.addCleanup(repo.cleanup)
        repo.path(mc.SOURCE_MAP_NAME).unlink()
        self.assertTrue(repo.errors()[0].startswith("MC1 "))

    def test_unparseable_source_map_fails_closed(self) -> None:
        repo = TempRepo()
        self.addCleanup(repo.cleanup)
        repo.path(mc.SOURCE_MAP_NAME).write_text("{not json", encoding="utf-8")
        self.assertTrue(repo.errors()[0].startswith("MC1 "))


class DeriveVisualStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.map = _real_map()

    def d(self, kind: str, state: str, freshness: str = "live") -> str:
        return mc.derive_visual_state(self.map, kind, state, freshness)

    def test_live_maps_through_state_map(self) -> None:
        self.assertEqual(self.d("job", "running"), "in_progress")
        self.assertEqual(self.d("job", "succeeded"), "ok")
        self.assertEqual(self.d("job", "cancelled"), "cancelled")

    def test_stale_failure_is_kept(self) -> None:
        self.assertEqual(self.d("job", "failed", "stale"), "failed")

    def test_stale_warning_is_kept(self) -> None:
        self.assertEqual(self.d("deployment", "degraded", "stale"), "warning")

    def test_stale_success_is_never_current(self) -> None:
        self.assertEqual(self.d("job", "succeeded", "stale"), "stale")
        self.assertEqual(self.d("server", "online", "stale"), "stale")

    def test_unknown_freshness_wins(self) -> None:
        self.assertEqual(self.d("job", "failed", "unknown"), "unknown")
        self.assertEqual(self.d("job", "succeeded", "unknown"), "unknown")

    def test_unmapped_state_is_unknown(self) -> None:
        self.assertEqual(self.d("job", "brand_new_state"), "unknown")
        self.assertEqual(self.d("job", "brand_new_state", "stale"), "stale")

    def test_unknown_kind_is_unknown(self) -> None:
        self.assertEqual(self.d("no_such_kind", "running"), "unknown")

    def test_hermes_all_eight_states(self) -> None:
        expected = {
            "PENDING": "pending",
            "STARTING": "in_progress",
            "RUNNING": "in_progress",
            "SUCCEEDED": "ok",
            "FAILED": "failed",
            "INTERRUPTED": "warning",
            "CANCELLED": "cancelled",
            "UNKNOWN": "unknown",
        }
        for state, visual in expected.items():
            self.assertEqual(self.d("hermes_subagent", state), visual, state)

    def test_hermes_lowercase_is_unmapped(self) -> None:
        self.assertEqual(self.d("hermes_subagent", "running"), "unknown")


class FieldForbiddenTests(unittest.TestCase):
    TERMS = _real_map()["forbidden_field_terms"]

    def test_active_tool_allowed(self) -> None:
        self.assertIsNone(mc.field_is_forbidden("active_tool", self.TERMS))

    def test_html_url_and_dotted_paths_allowed(self) -> None:
        self.assertIsNone(mc.field_is_forbidden("html_url", self.TERMS))
        self.assertIsNone(mc.field_is_forbidden("desired_state.status", self.TERMS))

    def test_forbidden_terms_rejected(self) -> None:
        for field in ("prompt", "tool_args", "tool_result", "raw_output",
                      "chain_of_thought", "payload.raw", "access_token", "private_key"):
            self.assertIsNotNone(mc.field_is_forbidden(field, self.TERMS), field)

    def test_camel_case_rejected(self) -> None:
        self.assertIsNotNone(mc.field_is_forbidden("toolArgs", self.TERMS))


class MC1ShapeTests(TempRepoCase):
    def test_missing_top_level_key(self) -> None:
        self.repo.edit(mc.SOURCE_MAP_NAME, lambda m: m.pop("relations"))
        self.assertHasError(self.repo.errors(), "MC1 ", "relations")

    def test_state_map_value_not_visual_state(self) -> None:
        self.repo.edit(mc.SOURCE_MAP_NAME,
                       lambda m: m["kinds"]["job"]["state_map"].update({"queued": "glowing"}))
        self.assertHasError(self.repo.errors(), "MC1 ", "glowing")

    def test_read_only_kind_with_extra_action(self) -> None:
        self.repo.edit(mc.SOURCE_MAP_NAME,
                       lambda m: m["kinds"]["health_report"]["candidate_actions"].append("job.cancel"))
        self.assertHasError(self.repo.errors(), "MC1 ", "read_only")

    def test_kind_missing_open_details(self) -> None:
        self.repo.edit(mc.SOURCE_MAP_NAME,
                       lambda m: m["kinds"]["job"].update({"candidate_actions": ["job.cancel"]}))
        self.assertHasError(self.repo.errors(), "MC1 ", "open_details")

    def test_unknown_replay_basis_and_source(self) -> None:
        def mutate(m):
            m["kinds"]["job"]["replay_basis"] = "magic"
            m["kinds"]["backup"]["source"] = "nope"
        self.repo.edit(mc.SOURCE_MAP_NAME, mutate)
        errors = self.repo.errors()
        self.assertHasError(errors, "MC1 ", "replay_basis")
        self.assertHasError(errors, "MC1 ", "not in sources")

    def test_bad_authority(self) -> None:
        self.repo.edit(mc.SOURCE_MAP_NAME,
                       lambda m: m["sources"]["omes_job_status"].update({"authority": "tenant"}))
        self.assertHasError(self.repo.errors(), "MC1 ", "authority")


class MC2BijectionTests(TempRepoCase):
    def test_kind_added_to_schema_but_not_map(self) -> None:
        def mutate(s):
            s["properties"]["nodes"]["items"]["properties"]["kind"]["enum"].append("dashboard")
        self.repo.edit(mc.SCENE_SCHEMA_NAME, mutate)
        self.assertHasError(self.repo.errors(), "MC2 ", "dashboard")

    def test_kind_added_to_map_but_not_schemas(self) -> None:
        def mutate(m):
            m["kinds"]["extra"] = copy.deepcopy(m["kinds"]["job"])
        self.repo.edit(mc.SOURCE_MAP_NAME, mutate)
        errors = self.repo.errors()
        self.assertHasError(errors, "MC2 ", "extra")

    def test_replay_kind_enum_drift(self) -> None:
        def mutate(s):
            enum = s["properties"]["events"]["items"]["properties"]["kind"]["enum"]
            enum.remove("backup")
        self.repo.edit(mc.REPLAY_SCHEMA_NAME, mutate)
        self.assertHasError(self.repo.errors(), "MC2 ", "backup")

    def test_version_mismatch(self) -> None:
        self.repo.edit(mc.SOURCE_MAP_NAME, lambda m: m.update({"source_map_version": "1.1.0"}))
        self.assertHasError(self.repo.errors(), "MC2 ", "source_map_version")

    def test_relation_and_source_enum_drift(self) -> None:
        def mutate(s):
            props = s["properties"]
            props["relations"]["items"]["properties"]["relation"]["enum"].append("owns")
            props["sources"]["items"]["properties"]["source_kind"]["enum"].append("other")
        self.repo.edit(mc.SCENE_SCHEMA_NAME, mutate)
        errors = self.repo.errors()
        self.assertHasError(errors, "MC2 ", "relation")
        self.assertHasError(errors, "MC2 ", "source_kind")

    def test_visual_state_enum_drift(self) -> None:
        def mutate(s):
            s["properties"]["nodes"]["items"]["properties"]["visual_state"]["enum"].append("glowing")
        self.repo.edit(mc.SCENE_SCHEMA_NAME, mutate)
        self.assertHasError(self.repo.errors(), "MC2 ", "glowing")


class MC3MC4Tests(TempRepoCase):
    def test_missing_source_contract(self) -> None:
        self.repo.path("deployment-view.schema.json").unlink()
        self.assertHasError(self.repo.errors(), "MC3 ", "deployment-view")

    def test_non_awcms_source_without_contracts(self) -> None:
        self.repo.edit(mc.SOURCE_MAP_NAME,
                       lambda m: m["sources"]["omes_job_status"].update({"contracts": []}))
        self.assertHasError(self.repo.errors(), "MC3 ", "omes_job_status")

    def test_new_upstream_state_must_be_mapped(self) -> None:
        def mutate(s):
            s["properties"]["state"]["enum"].append("paused")
        self.repo.edit("job-status.response.schema.json", mutate)
        self.assertHasError(self.repo.errors(), "MC4 ", "paused")

    def test_projection_value_must_be_mapped(self) -> None:
        self.repo.edit(mc.SOURCE_MAP_NAME,
                       lambda m: m["kinds"]["job"]["projection_state_values"].append("zombie"))
        self.assertHasError(self.repo.errors(), "MC4 ", "zombie")

    def test_path_not_resolving_to_enum(self) -> None:
        self.repo.edit(mc.SOURCE_MAP_NAME, lambda m: m["kinds"]["job"]["contract_state_enums"]
                       .__setitem__(0, {"contract": "job-status.response",
                                        "path": ["properties", "job_id"]}))
        self.assertHasError(self.repo.errors(), "MC4 ", "enum")

    def test_unresolvable_path(self) -> None:
        self.repo.edit(mc.SOURCE_MAP_NAME, lambda m: m["kinds"]["job"]["contract_state_enums"]
                       .__setitem__(0, {"contract": "job-status.response",
                                        "path": ["properties", "nonexistent"]}))
        self.assertHasError(self.repo.errors(), "MC4 ", "nonexistent")


class MC5RouteTests(TempRepoCase):
    def test_detail_route_not_in_inventory(self) -> None:
        self.repo.edit(mc.SOURCE_MAP_NAME,
                       lambda m: m["kinds"]["job"].update({"detail_route": "/admin/omes/mission-jobs"}))
        self.assertHasError(self.repo.errors(), "MC5 ", "detail_route")

    def test_workspace_route_collides_with_screen(self) -> None:
        self.repo.edit(mc.SOURCE_MAP_NAME,
                       lambda m: m["workspace"].update({"route": "/admin/omes/jobs"}))
        self.assertHasError(self.repo.errors(), "MC5 ", "collides")

    def test_workspace_route_must_be_omes(self) -> None:
        self.repo.edit(mc.SOURCE_MAP_NAME,
                       lambda m: m["workspace"].update({"route": "/admin/mission-control"}))
        self.assertHasError(self.repo.errors(), "MC5 ", "/admin/omes/")

    def test_workspace_must_not_replace_screens(self) -> None:
        self.repo.edit(mc.SOURCE_MAP_NAME,
                       lambda m: m["workspace"].update({"replaces_screens": ["/admin/omes/jobs"]}))
        self.assertHasError(self.repo.errors(), "MC5 ", "replaces_screens")

    def test_inventory_missing_screen(self) -> None:
        def mutate(m):
            m["canonical_screens"] = [s for s in m["canonical_screens"]
                                      if s["route"] != "/admin/omes/audit"]
        self.repo.edit(mc.SOURCE_MAP_NAME, mutate)
        errors = self.repo.errors()
        self.assertHasError(errors, "MC5 ", "/admin/omes/audit")

    def test_duplicate_screen(self) -> None:
        self.repo.edit(mc.SOURCE_MAP_NAME,
                       lambda m: m["canonical_screens"].append(dict(m["canonical_screens"][0])))
        self.assertHasError(self.repo.errors(), "MC5 ", "duplicate")


class MC6RelationTests(TempRepoCase):
    def test_unknown_kind_in_relation(self) -> None:
        self.repo.edit(mc.SOURCE_MAP_NAME,
                       lambda m: m["relations"]["hosts"]["to"].append("ghost"))
        self.assertHasError(self.repo.errors(), "MC6 ", "ghost")


class MC7ActionTests(TempRepoCase):
    def test_invented_operation_rejected(self) -> None:
        def mutate(m):
            m["actions"]["operation.shell"] = {
                "mutating": True, "existing_path": "POST /api/v1/omes/operations"}
        self.repo.edit(mc.SOURCE_MAP_NAME, mutate)
        self.assertHasError(self.repo.errors(), "MC7 ", "operation.shell")
        self.assertHasError(self.repo.errors(), "MC7 ", "AGENTS.md")

    def test_candidate_action_not_defined(self) -> None:
        self.repo.edit(mc.SOURCE_MAP_NAME,
                       lambda m: m["kinds"]["job"]["candidate_actions"].append("job.delete"))
        self.assertHasError(self.repo.errors(), "MC7 ", "job.delete")

    def test_action_shape(self) -> None:
        def mutate(m):
            del m["actions"]["job.cancel"]["mutating"]
            del m["actions"]["job.requeue"]["existing_path"]
        self.repo.edit(mc.SOURCE_MAP_NAME, mutate)
        errors = self.repo.errors()
        self.assertHasError(errors, "MC7 ", "mutating")
        self.assertHasError(errors, "MC7 ", "existing_path")

    def test_non_mutating_post_rejected(self) -> None:
        self.repo.edit(mc.SOURCE_MAP_NAME, lambda m: m["actions"]["open_details"]
                       .update({"existing_path": "POST /admin/omes"}))
        self.assertHasError(self.repo.errors(), "MC7 ", "POST")


class MC9ForbiddenFieldTests(TempRepoCase):
    def test_prompt_rejected(self) -> None:
        self.repo.edit(mc.SOURCE_MAP_NAME,
                       lambda m: m["kinds"]["hermes_subagent"]["allowed_fields"].append("prompt"))
        self.assertHasError(self.repo.errors(), "MC9 ", "prompt")

    def test_tool_args_rejected(self) -> None:
        self.repo.edit(mc.SOURCE_MAP_NAME,
                       lambda m: m["kinds"]["hermes_subagent"]["allowed_fields"].append("tool_args"))
        self.assertHasError(self.repo.errors(), "MC9 ", "tool_args")

    def test_summary_field_checked(self) -> None:
        self.repo.edit(mc.SOURCE_MAP_NAME,
                       lambda m: m["kinds"]["hermes_subagent"].update({"summary_field": "transcript"}))
        self.assertHasError(self.repo.errors(), "MC9 ", "transcript")

    def test_active_tool_accepted(self) -> None:
        self.assertEqual(self.repo.errors(), [])
        self.assertIn("active_tool", _real_map()["kinds"]["hermes_subagent"]["allowed_fields"])


def _scene(**overrides) -> dict:
    doc = {
        "schema_version": "1.0.0",
        "source_map_version": "1.0.0",
        "tenant_id": "tenant-a",
        "generated_at": "2026-01-01T00:00:00Z",
        "mode": "live",
        "as_of": "2026-01-01T00:00:00Z",
        "sources": [
            {"source_kind": "omes_server_inventory", "authority": "omes",
             "status": "available", "observed_at": "2026-01-01T00:00:00Z"},
        ],
        "nodes": [
            {"node_id": "n1", "kind": "server", "source_id": "srv-1", "label": "host",
             "source_state": "online", "visual_state": "ok", "freshness": "live",
             "observed_at": "2026-01-01T00:00:00Z", "detail_route": "/admin/omes/servers"},
            {"node_id": "n2", "kind": "deployment", "source_id": "dep-1", "label": "dep",
             "source_state": "running", "visual_state": "ok", "freshness": "live",
             "observed_at": "2026-01-01T00:00:00Z",
             "detail_route": "/admin/omes/deployments?id=dep-1"},
        ],
        "relations": [{"from": "n1", "to": "n2", "relation": "hosts"}],
        "truncated": {"nodes": 0, "relations": 0},
    }
    doc.update(overrides)
    return doc


class MC8SceneFixtureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.map = _real_map()

    def run_check(self, doc: dict) -> list[str]:
        return mc.check_scene_fixture(self.map, doc, "fx.json")

    def test_valid_scene_passes(self) -> None:
        self.assertEqual(self.run_check(_scene()), [])

    def test_wrong_visual_state(self) -> None:
        doc = _scene()
        doc["nodes"][0]["visual_state"] = "failed"
        self.assertTrue(any("visual_state" in e for e in self.run_check(doc)))

    def test_stale_success_must_be_stale(self) -> None:
        doc = _scene()
        doc["nodes"][0]["freshness"] = "stale"
        errors = self.run_check(doc)
        self.assertTrue(any("derived 'stale'" in e for e in errors), errors)
        doc["nodes"][0]["visual_state"] = "stale"
        self.assertEqual(self.run_check(doc), [])

    def test_dangling_relation(self) -> None:
        doc = _scene()
        doc["relations"].append({"from": "n1", "to": "n99", "relation": "hosts"})
        self.assertTrue(any("missing node" in e for e in self.run_check(doc)))

    def test_disallowed_relation_pair(self) -> None:
        doc = _scene()
        doc["relations"] = [{"from": "n2", "to": "n1", "relation": "hosts"}]
        self.assertTrue(any("does not allow" in e for e in self.run_check(doc)))

    def test_duplicate_node_id(self) -> None:
        doc = _scene()
        doc["nodes"][1]["node_id"] = "n1"
        self.assertTrue(any("duplicate node_id" in e for e in self.run_check(doc)))

    def test_duplicate_kind_source_id(self) -> None:
        doc = _scene()
        doc["nodes"][1] = dict(doc["nodes"][0], node_id="n2")
        self.assertTrue(any("duplicate (kind, source_id)" in e for e in self.run_check(doc)))

    def test_wrong_detail_route(self) -> None:
        doc = _scene()
        doc["nodes"][0]["detail_route"] = "/admin/omes/jobs?id=x"
        self.assertTrue(any("detail_route" in e for e in self.run_check(doc)))

    def test_duplicate_source_kind(self) -> None:
        doc = _scene()
        doc["sources"].append(dict(doc["sources"][0]))
        self.assertTrue(any("duplicate sources" in e for e in self.run_check(doc)))

    def test_live_requires_as_of_equals_generated_at_and_no_gaps(self) -> None:
        doc = _scene(as_of="2025-12-31T00:00:00Z")
        self.assertTrue(any("as_of" in e for e in self.run_check(doc)))
        doc = _scene(evidence_gaps=[{"source_kind": "omes_job_status", "from": "2026-01-01T00:00:00Z",
                                     "to": "2026-01-01T00:01:00Z", "reason": "not_retained"}])
        self.assertTrue(any("evidence_gaps" in e for e in self.run_check(doc)))

    def test_historical_may_have_gaps(self) -> None:
        doc = _scene(mode="historical", as_of="2025-12-31T00:00:00Z",
                     evidence_gaps=[{"source_kind": "omes_job_status", "from": "2026-01-01T00:00:00Z",
                                     "to": "2026-01-01T00:01:00Z", "reason": "not_retained"}])
        self.assertEqual(self.run_check(doc), [])

    def test_malformed_fixture_is_an_error_not_a_crash(self) -> None:
        self.assertTrue(self.run_check({"nodes": "x"}))
        self.assertTrue(mc.check_scene_fixture(self.map, [], "fx.json"))


def _event(**overrides) -> dict:
    ev = {
        "at": "2026-01-01T00:00:10Z",
        "evidence_kind": "hermes_event",
        "evidence_id": "e1",
        "kind": "hermes_subagent",
        "source_id": "sa-1",
        "parent_source_id": None,
        "source_state": "RUNNING",
        "visual_state": "in_progress",
        "provenance": "observed",
    }
    ev.update(overrides)
    return ev


def _replay(events: list[dict]) -> dict:
    return {
        "schema_version": "1.0.0",
        "source_map_version": "1.0.0",
        "tenant_id": "tenant-a",
        "generated_at": "2026-01-01T01:00:00Z",
        "window": {"from": "2026-01-01T00:00:00Z", "to": "2026-01-01T01:00:00Z"},
        "events": events,
        "evidence_gaps": [],
        "next_cursor": None,
    }


class MC8ReplayFixtureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.map = _real_map()

    def run_check(self, events: list[dict]) -> list[str]:
        return mc.check_replay_fixture(self.map, _replay(events), "rp.json")

    def test_valid_replay_passes(self) -> None:
        events = [
            _event(),
            _event(at="2026-01-01T00:00:20Z", evidence_id="e2", source_state="SUCCEEDED",
                   visual_state="ok", parent_source_id="sa-0"),
            _event(at="2026-01-01T00:00:30Z", evidence_kind="job_record", evidence_id="j1",
                   kind="job", source_id="job-1", source_state="failed", visual_state="failed"),
        ]
        self.assertEqual(self.run_check(events), [])

    def test_unsorted_events_rejected(self) -> None:
        events = [_event(at="2026-01-01T00:00:20Z", evidence_id="e2"), _event()]
        self.assertTrue(any("sorted" in e for e in self.run_check(events)))

    def test_duplicate_evidence_key_rejected(self) -> None:
        events = [_event(), _event()]
        self.assertTrue(any("duplicate evidence key" in e for e in self.run_check(events)))

    def test_wrong_visual_state_rejected(self) -> None:
        self.assertTrue(any("visual_state" in e for e in self.run_check([_event(visual_state="ok")])))

    def test_unmapped_state_must_be_unknown(self) -> None:
        self.assertEqual(self.run_check([_event(source_state="MYSTERY", visual_state="unknown")]), [])
        self.assertTrue(self.run_check([_event(source_state="MYSTERY", visual_state="ok")]))

    def test_parent_only_on_hermes_subagent(self) -> None:
        ev = _event(evidence_kind="job_record", kind="job", source_id="job-1",
                    source_state="failed", visual_state="failed", parent_source_id="x")
        self.assertTrue(any("parent_source_id" in e for e in self.run_check([ev])))
        ev["parent_source_id"] = None
        self.assertEqual(self.run_check([ev]), [])

    def test_event_outside_window(self) -> None:
        self.assertTrue(any("outside window" in e
                            for e in self.run_check([_event(at="2025-01-01T00:00:00Z")])))
        self.assertTrue(any("outside window" in e
                            for e in self.run_check([_event(at="2026-01-02T00:00:00Z")])))


class MC8FixtureDiscoveryTests(TempRepoCase):
    def test_valid_scene_fixture_on_disk_is_checked(self) -> None:
        doc = _scene()
        doc["nodes"][0]["visual_state"] = "failed"
        self.repo.write_fixture("mission-control-scene-view", "valid-01-bad.json", doc)
        self.assertHasError(self.repo.errors(), "MC8 ", "valid-01-bad.json")

    def test_invalid_prefixed_fixtures_are_not_semantically_checked(self) -> None:
        doc = _scene()
        doc["nodes"][0]["visual_state"] = "failed"
        self.repo.write_fixture("mission-control-scene-view", "invalid-01-bad.json", doc)
        self.assertEqual(self.repo.errors(), [])

    def test_unparseable_valid_fixture_fails_closed(self) -> None:
        d = self.repo.root / CONTRACTS / "fixtures" / "mission-control-replay-window"
        d.mkdir(parents=True)
        (d / "valid-01-broken.json").write_text("{oops", encoding="utf-8")
        self.assertHasError(self.repo.errors(), "MC8 ", "cannot load")

    def test_no_fixtures_is_not_an_mc8_error(self) -> None:
        self.assertEqual(self.repo.errors(), [])


if __name__ == "__main__":
    unittest.main()
