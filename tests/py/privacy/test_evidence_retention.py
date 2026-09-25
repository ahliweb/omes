"""Unit tests for lib/omes/py/privacy/evidence_retention.py - opt-in
retention/rotation for AI-privacy posture evidence OMES itself persists
(issue #234).

These tests exercise `persist()`/`prune()` directly against a temporary
state directory (never a real OMES state dir), with an injected clock
(no `time.sleep`) for every expiry assertion.
"""
import datetime
import json
import os
import shutil
import stat
import tempfile
import unittest

from . import _pathfix  # noqa: F401  (sets sys.path)

from privacy import evidence_retention as er  # noqa: E402
from privacy import posture_evidence as pe  # noqa: E402


def _valid_evidence(**overrides):
    base = {
        "policy_version": "v1",
        "classification_mode": "fail_closed_v1",
        "destination_class": "local",
        "local_endpoint_classification": "loopback",
        "cloud_fallback_enabled": "disabled",
        "network_isolation_active": "active",
        "last_verified_at": "2026-09-25T00:00:00Z",
        "evidence_source": "omes-host:network-classification",
        "local_only_posture": {
            "available": False,
            "status": "unknown",
            "source": "local-only-posture-source:unavailable",
        },
        "hermes_version_reference": None,
        "model_artifact_provenance": {
            "available": False,
            "status": "unknown",
            "reason": "not_declared",
            "declared_count": 0,
            "verified_count": 0,
            "last_verified_at": None,
        },
        "status": "PASS",
        "reason_codes": ["AI_PRIVACY_POSTURE_PASS_CONSISTENT"],
    }
    base.update(overrides)
    return base


class EvidenceRetentionTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="omes-234-evidence-retention-unit-")
        self.state_dir = self._tmp
        self.evidence_dir = os.path.join(self.state_dir, er.EVIDENCE_DIR_NAME)

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)


class TestValidateRetentionConfig(EvidenceRetentionTestCase):
    def test_valid_values_pass_through_unchanged(self):
        self.assertEqual(er.validate_retention_config(90 * 86400, 500), (90 * 86400, 500))

    def test_zero_max_age_fails_closed(self):
        with self.assertRaises(er.RetentionConfigError):
            er.validate_retention_config(0, 500)

    def test_negative_max_age_fails_closed(self):
        with self.assertRaises(er.RetentionConfigError):
            er.validate_retention_config(-1, 500)

    def test_zero_max_count_fails_closed(self):
        with self.assertRaises(er.RetentionConfigError):
            er.validate_retention_config(86400, 0)

    def test_negative_max_count_fails_closed(self):
        with self.assertRaises(er.RetentionConfigError):
            er.validate_retention_config(86400, -5)

    def test_absurdly_large_max_age_fails_closed(self):
        with self.assertRaises(er.RetentionConfigError):
            er.validate_retention_config(er.MAX_MAX_AGE_SECONDS + 1, 500)

    def test_absurdly_large_max_count_fails_closed(self):
        with self.assertRaises(er.RetentionConfigError):
            er.validate_retention_config(86400, er.MAX_MAX_COUNT + 1)

    def test_non_integer_types_fail_closed(self):
        with self.assertRaises(er.RetentionConfigError):
            er.validate_retention_config(1.5, 500)
        with self.assertRaises(er.RetentionConfigError):
            er.validate_retention_config(86400, "500")
        with self.assertRaises(er.RetentionConfigError):
            er.validate_retention_config(True, 500)  # bool is an int subclass - must still be rejected

    def test_boundary_values_are_accepted(self):
        er.validate_retention_config(er.MIN_MAX_AGE_SECONDS, er.MIN_MAX_COUNT)
        er.validate_retention_config(er.MAX_MAX_AGE_SECONDS, er.MAX_MAX_COUNT)


class TestPersist(EvidenceRetentionTestCase):
    def test_default_mode_writes_nothing(self):
        """Merely calling `evaluate()` - what every `omes health
        ai-privacy` invocation does - must never itself create the
        evidence directory. Only an explicit `persist()` call does."""
        pe.evaluate({"policy_version": "v1", "destination_class": "unknown", "observed_at": "2026-09-25T00:00:00Z"})
        self.assertFalse(os.path.isdir(self.evidence_dir))

    def test_a_valid_record_persists_with_restrictive_permissions(self):
        result = er.persist(self.state_dir, _valid_evidence())
        self.assertTrue(result["ok"], msg=result)
        self.assertTrue(os.path.isfile(result["path"]))

        dir_mode = stat.S_IMODE(os.stat(self.evidence_dir).st_mode)
        file_mode = stat.S_IMODE(os.stat(result["path"]).st_mode)
        self.assertEqual(dir_mode, 0o700)
        self.assertEqual(file_mode, 0o600)

        with open(result["path"], "r", encoding="utf-8") as fh:
            on_disk = json.load(fh)
        self.assertEqual(on_disk["evidence"], _valid_evidence())
        self.assertEqual(on_disk["persisted_at"], result["persisted_at"])

    def test_filename_matches_the_declared_naming_pattern(self):
        result = er.persist(self.state_dir, _valid_evidence())
        name = os.path.basename(result["path"])
        self.assertRegex(name, er._FILENAME_PATTERN.pattern)

    def test_a_non_dict_evidence_value_is_refused(self):
        result = er.persist(self.state_dir, ["not", "a", "dict"])
        self.assertFalse(result["ok"])
        self.assertFalse(os.path.isdir(self.evidence_dir))

    def test_a_record_missing_a_required_field_is_refused(self):
        broken = _valid_evidence()
        del broken["status"]
        result = er.persist(self.state_dir, broken)
        self.assertFalse(result["ok"], msg=result)
        self.assertFalse(os.listdir(self.evidence_dir) if os.path.isdir(self.evidence_dir) else False)

    def test_a_secret_shaped_value_in_the_one_echoed_free_text_field_is_refused(self):
        canary = "AKIA" + "CANARY234RETENTIONTESTVALUE"
        poisoned = _valid_evidence(evidence_source=canary)
        result = er.persist(self.state_dir, poisoned)
        self.assertFalse(result["ok"], msg=result)
        self.assertFalse(os.path.isdir(self.evidence_dir))

    def test_an_unexpected_additional_top_level_field_is_refused(self):
        poisoned = _valid_evidence()
        poisoned["raw_prompt"] = "this must never be accepted"
        result = er.persist(self.state_dir, poisoned)
        self.assertFalse(result["ok"], msg=result)

    def test_two_persisted_records_get_distinct_filenames(self):
        first = er.persist(self.state_dir, _valid_evidence())
        second = er.persist(self.state_dir, _valid_evidence())
        self.assertNotEqual(first["path"], second["path"])

    def test_a_symlinked_evidence_directory_is_refused(self):
        """The evidence directory path ITSELF being a symlink must be
        refused before any write is attempted - the fail-open bypass a
        code review caught: `os.path.realpath()` alone would silently
        follow the symlink and write through it."""
        real_target = os.path.join(self._tmp, "real-target")
        os.makedirs(real_target, mode=0o700)
        os.symlink(real_target, self.evidence_dir)

        result = er.persist(self.state_dir, _valid_evidence())

        self.assertFalse(result["ok"], msg=result)
        self.assertEqual(os.listdir(real_target), [])
        self.assertTrue(os.path.islink(self.evidence_dir))

    def test_a_group_or_world_writable_evidence_directory_is_refused(self):
        os.makedirs(self.evidence_dir, mode=0o700)
        os.chmod(self.evidence_dir, 0o777)  # explicit chmod: makedirs' mode is masked by umask
        result = er.persist(self.state_dir, _valid_evidence())
        self.assertFalse(result["ok"], msg=result)
        self.assertEqual(os.listdir(self.evidence_dir), [])


class TestPrune(EvidenceRetentionTestCase):
    def _write_raw(self, name: str, persisted_at: str, evidence: dict = None) -> str:
        os.makedirs(self.evidence_dir, mode=0o700, exist_ok=True)
        path = os.path.join(self.evidence_dir, name)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"schema_version": "v1", "persisted_at": persisted_at, "evidence": evidence or {}}, fh)
        return path

    def test_prune_on_a_missing_directory_is_a_no_op(self):
        result = er.prune(self.state_dir, max_age_seconds=86400, max_count=10)
        self.assertTrue(result["ok"])
        self.assertEqual(result["pruned"], [])
        self.assertEqual(result["kept"], [])

    def test_expiry_removes_only_records_older_than_max_age(self):
        now = datetime.datetime(2026, 9, 25, 0, 0, 0, tzinfo=datetime.timezone.utc)
        old_name = "ai-privacy-evidence-20260101T000000Z-11111111.json"
        new_name = "ai-privacy-evidence-20260924T000000Z-22222222.json"
        self._write_raw(old_name, (now - datetime.timedelta(days=200)).strftime("%Y-%m-%dT%H:%M:%SZ"))
        self._write_raw(new_name, (now - datetime.timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ"))
        os.utime(os.path.join(self.evidence_dir, old_name), ((now - datetime.timedelta(days=200)).timestamp(),) * 2)
        os.utime(os.path.join(self.evidence_dir, new_name), ((now - datetime.timedelta(days=1)).timestamp(),) * 2)

        result = er.prune(self.state_dir, max_age_seconds=90 * 86400, max_count=500, now=now)

        self.assertEqual(result["pruned"], [old_name])
        self.assertEqual(result["kept"], [new_name])
        self.assertFalse(os.path.exists(os.path.join(self.evidence_dir, old_name)))
        self.assertTrue(os.path.exists(os.path.join(self.evidence_dir, new_name)))

    def test_expiry_is_idempotent(self):
        now = datetime.datetime(2026, 9, 25, 0, 0, 0, tzinfo=datetime.timezone.utc)
        name = "ai-privacy-evidence-20260101T000000Z-33333333.json"
        self._write_raw(name, (now - datetime.timedelta(days=200)).strftime("%Y-%m-%dT%H:%M:%SZ"))

        first = er.prune(self.state_dir, max_age_seconds=90 * 86400, max_count=500, now=now)
        second = er.prune(self.state_dir, max_age_seconds=90 * 86400, max_count=500, now=now)

        self.assertEqual(first["pruned"], [name])
        self.assertEqual(second["pruned"], [])
        self.assertEqual(second["kept"], [])

    def test_max_count_cap_keeps_only_the_newest_n(self):
        now = datetime.datetime(2026, 9, 25, 0, 0, 0, tzinfo=datetime.timezone.utc)
        names = []
        for i in range(5):
            name = f"ai-privacy-evidence-2026010{i}T000000Z-{i:08x}.json"
            ts = (now - datetime.timedelta(days=i)).strftime("%Y-%m-%dT%H:%M:%SZ")
            self._write_raw(name, ts)
            os.utime(os.path.join(self.evidence_dir, name), ((now - datetime.timedelta(days=i)).timestamp(),) * 2)
            names.append(name)

        # names[0] is newest (0 days old), names[4] is oldest (4 days old).
        result = er.prune(self.state_dir, max_age_seconds=er.MAX_MAX_AGE_SECONDS, max_count=2, now=now)

        self.assertEqual(sorted(result["kept"]), sorted(names[:2]))
        self.assertEqual(sorted(result["pruned"]), sorted(names[2:]))

    def test_dry_run_reports_without_deleting(self):
        now = datetime.datetime(2026, 9, 25, 0, 0, 0, tzinfo=datetime.timezone.utc)
        name = "ai-privacy-evidence-20260101T000000Z-44444444.json"
        self._write_raw(name, (now - datetime.timedelta(days=200)).strftime("%Y-%m-%dT%H:%M:%SZ"))

        result = er.prune(self.state_dir, max_age_seconds=90 * 86400, max_count=500, now=now, dry_run=True)

        self.assertEqual(result["pruned"], [name])
        self.assertTrue(result["dry_run"])
        self.assertTrue(os.path.exists(os.path.join(self.evidence_dir, name)), "dry-run must not delete anything")

    def test_prune_refuses_a_symlink_regardless_of_its_target(self):
        os.makedirs(self.evidence_dir, mode=0o700, exist_ok=True)
        outside = os.path.join(self._tmp, "outside.json")
        with open(outside, "w", encoding="utf-8") as fh:
            fh.write("{}")
        link_name = "ai-privacy-evidence-20260101T000000Z-55555555.json"
        os.symlink(outside, os.path.join(self.evidence_dir, link_name))

        result = er.prune(self.state_dir, max_age_seconds=1, max_count=1)

        self.assertEqual(result["pruned"], [])
        self.assertEqual([s["reason"] for s in result["skipped"] if s["name"] == link_name], ["symlink_refused"])
        self.assertTrue(os.path.exists(outside))
        self.assertTrue(os.path.islink(os.path.join(self.evidence_dir, link_name)))

    def test_prune_refuses_a_file_not_matching_the_naming_pattern(self):
        os.makedirs(self.evidence_dir, mode=0o700, exist_ok=True)
        foreign = os.path.join(self.evidence_dir, "unrelated-tool-state.json")
        with open(foreign, "w", encoding="utf-8") as fh:
            fh.write("{}")

        result = er.prune(self.state_dir, max_age_seconds=1, max_count=1)

        self.assertEqual(result["pruned"], [])
        self.assertTrue(os.path.exists(foreign))
        reasons = {s["name"]: s["reason"] for s in result["skipped"]}
        self.assertEqual(reasons.get("unrelated-tool-state.json"), "unrecognized_name")

    def test_a_record_with_unparsable_persisted_at_falls_back_to_mtime(self):
        now = datetime.datetime(2026, 9, 25, 0, 0, 0, tzinfo=datetime.timezone.utc)
        name = "ai-privacy-evidence-20260101T000000Z-66666666.json"
        path = self._write_raw(name, "not-a-timestamp")
        old_ts = (now - datetime.timedelta(days=200)).timestamp()
        os.utime(path, (old_ts, old_ts))

        result = er.prune(self.state_dir, max_age_seconds=90 * 86400, max_count=500, now=now)

        self.assertEqual(result["pruned"], [name])

    def test_invalid_retention_config_raises_before_touching_the_filesystem(self):
        name = "ai-privacy-evidence-20260101T000000Z-77777777.json"
        self._write_raw(name, "2026-01-01T00:00:00Z")
        with self.assertRaises(er.RetentionConfigError):
            er.prune(self.state_dir, max_age_seconds=0, max_count=500)
        self.assertTrue(os.path.exists(os.path.join(self.evidence_dir, name)))

    def test_prune_refuses_a_symlinked_evidence_directory_and_leaves_its_target_alone(self):
        """The directory-confinement fail-open a code review caught:
        `<state-dir>/ai-privacy-evidence` replaced with a symlink to an
        arbitrary directory must not let `prune()` delete pattern-
        matching files it finds through that symlink."""
        real_target = os.path.join(self._tmp, "real-target")
        os.makedirs(real_target, mode=0o700)
        old_name = "ai-privacy-evidence-20200101T000000Z-aaaaaaaa.json"
        old_path = os.path.join(real_target, old_name)
        with open(old_path, "w", encoding="utf-8") as fh:
            json.dump({"schema_version": "v1", "persisted_at": "2020-01-01T00:00:00Z", "evidence": {}}, fh)
        os.symlink(real_target, self.evidence_dir)

        result = er.prune(self.state_dir, max_age_seconds=1, max_count=1)

        self.assertFalse(result["ok"], msg=result)
        self.assertEqual(result["pruned"], [])
        self.assertTrue(os.path.exists(old_path), "prune must never delete through a symlinked evidence dir")
        self.assertTrue(os.path.islink(self.evidence_dir))

    def test_prune_refuses_a_group_or_world_writable_evidence_directory(self):
        os.makedirs(self.evidence_dir, mode=0o700)
        os.chmod(self.evidence_dir, 0o777)
        name = "ai-privacy-evidence-20200101T000000Z-bbbbbbbb.json"
        path = os.path.join(self.evidence_dir, name)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"schema_version": "v1", "persisted_at": "2020-01-01T00:00:00Z", "evidence": {}}, fh)

        result = er.prune(self.state_dir, max_age_seconds=1, max_count=1)

        self.assertFalse(result["ok"], msg=result)
        self.assertEqual(result["pruned"], [])
        self.assertTrue(os.path.exists(path))

    def test_a_far_future_persisted_at_does_not_suppress_pruning_by_mtime(self):
        """Retention must fail TOWARD deletion: a tampered/clock-skewed
        `persisted_at` claiming to be in the year 2099 must not let a
        record with an old real mtime dodge max-age pruning forever."""
        now = datetime.datetime(2026, 9, 25, 0, 0, 0, tzinfo=datetime.timezone.utc)
        name = "ai-privacy-evidence-20200101T000000Z-cccccccc.json"
        path = self._write_raw(name, "2099-01-01T00:00:00Z")
        old_ts = (now - datetime.timedelta(days=200)).timestamp()
        os.utime(path, (old_ts, old_ts))

        result = er.prune(self.state_dir, max_age_seconds=90 * 86400, max_count=500, now=now)

        self.assertEqual(result["pruned"], [name], msg=result)
        self.assertFalse(os.path.exists(path))

    def test_a_moderately_future_persisted_at_does_not_make_a_fresh_file_look_older(self):
        """The `max(mtime_age, persisted_at_age)` rule must not let a
        `persisted_at` a few minutes in the future (ordinary clock skew,
        NOT the far-future tamper case above) make a genuinely fresh
        record look artificially old and get pruned early: a
        slightly-negative persisted_at_age is still less than a small
        positive mtime_age, so max() correctly picks mtime_age here."""
        now = datetime.datetime(2026, 9, 25, 0, 0, 0, tzinfo=datetime.timezone.utc)
        name = "ai-privacy-evidence-20260925T000000Z-dddddddd.json"
        skewed = (now + datetime.timedelta(minutes=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
        path = self._write_raw(name, skewed)
        fresh_ts = now.timestamp()
        os.utime(path, (fresh_ts, fresh_ts))

        result = er.prune(self.state_dir, max_age_seconds=90 * 86400, max_count=500, now=now)

        self.assertEqual(result["kept"], [name], msg=result)
        self.assertTrue(os.path.exists(path))


if __name__ == "__main__":
    unittest.main()
