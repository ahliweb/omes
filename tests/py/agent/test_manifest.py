"""tests/py/agent/test_manifest.py - schema + semantic validation for
AgentDeployment manifests (issue #87)."""
from __future__ import annotations

import json
import os
import unittest
from pathlib import Path

from . import _pathfix  # noqa: F401

from agent import jsonschema_lite, manifest  # noqa: E402

OMES_ROOT = Path(_pathfix.OMES_ROOT)
FIXTURES = OMES_ROOT / "contracts" / "agent" / "v1" / "fixtures" / "agent-deployment"


def _load_fixture(name: str) -> dict:
    with open(FIXTURES / name, "r", encoding="utf-8") as fh:
        return json.load(fh)


class TestSchemaFixtures(unittest.TestCase):
    def test_all_valid_fixtures_pass(self):
        for path in sorted(FIXTURES.glob("valid-*.json")):
            with self.subTest(fixture=path.name):
                data = json.loads(path.read_text(encoding="utf-8"))
                errors = manifest.validate(data, OMES_ROOT)
                self.assertEqual(errors, [], f"{path.name}: {errors}")

    def test_all_invalid_fixtures_fail(self):
        for path in sorted(FIXTURES.glob("invalid-*.json")):
            with self.subTest(fixture=path.name):
                data = json.loads(path.read_text(encoding="utf-8"))
                errors = manifest.validate(data, OMES_ROOT)
                self.assertTrue(errors, f"{path.name} should be invalid but validated cleanly")


class TestSemanticChecks(unittest.TestCase):
    def setUp(self):
        self.base = _load_fixture("valid-generic-user.json")

    def test_name_path_traversal_rejected(self):
        data = json.loads(json.dumps(self.base))
        data["metadata"]["name"] = "../../etc/passwd"
        errors = manifest.validate(data, OMES_ROOT)
        self.assertTrue(any("metadata.name" in e for e in errors))

    def test_name_with_slash_rejected(self):
        data = json.loads(json.dumps(self.base))
        data["metadata"]["name"] = "a/b"
        errors = manifest.validate(data, OMES_ROOT)
        self.assertTrue(errors)

    def test_name_too_short_rejected(self):
        data = json.loads(json.dumps(self.base))
        data["metadata"]["name"] = "a"
        errors = manifest.validate(data, OMES_ROOT)
        self.assertTrue(errors)

    def test_name_uppercase_rejected(self):
        data = json.loads(json.dumps(self.base))
        data["metadata"]["name"] = "Researcher"
        errors = manifest.validate(data, OMES_ROOT)
        self.assertTrue(errors)

    def test_unsupported_runtime_rejected(self):
        data = json.loads(json.dumps(self.base))
        data["spec"]["runtime"] = "docker-compose"
        errors = manifest.validate(data, OMES_ROOT)
        self.assertTrue(any("spec.runtime" in e for e in errors))

    def test_unsupported_backend_rejected(self):
        data = json.loads(json.dumps(self.base))
        data["spec"]["backend"] = "coolify"
        errors = manifest.validate(data, OMES_ROOT)
        self.assertTrue(any("spec.backend" in e for e in errors))

    def test_unsupported_service_mode_rejected(self):
        data = json.loads(json.dumps(self.base))
        data["spec"]["serviceMode"] = "root"
        errors = manifest.validate(data, OMES_ROOT)
        self.assertTrue(errors)

    def test_secret_value_shaped_like_a_key_rejected_by_pattern(self):
        data = json.loads(json.dumps(self.base))
        data["spec"]["secrets"] = ["sk-live-abcdef0123456789=="]
        errors = manifest.validate(data, OMES_ROOT)
        self.assertTrue(errors)

    def test_bare_reference_name_secret_accepted(self):
        data = json.loads(json.dumps(self.base))
        data["spec"]["secrets"] = ["provider-primary", "telegram-bot-token"]
        errors = manifest.validate(data, OMES_ROOT)
        self.assertEqual(errors, [])

    def test_additional_top_level_property_rejected(self):
        data = json.loads(json.dumps(self.base))
        data["unexpected"] = "field"
        errors = manifest.validate(data, OMES_ROOT)
        self.assertTrue(errors)

    def test_missing_required_spec_field_rejected(self):
        data = json.loads(json.dumps(self.base))
        del data["spec"]["resources"]
        errors = manifest.validate(data, OMES_ROOT)
        self.assertTrue(any("resources" in e for e in errors))


class TestLoadAndValidate(unittest.TestCase):
    def test_load_and_validate_raises_manifest_error_for_invalid_json(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "researcher.json"
            path.write_text("{not json", encoding="utf-8")
            with self.assertRaises(manifest.ManifestError):
                manifest.load_and_validate(path, OMES_ROOT)

    def test_load_and_validate_raises_manifest_error_with_all_problems(self):
        import tempfile

        data = _load_fixture("valid-generic-user.json")
        data["spec"]["runtime"] = "docker-compose"
        data["spec"]["backend"] = "coolify"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "researcher.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaises(manifest.ManifestError) as ctx:
                manifest.load_and_validate(path, OMES_ROOT)
            self.assertGreaterEqual(len(ctx.exception.errors), 2)


class TestJsonSchemaLite(unittest.TestCase):
    def test_type_mismatch_reported(self):
        errors = jsonschema_lite.validate("not-an-object", {"type": "object"})
        self.assertTrue(errors)

    def test_enum_violation_reported(self):
        errors = jsonschema_lite.validate("x", {"enum": ["a", "b"]})
        self.assertTrue(errors)

    def test_nested_object_required_property(self):
        schema = {
            "type": "object",
            "required": ["a"],
            "properties": {"a": {"type": "string"}},
        }
        self.assertEqual(jsonschema_lite.validate({"a": "x"}, schema), [])
        self.assertTrue(jsonschema_lite.validate({}, schema))


if __name__ == "__main__":
    unittest.main()
