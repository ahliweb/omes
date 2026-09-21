"""Exercises scripts/check-contracts.py against contracts/control-center/v1
(issue #89). Loaded by file path via importlib because the script's
filename has a hyphen (not a valid module name)."""
import importlib.util
import os
import unittest

from . import _pathfix  # noqa: F401  (sets sys.path)

_THIS = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_THIS, "..", "..", ".."))
_SCRIPT_PATH = os.path.join(_REPO_ROOT, "scripts", "check-contracts.py")

_spec = importlib.util.spec_from_file_location("check_contracts", _SCRIPT_PATH)
check_contracts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_contracts)


class TestCheckContracts(unittest.TestCase):
    def test_main_returns_zero_for_the_real_contracts_directory(self):
        self.assertEqual(check_contracts.main([]), 0)

    def test_every_schema_has_at_least_one_valid_and_one_invalid_fixture(self):
        contracts_root = os.path.join(_REPO_ROOT, "contracts")
        from pathlib import Path

        dirs = check_contracts.find_contract_dirs(Path(contracts_root))
        self.assertGreater(len(dirs), 0)
        for d in dirs:
            failures = check_contracts.check_contract_dir(d)
            self.assertEqual(failures, [], f"{d}: {failures}")

    def test_invalid_fixture_that_should_pass_is_flagged(self):
        # A schema requiring "a" applied to a fixture directory whose
        # "invalid" fixture actually satisfies the schema must produce a
        # failure describing the mismatch (defends the harness itself
        # against a fixture bug that would silently stop catching
        # regressions).
        import json
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "widget.schema.json").write_text(
                json.dumps({"type": "object", "required": ["a"], "properties": {"a": {"type": "string"}}})
            )
            fixtures = root / "fixtures" / "widget"
            fixtures.mkdir(parents=True)
            (fixtures / "valid-01.json").write_text(json.dumps({"a": "x"}))
            # This "invalid" fixture is actually valid - the harness must
            # say so rather than silently pass.
            (fixtures / "invalid-01.json").write_text(json.dumps({"a": "y"}))

            failures = check_contracts.check_contract_dir(root)
            self.assertTrue(any("expected INVALID" in f for f in failures))

    def test_schema_with_unsupported_keyword_is_rejected(self):
        import json
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "widget.schema.json").write_text(
                json.dumps({
                    "type": "object",
                    "properties": {"a": {"type": "string", "$ref": "#/defs/Foo"}},
                })
            )
            fixtures = root / "fixtures" / "widget"
            fixtures.mkdir(parents=True)
            (fixtures / "valid-01.json").write_text(json.dumps({"a": "x"}))
            (fixtures / "invalid-01.json").write_text(json.dumps({"a": 123}))

            failures = check_contracts.check_contract_dir(root)
            self.assertTrue(any("unsupported JSON Schema keyword '$ref'" in f for f in failures))
