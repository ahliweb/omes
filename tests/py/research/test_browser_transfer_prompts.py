"""Structural checks for the synthetic browser-transfer benchmark prompt set
(docs/research/fixtures/browser-transfer-prompts.v1.json, issue #297).

These checks only verify the benchmark INPUT file is internally consistent:
the declared case_count matches the case list, case ids are unique, and every
case carries the fields the benchmark runner reads. They make no claim about
model behaviour or measurements.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PROMPTS = ROOT / "docs" / "research" / "fixtures" / "browser-transfer-prompts.v1.json"

# Fields every benchmark case must carry (present in all 60 cases today).
REQUIRED_CASE_FIELDS = ("id", "category", "lang", "page", "instruction", "expected")


class BrowserTransferPromptSetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.data = json.loads(PROMPTS.read_text(encoding="utf-8"))
        self.cases = self.data["cases"]

    def test_case_count_matches_number_of_cases(self) -> None:
        self.assertIsInstance(self.data["case_count"], int)
        self.assertEqual(self.data["case_count"], len(self.cases))

    def test_case_ids_are_unique(self) -> None:
        ids = [case.get("id") for case in self.cases]
        duplicates = sorted({i for i in ids if ids.count(i) > 1}, key=str)
        self.assertEqual(duplicates, [], f"duplicate case ids: {duplicates}")

    def test_every_case_carries_required_fields(self) -> None:
        for index, case in enumerate(self.cases):
            with self.subTest(index=index, id=case.get("id")):
                missing = [field for field in REQUIRED_CASE_FIELDS if field not in case]
                self.assertEqual(missing, [], f"case {case.get('id', index)} missing {missing}")


if __name__ == "__main__":
    unittest.main()
