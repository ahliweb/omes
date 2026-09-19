import json
import os
import shutil
import tempfile
import unittest

from . import _pathfix  # noqa: F401

from graphify import obsidian  # noqa: E402


SAMPLE_GRAPH = {
    "nodes": [
        {"id": "f1", "type": "file", "path": "src/a.py"},
        {"id": "f2", "type": "file", "path": "src/b.py"},
        {"id": "s1", "type": "function", "name": "do_thing", "file": "src/a.py"},
    ],
    "edges": [
        {"source": "f1", "target": "f2", "type": "EXTRACTED"},
    ],
}


class TestGraphLoading(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="omes-graphify-test-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_graph(self, data):
        path = os.path.join(self.tmp, "graph.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)
        return path

    def test_load_graph_defaults_missing_keys(self):
        path = self._write_graph({})
        graph = obsidian.load_graph(path)
        self.assertEqual(graph["nodes"], [])
        self.assertEqual(graph["edges"], [])

    def test_load_graph_rejects_non_object(self):
        path = self._write_graph_raw("[]")
        with self.assertRaises(ValueError):
            obsidian.load_graph(path)

    def _write_graph_raw(self, text):
        path = os.path.join(self.tmp, "graph.json")
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path

    def test_sha256_file_is_stable(self):
        path = self._write_graph(SAMPLE_GRAPH)
        first = obsidian.sha256_file(path)
        second = obsidian.sha256_file(path)
        self.assertEqual(first, second)
        self.assertEqual(len(first), 64)


class TestBuildNotes(unittest.TestCase):
    def _notes(self, graph=SAMPLE_GRAPH):
        return obsidian.build_notes(
            graph,
            project_name="myrepo",
            source_root="/home/op/code/myrepo",
            graphify_version="0.9.64",
            extraction_mode="code",
            generated_at="2026-09-19T12:00:00Z",
            graph_sha256="deadbeef" * 8,
        )

    def test_empty_graph_still_produces_index_and_provenance(self):
        notes = self._notes({"nodes": [], "edges": []})
        self.assertIn(obsidian.INDEX_NOTE_NAME, notes)
        self.assertIn(obsidian.PROVENANCE_NOTE_NAME, notes)
        self.assertEqual(len(notes), 2)

    def test_every_note_carries_required_front_matter(self):
        notes = self._notes()
        for name, content in notes.items():
            self.assertTrue(content.startswith("---\n"), name)
            self.assertIn("omes_generated: true", content)
            self.assertIn('graphify_version: "0.9.64"', content)
            self.assertIn('extraction_mode: "code"', content)
            self.assertIn('generated_at: "2026-09-19T12:00:00Z"', content)
            self.assertIn("graph_sha256:", content)

    def test_file_notes_carry_source_path_and_symbol(self):
        notes = self._notes()
        a_note = notes["src__a.py.md"]
        self.assertIn('source: "src/a.py"', a_note)
        self.assertIn("do_thing", a_note)

    def test_edge_becomes_wikilink(self):
        notes = self._notes()
        a_note = notes["src__a.py.md"]
        self.assertIn("[[src__b.py]]", a_note)
        self.assertIn("EXTRACTED", a_note)

    def test_index_lists_all_files(self):
        notes = self._notes()
        index = notes[obsidian.INDEX_NOTE_NAME]
        self.assertIn("[[src__a.py]]", index)
        self.assertIn("[[src__b.py]]", index)

    def test_provenance_note_has_counts(self):
        notes = self._notes()
        prov = notes[obsidian.PROVENANCE_NOTE_NAME]
        self.assertIn("Nodes: 3", prov)
        self.assertIn("Edges: 1", prov)


class TestMarkerDetection(unittest.TestCase):
    def test_has_marker_true(self):
        content = "---\nomes_generated: true\nfoo: \"bar\"\n---\n\n# hi\n"
        self.assertTrue(obsidian.has_omes_marker(content))

    def test_has_marker_false_for_user_note(self):
        content = "# My own note\n\nSome thoughts.\n"
        self.assertFalse(obsidian.has_omes_marker(content))

    def test_has_marker_false_for_frontmatter_without_marker(self):
        content = "---\ntitle: \"hello\"\n---\n\nbody\n"
        self.assertFalse(obsidian.has_omes_marker(content))


class TestPlanAndWrite(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="omes-graphify-write-test-")
        self.notes = {
            "a.md": "---\nomes_generated: true\n---\nA\n",
            "b.md": "---\nomes_generated: true\n---\nB\n",
        }

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_plan_on_empty_dir_creates_everything(self):
        planned, conflicts = obsidian.plan_notes(self.notes, self.tmp)
        self.assertEqual(conflicts, [])
        actions = {p["path"]: p["action"] for p in planned}
        self.assertEqual(actions, {"a.md": "create", "b.md": "create"})

    def test_plan_never_writes_files(self):
        obsidian.plan_notes(self.notes, self.tmp)
        self.assertEqual(os.listdir(self.tmp), [])

    def test_write_then_plan_reports_update(self):
        written, conflicts = obsidian.write_notes(self.notes, self.tmp)
        self.assertEqual(sorted(written), ["a.md", "b.md"])
        self.assertEqual(conflicts, [])

        planned, conflicts2 = obsidian.plan_notes(self.notes, self.tmp)
        self.assertEqual(conflicts2, [])
        actions = {p["path"]: p["action"] for p in planned}
        self.assertEqual(actions, {"a.md": "update", "b.md": "update"})

    def test_user_authored_collision_is_refused(self):
        with open(os.path.join(self.tmp, "a.md"), "w", encoding="utf-8") as f:
            f.write("# My own note, not generated by OMES\n")

        written, conflicts = obsidian.write_notes(self.notes, self.tmp)
        self.assertEqual(written, ["b.md"])
        self.assertEqual(conflicts, ["a.md"])

        with open(os.path.join(self.tmp, "a.md"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "# My own note, not generated by OMES\n")

    def test_unrelated_existing_file_untouched(self):
        with open(os.path.join(self.tmp, "unrelated.md"), "w", encoding="utf-8") as f:
            f.write("unrelated note\n")

        obsidian.write_notes(self.notes, self.tmp)

        with open(os.path.join(self.tmp, "unrelated.md"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "unrelated note\n")


if __name__ == "__main__":
    unittest.main()
