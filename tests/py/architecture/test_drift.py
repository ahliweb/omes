"""tests/py/architecture/test_drift.py - Test suite for upstream drift automation (ADR-0026, issue #181)."""
from __future__ import annotations

import sys
import unittest

from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
PY_ROOT = REPO_ROOT / "lib" / "omes" / "py"
if str(PY_ROOT) not in sys.path:
    sys.path.insert(0, str(PY_ROOT))

from architecture import drift



class TestUpstreamDrift(unittest.TestCase):
    def setUp(self) -> None:
        self.sample_registry: dict[str, Any] = {
            "schema_version": "1.0.0",
            "precedence": ["delegate", "port", "adapt", "defer", "reject"],
            "capabilities": [
                {
                    "capability_id": "hermes.agent.reasoning",
                    "title": "Hermes Agent Core",
                    "authority": "hermes",
                    "upstream_project": "NousResearch/hermes-agent",
                    "supported_baseline": "v2026.9.14",
                    "observed_upstream_revision": "v2026.9.14",
                    "maturity": "released_supported",
                    "disposition": "delegate",
                    "omes_module": None,
                    "duplication_allowed": False,
                    "evidence_urls": ["https://github.com/NousResearch/hermes-agent/releases/tag/v2026.9.14"],
                },
                {
                    "capability_id": "hermes.agent.gateway",
                    "title": "Hermes Gateway Management",
                    "authority": "hermes",
                    "upstream_project": "NousResearch/hermes-agent",
                    "supported_baseline": "v2026.9.14",
                    "observed_upstream_revision": "v2026.9.14",
                    "maturity": "released_supported",
                    "disposition": "delegate",
                    "omes_module": "agent",
                    "duplication_allowed": True,
                    "adr_reference": "ADR-0019",
                    "removal_trigger": "Hermes native multi-gateway CLI verified",
                    "evidence_urls": ["https://github.com/NousResearch/hermes-agent/releases/tag/v2026.9.14"],
                },
                {
                    "capability_id": "graphify.knowledge.extraction",
                    "title": "Graphify Knowledge Extraction",
                    "authority": "graphify",
                    "upstream_project": "graphify",
                    "supported_baseline": "0.9.64",
                    "observed_upstream_revision": "0.9.64",
                    "maturity": "released_supported",
                    "disposition": "delegate",
                    "omes_module": None,
                    "duplication_allowed": False,
                    "evidence_urls": ["https://pypi.org/project/graphifyy/"],
                },
            ],
        }

    def test_version_gt(self) -> None:
        self.assertTrue(drift.version_gt("v2026.9.15", "v2026.9.14"))
        self.assertTrue(drift.version_gt("0.9.65", "0.9.64"))
        self.assertTrue(drift.version_gt("1.0.0", "0.9.99"))
        self.assertFalse(drift.version_gt("v2026.9.14", "v2026.9.14"))
        self.assertFalse(drift.version_gt("0.9.64", "0.9.65"))

    def test_no_change_baseline(self) -> None:
        """Scenario: Upstream releases match supported baselines exactly -> NO_IMPACT, status OK."""
        fixtures = {
            "github:NousResearch/hermes-agent:release": {
                "tag_name": "v2026.9.14",
                "html_url": "https://github.com/NousResearch/hermes-agent/releases/tag/v2026.9.14",
            },
            "pypi:graphifyy": {
                "info": {
                    "version": "0.9.64",
                    "license": "MIT",
                    "project_url": "https://pypi.org/project/graphifyy/",
                }
            },
        }
        client = drift.UpstreamClient(fixtures=fixtures, offline=True)
        report = drift.evaluate_upstream_drift(
            registry_data=self.sample_registry,
            client=client,
            check_main_candidate=False,
        )

        self.assertEqual(report.status, drift.STATUS_OK)
        self.assertFalse(report.has_actionable_drift)
        self.assertEqual(report.summary_counts[drift.NO_IMPACT], 2)
        self.assertEqual(len(report.findings), 0)

    def test_new_released_version_and_delegation_opportunity(self) -> None:
        """Scenario: Upstream released newer version -> detects delegation opportunity for duplicated capabilities."""
        fixtures = {
            "github:NousResearch/hermes-agent:release": {
                "tag_name": "v2026.9.15",
                "html_url": "https://github.com/NousResearch/hermes-agent/releases/tag/v2026.9.15",
            },
            "pypi:graphifyy": {
                "info": {
                    "version": "0.9.65",
                    "license": "MIT",
                    "project_url": "https://pypi.org/project/graphifyy/0.9.65/",
                }
            },
        }
        client = drift.UpstreamClient(fixtures=fixtures, offline=True)
        report = drift.evaluate_upstream_drift(
            registry_data=self.sample_registry,
            client=client,
            check_main_candidate=False,
        )

        self.assertTrue(report.has_actionable_drift)
        # hermes has duplicated capability hermes.agent.gateway -> NEW_DELEGATE_CANDIDATE
        self.assertEqual(report.summary_counts[drift.NEW_DELEGATE_CANDIDATE], 1)
        # graphify has no duplicated capability -> BASELINE_UPDATE_AVAILABLE
        self.assertEqual(report.summary_counts[drift.BASELINE_UPDATE_AVAILABLE], 1)

        hermes_finding = next(f for f in report.findings if f.upstream_project == "NousResearch/hermes-agent")
        self.assertEqual(hermes_finding.classification, drift.NEW_DELEGATE_CANDIDATE)
        self.assertIn("hermes.agent.gateway", hermes_finding.capability_ids)
        self.assertEqual(hermes_finding.adr_reference, "ADR-0019")
        self.assertIn("Hermes native multi-gateway", hermes_finding.removal_trigger)

        graphify_finding = next(f for f in report.findings if f.upstream_project == "graphify")
        self.assertEqual(graphify_finding.classification, drift.BASELINE_UPDATE_AVAILABLE)
        self.assertEqual(graphify_finding.discovered_revision, "0.9.65")

    def test_main_only_candidate_tracking(self) -> None:
        """Scenario: Upstream has active main commits beyond recorded baseline -> PORT_ADAPT_REVIEW."""
        sample_reg = {
            "capabilities": [
                {
                    "capability_id": "hermes.candidate.feature",
                    "upstream_project": "NousResearch/hermes-agent",
                    "supported_baseline": "v2026.9.14",
                    "observed_upstream_revision": "v2026.9.14",
                    "maturity": "upstream_main_candidate",
                    "disposition": "defer",
                }
            ]
        }
        fixtures = {
            "github:NousResearch/hermes-agent:release": {
                "tag_name": "v2026.9.14",
                "html_url": "https://github.com/NousResearch/hermes-agent/releases/tag/v2026.9.14",
            },
            "github:NousResearch/hermes-agent:main": {
                "sha": "12345678abcdef",
                "html_url": "https://github.com/NousResearch/hermes-agent/commit/12345678abcdef",
                "commit": {"committer": {"date": "2026-09-21T12:00:00Z"}},
            },
        }
        client = drift.UpstreamClient(fixtures=fixtures, offline=True)
        report = drift.evaluate_upstream_drift(
            registry_data=sample_reg,
            client=client,
            check_main_candidate=True,
        )

        self.assertEqual(report.summary_counts[drift.PORT_ADAPT_REVIEW], 1)
        finding = report.findings[0]
        self.assertEqual(finding.classification, drift.PORT_ADAPT_REVIEW)
        self.assertIn("main@12345678", finding.discovered_revision)

    def test_request_failure_blocked(self) -> None:
        """Scenario: API request fails -> reports BLOCKED and BREAKING_CHANGE warning, fail-closed."""
        client = drift.UpstreamClient(fixtures={}, offline=True)
        report = drift.evaluate_upstream_drift(
            registry_data=self.sample_registry,
            client=client,
            check_main_candidate=False,
        )

        self.assertEqual(report.status, drift.STATUS_BLOCKED)
        self.assertIn("NousResearch/hermes-agent", report.upstreams_blocked)
        self.assertIn("graphify", report.upstreams_blocked)
        self.assertTrue(report.has_actionable_drift)

    def test_license_change_detection(self) -> None:
        """Scenario: Upstream modified license -> SECURITY_OR_LICENSE_REVIEW with FAIL severity."""
        fixtures = {
            "github:NousResearch/hermes-agent:release": {
                "tag_name": "v2026.9.14",
                "html_url": "https://github.com/NousResearch/hermes-agent/releases/tag/v2026.9.14",
            },
            "pypi:graphifyy": {
                "info": {
                    "version": "0.9.64",
                    "license": "AGPL-3.0",  # Changed from MIT!
                    "project_url": "https://pypi.org/project/graphifyy/",
                }
            },
        }
        client = drift.UpstreamClient(fixtures=fixtures, offline=True)
        report = drift.evaluate_upstream_drift(
            registry_data=self.sample_registry,
            client=client,
            check_main_candidate=False,
        )

        self.assertEqual(report.summary_counts[drift.SECURITY_OR_LICENSE_REVIEW], 1)
        finding = next(f for f in report.findings if f.classification == drift.SECURITY_OR_LICENSE_REVIEW)
        self.assertEqual(finding.severity, drift.SEVERITY_FAIL)
        self.assertIn("AGPL-3.0", finding.description)

    def test_reconcile_github_issue_dry_run(self) -> None:
        """Scenario: Reconcile GitHub issue in dry-run mode makes no external calls."""
        report = drift.DriftReport(
            status=drift.STATUS_OK,
            checked_at="2026-09-21T00:00:00Z",
            upstreams_checked=["NousResearch/hermes-agent"],
            upstreams_blocked=[],
            findings=[],
            summary_counts={drift.NO_IMPACT: 1},
            is_dry_run=True,
            read_only=True,
        )
        res = drift.reconcile_github_issue(report, "ahliweb/omes", token=None, dry_run=True)
        self.assertEqual(res["action"], "simulate_noop")
        self.assertFalse(res["actionable"])

    def test_format_markdown_report(self) -> None:
        """Scenario: Markdown output contains required sections and summary table."""
        report = drift.DriftReport(
            status=drift.STATUS_OK,
            checked_at="2026-09-21T00:00:00Z",
            upstreams_checked=["NousResearch/hermes-agent"],
            upstreams_blocked=[],
            findings=[],
            summary_counts={drift.NO_IMPACT: 1, drift.BASELINE_UPDATE_AVAILABLE: 0},
            is_dry_run=True,
            read_only=True,
        )
        md = drift.format_markdown_report(report)
        self.assertIn("# Upstream Drift Review & Deprecation Report", md)
        self.assertIn("## Summary Counts", md)
        self.assertIn("Zero repository or host mutation", md)

    def test_cli_offline_blocked_exit_code(self) -> None:
        """CLI under --offline without fixtures exits with code 2 (fail-closed BLOCKED)."""
        import subprocess

        script = REPO_ROOT / "scripts" / "upstream-drift.py"
        res = subprocess.run(
            [sys.executable, str(script), "--offline"],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
        )
        self.assertEqual(res.returncode, 2)
        self.assertIn("BLOCKED", res.stdout)

    def test_cli_with_fixtures_and_json(self) -> None:
        """CLI with fixtures emits valid JSON and exits 0 when clean."""
        import json
        import subprocess
        import tempfile

        fixtures = {
            "github:NousResearch/hermes-agent:release": {"tag_name": "v2026.9.14"},
            "pypi:graphifyy": {"info": {"version": "0.9.64", "license": "MIT"}},
            "github:omacom/omarchy:release": {"tag_name": "v4.0.4"},
            "github:coollabsio/coolify:release": {"tag_name": "v4.0.0"},
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(fixtures, f)
            temp_path = f.name

        try:
            script = REPO_ROOT / "scripts" / "upstream-drift.py"
            res = subprocess.run(
                [sys.executable, str(script), "--offline", "--fixtures", temp_path, "--json"],
                capture_output=True,
                text=True,
                cwd=str(REPO_ROOT),
            )
            self.assertEqual(res.returncode, 0, f"stdout: {res.stdout}, stderr: {res.stderr}")
            data = json.loads(res.stdout)
            self.assertIn("status", data)
            self.assertIn("summary_counts", data)
            self.assertEqual(data["read_only"], True)
        finally:
            Path(temp_path).unlink(missing_ok=True)



if __name__ == "__main__":
    unittest.main()
