"""Static contract checks for the reference Control Center prototype.

These checks do not replace a real browser smoke test. They make the declared
screen inventory, metadata, and accessibility hooks reproducible in the
stdlib-only default test suite while the browser check remains manual/opt-in.
"""

from __future__ import annotations

import re
import unittest
from html import unescape
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
INDEX = ROOT / "ui" / "control-center" / "index.html"
DATA = ROOT / "ui" / "control-center" / "data.js"


class ControlCenterBrowserContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.html = INDEX.read_text(encoding="utf-8")
        self.data = DATA.read_text(encoding="utf-8")

    def test_declares_all_twelve_views_and_matching_render_guards(self) -> None:
        match = re.search(r'"options":\[([^]]+)\]', unescape(self.html))
        if match is None:
            self.fail("prototype does not declare a defaultScreen options list")
        views = re.findall(r'"([a-z]+)"', match.group(1))
        self.assertEqual(
            views,
            [
                "overview",
                "servers",
                "deployments",
                "operations",
                "hermes",
                "live",
                "health",
                "backup",
                "audit",
                "workers",
                "arch",
                "progress",
            ],
        )
        for view in views:
            self.assertIn(
                f"is{view[0].upper()}{view[1:]}",
                self.html,
                f"missing render guard for {view}",
            )

    def test_prototype_has_required_metadata_and_accessibility_hooks(self) -> None:
        self.assertIn('<html lang="id">', self.html)
        self.assertIn("<title>OMES Control Center</title>", self.html)
        self.assertIn('role="dialog"', self.html)
        self.assertIn('aria-modal="true"', self.html)
        self.assertIn('aria-live="polite"', self.html)
        self.assertIn('aria-labelledby="operation-drawer-title"', self.html)
        self.assertNotIn('<div onClick="{{ toggleDrawer }}"', self.html)

    def test_generated_data_contains_expected_contract_sections(self) -> None:
        for section in (
            "servers",
            "deployments",
            "queue",
            "health",
            "backupPolicy",
            "audit",
            "workers",
            "hermesEvents",
            "hermesMeta",
            "hermesTree",
        ):
            self.assertRegex(self.data, rf"\b{section}\b")


if __name__ == "__main__":
    unittest.main()