import os
import shutil
import tempfile
import unittest

from . import _pathfix  # noqa: F401

from graphify import sync  # noqa: E402


class TestScanTree(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="omes-graphify-sync-test-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, rel, content):
        path = os.path.join(self.tmp, rel)
        os.makedirs(os.path.dirname(path) or self.tmp, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)

    def test_scan_finds_files_with_hash_and_mtime(self):
        self._write("src/a.py", "print(1)\n")
        snapshot = sync.scan_tree(self.tmp, [])
        self.assertIn("src/a.py", snapshot)
        self.assertIn("sha256", snapshot["src/a.py"])
        self.assertIn("mtime", snapshot["src/a.py"])

    def test_scan_excludes_dot_git(self):
        self._write(".git/HEAD", "ref: refs/heads/main\n")
        self._write("src/a.py", "print(1)\n")
        snapshot = sync.scan_tree(self.tmp, [])
        self.assertNotIn(".git/HEAD", snapshot)
        self.assertIn("src/a.py", snapshot)

    def test_scan_excludes_configured_abs_dirs(self):
        self._write("graphify-out/graph.json", "{}")
        self._write("src/a.py", "print(1)\n")
        exclude = [os.path.join(self.tmp, "graphify-out")]
        snapshot = sync.scan_tree(self.tmp, exclude)
        self.assertNotIn("graphify-out/graph.json", snapshot)
        self.assertIn("src/a.py", snapshot)

    def test_scan_excludes_gitignore_patterns(self):
        self._write(".gitignore", "node_modules\n*.pyc\n")
        self._write("node_modules/pkg/index.js", "module.exports = {}\n")
        self._write("src/a.pyc", "compiled")
        self._write("src/a.py", "print(1)\n")
        snapshot = sync.scan_tree(self.tmp, [])
        self.assertNotIn("node_modules/pkg/index.js", snapshot)
        self.assertNotIn("src/a.pyc", snapshot)
        self.assertIn("src/a.py", snapshot)

    def test_scan_skips_symlinks(self):
        self._write("real.py", "print(1)\n")
        link_path = os.path.join(self.tmp, "link.py")
        os.symlink(os.path.join(self.tmp, "real.py"), link_path)
        snapshot = sync.scan_tree(self.tmp, [])
        self.assertIn("real.py", snapshot)
        self.assertNotIn("link.py", snapshot)


class TestDiffSnapshot(unittest.TestCase):
    def test_added_removed_modified(self):
        old = {
            "a.py": {"sha256": "1"},
            "b.py": {"sha256": "2"},
        }
        new = {
            "a.py": {"sha256": "1"},
            "b.py": {"sha256": "changed"},
            "c.py": {"sha256": "3"},
        }
        added, removed, modified = sync.diff_snapshot(old, new)
        self.assertEqual(added, ["c.py"])
        self.assertEqual(removed, [])
        self.assertEqual(modified, ["b.py"])

    def test_no_changes(self):
        old = {"a.py": {"sha256": "1"}}
        new = {"a.py": {"sha256": "1"}}
        added, removed, modified = sync.diff_snapshot(old, new)
        self.assertFalse(sync.has_changes(added, removed, modified))

    def test_mtime_only_change_is_not_modified(self):
        old = {"a.py": {"sha256": "1", "mtime": 100}}
        new = {"a.py": {"sha256": "1", "mtime": 200}}
        added, removed, modified = sync.diff_snapshot(old, new)
        self.assertEqual(modified, [])


if __name__ == "__main__":
    unittest.main()
