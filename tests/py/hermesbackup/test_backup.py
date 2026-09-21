import json
import os
import shutil
import stat
import tarfile
import tempfile
import unittest
from pathlib import Path

from . import _pathfix  # noqa: F401

from hermesbackup import archive, backup, classes as classes_mod, manifest  # noqa: E402


def _make_hermes_home(root: Path) -> Path:
    home = root / "hermeshome"
    (home / "skills").mkdir(parents=True)
    (home / "memories").mkdir(parents=True)
    (home / "sessions").mkdir(parents=True)
    (home / "logs").mkdir(parents=True)

    (home / "config.yaml").write_text("model: gpt\n", encoding="utf-8")
    (home / "SOUL.md").write_text("I am Hermes\n", encoding="utf-8")
    (home / "skills" / "foo.py").write_text("print('hi')\n", encoding="utf-8")
    (home / "memories" / "MEMORY.md").write_text("remembered\n", encoding="utf-8")
    (home / "sessions" / "s1.json").write_text("{}\n", encoding="utf-8")
    (home / "logs" / "gateway.log").write_text("log line\n", encoding="utf-8")
    (home / ".env").write_text("TELEGRAM_BOT_TOKEN=canary-secret-value\n", encoding="utf-8")
    (home / "auth.json").write_text('{"token": "canary-secret-value"}\n', encoding="utf-8")
    return home


class HermesBackupTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="omes-hermesbackup-test-")
        self.root = Path(self._tmp)
        self.hermes_home = _make_hermes_home(self.root)
        self.backups_root = self.root / "state" / "backups" / "hermes"

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)


class TestClasses(HermesBackupTestCase):
    def test_default_classes_are_config_and_skills_only(self):
        self.assertEqual(classes_mod.resolve_classes(None), ("config", "skills"))
        self.assertEqual(classes_mod.resolve_classes([]), ("config", "skills"))

    def test_invalid_class_name_rejected(self):
        with self.assertRaises(ValueError):
            classes_mod.resolve_classes(["bogus"])

    def test_missing_data_class_is_skipped_not_an_error(self):
        # 'memory' declares memories/ which this fixture creates, but
        # 'sessions' also declares a state.db file the fixture never
        # creates - build_archive must not error on the missing file, it
        # just backs up nothing for that one declared path.
        entries = archive.build_archive(self.hermes_home, ("sessions",), self._session_dir())
        paths = [e["path"] for e in entries]
        self.assertIn("sessions/s1.json", paths)
        self.assertNotIn("state.db", paths)

    def _session_dir(self):
        d = self.root / "adhoc-session"
        d.mkdir(parents=True, exist_ok=True)
        return d


class TestCreateAndSecretExclusion(HermesBackupTestCase):
    def test_default_create_excludes_secrets_everywhere(self):
        result = backup.create(self.hermes_home, ["config", "skills"], include_secrets=False, dry_run=False, dest_root=self.backups_root)
        session_dir = Path(result["path"])

        manifest_text = (session_dir / "MANIFEST").read_text(encoding="utf-8")
        self.assertNotIn("canary-secret-value", manifest_text)
        self.assertNotIn(".env", manifest_text)
        self.assertNotIn("auth.json", manifest_text)

        with tarfile.open(session_dir / "archive.tar", "r") as tar:
            names = tar.getnames()
        self.assertNotIn(".env", names)
        self.assertNotIn("auth.json", names)

        as_json = json.dumps(result)
        self.assertNotIn("canary-secret-value", as_json)

    def test_secrets_class_requires_include_secrets_flag(self):
        with self.assertRaises(backup.BackupError):
            backup.create(self.hermes_home, ["secrets"], include_secrets=False, dry_run=False, dest_root=self.backups_root)

    def test_include_secrets_backs_up_env_and_auth(self):
        result = backup.create(
            self.hermes_home, ["secrets"], include_secrets=True, dry_run=False, dest_root=self.backups_root
        )
        session_dir = Path(result["path"])
        with tarfile.open(session_dir / "archive.tar", "r") as tar:
            names = tar.getnames()
        self.assertIn(".env", names)
        self.assertIn("auth.json", names)

    def test_dry_run_never_opens_env_content_even_when_unreadable(self):
        env_path = self.hermes_home / ".env"
        original_mode = env_path.stat().st_mode
        os.chmod(env_path, 0)
        try:
            result = backup.create(
                self.hermes_home, ["secrets"], include_secrets=True, dry_run=True, dest_root=self.backups_root
            )
            self.assertTrue(result["dry_run"])
            paths = [e["path"] for e in result["entries"]]
            self.assertIn(".env", paths)
        finally:
            os.chmod(env_path, original_mode)

    def test_dry_run_creates_no_session_directory(self):
        backup.create(self.hermes_home, ["config", "skills"], include_secrets=False, dry_run=True, dest_root=self.backups_root)
        self.assertFalse(self.backups_root.exists() and any(self.backups_root.iterdir()))


class TestVerifyAndCorruption(HermesBackupTestCase):
    def test_verify_ok_on_freshly_created_backup(self):
        result = backup.create(self.hermes_home, ["config", "skills"], include_secrets=False, dry_run=False, dest_root=self.backups_root)
        verified = backup.verify(result["timestamp"], dest_root=self.backups_root)
        self.assertTrue(verified["ok"])
        self.assertEqual(verified["problems"], [])

    def test_corrupted_manifest_json_is_refused(self):
        result = backup.create(self.hermes_home, ["config"], include_secrets=False, dry_run=False, dest_root=self.backups_root)
        session_dir = self.backups_root / result["timestamp"]
        (session_dir / "MANIFEST").write_text("{not valid json\n", encoding="utf-8")
        with self.assertRaises(manifest.ManifestError):
            backup.verify(result["timestamp"], dest_root=self.backups_root)

    def test_manifest_missing_required_field_is_refused(self):
        result = backup.create(self.hermes_home, ["config"], include_secrets=False, dry_run=False, dest_root=self.backups_root)
        session_dir = self.backups_root / result["timestamp"]
        (session_dir / "MANIFEST").write_text('{"path": "config.yaml"}\n', encoding="utf-8")
        with self.assertRaises(manifest.ManifestError):
            backup.verify(result["timestamp"], dest_root=self.backups_root)

    def test_checksum_mismatch_detected_after_tampering(self):
        result = backup.create(self.hermes_home, ["config"], include_secrets=False, dry_run=False, dest_root=self.backups_root)
        session_dir = self.backups_root / result["timestamp"]

        # Tamper with the archive by rewriting it with different content
        # for the same member name.
        with tarfile.open(session_dir / "archive.tar", "w") as tar:
            data = b"tampered content"
            info = tarfile.TarInfo(name="config.yaml")
            info.size = len(data)
            import io
            tar.addfile(info, io.BytesIO(data))

        verified = backup.verify(result["timestamp"], dest_root=self.backups_root)
        self.assertFalse(verified["ok"])
        self.assertTrue(any("checksum mismatch" in p for p in verified["problems"]))

    def test_missing_archive_is_a_clear_backup_error(self):
        result = backup.create(self.hermes_home, ["config"], include_secrets=False, dry_run=False, dest_root=self.backups_root)
        session_dir = self.backups_root / result["timestamp"]
        (session_dir / "archive.tar").unlink()
        with self.assertRaises(backup.BackupError):
            backup.verify(result["timestamp"], dest_root=self.backups_root)


class TestRestore(HermesBackupTestCase):
    def test_restore_validates_checksums_before_writing_anything(self):
        result = backup.create(self.hermes_home, ["config", "skills"], include_secrets=False, dry_run=False, dest_root=self.backups_root)
        session_dir = self.backups_root / result["timestamp"]
        with tarfile.open(session_dir / "archive.tar", "w") as tar:
            import io
            data = b"tampered"
            info = tarfile.TarInfo(name="config.yaml")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))

        (self.hermes_home / "config.yaml").write_text("untouched\n", encoding="utf-8")
        with self.assertRaises(backup.BackupError):
            backup.restore(
                result["timestamp"], self.hermes_home, None, dry_run=False,
                restore_secrets=False, force_home=False, dest_root=self.backups_root,
            )
        self.assertEqual((self.hermes_home / "config.yaml").read_text(encoding="utf-8"), "untouched\n")

    def test_partial_restore_by_class(self):
        result = backup.create(self.hermes_home, ["config", "skills"], include_secrets=False, dry_run=False, dest_root=self.backups_root)

        (self.hermes_home / "config.yaml").write_text("changed after backup\n", encoding="utf-8")
        (self.hermes_home / "skills" / "foo.py").write_text("changed after backup\n", encoding="utf-8")

        out = backup.restore(
            result["timestamp"], self.hermes_home, ["config"], dry_run=False,
            restore_secrets=False, force_home=False, dest_root=self.backups_root,
        )
        self.assertIn("config.yaml", out["restored"])
        self.assertEqual((self.hermes_home / "config.yaml").read_text(encoding="utf-8"), "model: gpt\n")
        # skills was NOT requested, so it stays changed.
        self.assertEqual((self.hermes_home / "skills" / "foo.py").read_text(encoding="utf-8"), "changed after backup\n")

    def test_restore_creates_a_pre_restore_backup(self):
        result = backup.create(self.hermes_home, ["config"], include_secrets=False, dry_run=False, dest_root=self.backups_root)
        (self.hermes_home / "config.yaml").write_text("changed\n", encoding="utf-8")

        out = backup.restore(
            result["timestamp"], self.hermes_home, ["config"], dry_run=False,
            restore_secrets=False, force_home=False, dest_root=self.backups_root,
        )
        self.assertIsNotNone(out["pre_restore_backup"])
        pre_dir = self.backups_root / out["pre_restore_backup"]
        self.assertTrue(pre_dir.is_dir())

    def test_restore_never_overwrites_live_secrets_by_default(self):
        result = backup.create(self.hermes_home, ["secrets"], include_secrets=True, dry_run=False, dest_root=self.backups_root)
        (self.hermes_home / ".env").write_text("TELEGRAM_BOT_TOKEN=live-value\n", encoding="utf-8")

        out = backup.restore(
            result["timestamp"], self.hermes_home, ["secrets"], dry_run=False,
            restore_secrets=False, force_home=False, dest_root=self.backups_root,
        )
        self.assertEqual(out["restored"], [])
        self.assertTrue(out["skipped_secrets"])
        self.assertIn("live-value", (self.hermes_home / ".env").read_text(encoding="utf-8"))

    def test_restore_secrets_when_explicitly_requested(self):
        result = backup.create(self.hermes_home, ["secrets"], include_secrets=True, dry_run=False, dest_root=self.backups_root)
        (self.hermes_home / ".env").write_text("TELEGRAM_BOT_TOKEN=live-value\n", encoding="utf-8")

        out = backup.restore(
            result["timestamp"], self.hermes_home, ["secrets"], dry_run=False,
            restore_secrets=True, force_home=False, dest_root=self.backups_root,
        )
        self.assertIn(".env", out["restored"])
        self.assertIn("canary-secret-value", (self.hermes_home / ".env").read_text(encoding="utf-8"))

    def test_restore_refuses_a_different_hermes_home_without_force(self):
        result = backup.create(self.hermes_home, ["config"], include_secrets=False, dry_run=False, dest_root=self.backups_root)
        other_home = self.root / "other-hermeshome"
        other_home.mkdir()

        with self.assertRaises(backup.BackupError):
            backup.restore(
                result["timestamp"], other_home, ["config"], dry_run=False,
                restore_secrets=False, force_home=False, dest_root=self.backups_root,
            )

        out = backup.restore(
            result["timestamp"], other_home, ["config"], dry_run=False,
            restore_secrets=False, force_home=True, dest_root=self.backups_root,
        )
        self.assertIn("config.yaml", out["restored"])

    def test_restore_dry_run_writes_nothing(self):
        result = backup.create(self.hermes_home, ["config"], include_secrets=False, dry_run=False, dest_root=self.backups_root)
        (self.hermes_home / "config.yaml").write_text("changed\n", encoding="utf-8")

        out = backup.restore(
            result["timestamp"], self.hermes_home, ["config"], dry_run=True,
            restore_secrets=False, force_home=False, dest_root=self.backups_root,
        )
        self.assertTrue(out["dry_run"])
        self.assertEqual((self.hermes_home / "config.yaml").read_text(encoding="utf-8"), "changed\n")


class TestProfileIsolation(HermesBackupTestCase):
    def test_two_hermes_homes_produce_independent_backups(self):
        home_a = self.hermes_home
        home_b = self.root / "hermeshome-b"
        shutil.copytree(home_a, home_b)
        (home_b / "config.yaml").write_text("model: other\n", encoding="utf-8")

        res_a = backup.create(home_a, ["config"], include_secrets=False, dry_run=False, dest_root=self.backups_root)
        res_b = backup.create(home_b, ["config"], include_secrets=False, dry_run=False, dest_root=self.backups_root)

        self.assertNotEqual(res_a["timestamp"], res_b["timestamp"])
        meta_a = manifest.read_meta(self.backups_root / res_a["timestamp"])
        meta_b = manifest.read_meta(self.backups_root / res_b["timestamp"])
        self.assertEqual(meta_a["hermes_home"], str(home_a))
        self.assertEqual(meta_b["hermes_home"], str(home_b))
        self.assertNotEqual(meta_a["hermes_home"], meta_b["hermes_home"])

        # Restoring backup A into home B's directory must be refused
        # without --force-home, even though both are valid HERMES_HOME
        # trees (profile isolation).
        with self.assertRaises(backup.BackupError):
            backup.restore(
                res_a["timestamp"], home_b, ["config"], dry_run=False,
                restore_secrets=False, force_home=False, dest_root=self.backups_root,
            )


class TestPrune(HermesBackupTestCase):
    def test_prune_keeps_only_the_newest_n_sessions(self):
        for _ in range(5):
            backup.create(self.hermes_home, ["config"], include_secrets=False, dry_run=False, dest_root=self.backups_root)
        removed = backup.prune(keep=2, dest_root=self.backups_root)
        self.assertEqual(len(removed), 3)
        remaining = backup.list_sessions(dest_root=self.backups_root)
        self.assertEqual(len(remaining), 2)


class TestManifestPermissions(HermesBackupTestCase):
    def test_session_and_files_are_private(self):
        result = backup.create(self.hermes_home, ["config"], include_secrets=False, dry_run=False, dest_root=self.backups_root)
        session_dir = Path(result["path"])
        self.assertEqual(stat.S_IMODE(session_dir.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((session_dir / "MANIFEST").stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE((session_dir / "META").stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE((session_dir / "archive.tar").stat().st_mode), 0o600)


if __name__ == "__main__":
    unittest.main()
