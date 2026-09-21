"""tests/py/architecture/test_registry.py - Unit tests for capability registry
and architecture boundary enforcement (ADR-0017, issue #171).
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
PY_ROOT = REPO_ROOT / "lib" / "omes" / "py"
if str(PY_ROOT) not in sys.path:
    sys.path.insert(0, str(PY_ROOT))

from architecture import registry  # noqa: E402


class ArchitectureRegistryTests(unittest.TestCase):
    def test_current_repository_classified_completely(self) -> None:
        """The active repository must pass all architecture registry and boundary checks."""
        errors = registry.check_all(REPO_ROOT)
        self.assertEqual(errors, [], f"Expected 0 architecture errors, got: {errors}")

    def test_temporary_duplication_without_expiry_fails(self) -> None:
        """A capability with duplication_allowed=True without removal_trigger or adr_reference must fail."""
        cap_missing_trigger = {
            "capability_id": "test.duplicate.cap",
            "title": "Test Duplicate Capability",
            "authority": "hermes",
            "upstream_project": "NousResearch/hermes-agent",
            "supported_baseline": "v2026.9.14",
            "observed_upstream_revision": "v2026.9.14",
            "maturity": "released_supported",
            "disposition": "delegate",
            "omes_module": "agent",
            "duplication_allowed": True,
            "adr_reference": "ADR-0013",
            "removal_trigger": None,
            "evidence_urls": ["https://example.com/evidence"],
        }
        errs = registry.validate_capability(cap_missing_trigger)
        self.assertTrue(
            any("removal_trigger" in e for e in errs),
            f"Expected removal_trigger error, got: {errs}",
        )

        cap_missing_adr = dict(cap_missing_trigger)
        cap_missing_adr["removal_trigger"] = "issue #175"
        cap_missing_adr["adr_reference"] = None
        errs = registry.validate_capability(cap_missing_adr)
        self.assertTrue(
            any("adr_reference" in e for e in errs),
            f"Expected adr_reference error, got: {errs}",
        )

    def test_main_only_feature_cannot_be_released_supported(self) -> None:
        """A feature observed only on upstream main cannot be classified as released_supported."""
        cap_main_only = {
            "capability_id": "test.main.feature",
            "title": "Test Main Only Feature",
            "authority": "hermes",
            "upstream_project": "NousResearch/hermes-agent",
            "supported_baseline": "v2026.9.14",
            "observed_upstream_revision": "main",
            "maturity": "released_supported",
            "disposition": "delegate",
            "omes_module": None,
            "duplication_allowed": False,
            "adr_reference": None,
            "removal_trigger": None,
            "evidence_urls": ["https://example.com/evidence"],
        }
        errs = registry.validate_capability(cap_main_only)
        self.assertTrue(
            any("main cannot be classified as 'released_supported'" in e for e in errs),
            f"Expected main/released_supported rejection, got: {errs}",
        )

    def test_allowed_omes_to_hermes_cli_adapter_passes(self) -> None:
        """An allowed OMES to Hermes delegation adapter passes validation."""
        cap = {
            "capability_id": "hermes.agent.reasoning",
            "title": "Hermes Reasoning",
            "authority": "hermes",
            "upstream_project": "NousResearch/hermes-agent",
            "supported_baseline": "v2026.9.14",
            "observed_upstream_revision": "v2026.9.14",
            "maturity": "released_supported",
            "disposition": "delegate",
            "omes_module": None,
            "duplication_allowed": False,
            "adr_reference": None,
            "removal_trigger": None,
            "evidence_urls": ["https://example.com/evidence"],
        }
        errs = registry.validate_capability(cap)
        self.assertEqual(errs, [])

    def test_allowed_omarchy_port_adapt_passes(self) -> None:
        """An allowed Omarchy port/adapt capability declaration passes validation."""
        cap_port = {
            "capability_id": "omarchy.release.channels",
            "title": "Omarchy Release Channels",
            "authority": "omarchy",
            "upstream_project": "omacom/omarchy",
            "supported_baseline": "v4.0.4",
            "observed_upstream_revision": "v4.0.4",
            "maturity": "released_supported",
            "disposition": "port",
            "omes_module": "bootstrap",
            "duplication_allowed": False,
            "adr_reference": "ADR-0010",
            "removal_trigger": None,
            "evidence_urls": ["https://example.com/evidence"],
        }
        errs = registry.validate_capability(cap_port)
        self.assertEqual(errs, [])

        cap_adapt = {
            "capability_id": "omarchy.theme.composition",
            "title": "Omarchy Theme Composition",
            "authority": "omarchy",
            "upstream_project": "omacom/omarchy",
            "supported_baseline": "v4.0.4",
            "observed_upstream_revision": "v4.0.4",
            "maturity": "released_supported",
            "disposition": "adapt",
            "omes_module": "desktop",
            "duplication_allowed": False,
            "adr_reference": "ADR-0008",
            "removal_trigger": None,
            "evidence_urls": ["https://example.com/evidence"],
        }
        errs = registry.validate_capability(cap_adapt)
        self.assertEqual(errs, [])

    def test_prohibited_import_dependency_fails(self) -> None:
        """Core modules importing commercial/domain workflow code must be rejected."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmproot = Path(tmpdir)
            py_lib = tmproot / "lib" / "omes" / "py"
            core_agent = py_lib / "agent"
            core_agent.mkdir(parents=True)
            bad_file = core_agent / "bad_import.py"
            bad_file.write_text("import content\n", encoding="utf-8")

            errs = registry.check_layer_boundaries(tmproot)
            self.assertTrue(
                any("prohibited layer boundary violation" in e for e in errs),
                f"Expected layer boundary error, got: {errs}",
            )

    def test_prohibited_duplicate_runtime_coupling_fails(self) -> None:
        """OMES directly accessing Hermes internal sqlite database must be rejected."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmproot = Path(tmpdir)
            py_lib = tmproot / "lib" / "omes" / "py"
            core_agent = py_lib / "agent"
            core_agent.mkdir(parents=True)
            bad_file = core_agent / "bad_coupling.py"
            bad_file.write_text(
                "import sqlite3\nconn = sqlite3.connect('messages.db')\n",
                encoding="utf-8",
            )

            errs = registry.check_runtime_coupling(tmproot)
            self.assertTrue(
                any("prohibited runtime coupling" in e for e in errs),
                f"Expected runtime coupling error, got: {errs}",
            )

    def test_cli_script(self) -> None:
        """scripts/check-architecture.py runs cleanly on the repository."""
        proc = subprocess.run(
            [sys.executable, str(REPO_ROOT / "scripts" / "check-architecture.py")],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(
            proc.returncode,
            0,
            f"check-architecture.py failed:\nstdout: {proc.stdout}\nstderr: {proc.stderr}",
        )
        self.assertIn("check-architecture: OK", proc.stdout)


if __name__ == "__main__":
    unittest.main()
