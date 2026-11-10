"""Tests for lib/omes/py/jobs/decisions.py and its use in store.submit
(issue #358): OMES as producer (operation allowlist, tenant scope, approval
requirement) and consumer (composition with fake external decisions) of
policy-decision envelopes. Synthetic identifiers only; no network."""
import copy
import datetime
import json
import os
import unittest

from . import _pathfix  # noqa: F401

from jobs import decisions, store  # noqa: E402
from observability import envelope, policy_decision  # noqa: E402

from .test_store import JobsStoreTestBase, make_request  # noqa: E402

NOW = datetime.datetime(2026, 11, 10, 12, 0, 0, tzinfo=datetime.timezone.utc)


def _facts(operation, tenant="tenant-acme", local="tenant-acme", operations=store.OPERATIONS, **req):
    request = make_request(operation=operation, tenant_id=tenant, **req)
    return request, decisions.omes_decisions(
        request,
        operations=operations,
        local_tenant=local,
        destructive=store.is_destructive(operation),
        auto_approvable=store.can_auto_approve(operation),
        now=NOW,
    )


def _external(request, authority="awcms", decision="allow", **overrides):
    base = {
        "schema_version": "1.0.0",
        "decision_id": f"ext-{authority}-{decision}-0001",
        "tenant_id": request["tenant_id"],
        "correlation_id": decisions.decision_correlation_id(request["correlation_id"]),
        "actor": {"type": request["actor"]["type"], "id": request["actor"]["id"]},
        "resource": {"kind": "host", "id": request["target"]["server_id"]},
        "requested_action": request["operation"],
        "deciding_authority": authority,
        "decision": decision,
        "policy_id": f"{authority}.fake.policy",
        "reason_code": f"FAKE_{decision.upper()}",
        "evidence": {"version": "fake-1", "observed_at": "2026-11-10T11:59:00Z", "freshness": "live"},
        "approval_ref": "awcms:approvals/apr-0001" if decision == "approval_required" else None,
        "expires_at": "2026-11-10T13:00:00Z",
        "idempotency_key": None,
        "classification": "INTERNAL",
        "redaction_state": "none_required",
    }
    base.update(overrides)
    return base


def _by_policy(envelopes, policy_id):
    return [d for d in envelopes if d["policy_id"] == policy_id][0]


class TestProducer(unittest.TestCase):
    def test_every_operation_emits_schema_valid_envelopes(self):
        for operation in store.OPERATIONS:
            _, envelopes = _facts(operation)
            self.assertEqual(len(envelopes), 3)
            for item in envelopes:
                self.assertEqual(policy_decision.validate(item), [], (operation, item))
                self.assertEqual(item["deciding_authority"], "omes")
                self.assertFalse(item["reason_code"].startswith(policy_decision.EGRESS_REASON_PREFIX))
                self.assertEqual(list(envelope.scan_forbidden(item)), [])

    def test_allow_and_approval_required(self):
        _, status = _facts("status")
        self.assertEqual(
            {d["policy_id"]: d["decision"] for d in status},
            {"omes.jobs.allowlist": "allow", "omes.jobs.tenant_scope": "allow", "omes.jobs.approval": "allow"},
        )
        _, restore = _facts("restore")
        approval = _by_policy(restore, "omes.jobs.approval")
        self.assertEqual(approval["decision"], "approval_required")
        self.assertEqual(approval["reason_code"], "APPROVAL_REQUIRED_DESTRUCTIVE_OPERATION")
        _, update = _facts("update")  # not destructive, not in the default auto-approve list
        self.assertEqual(
            _by_policy(update, "omes.jobs.approval")["reason_code"], "APPROVAL_REQUIRED_NOT_AUTO_APPROVED"
        )

    def test_unsupported_operation_is_denied(self):
        _, envelopes = _facts("run_shell")
        allowlist = _by_policy(envelopes, "omes.jobs.allowlist")
        self.assertEqual((allowlist["decision"], allowlist["reason_code"]), ("deny", "OPERATION_NOT_ALLOWLISTED"))
        self.assertEqual(policy_decision.validate(allowlist), [])

    def test_tenant_scope_denies(self):
        _, mismatch = _facts("status", tenant="tenant-other")
        tenant = _by_policy(mismatch, "omes.jobs.tenant_scope")
        self.assertEqual((tenant["decision"], tenant["reason_code"]), ("deny", "TENANT_MISMATCH"))
        _, unset = _facts("status", local=None)
        tenant = _by_policy(unset, "omes.jobs.tenant_scope")
        self.assertEqual((tenant["decision"], tenant["reason_code"]), ("deny", "TENANT_NOT_CONFIGURED"))

    def test_approval_is_a_reference_only(self):
        _, envelopes = _facts("restore")
        approval = _by_policy(envelopes, "omes.jobs.approval")
        self.assertTrue(approval["approval_ref"].startswith("omes:job-approval/"))
        self.assertNotIn("approved_by", approval)
        _, again = _facts("restore")
        self.assertEqual(approval["approval_ref"], _by_policy(again, "omes.jobs.approval")["approval_ref"])

    def test_short_correlation_id_is_normalised_and_valid(self):
        request, envelopes = _facts("status")
        self.assertEqual(request["correlation_id"], "corr-1")
        self.assertEqual(envelopes[0]["correlation_id"], "corr:corr-1")
        self.assertEqual(decisions.decision_correlation_id("corr-0000-0001"), "corr-0000-0001")


class TestConsumerCompose(unittest.TestCase):
    def evaluate(self, envelopes, external=None, **kw):
        return decisions.evaluate(envelopes, external, now=NOW, **kw)

    def test_omes_only_allow_and_approval_required(self):
        _, envelopes = _facts("status")
        self.assertEqual(self.evaluate(envelopes)["decision"], "allow")
        _, envelopes = _facts("rollback", rollback_ref="rb-1")
        composed = self.evaluate(envelopes)
        self.assertEqual(composed["decision"], "approval_required")
        self.assertEqual(len(composed["approval_refs"]), 1)

    def test_external_deny_wins(self):
        request, envelopes = _facts("status")
        composed = self.evaluate(envelopes, [_external(request, decision="deny")])
        self.assertEqual((composed["decision"], composed["winning_authority"]), ("deny", "awcms"))

    def test_external_unavailable_wins_over_allow(self):
        request, envelopes = _facts("status")
        self.assertEqual(self.evaluate(envelopes, [_external(request, decision="unavailable")])["decision"], "unavailable")

    def test_stale_unknown_and_expired_evidence_is_unavailable(self):
        request, envelopes = _facts("status")
        for freshness in ("stale", "unknown"):
            ext = _external(request)
            ext["evidence"]["freshness"] = freshness
            self.assertEqual(self.evaluate(envelopes, [ext])["decision"], "unavailable", freshness)
        expired = _external(request, expires_at="2026-11-10T11:59:30Z")
        composed = self.evaluate(envelopes, [expired])
        self.assertEqual((composed["decision"], composed["winning_reason_code"]), ("unavailable", "DECISION_EXPIRED"))

    def test_missing_required_authority_fails_closed(self):
        request, envelopes = _facts("status")
        composed = self.evaluate(envelopes, None, required_authorities=("awcms",))
        self.assertEqual(composed["decision"], "unavailable")
        self.assertEqual(composed["missing_authorities"], ["awcms"])
        ok = self.evaluate(envelopes, [_external(request)], required_authorities=("awcms",))
        self.assertEqual(ok["decision"], "allow")

    def test_cross_tenant_substitution_fails_closed(self):
        request, envelopes = _facts("status")
        composed = self.evaluate(envelopes, [_external(request, tenant_id="tenant-other")])
        self.assertEqual((composed["decision"], composed["winning_reason_code"]), ("unavailable", "POLICY_DECISION_INVALID"))

    def test_mixed_correlation_and_action_fail_closed(self):
        request, envelopes = _facts("status")
        for field, value in (("correlation_id", "corr-someone-else"), ("requested_action", "backup")):
            composed = self.evaluate(envelopes, [_external(request, **{field: value})])
            self.assertEqual(composed["decision"], "unavailable", field)

    def test_invalid_external_fails_closed(self):
        request, envelopes = _facts("status")
        for ext in (_external(request, decision="maybe"), _external(request, reasoning="free text"), "not a dict"):
            self.assertEqual(self.evaluate(envelopes, [ext])["decision"], "unavailable")

    def test_external_cannot_claim_omes_authority(self):
        request, envelopes = _facts("status")
        composed = self.evaluate(envelopes, [_external(request, authority="omes")])
        self.assertEqual(
            (composed["decision"], composed["winning_reason_code"]),
            ("unavailable", "EXTERNAL_DECISION_CLAIMS_OMES_AUTHORITY"),
        )

    def test_external_allow_cannot_override_omes_deny(self):
        request, envelopes = _facts("status", tenant="tenant-other")
        composed = self.evaluate(envelopes, [_external(request)])
        self.assertEqual((composed["decision"], composed["winning_authority"]), ("deny", "omes"))

    def test_inputs_not_mutated(self):
        _, envelopes = _facts("restore")
        before = copy.deepcopy(envelopes)
        self.evaluate(envelopes)
        self.assertEqual(envelopes, before)


class TestSubmitConsumer(JobsStoreTestBase):
    def assertNoJob(self):
        self.assertEqual(store.list_jobs(self.root), [])
        audit_path = store.paths.audit_log_path(self.root)
        self.assertFalse(audit_path.exists() and audit_path.read_text())

    def test_external_deny_blocks_submit_without_writing(self):
        request = make_request()
        with self.assertRaises(store.PolicyDeniedError) as ctx:
            store.submit(request, root=self.root, external_decisions=[_external(request, decision="deny")])
        self.assertEqual(ctx.exception.reason_code, "FAKE_DENY")
        self.assertNoJob()

    def test_external_unavailable_stale_and_missing_authority_block_submit(self):
        request = make_request()
        stale = _external(request)
        stale["evidence"]["freshness"] = "stale"
        for kwargs in (
            {"external_decisions": [_external(request, decision="unavailable")]},
            {"external_decisions": [stale]},
            {"required_authorities": ("awcms",)},
            {"external_decisions": [_external(request, tenant_id="tenant-other")]},
        ):
            with self.assertRaises(store.PolicyUnavailableError, msg=str(sorted(kwargs))):
                store.submit(request, root=self.root, **kwargs)
        self.assertNoJob()

    def test_external_allow_and_required_authority_present_accepts(self):
        request = make_request()
        record, replayed = store.submit(
            request, root=self.root, external_decisions=[_external(request)], required_authorities=("awcms",)
        )
        self.assertFalse(replayed)
        self.assertEqual(record["policy"]["composed"]["decision"], "allow")
        self.assertIn("ext-awcms-allow-0001", record["policy"]["composed"]["contributing_decision_ids"])

    def test_external_approval_required_queues_and_never_approves(self):
        request = make_request(operation="restore", backup_id="bk-1", idempotency_key="idem-restore-1")
        record, _ = store.submit(
            request, root=self.root, external_decisions=[_external(request, decision="approval_required")]
        )
        composed = record["policy"]["composed"]
        self.assertEqual(composed["decision"], "approval_required")
        self.assertIn("awcms:approvals/apr-0001", composed["approval_refs"])
        self.assertTrue(any(r.startswith("omes:job-approval/") for r in composed["approval_refs"]))
        self.assertEqual(record["state"], "queued")
        self.assertEqual([h["to"] for h in record["history"]], ["queued"])
        lines = store.paths.audit_log_path(self.root).read_text().splitlines()
        self.assertEqual([json.loads(line)["event"] for line in lines], ["submitted"])

    def test_evidence_is_bounded_and_audited(self):
        request = make_request(operation="stop", idempotency_key="idem-stop-001")
        record, _ = store.submit(request, root=self.root)
        self.assertEqual(set(record["policy"]), {"composed", "omes_decisions"})
        self.assertEqual(len(record["policy"]["omes_decisions"]), 3)
        for item in record["policy"]["omes_decisions"]:
            self.assertEqual(policy_decision.validate(item), [])
        self.assertEqual(list(envelope.scan_forbidden(record["policy"])), [])
        line = json.loads(store.paths.audit_log_path(self.root).read_text().splitlines()[0])
        self.assertEqual(line["detail"]["policy"]["decision"], "approval_required")
        self.assertEqual(line["detail"]["policy"]["reason_code"], "APPROVAL_REQUIRED_DESTRUCTIVE_OPERATION")
        self.assertEqual(store.load_job(record["job_id"], self.root)["policy"], record["policy"])

    def test_unsupported_operation_is_denied_not_queued(self):
        with self.assertRaises(store.ValidationFailedError):
            store.submit(make_request(operation="run_shell"), root=self.root)
        self.assertNoJob()

    def test_policy_errors_do_not_echo_request_values(self):
        request = make_request(idempotency_key="idem-secret-value-xyz")
        with self.assertRaises(store.PolicyDeniedError) as ctx:
            store.submit(request, root=self.root, external_decisions=[_external(request, decision="deny")])
        self.assertNotIn("idem-secret-value-xyz", str(ctx.exception))
        self.assertNotIn("tenant-acme", str(ctx.exception))


class TestOutcomeEquivalence(JobsStoreTestBase):
    """Accept/deny outcomes of submit() without external decisions are the
    pre-#358 outcomes (tenant default-deny, mismatch deny, everything else
    accepted), for every allowlisted operation."""

    def test_matrix_matches_legacy_rules(self):
        cases = (("tenant-acme", "tenant-acme"), ("tenant-other", "tenant-acme"), ("tenant-acme", None))
        accepted = 0
        for operation in store.OPERATIONS:
            for request_tenant, local in cases:
                with self.subTest(operation=operation, request=request_tenant, local=local):
                    if local is None:
                        os.environ.pop("OMES_JOBS_TENANT_ID", None)
                    else:
                        os.environ["OMES_JOBS_TENANT_ID"] = local
                    request = make_request(
                        operation=operation,
                        tenant_id=request_tenant,
                        idempotency_key=f"idem-{operation}-{request_tenant}-{local}",
                        backup_id="bk-1",
                        rollback_ref="rb-1",
                    )
                    if local is None:
                        with self.assertRaises(store.TenantNotConfiguredError):
                            store.submit(request, root=self.root)
                    elif request_tenant != local:
                        with self.assertRaises(store.CrossTenantError):
                            store.submit(request, root=self.root)
                    else:
                        record, replayed = store.submit(request, root=self.root)
                        accepted += 1
                        self.assertEqual(record["state"], "queued")
                        self.assertFalse(replayed)
                        self.assertEqual(
                            record["policy"]["composed"]["decision"],
                            "allow" if store.can_auto_approve(operation) else "approval_required",
                        )
        os.environ["OMES_JOBS_TENANT_ID"] = "tenant-acme"
        self.assertEqual(accepted, len(store.OPERATIONS))
        self.assertEqual(len(store.list_jobs(self.root)), len(store.OPERATIONS))

    def test_tenant_error_messages_unchanged(self):
        os.environ["OMES_JOBS_TENANT_ID"] = "tenant-other"
        with self.assertRaises(store.CrossTenantError) as ctx:
            store.submit(make_request(), root=self.root)
        self.assertEqual(
            str(ctx.exception),
            "request tenant_id='tenant-acme' does not match local tenant 'tenant-other'",
        )
        os.environ.pop("OMES_JOBS_TENANT_ID", None)
        with self.assertRaises(store.TenantNotConfiguredError) as ctx:
            store.submit(make_request(), root=self.root)
        self.assertIn("no local tenant configured", str(ctx.exception))

    def test_malformed_correlation_keeps_precedence(self):
        with self.assertRaises(store.InvalidCorrelationError):
            store.submit(make_request(correlation_id="bad id!"), root=self.root)
        with self.assertRaises(store.CrossTenantError):
            store.submit(make_request(correlation_id="bad id!", tenant_id="tenant-other"), root=self.root)
        request = make_request()
        del request["correlation_id"]
        with self.assertRaises(store.InvalidCorrelationError):
            store.submit(request, root=self.root)

    def test_replay_unchanged(self):
        request = make_request()
        first, _ = store.submit(request, root=self.root)
        second, replayed = store.submit(request, root=self.root)
        self.assertTrue(replayed)
        self.assertEqual(first["job_id"], second["job_id"])


if __name__ == "__main__":
    unittest.main()
