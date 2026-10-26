"""tests/py/jobs/test_correlation.py - correlation/trace propagation across
AWCMS -> job -> pull worker -> host operation (issue #356). Fakes only: an
in-memory Control Center, a `true` argv override, no network, no host."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from typing import Any

from . import _pathfix  # noqa: F401
from jobs import audit, correlation, schema, store, worker
from observability import envelope

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
CC = REPO_ROOT / "contracts" / "control-center" / "v1"

CORR = "corr-356-e2e-0001"
CAUSE = "evt-awcms-0001"
TRACE = "4bf92f3577b34da6a3ce929d0e0e4736"
SPAN = "00f067aa0ba902b7"


def _job(**extra: Any) -> dict[str, Any]:
    job = {
        "tenant_id": "tenant-acme",
        "correlation_id": CORR,
        "idempotency_key": "idem-356-status-0001",
        "actor": {"type": "service", "id": "control-center"},
        "operation": "status",
        "target": {"server_id": "srv-test-01"},
        "permission": {"granted": True, "policy_id": "pol-allow-all", "requires_approval": False},
    }
    job.update(extra)
    return job


def _creds(tenant: str = "tenant-acme") -> worker.WorkerCredentials:
    return worker.WorkerCredentials(
        tenant_id=tenant, server_id="srv-test-01", worker_id="wrk-001",
        control_center_endpoint="https://control-center.example.com",
        public_key="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIPubKey12345", private_key="priv12345",
    )


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self._saved = {k: os.environ.get(k) for k in (
            "OMES_STATE_DIR", "OMES_ROOT", "OMES_JOBS_TENANT_ID", "OMES_JOBS_SERVER_ID",
            "OMES_JOBS_TEST_MODE", "OMES_JOBS_TEST_ARGV_OVERRIDE")}
        os.environ["OMES_STATE_DIR"] = str(self.root)
        os.environ["OMES_ROOT"] = str(REPO_ROOT)
        os.environ["OMES_JOBS_TENANT_ID"] = "tenant-acme"
        os.environ.pop("OMES_JOBS_SERVER_ID", None)
        os.environ["OMES_JOBS_TEST_MODE"] = "1"
        os.environ["OMES_JOBS_TEST_ARGV_OVERRIDE"] = json.dumps(["true"])

    def tearDown(self) -> None:
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def poll(self, job: dict[str, Any], creds: worker.WorkerCredentials | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
        sent: dict[str, Any] = {}

        def handle_result(data: dict[str, Any]) -> dict[str, Any]:
            sent.update(data)
            return {"job_id": "cc-job-0001", "status": "recorded", "reconciled": True,
                    "recorded_at": "2026-09-21T12:06:00Z"}

        creds = creds or _creds()
        client = worker.HttpClient(mock_handlers={
            "/api/v1/worker/poll": {"status": "job_available", "server_id": creds.server_id,
                                    "tenant_id": creds.tenant_id, "job": job, "poll_interval_seconds": 10},
            "/api/v1/worker/result": handle_result,
        })
        res = worker.poll_and_dispatch_once(creds, state_dir=self.root, client=client)
        return res, sent

    def audit_lines(self) -> list[dict[str, Any]]:
        path = self.root / "jobs" / "audit.jsonl"
        return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


class TestContractsAdditive(unittest.TestCase):
    def test_optional_id_fields_declared_and_not_required(self) -> None:
        for name in ("operation-request", "deployment.request", "worker-result.request", "job-status.response"):
            doc = json.loads((CC / f"{name}.schema.json").read_text())
            for f in ("causation_id", "trace_id", "span_id"):
                self.assertIn(f, doc["properties"], name)
                self.assertNotIn(f, doc["required"], name)
        poll = json.loads((CC / "worker-poll.response.schema.json").read_text())["properties"]["job"]
        for f in ("causation_id", "trace_id", "span_id"):
            self.assertIn(f, poll["properties"])
            self.assertNotIn(f, poll["required"])

    def test_no_command_like_keys_added(self) -> None:
        for name in ("operation-request", "deployment.request", "worker-result.request"):
            props = json.loads((CC / f"{name}.schema.json").read_text())["properties"]
            for bad in ("command", "cmd", "shell", "argv", "script", "exec"):
                self.assertNotIn(bad, props)

    def test_id_patterns_reject_free_text(self) -> None:
        doc = json.loads((CC / "operation-request.schema.json").read_text())
        base = _job()
        self.assertEqual(schema.validate({**base, "causation_id": CAUSE, "trace_id": TRACE, "span_id": SPAN}, doc), [])
        for field, bad in (("trace_id", "0" * 32), ("trace_id", TRACE.upper()), ("span_id", "0" * 16),
                           ("span_id", SPAN + "0"), ("causation_id", "short"),
                           ("causation_id", "has spaces and free text"), ("trace_id", "password=hunter2")):
            self.assertNotEqual(schema.validate({**base, field: bad}, doc), [], (field, bad))


class TestResolve(unittest.TestCase):
    def test_supplied_ids_pass_through_unchanged(self) -> None:
        got = correlation.resolve(_job(causation_id=CAUSE, trace_id=TRACE, span_id=SPAN))
        self.assertEqual((got["causation_id"], got["trace_id"], got["span_id"]), (CAUSE, TRACE, SPAN))
        self.assertEqual(got["generated_ids"], [])

    def test_missing_trace_and_span_are_generated_and_marked(self) -> None:
        got = correlation.resolve(_job())
        self.assertRegex(got["trace_id"], r"^[0-9a-f]{32}$")
        self.assertRegex(got["span_id"], r"^[0-9a-f]{16}$")
        self.assertEqual(got["generated_ids"], ["trace_id", "span_id"])
        self.assertNotIn("causation_id", got)  # never generated: absent means root
        self.assertNotEqual(correlation.resolve(_job())["trace_id"], got["trace_id"])

    def test_partial_supply_marks_only_generated(self) -> None:
        got = correlation.resolve(_job(trace_id=TRACE))
        self.assertEqual(got["trace_id"], TRACE)
        self.assertEqual(got["generated_ids"], ["span_id"])

    def test_malformed_ids_fail_closed_without_echoing_value(self) -> None:
        secret = "Bearer sk-live-SUPERSECRET"
        for field in ("correlation_id", "causation_id", "trace_id", "span_id"):
            with self.assertRaises(correlation.CorrelationError) as ctx:
                correlation.resolve(_job(**{field: secret}))
            self.assertIn(field, str(ctx.exception))
            self.assertNotIn("SUPERSECRET", str(ctx.exception))
        with self.assertRaises(correlation.CorrelationError):
            correlation.resolve(_job(trace_id=None))
        with self.assertRaises(correlation.CorrelationError):
            correlation.resolve(_job(trace_id=TRACE + "\n"))


class TestSubmitAndAudit(_Base):
    def test_malformed_id_rejected_before_any_write(self) -> None:
        with self.assertRaises(store.InvalidCorrelationError):
            store.submit(_job(trace_id="nope"), root=self.root)
        self.assertEqual(store.list_jobs(self.root), [])
        self.assertFalse((self.root / "jobs" / "audit.jsonl").exists())

    def test_ids_in_record_and_every_audit_line(self) -> None:
        record, _ = store.submit(_job(causation_id=CAUSE, trace_id=TRACE, span_id=SPAN), root=self.root)
        self.assertEqual(record["correlation_id"], CORR)
        store.approve(record, actor="op", root=self.root)
        store.cancel(record, actor="op", root=self.root)
        lines = self.audit_lines()
        self.assertGreaterEqual(len(lines), 3)
        for line in lines:
            self.assertEqual((line["correlation_id"], line["causation_id"], line["trace_id"], line["span_id"]),
                             (CORR, CAUSE, TRACE, SPAN))
        audit.verify_chain(self.root)

    def test_replay_audit_carries_original_ids(self) -> None:
        first, _ = store.submit(_job(trace_id=TRACE), root=self.root)
        again, replayed = store.submit(_job(), root=self.root)
        self.assertTrue(replayed)
        self.assertEqual(again["trace_id"], first["trace_id"])
        self.assertEqual(self.audit_lines()[-1]["trace_id"], TRACE)

    def test_legacy_record_without_ids_still_audits(self) -> None:
        self.assertEqual(correlation.audit_fields({"job_id": "job-x"}), {})


class TestEndToEnd(_Base):
    def test_one_correlation_id_survives_submit_poll_result_audit_envelope(self) -> None:
        res, sent = self.poll(_job(causation_id=CAUSE, trace_id=TRACE, span_id=SPAN))
        self.assertEqual(res["state"], "succeeded")

        # worker result echoes the ids unchanged and validates against the contract
        self.assertEqual((sent["correlation_id"], sent["causation_id"], sent["trace_id"], sent["span_id"]),
                         (CORR, CAUSE, TRACE, SPAN))
        self.assertNotIn("generated_ids", sent)
        result_schema = schema.load_json(CC / "worker-result.request.schema.json")
        self.assertEqual(schema.validate(sent, result_schema), [])

        record = store.load_job(res["job_id"], self.root)
        self.assertEqual(record["correlation_id"], CORR)
        for line in self.audit_lines():
            self.assertEqual(line["correlation_id"], CORR)
            self.assertEqual(line["trace_id"], TRACE)

        events = correlation.envelopes_for_job(record)
        self.assertEqual([e["status"] for e in events], ["pending", "pending", "running", "succeeded"])
        for e in events:
            self.assertEqual(envelope.validate(e), [])
            self.assertEqual((e["correlation_id"], e["trace_id"], e["span_id"], e["tenant_id"]),
                             (CORR, TRACE, SPAN, "tenant-acme"))
        self.assertEqual(events[0]["causation_id"], CAUSE)
        for prev, cur in zip(events, events[1:]):
            self.assertEqual(cur["causation_id"], prev["event_id"])

    def test_generated_ids_are_marked_in_result_and_record(self) -> None:
        res, sent = self.poll(_job())
        self.assertEqual(sent["generated_ids"], ["trace_id", "span_id"])
        self.assertNotIn("causation_id", sent)
        record = store.load_job(res["job_id"], self.root)
        self.assertEqual(record["generated_ids"], ["trace_id", "span_id"])
        self.assertEqual(sent["trace_id"], record["trace_id"])
        self.assertEqual(schema.validate(sent, schema.load_json(CC / "worker-result.request.schema.json")), [])

    def test_rejected_job_echoes_only_supplied_ids(self) -> None:
        # Not in the worker's capability set -> rejected locally, nothing generated.
        _, sent = self.poll(_job(operation="start"))
        self.assertEqual(sent["state"], "rejected")
        self.assertEqual(sent["correlation_id"], CORR)
        self.assertNotIn("trace_id", sent)
        _, sent = self.poll(_job(operation="start", idempotency_key="idem-356-start-00002", trace_id=TRACE))
        self.assertEqual(sent["trace_id"], TRACE)

    def test_malformed_inbound_id_is_rejected_not_executed(self) -> None:
        # The poll response contract rejects the whole response (fail closed
        # with a clear error); nothing is stored, executed or posted back.
        with self.assertRaises(worker.WorkerError) as ctx:
            self.poll(_job(trace_id="not-a-trace-id"))
        self.assertIn("$.job.trace_id", str(ctx.exception))
        self.assertEqual(store.list_jobs(self.root), [])

    def test_cross_tenant_substitution_rejected(self) -> None:
        # A job whose tenant differs from the enrolled worker's is rejected
        # by the worker before anything is stored or executed.
        _, sent = self.poll(_job(tenant_id="tenant-evil"))
        self.assertEqual(sent["state"], "rejected")
        self.assertEqual(sent["tenant_id"], "tenant-acme")
        self.assertEqual(sent["error"]["code"], "tenant_mismatch")
        self.assertEqual(store.list_jobs(self.root), [])
        # The store enforces the same boundary independently of the worker.
        with self.assertRaises(store.CrossTenantError):
            store.submit(_job(tenant_id="tenant-evil", trace_id=TRACE), root=self.root)
        self.assertEqual(store.list_jobs(self.root), [])

    def test_envelope_never_carries_job_payload(self) -> None:
        res, _ = self.poll(_job(parameters={"note": "free text here"}))
        record = store.load_job(res["job_id"], self.root)
        blob = json.dumps(correlation.envelopes_for_job(record))
        for needle in ("free text here", "control-center", "parameters", "returncode"):
            self.assertNotIn(needle, blob)


class TestEnvelopeConvergence(_Base):
    def _events(self) -> list[dict[str, Any]]:
        res, _ = self.poll(_job(trace_id=TRACE))
        return correlation.envelopes_for_job(store.load_job(res["job_id"], self.root))

    def test_projection_is_deterministic(self) -> None:
        res, _ = self.poll(_job())
        record = store.load_job(res["job_id"], self.root)
        self.assertEqual(correlation.envelopes_for_job(record), correlation.envelopes_for_job(record))

    def test_duplicate_and_out_of_order_delivery_converges(self) -> None:
        events = self._events()
        canonical = envelope.reduce_events(events)
        self.assertTrue(canonical["ok"], canonical["errors"])
        self.assertEqual(len(canonical["projection"]), 1)
        self.assertEqual(canonical["projection"][0]["status"], "succeeded")
        self.assertTrue(canonical["projection"][0]["terminal"])
        shuffled = list(reversed(events)) + events[:2] + list(reversed(events))
        got = envelope.reduce_events(shuffled)
        self.assertTrue(got["ok"], got["errors"])
        self.assertEqual(got["projection"], canonical["projection"])
        self.assertEqual(got["stats"]["unique"], len(events))

    def test_late_nonterminal_event_never_reverts_terminal(self) -> None:
        events = self._events()
        got = envelope.reduce_events([events[-1], events[0], events[2]])
        self.assertEqual(got["projection"][0]["status"], "succeeded")

    def test_cross_tenant_substitution_rejected_by_reducer(self) -> None:
        events = self._events()
        forged = dict(events[1], tenant_id="tenant-evil", event_id="forged-event-0001")
        got = envelope.reduce_events(events + [forged])
        self.assertFalse(got["ok"])
        self.assertTrue(any("cross-tenant" in e for e in got["errors"]))

    def test_unexpressible_correlation_id_raises_instead_of_rewriting(self) -> None:
        record, _ = store.submit(_job(correlation_id="short"), root=self.root)
        with self.assertRaises(correlation.CorrelationError):
            correlation.envelopes_for_job(record)


if __name__ == "__main__":
    unittest.main()
