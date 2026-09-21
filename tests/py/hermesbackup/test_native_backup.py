import json
import os
import shutil
import tarfile
import tempfile
import unittest
from pathlib import Path

from . import _pathfix  # noqa: F401

from hermesbackup import backup, classes as classes_mod, manifest


# Ensure tests/shims is in PATH so mock `hermes` is executed
SHIM_DIR = str(Path(__file__).resolve().parents[2] / "shims")
if SHIM_DIR not in os.environ.get("PATH", ""):
    os.environ["PATH"] = f"{SHIM_DIR}:{os.environ.get('PATH', '')}"


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


class NativeBackupTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="omes-nativebackup-test-")
        self.root = Path(self._tmp)
        self.hermes_home = _make_hermes_home(self.root)
        self.backups_root = self.root / "state" / "backups" / "hermes"

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)
        os.environ.pop("SHIM_HERMES_DOCTOR_EXIT", None)
        os.environ.pop("SHIM_HERMES_PROFILE_EXPORT_EXIT", None)
        os.environ.pop("SHIM_HERMES_BACKUP_EXIT", None)


class TestNativeCreate(NativeBackupTestCase):
    def test_default_create_is_native_portable_profile(self):
        result = backup.create(self.hermes_home, dest_root=self.backups_root)
        self.assertEqual(result["format"], "native-hermes-profile")
        self.assertEqual(result["recovery_class"], "portable-profile")
        self.assertEqual(result["profile"], "default")
        self.assertFalse(result["sensitive"])

        session_dir = Path(result["path"])
        self.assertTrue((session_dir / "default.hermes-profile.tar.gz").is_file())
        self.assertTrue((session_dir / "MANIFEST").is_file())
        self.assertTrue((session_dir / "META").is_file())

        meta = manifest.read_meta(session_dir)
        self.assertEqual(meta["format"], "native-hermes-profile")
        self.assertEqual(meta["recovery_class"], "portable-profile")
        self.assertEqual(meta["profile"], "default")
        self.assertEqual(meta["artifact"], "default.hermes-profile.tar.gz")
        self.assertTrue(meta.get("sha256"))

    def test_create_custom_profile(self):
        result = backup.create(
            self.hermes_home,
            profile="researcher",
            dest_root=self.backups_root,
        )
        self.assertEqual(result["profile"], "researcher")
        self.assertEqual(result["artifact"], "researcher.hermes-profile.tar.gz")
        session_dir = Path(result["path"])
        self.assertTrue((session_dir / "researcher.hermes-profile.tar.gz").is_file())

    def test_full_runtime_dr_requires_sensitive_credentials(self):
        with self.assertRaises(backup.BackupError):
            backup.create(
                self.hermes_home,
                recovery_class="full-runtime-dr",
                allow_sensitive_credentials=False,
                dest_root=self.backups_root,
            )

    def test_full_runtime_dr_with_sensitive_credentials(self):
        result = backup.create(
            self.hermes_home,
            recovery_class="full-runtime-dr",
            allow_sensitive_credentials=True,
            dest_root=self.backups_root,
        )
        self.assertEqual(result["format"], "native-hermes-runtime")
        self.assertEqual(result["recovery_class"], "full-runtime-dr")
        self.assertTrue(result["sensitive"])
        self.assertEqual(result["artifact"], "hermes-backup.tar.gz")
        session_dir = Path(result["path"])
        self.assertTrue((session_dir / "hermes-backup.tar.gz").is_file())

    def test_dry_run_native_profile(self):
        result = backup.create(
            self.hermes_home,
            dry_run=True,
            dest_root=self.backups_root,
        )
        self.assertTrue(result["dry_run"])
        self.assertEqual(result["format"], "native-hermes-profile")
        self.assertFalse(self.backups_root.exists() and any(self.backups_root.iterdir()))

    def test_export_failure_raises_backup_error(self):
        os.environ["SHIM_HERMES_PROFILE_EXPORT_EXIT"] = "1"
        with self.assertRaises(backup.BackupError):
            backup.create(self.hermes_home, dest_root=self.backups_root)


class TestNativeVerify(NativeBackupTestCase):
    def test_verify_native_profile_ok(self):
        result = backup.create(self.hermes_home, dest_root=self.backups_root)
        verified = backup.verify(result["timestamp"], dest_root=self.backups_root)
        self.assertTrue(verified["ok"])
        self.assertEqual(verified["problems"], [])
        self.assertEqual(verified["format"], "native-hermes-profile")

    def test_verify_detects_tampered_native_artifact_sha256(self):
        result = backup.create(self.hermes_home, dest_root=self.backups_root)
        session_dir = self.backups_root / result["timestamp"]
        artifact_path = session_dir / result["artifact"]
        with tarfile.open(artifact_path, "w:gz") as tar:
            import io
            data = b"tampered"
            ti = tarfile.TarInfo("profile.json")
            ti.size = len(data)
            tar.addfile(ti, io.BytesIO(data))

        verified = backup.verify(result["timestamp"], dest_root=self.backups_root)
        self.assertFalse(verified["ok"])
        self.assertTrue(any("sha256 mismatch" in p for p in verified["problems"]))

    def test_verify_detects_corrupted_archive_bytes(self):
        result = backup.create(self.hermes_home, dest_root=self.backups_root)
        session_dir = self.backups_root / result["timestamp"]
        artifact_path = session_dir / result["artifact"]
        artifact_path.write_text("corrupted content", encoding="utf-8")

        with self.assertRaises(backup.BackupError):
            backup.verify(result["timestamp"], dest_root=self.backups_root)

    def test_verify_detects_missing_native_artifact(self):
        result = backup.create(self.hermes_home, dest_root=self.backups_root)
        session_dir = self.backups_root / result["timestamp"]
        (session_dir / result["artifact"]).unlink()

        with self.assertRaises(backup.BackupError):
            backup.verify(result["timestamp"], dest_root=self.backups_root)


class TestNativeRestore(NativeBackupTestCase):
    def test_restore_native_profile_success(self):
        created = backup.create(self.hermes_home, dest_root=self.backups_root)
        res = backup.restore(
            created["timestamp"],
            self.hermes_home,
            dest_root=self.backups_root,
            backups_dest_root=self.backups_root,
        )
        self.assertTrue(res["health_verified"])
        self.assertEqual(res["format"], "native-hermes-profile")
        self.assertIsNotNone(res["pre_restore_backup"])

    def test_restore_full_runtime_refuses_without_sensitive_credentials(self):
        created = backup.create(
            self.hermes_home,
            recovery_class="full-runtime-dr",
            allow_sensitive_credentials=True,
            dest_root=self.backups_root,
        )
        with self.assertRaises(backup.BackupError) as ctx:
            backup.restore(
                created["timestamp"],
                self.hermes_home,
                allow_sensitive_credentials=False,
                dest_root=self.backups_root,
                backups_dest_root=self.backups_root,
            )
        self.assertIn("sensitive credentials", str(ctx.exception))

    def test_restore_full_runtime_with_sensitive_credentials_success(self):
        created = backup.create(
            self.hermes_home,
            recovery_class="full-runtime-dr",
            allow_sensitive_credentials=True,
            dest_root=self.backups_root,
        )
        res = backup.restore(
            created["timestamp"],
            self.hermes_home,
            allow_sensitive_credentials=True,
            dest_root=self.backups_root,
            backups_dest_root=self.backups_root,
        )
        self.assertTrue(res["health_verified"])
        self.assertEqual(res["format"], "native-hermes-runtime")

    def test_restore_rejects_tampered_artifact(self):
        created = backup.create(self.hermes_home, dest_root=self.backups_root)
        session_dir = self.backups_root / created["timestamp"]
        (session_dir / created["artifact"]).write_text("tampered", encoding="utf-8")

        with self.assertRaises(backup.BackupError) as ctx:
            backup.restore(
                created["timestamp"],
                self.hermes_home,
                dest_root=self.backups_root,
                backups_dest_root=self.backups_root,
            )
        self.assertIn("corrupt", str(ctx.exception))

    def test_restore_fails_when_health_check_fails(self):
        created = backup.create(self.hermes_home, dest_root=self.backups_root)
        os.environ["SHIM_HERMES_DOCTOR_EXIT"] = "1"

        with self.assertRaises(backup.BackupError) as ctx:
            backup.restore(
                created["timestamp"],
                self.hermes_home,
                dest_root=self.backups_root,
                backups_dest_root=self.backups_root,
            )
        self.assertIn("health check failed", str(ctx.exception))

    def test_restore_refuses_different_home_without_force(self):
        created = backup.create(self.hermes_home, dest_root=self.backups_root)
        other_home = self.root / "other-home"
        other_home.mkdir()

        with self.assertRaises(backup.BackupError):
            backup.restore(
                created["timestamp"],
                other_home,
                force_home=False,
                dest_root=self.backups_root,
                backups_dest_root=self.backups_root,
            )

        res = backup.restore(
            created["timestamp"],
            other_home,
            force_home=True,
            dest_root=self.backups_root,
            backups_dest_root=self.backups_root,
        )
        self.assertTrue(res["health_verified"])


class TestInventoryAndPruning(NativeBackupTestCase):
    def test_list_sessions_reports_native_attributes(self):
        res_profile = backup.create(self.hermes_home, profile="researcher", dest_root=self.backups_root)
        res_runtime = backup.create(
            self.hermes_home,
            recovery_class="full-runtime-dr",
            allow_sensitive_credentials=True,
            dest_root=self.backups_root,
        )

        sessions = backup.list_sessions(dest_root=self.backups_root)
        self.assertEqual(len(sessions), 2)
        by_ts = {s["timestamp"]: s for s in sessions}

        s_prof = by_ts[res_profile["timestamp"]]
        self.assertEqual(s_prof["format"], "native-hermes-profile")
        self.assertEqual(s_prof["recovery_class"], "portable-profile")
        self.assertEqual(s_prof["profile"], "researcher")
        self.assertFalse(s_prof["sensitive"])

        s_run = by_ts[res_runtime["timestamp"]]
        self.assertEqual(s_run["format"], "native-hermes-runtime")
        self.assertEqual(s_run["recovery_class"], "full-runtime-dr")
        self.assertTrue(s_run["sensitive"])

    def test_prune_native_sessions(self):
        for _ in range(4):
            backup.create(self.hermes_home, dest_root=self.backups_root)
        removed = backup.prune(keep=2, dest_root=self.backups_root)
        self.assertEqual(len(removed), 2)
        remaining = backup.list_sessions(dest_root=self.backups_root)
        self.assertEqual(len(remaining), 2)


if __name__ == "__main__":
    unittest.main()
