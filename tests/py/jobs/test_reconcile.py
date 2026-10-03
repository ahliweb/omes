"""Tests for lib/omes/py/jobs/reconcile.py and lib/omes/py/jobs/identity.py
(issue #271): orphaned `running` host-job detection and read-back
reconciliation. Boot id, pid liveness and process start times are faked; no
host state is mutated and no bin/omes process is executed."""
import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from . import _pathfix  # noqa: F401

from jobs import audit, cli, identity, reconcile, runner, schema, store  # noqa: E402

BOOT_A = "11111111-1111-1111-1111-111111111111"
BOOT_B = "22222222-2222-2222-2222-222222222222"
_MISSING = object()

READBACK_MATCH = {
    "ok": True,
    "timed_out": False,
    "parsed": {"ok": True, "desired": {"state": "complete"}, "observed": {"state": "complete"}},
    "output_tail": "",
}
READBACK_MISMATCH = {
    "ok": True,
    "timed_out": False,
    "parsed": {"ok": True, "desired": {"state": "complete"}, "observed": {"state": "partial"}},
    "output_tail": "",
}


def make_request(**overrides) -> dict:
    request = {
        "tenant_id": "tenant-acme",
        "correlation_id": "corr-1",
        "idempotency_key": "idem-0001",
        "actor": {"type": "user", "id": "op-1"},
        "operation": "backup",
        "target": {"server_id": "srv-1"},
    }
    request.update(overrides)
    return request


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def runner_info(pid=4242, boot_id=BOOT_A, ticks=1000) -> dict:
    return {"pid": pid, "boot_id": boot_id, "started_at": "2026-10-03T00:00:00Z", "pid_start_ticks": ticks}


class ReconcileTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="omes-jobs-reconcile-test-")
        self.root = Path(self._tmp) / "state"
        store.paths.ensure_layout(self.root)
        for var in ("OMES_JOBS_TENANT_ID", "OMES_JOBS_SERVER_ID", "OMES_JOBS_ORPHAN_LEGACY_SECONDS"):
            os.environ.pop(var, None)

    def tearDown(self):
        for var in ("OMES_JOBS_TENANT_ID", "OMES_JOBS_SERVER_ID", "OMES_JOBS_ORPHAN_LEGACY_SECONDS"):
            os.environ.pop(var, None)
        shutil.rmtree(self._tmp, ignore_errors=True)

    def make_running(self, *, runner=_MISSING, updated_at=None, key="idem-0001", **request_overrides) -> dict:
        record, _ = store.submit(make_request(idempotency_key=key, **request_overrides), root=self.root)
        store.apply_transition(record, "approved", "t")
        store.apply_transition(record, "running", "t")
        if runner is not _MISSING:
            record["runner"] = runner
        if updated_at is not None:
            record["updated_at"] = updated_at
        store.save_job(record, self.root)
        return record

    def audit_lines(self) -> list[dict]:
        path = store.paths.audit_log_path(self.root)
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]

    @contextlib.contextmanager
    def host(self, *, boot=BOOT_A, liveness=identity.ALIVE, ticks=1000):
        with mock.patch.object(identity, "current_boot_id", return_value=boot), \
             mock.patch.object(identity, "pid_liveness", return_value=liveness), \
             mock.patch.object(identity, "process_start_ticks", return_value=ticks):
            yield


class TestRunnerRecordsIdentity(ReconcileTestBase):
    def test_running_record_carries_runner_identity_while_executing(self):
        record, _ = store.submit(make_request(operation="status"), root=self.root)
        seen = {}

        def fake_run_argv(argv):
            seen["runner"] = store.load_job(record["job_id"], self.root).get("runner")
            seen["state"] = store.load_job(record["job_id"], self.root)["state"]
            return {"ok": True, "timed_out": False, "duration_seconds": 0.0, "parsed": {"ok": True}, "output_tail": ""}

        with mock.patch.object(identity, "current_boot_id", return_value=BOOT_A), \
             mock.patch.object(runner, "_run_argv", side_effect=fake_run_argv):
            updated = runner.run(record, actor="op-1", root=self.root)

        self.assertEqual(seen["state"], "running")
        self.assertEqual(seen["runner"]["pid"], os.getpid())
        self.assertEqual(seen["runner"]["boot_id"], BOOT_A)
        self.assertRegex(seen["runner"]["started_at"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
        self.assertEqual(updated["state"], "succeeded")

    def test_unreadable_boot_id_is_recorded_as_null(self):
        with mock.patch.object(identity, "BOOT_ID_PATH", str(Path(self._tmp) / "no-such-boot-id")):
            self.assertIsNone(identity.current_boot_id())
            self.assertIsNone(identity.runner_identity()["boot_id"])

    def test_real_boot_id_and_start_ticks_are_stable_for_this_process(self):
        first = identity.runner_identity()
        second = identity.runner_identity()
        self.assertEqual(first["pid"], os.getpid())
        self.assertEqual(first["boot_id"], second["boot_id"])
        self.assertEqual(first["pid_start_ticks"], second["pid_start_ticks"])

    def test_pid_liveness_refuses_non_positive_and_non_int_pids(self):
        for bad in (0, -1, True, "12", None):
            self.assertEqual(identity.pid_liveness(bad), identity.UNKNOWN)

    def test_pid_liveness_maps_os_errors(self):
        with mock.patch.object(os, "kill", side_effect=ProcessLookupError):
            self.assertEqual(identity.pid_liveness(4242), identity.DEAD)
        with mock.patch.object(os, "kill", side_effect=PermissionError):
            self.assertEqual(identity.pid_liveness(4242), identity.ALIVE)
        with mock.patch.object(os, "kill", side_effect=OSError):
            self.assertEqual(identity.pid_liveness(4242), identity.UNKNOWN)
        with mock.patch.object(os, "kill", return_value=None):
            self.assertEqual(identity.pid_liveness(4242), identity.ALIVE)


class TestOrphanDetection(ReconcileTestBase):
    def test_boot_id_change_is_orphan_even_if_pid_is_alive(self):
        record = self.make_running(runner=runner_info(boot_id=BOOT_A))
        with self.host(boot=BOOT_B, liveness=identity.ALIVE):
            verdict = reconcile.assess(record)
        self.assertEqual(verdict, {"orphaned": True, "reason": "boot_id_changed"})

    def test_dead_pid_is_orphan(self):
        record = self.make_running(runner=runner_info())
        with self.host(liveness=identity.DEAD):
            verdict = reconcile.assess(record)
        self.assertEqual(verdict, {"orphaned": True, "reason": "pid_not_alive"})

    def test_reused_pid_with_different_start_time_is_orphan(self):
        record = self.make_running(runner=runner_info(ticks=1000))
        with self.host(liveness=identity.ALIVE, ticks=999999):
            verdict = reconcile.assess(record)
        self.assertEqual(verdict, {"orphaned": True, "reason": "pid_reused"})

    def test_live_runner_is_not_orphaned(self):
        record = self.make_running(runner=runner_info())
        with self.host(liveness=identity.ALIVE, ticks=1000):
            verdict = reconcile.assess(record)
        self.assertEqual(verdict, {"orphaned": False, "reason": "runner_alive"})

    def test_alive_pid_with_unreadable_start_time_is_not_orphaned(self):
        record = self.make_running(runner=runner_info())
        with self.host(liveness=identity.ALIVE, ticks=None):
            self.assertFalse(reconcile.assess(record)["orphaned"])

    def test_unprovable_liveness_is_not_orphaned(self):
        record = self.make_running(runner=runner_info())
        with self.host(liveness=identity.UNKNOWN):
            self.assertEqual(reconcile.assess(record), {"orphaned": False, "reason": "indeterminate"})

    def test_unreadable_boot_ids_fall_back_to_pid_check_and_stay_conservative(self):
        record = self.make_running(runner=runner_info(boot_id=None))
        with self.host(boot=None, liveness=identity.ALIVE, ticks=1000):
            self.assertFalse(reconcile.assess(record)["orphaned"])
        with self.host(boot=BOOT_B, liveness=identity.ALIVE, ticks=1000):
            # recorded boot id is unknown, so a "different" current one proves nothing
            self.assertFalse(reconcile.assess(record)["orphaned"])
        with self.host(boot=None, liveness=identity.DEAD):
            self.assertTrue(reconcile.assess(record)["orphaned"])

    def test_current_pid_is_never_orphaned(self):
        # Uses the real probes: this very process is alive and has matching identity.
        record = self.make_running(runner=identity.runner_identity())
        self.assertEqual(reconcile.assess(record), {"orphaned": False, "reason": "runner_alive"})

    def test_non_running_records_are_never_orphaned(self):
        record = self.make_running(runner=runner_info())
        record["state"] = "failed"
        with self.host(boot=BOOT_B, liveness=identity.DEAD):
            self.assertEqual(reconcile.assess(record), {"orphaned": False, "reason": "not_running"})

    def test_legacy_record_older_than_threshold_is_orphan_candidate(self):
        old = iso(datetime.now(timezone.utc) - timedelta(hours=2))
        record = self.make_running(runner=_MISSING, updated_at=old)
        self.assertNotIn("runner", record)
        self.assertEqual(reconcile.assess(record), {"orphaned": True, "reason": "legacy_record_stale"})

    def test_recent_legacy_record_is_not_orphaned(self):
        record = self.make_running(runner=_MISSING, updated_at=iso(datetime.now(timezone.utc)))
        self.assertEqual(reconcile.assess(record), {"orphaned": False, "reason": "legacy_record_recent"})

    def test_legacy_threshold_is_configurable(self):
        stamp = iso(datetime.now(timezone.utc) - timedelta(seconds=120))
        record = self.make_running(runner=_MISSING, updated_at=stamp)
        self.assertFalse(reconcile.assess(record)["orphaned"])
        os.environ["OMES_JOBS_ORPHAN_LEGACY_SECONDS"] = "60"
        self.assertTrue(reconcile.assess(record)["orphaned"])
        os.environ["OMES_JOBS_ORPHAN_LEGACY_SECONDS"] = "not-a-number"
        self.assertEqual(reconcile.legacy_orphan_seconds(), reconcile.DEFAULT_LEGACY_ORPHAN_SECONDS)

    def test_malformed_runner_is_treated_as_legacy_not_signalled(self):
        stamp = iso(datetime.now(timezone.utc))
        for bad in ({"pid": 0, "boot_id": BOOT_A}, {"pid": -5}, {"pid": True}, {"pid": "1"}, "garbage", None):
            record = self.make_running(runner=bad, updated_at=stamp, key=f"idem-bad-{abs(hash(str(bad)))}")
            with mock.patch.object(identity, "pid_liveness") as liveness:
                self.assertEqual(reconcile.assess(record), {"orphaned": False, "reason": "legacy_record_recent"})
            liveness.assert_not_called()

    def test_unparseable_updated_at_on_legacy_record_is_not_orphaned(self):
        record = self.make_running(runner=_MISSING, updated_at="yesterday")
        self.assertEqual(reconcile.assess(record), {"orphaned": False, "reason": "indeterminate"})


class TestReconcileOutcomes(ReconcileTestBase):
    def reconcile_with(self, readback, job_id=None):
        with self.host(liveness=identity.DEAD), mock.patch.object(runner, "_run_argv", return_value=readback) as run_argv:
            result = reconcile.reconcile(self.root, job_id=job_id)
        return result, run_argv

    def test_readback_match_marks_succeeded_with_evidence_and_audit(self):
        record = self.make_running(runner=runner_info())
        result, run_argv = self.reconcile_with(READBACK_MATCH)

        run_argv.assert_called_once_with(["status", "--json"])
        self.assertEqual([r["job_id"] for r in result["reconciled"]], [record["job_id"]])
        saved = store.load_job(record["job_id"], self.root)
        self.assertEqual(saved["state"], "succeeded")
        self.assertIsNone(saved["error"])
        self.assertEqual(saved["evidence"]["readback"]["argv"], ["status", "--json"])
        self.assertTrue(saved["evidence"]["readback"]["ok"])
        self.assertEqual(saved["evidence"]["reconciliation"]["note"], "reconciled after orphaned run")
        self.assertEqual(saved["evidence"]["reconciliation"]["orphan_reason"], "pid_not_alive")
        self.assertEqual(saved["evidence"]["reconciliation"]["previous_runner"]["pid"], 4242)
        self.assertEqual(saved["history"][-1]["from"], "running")
        self.assertEqual(saved["history"][-1]["to"], "succeeded")
        self.assertEqual(saved["history"][-1]["note"], "reconciled after orphaned run")

        entry = self.audit_lines()[-1]
        self.assertEqual(entry["event"], "reconciled_orphan")
        self.assertEqual((entry["from"], entry["to"]), ("running", "succeeded"))
        self.assertEqual(entry["detail"]["classification"], "match")
        audit.verify_chain(self.root)

    def test_rollback_match_reaches_rolled_back_like_a_normal_run(self):
        record = self.make_running(runner=runner_info(), operation="rollback", rollback_ref="20261001T000000Z", key="idem-rb-01")
        self.reconcile_with(READBACK_MATCH)
        self.assertEqual(store.load_job(record["job_id"], self.root)["state"], "rolled_back")
        audit.verify_chain(self.root)

    def test_readback_mismatch_marks_failed_reconciliation_mismatch(self):
        record = self.make_running(runner=runner_info())
        self.reconcile_with(READBACK_MISMATCH)
        saved = store.load_job(record["job_id"], self.root)
        self.assertEqual(saved["state"], "failed")
        self.assertEqual(saved["error"]["code"], "reconciliation_mismatch")
        self.assertIs(saved["error"]["retryable"], False)
        self.assertEqual(saved["evidence"]["readback"]["ok"], True)
        self.assertEqual(self.audit_lines()[-1]["detail"]["classification"], "mismatch")
        audit.verify_chain(self.root)

    def test_readback_ok_false_is_a_mismatch(self):
        record = self.make_running(runner=runner_info())
        self.reconcile_with({"ok": True, "timed_out": False, "parsed": {"ok": False}, "output_tail": ""})
        saved = store.load_job(record["job_id"], self.root)
        self.assertEqual(saved["error"]["code"], "reconciliation_mismatch")

    def assert_outcome_unknown(self, record_id: str) -> dict:
        saved = store.load_job(record_id, self.root)
        self.assertEqual(saved["state"], "failed")
        self.assertEqual(saved["error"]["code"], "outcome_unknown")
        self.assertIs(saved["error"]["retryable"], False)
        self.assertIn("manual review required", saved["error"]["message"])
        self.assertEqual(self.audit_lines()[-1]["event"], "reconciled_orphan")
        self.assertEqual(self.audit_lines()[-1]["detail"]["classification"], "unknown")
        audit.verify_chain(self.root)
        return saved

    def test_readback_timeout_is_outcome_unknown_never_success(self):
        record = self.make_running(runner=runner_info())
        self.reconcile_with({"ok": False, "timed_out": True, "error": {"code": "timeout"}, "output_tail": ""})
        self.assert_outcome_unknown(record["job_id"])

    def test_readback_command_failure_is_outcome_unknown(self):
        record = self.make_running(runner=runner_info())
        self.reconcile_with({"ok": False, "timed_out": False, "error": {"code": "operation_failed"}, "output_tail": ""})
        self.assert_outcome_unknown(record["job_id"])

    def test_readback_exception_is_outcome_unknown(self):
        record = self.make_running(runner=runner_info())
        with self.host(liveness=identity.DEAD), mock.patch.object(runner, "_run_argv", side_effect=runner.UnsafeArgvError("x")):
            reconcile.reconcile(self.root)
        self.assert_outcome_unknown(record["job_id"])

    def test_unparseable_readback_is_outcome_unknown(self):
        record = self.make_running(runner=runner_info())
        self.reconcile_with({"ok": True, "timed_out": False, "parsed": None, "output_tail": "not json"})
        self.assert_outcome_unknown(record["job_id"])

    def test_ok_only_readback_cannot_prove_an_orphaned_mutation(self):
        record = self.make_running(runner=runner_info())
        self.reconcile_with({"ok": True, "timed_out": False, "parsed": {"ok": True}, "output_tail": ""})
        saved = self.assert_outcome_unknown(record["job_id"])
        self.assertIn("desired/observed", saved["error"]["message"])

    def test_incomplete_pair_is_outcome_unknown(self):
        record = self.make_running(runner=runner_info())
        self.reconcile_with({"ok": True, "timed_out": False, "parsed": {"ok": True, "desired": {"a": 1}}, "output_tail": ""})
        self.assert_outcome_unknown(record["job_id"])

    def test_operation_without_readback_is_outcome_unknown_and_runs_nothing(self):
        record = self.make_running(runner=runner_info(), operation="status", key="idem-st-01")
        _result, run_argv = self.reconcile_with(READBACK_MATCH)
        run_argv.assert_not_called()
        self.assert_outcome_unknown(record["job_id"])

    def test_orphan_is_never_succeeded_without_readback_evidence(self):
        record = self.make_running(runner=runner_info(), operation="preflight", key="idem-pf-01")
        self.reconcile_with(READBACK_MATCH)
        saved = store.load_job(record["job_id"], self.root)
        self.assertNotIn(saved["state"], ("succeeded", "rolled_back"))
        self.assertNotIn("readback", saved["evidence"])

    def test_reconciled_failure_is_not_retryable(self):
        record = self.make_running(runner=runner_info())
        self.reconcile_with(READBACK_MISMATCH)
        saved = store.load_job(record["job_id"], self.root)
        with self.assertRaises(store.InvalidTransitionError):
            runner.run(saved, actor="op-1", root=self.root)

    def test_live_runner_is_left_running_and_unaudited(self):
        record = self.make_running(runner=runner_info())
        before = self.audit_lines()
        with self.host(liveness=identity.ALIVE, ticks=1000), mock.patch.object(runner, "_run_argv") as run_argv:
            result = reconcile.reconcile(self.root)
        run_argv.assert_not_called()
        self.assertEqual(result["reconciled"], [])
        self.assertEqual(result["unchanged"][0]["reason"], "runner_alive")
        self.assertEqual(store.load_job(record["job_id"], self.root)["state"], "running")
        self.assertEqual(self.audit_lines(), before)

    def test_second_reconcile_changes_nothing(self):
        record = self.make_running(runner=runner_info())
        self.reconcile_with(READBACK_MATCH)
        path = store.paths.job_record_path(record["job_id"], self.root)
        record_bytes = path.read_bytes()
        audit_bytes = store.paths.audit_log_path(self.root).read_bytes()

        result, run_argv = self.reconcile_with(READBACK_MATCH)

        run_argv.assert_not_called()
        self.assertEqual(result, {"reconciled": [], "unchanged": [], "skipped_out_of_scope": 0})
        self.assertEqual(path.read_bytes(), record_bytes)
        self.assertEqual(store.paths.audit_log_path(self.root).read_bytes(), audit_bytes)
        # explicit --job on an already reconciled job: unchanged, not an error
        result, run_argv = self.reconcile_with(READBACK_MATCH, job_id=record["job_id"])
        self.assertEqual(result["unchanged"][0]["reason"], "not_running")
        self.assertEqual(path.read_bytes(), record_bytes)

    def test_transition_goes_through_store_apply_transition(self):
        self.make_running(runner=runner_info())
        with mock.patch.object(store, "apply_transition", wraps=store.apply_transition) as spy:
            self.reconcile_with(READBACK_MATCH)
        spy.assert_called_once()
        self.assertEqual(spy.call_args.args[1], "succeeded")

    def test_no_new_job_state_and_wire_projection_still_validates(self):
        record = self.make_running(runner=runner_info())
        self.reconcile_with(READBACK_MATCH)
        saved = store.load_job(record["job_id"], self.root)
        self.assertIn(saved["state"], store.STATES)
        wire_schema = schema.load_json(
            Path(_pathfix._REPO_ROOT) / "contracts" / "control-center" / "v1" / "job-status.response.schema.json"
        )
        self.assertEqual(wire_schema["properties"]["state"]["enum"], list(store.STATES))
        projection = {k: saved[k] for k in wire_schema["properties"] if k in saved}
        self.assertNotIn("runner", projection)
        self.assertEqual(schema.validate(projection, wire_schema), [])
        # the failed variants (outcome_unknown) must validate too
        second = self.make_running(runner=runner_info(), operation="status", key="idem-st-02")
        self.reconcile_with(READBACK_MATCH)
        failed = store.load_job(second["job_id"], self.root)
        projection = {k: failed[k] for k in wire_schema["properties"] if k in failed}
        self.assertEqual(schema.validate(projection, wire_schema), [])
        self.assertEqual(cli._job_summary(failed).get("runner"), None)


class TestReconcileScope(ReconcileTestBase):
    def test_other_tenant_job_is_not_touched_in_batch(self):
        mine = self.make_running(runner=runner_info(), key="idem-mine-1")
        theirs = self.make_running(runner=runner_info(), tenant_id="tenant-other", key="idem-other-1")
        os.environ["OMES_JOBS_TENANT_ID"] = "tenant-acme"
        with self.host(liveness=identity.DEAD), mock.patch.object(runner, "_run_argv", return_value=READBACK_MATCH):
            result = reconcile.reconcile(self.root)
        self.assertEqual(result["skipped_out_of_scope"], 1)
        self.assertEqual([r["job_id"] for r in result["reconciled"]], [mine["job_id"]])
        self.assertEqual(store.load_job(theirs["job_id"], self.root)["state"], "running")
        audit.verify_chain(self.root)

    def test_explicit_job_in_other_tenant_is_rejected(self):
        theirs = self.make_running(runner=runner_info(), tenant_id="tenant-other", key="idem-other-2")
        os.environ["OMES_JOBS_TENANT_ID"] = "tenant-acme"
        with self.host(liveness=identity.DEAD), mock.patch.object(runner, "_run_argv") as run_argv:
            with self.assertRaises(store.CrossTenantError):
                reconcile.reconcile(self.root, job_id=theirs["job_id"])
        run_argv.assert_not_called()
        self.assertEqual(store.load_job(theirs["job_id"], self.root)["state"], "running")

    def test_other_server_job_is_not_touched(self):
        theirs = self.make_running(runner=runner_info(), target={"server_id": "srv-other"}, key="idem-other-3")
        os.environ["OMES_JOBS_SERVER_ID"] = "srv-1"
        with self.host(liveness=identity.DEAD), mock.patch.object(runner, "_run_argv", return_value=READBACK_MATCH):
            result = reconcile.reconcile(self.root)
        self.assertEqual(result["skipped_out_of_scope"], 1)
        self.assertEqual(store.load_job(theirs["job_id"], self.root)["state"], "running")

    def test_unknown_job_raises(self):
        with self.assertRaises(store.UnknownJobError):
            reconcile.reconcile(self.root, job_id="job-doesnotexist-20260101T000000Z")

    def test_only_running_jobs_are_examined_in_batch(self):
        record, _ = store.submit(make_request(idempotency_key="idem-queued-1"), root=self.root)
        with self.host(liveness=identity.DEAD), mock.patch.object(runner, "_run_argv") as run_argv:
            result = reconcile.reconcile(self.root)
        run_argv.assert_not_called()
        self.assertEqual(result, {"reconciled": [], "unchanged": [], "skipped_out_of_scope": 0})
        self.assertEqual(store.load_job(record["job_id"], self.root)["state"], "queued")


class TestReconcileCli(ReconcileTestBase):
    def setUp(self):
        super().setUp()
        os.environ["OMES_STATE_DIR"] = str(self.root)

    def tearDown(self):
        os.environ.pop("OMES_STATE_DIR", None)
        super().tearDown()

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_json_stdout_is_machine_readable_and_logs_go_to_stderr(self):
        record = self.make_running(runner=runner_info())
        with self.host(liveness=identity.DEAD), mock.patch.object(runner, "_run_argv", return_value=READBACK_MATCH):
            code, out, err = self.run_cli("reconcile", "--json")
        self.assertEqual(code, cli.EX_OK)
        payload = json.loads(out)
        self.assertEqual(payload["reconciled"][0]["job_id"], record["job_id"])
        self.assertEqual(payload["reconciled"][0]["state"], "succeeded")
        self.assertIn(record["job_id"], err)

    def test_job_flag_limits_scope_and_unknown_job_is_an_error(self):
        a = self.make_running(runner=runner_info(), key="idem-cli-a1")
        b = self.make_running(runner=runner_info(), key="idem-cli-b1")
        with self.host(liveness=identity.DEAD), mock.patch.object(runner, "_run_argv", return_value=READBACK_MATCH):
            code, out, _err = self.run_cli("reconcile", "--job", a["job_id"], "--json")
        self.assertEqual(code, cli.EX_OK)
        self.assertEqual([r["job_id"] for r in json.loads(out)["reconciled"]], [a["job_id"]])
        self.assertEqual(store.load_job(b["job_id"], self.root)["state"], "running")
        code, _out, err = self.run_cli("reconcile", "--job", "job-nope-20260101T000000Z")
        self.assertEqual(code, cli.EX_ERROR)
        self.assertIn("unknown job", err)

    def test_cross_tenant_job_flag_exits_with_error(self):
        theirs = self.make_running(runner=runner_info(), tenant_id="tenant-other", key="idem-cli-o1")
        os.environ["OMES_JOBS_TENANT_ID"] = "tenant-acme"
        code, _out, err = self.run_cli("reconcile", "--job", theirs["job_id"])
        self.assertEqual(code, cli.EX_ERROR)
        self.assertIn("does not match local tenant", err)

    def test_human_output(self):
        self.make_running(runner=runner_info())
        with self.host(liveness=identity.DEAD), mock.patch.object(runner, "_run_argv", return_value=READBACK_MISMATCH):
            code, out, _err = self.run_cli("reconcile")
        self.assertEqual(code, cli.EX_OK)
        self.assertIn("reconciled: 1", out)


if __name__ == "__main__":
    unittest.main()
