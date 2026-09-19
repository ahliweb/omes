import json
import os
import shutil
import tempfile
import unittest

from . import _pathfix  # noqa: F401

from graphify import obsidian  # noqa: E402


UPSTREAM_NOTE = """---
source_file: "src/app.py"
type: "code"
community: "Community 0"
tags:
  - graphify/code
  - graphify/EXTRACTED
---

# app.py

## Connections
- [[greet()]] - `contains` [EXTRACTED]
"""


class TestInjectFrontMatterFields(unittest.TestCase):
    def test_injects_into_existing_front_matter_preserving_nested_lists(self):
        out = obsidian.inject_front_matter_fields(
            UPSTREAM_NOTE,
            [("omes_generated", "true"), ("graphify_version", "0.9.64")],
        )
        self.assertIn("omes_generated: true", out)
        self.assertIn('graphify_version: "0.9.64"', out)
        # Upstream's own fields and nested list survive untouched.
        self.assertIn('source_file: "src/app.py"', out)
        self.assertIn("- graphify/code", out)
        self.assertIn("- graphify/EXTRACTED", out)
        # Body is untouched.
        self.assertIn("## Connections", out)
        self.assertIn("[[greet()]]", out)

    def test_prepends_front_matter_when_none_exists(self):
        out = obsidian.inject_front_matter_fields(
            "# My note\n\nbody text\n",
            [("omes_generated", "true")],
        )
        self.assertTrue(out.startswith("---\n"))
        self.assertIn("omes_generated: true", out)
        self.assertIn("# My note", out)
        self.assertIn("body text", out)


class TestUpstreamManifestAndPostprocess(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="omes-graphify-upstream-test-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, rel, content):
        path = os.path.join(self.tmp, rel)
        os.makedirs(os.path.dirname(path) or self.tmp, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)

    def _write_manifest(self, files):
        self._write(
            obsidian.UPSTREAM_MANIFEST_NAME,
            json.dumps({"files": files}),
        )

    def test_read_upstream_manifest_missing_returns_empty(self):
        self.assertEqual(obsidian.read_upstream_manifest(self.tmp), [])

    def test_read_upstream_manifest_returns_file_list(self):
        self._write_manifest(["app.py.md", "graph.canvas"])
        self.assertEqual(
            sorted(obsidian.read_upstream_manifest(self.tmp)),
            ["app.py.md", "graph.canvas"],
        )

    def test_postprocess_injects_marker_only_into_markdown(self):
        self._write("app.py.md", UPSTREAM_NOTE)
        self._write("graph.canvas", '{"nodes": [], "edges": []}')
        payload = obsidian.postprocess_upstream_export(
            self.tmp,
            ["app.py.md", "graph.canvas"],
            graphify_version="0.9.64",
            extraction_mode="code",
            generated_at="2026-09-19T12:00:00Z",
            graph_sha256="a" * 64,
        )
        self.assertIn("omes_generated: true", payload["app.py.md"])
        self.assertEqual(payload["graph.canvas"], '{"nodes": [], "edges": []}')


class TestPlanAndWriteUpstreamExport(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="omes-graphify-upstream-write-test-")
        self.payload = {
            "app.py.md": "---\nomes_generated: true\nsource_file: \"src/app.py\"\n---\nbody\n",
            "graph.canvas": '{"nodes": []}',
            ".obsidian/graph.json": "{}",
        }

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_first_write_creates_everything_and_records_ownership(self):
        written, conflicts = obsidian.write_upstream_export(self.payload, self.tmp)
        self.assertEqual(conflicts, [])
        self.assertEqual(
            sorted(written),
            sorted(self.payload.keys()),
        )
        manifest_path = os.path.join(self.tmp, obsidian.OMES_EXPORT_MANIFEST_NAME)
        self.assertTrue(os.path.exists(manifest_path))
        with open(manifest_path, encoding="utf-8") as f:
            owned = json.load(f)
        self.assertTrue(owned["omes_generated"])
        self.assertEqual(sorted(owned["files"]), sorted(self.payload.keys()))

    def test_second_write_overwrites_previously_owned_non_markdown_without_conflict(self):
        obsidian.write_upstream_export(self.payload, self.tmp)
        written, conflicts = obsidian.write_upstream_export(self.payload, self.tmp)
        self.assertEqual(conflicts, [])
        self.assertEqual(sorted(written), sorted(self.payload.keys()))

    def test_unowned_pre_existing_non_markdown_file_is_a_conflict(self):
        with open(os.path.join(self.tmp, "graph.canvas"), "w", encoding="utf-8") as f:
            f.write("not ours")

        written, conflicts = obsidian.write_upstream_export(self.payload, self.tmp)
        self.assertIn("graph.canvas", conflicts)
        self.assertNotIn("graph.canvas", written)
        with open(os.path.join(self.tmp, "graph.canvas"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "not ours")

    def test_markdown_collision_without_marker_is_a_conflict(self):
        with open(os.path.join(self.tmp, "app.py.md"), "w", encoding="utf-8") as f:
            f.write("# Not generated by OMES\n")

        written, conflicts = obsidian.write_upstream_export(self.payload, self.tmp)
        self.assertIn("app.py.md", conflicts)
        with open(os.path.join(self.tmp, "app.py.md"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "# Not generated by OMES\n")


if __name__ == "__main__":
    unittest.main()
