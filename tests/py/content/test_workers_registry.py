import unittest

from . import _pathfix  # noqa: F401

from content.workers import registry  # noqa: E402


class TestRegistry(unittest.TestCase):
    def test_generic_browser_is_registered(self):
        self.assertIn("generic_browser", registry.list_platforms())

    def test_worker_executable_for_known_platform_exists(self):
        path = registry.worker_executable_for_platform("generic_browser")
        self.assertTrue(path.is_file())
        self.assertEqual(path.name, "worker.py")

    def test_unknown_platform_raises(self):
        with self.assertRaises(registry.UnknownPlatformError):
            registry.worker_executable_for_platform("does-not-exist")

    def test_list_platforms_ignores_dunder_and_private_dirs(self):
        # __pycache__ (created by running these tests) must never show up
        # as a "platform".
        self.assertNotIn("__pycache__", registry.list_platforms())


if __name__ == "__main__":
    unittest.main()
