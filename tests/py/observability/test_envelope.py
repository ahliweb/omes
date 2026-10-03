"""Unit tests for lib/omes/py/observability/envelope.py, the cross-plane
correlation/event envelope validator and convergence reducer (issue #272,
ADR-0032 rule 5; threats MA-06, MA-07, MA-11).

Fixtures only contain synthetic identifiers; no prompt text, command line
or credential value is ever constructed except the deliberately forbidden
field names used as negative cases.
"""
import copy
import itertools
import json
import unittest
from pathlib import Path

from . import _pathfix  # noqa: F401  (sets sys.path)

from observability import envelope  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURES = REPO_ROOT / "contracts" / "observability" / "v1" / "fixtures" / "correlation-envelope"


def _ev(event_id, ts, status, **overrides):
    base = {
        "schema_version": "1.0.0",
        "event_id": event_id,
        "source_authority": "omes",
        "tenant_id": "tenant-a",
        "correlation_id": "corr-0000-0001",
        "run_id": "run-1",
        "event_type": "job.state_changed",
        "observed_at": "2026-10-03T10:00:00Z",
        "source_timestamp": ts,
        "freshness": "live",
        "status": status,
        "classification": "INTERNAL",
        "redaction_state": "none_required",
    }
    base.update(overrides)
    return base


class TestValidate(unittest.TestCase):
    def test_checked_in_valid_fixtures_pass(self):
        for path in sorted(FIXTURES.glob("valid-*.json")):
            with self.subTest(fixture=path.name):
                self.assertEqual(envelope.validate(json.loads(path.read_text())), [])

    def test_checked_in_invalid_fixtures_fail(self):
        paths = sorted(FIXTURES.glob("invalid-*.json"))
        self.assertGreaterEqual(len(paths), 5)
        for path in paths:
            with self.subTest(fixture=path.name):
                self.assertNotEqual(envelope.validate(json.loads(path.read_text())), [])

    def test_unknown_major_fails_closed_without_interpreting_other_fields(self):
        for version in ("2.0.0", "0.9.0", "10.0.0", "1", "v1.0.0", "", None, 1):
            with self.subTest(version=version):
                bad = _ev("evt-00000001", "2026-10-03T10:00:00Z", "running", schema_version=version)
                errors = envelope.validate(bad)
                self.assertEqual(len(errors), 1)
                self.assertIn("schema_version", errors[0])
                self.assertIn("fail closed", errors[0])

    def test_unknown_major_with_garbage_body_still_single_fail_closed_error(self):
        errors = envelope.validate({"schema_version": "3.1.4", "anything": "goes"})
        self.assertEqual(len(errors), 1)
        self.assertIn("unsupported major version 3", errors[0])

    def test_known_major_unknown_minor_fails_closed(self):
        errors = envelope.validate(_ev("evt-00000001", "2026-10-03T10:00:00Z", "running", schema_version="1.1.0"))
        self.assertTrue(errors)

    def test_non_object_rejected(self):
        self.assertTrue(envelope.validate([]))
        self.assertTrue(envelope.validate("x"))
        self.assertTrue(envelope.validate(None))

    def test_missing_classification_rejected_no_default(self):
        bad = _ev("evt-00000001", "2026-10-03T10:00:00Z", "running")
        del bad["classification"]
        self.assertTrue(any("classification" in e for e in envelope.validate(bad)))

    def test_missing_tenant_and_correlation_rejected(self):
        for field in ("tenant_id", "correlation_id", "redaction_state", "freshness", "status"):
            with self.subTest(field=field):
                bad = _ev("evt-00000001", "2026-10-03T10:00:00Z", "running")
                del bad[field]
                self.assertTrue(any(field in e for e in envelope.validate(bad)))

    def test_forbidden_raw_fields_rejected_by_name(self):
        for name in sorted(envelope.FORBIDDEN_FIELD_NAMES):
            with self.subTest(field=name):
                bad = _ev("evt-00000001", "2026-10-03T10:00:00Z", "running")
                bad[name] = "x"
                errors = envelope.validate(bad)
                self.assertTrue(any("forbidden field" in e and name in e for e in errors), errors)

    def test_forbidden_name_case_insensitive(self):
        bad = _ev("evt-00000001", "2026-10-03T10:00:00Z", "running", Prompt="x")
        self.assertTrue(any("forbidden field" in e for e in envelope.validate(bad)))

    def test_schema_declares_none_of_the_forbidden_fields(self):
        schema = envelope.load_schema()
        self.assertIs(schema["additionalProperties"], False)
        declared = {k.lower() for k in schema["properties"]}
        self.assertEqual(declared & envelope.FORBIDDEN_FIELD_NAMES, set())

    def test_secret_shaped_value_rejected(self):
        bad = _ev("evt-00000001", "2026-10-03T10:00:00Z", "running", evidence_ref="ghp_abcdefghijklmnop")
        self.assertTrue(any("secret-value shape" in e for e in envelope.validate(bad)))

    def test_trace_and_span_ids(self):
        ok = _ev("evt-00000001", "2026-10-03T10:00:00Z", "running",
                 trace_id="4bf92f3577b34da6a3ce929d0e0e4736", span_id="00f067aa0ba902b7")
        self.assertEqual(envelope.validate(ok), [])
        for field, value in (("trace_id", "0" * 32), ("span_id", "0" * 16),
                             ("trace_id", "4BF92F3577B34DA6A3CE929D0E0E4736"),
                             ("trace_id", "abc"), ("span_id", "00f067aa0ba902b7aa")):
            with self.subTest(field=field, value=value):
                bad = _ev("evt-00000001", "2026-10-03T10:00:00Z", "running", **{field: value})
                self.assertTrue(envelope.validate(bad))

    def test_timestamp_must_be_valid_utc_calendar_time(self):
        for value in ("2026-13-03T10:00:00Z", "2026-02-30T10:00:00Z", "2026-10-03T25:00:00Z",
                      "2026-10-03T10:00:00+07:00", "2026-10-03 10:00:00Z", "2026-10-03T10:00:00",
                      "2026-10-03T10:00:00Z\n"):
            with self.subTest(value=value):
                self.assertTrue(envelope.validate(_ev("evt-00000001", value, "running")))

    def test_trailing_newline_in_identifier_rejected(self):
        self.assertTrue(envelope.validate(_ev("evt-00000001\n", "2026-10-03T10:00:00Z", "running")))

    def test_status_is_closed_and_allows_unknown(self):
        self.assertEqual(envelope.validate(_ev("evt-00000001", "2026-10-03T10:00:00Z", "unknown")), [])
        self.assertTrue(envelope.validate(_ev("evt-00000001", "2026-10-03T10:00:00Z", "success")))
        self.assertEqual(
            envelope.TERMINAL_STATUSES | envelope.NON_TERMINAL_STATUSES,
            set(envelope.load_schema()["properties"]["status"]["enum"]),
        )

    def test_source_authority_mirrors_registry_authority_enum(self):
        registry_schema = json.loads(
            (REPO_ROOT / "contracts" / "architecture" / "v1" / "capabilities.schema.json").read_text()
        )
        registry_enum = registry_schema["properties"]["capabilities"]["items"]["properties"]["authority"]["enum"]
        self.assertEqual(
            sorted(envelope.load_schema()["properties"]["source_authority"]["enum"]), sorted(registry_enum)
        )

    def test_classification_enum_matches_ai_egress_contract(self):
        egress = json.loads(
            (REPO_ROOT / "contracts" / "ai-egress" / "v1" / "egress-decision-request.schema.json").read_text()
        )
        self.assertEqual(
            envelope.load_schema()["properties"]["classification"]["enum"],
            egress["properties"]["classification"]["enum"],
        )

    def test_causation_id_may_be_null_or_absent(self):
        self.assertEqual(envelope.validate(_ev("evt-00000001", "2026-10-03T10:00:00Z", "running", causation_id=None)), [])
        self.assertEqual(envelope.validate(_ev("evt-00000001", "2026-10-03T10:00:00Z", "running")), [])


class TestReduce(unittest.TestCase):
    def test_empty_input(self):
        result = envelope.reduce_events([])
        self.assertTrue(result["ok"])
        self.assertEqual(result["projection"], [])

    def test_duplicates_converge(self):
        a = _ev("evt-00000001", "2026-10-03T10:00:01Z", "running")
        b = _ev("evt-00000002", "2026-10-03T10:00:02Z", "succeeded")
        once = envelope.reduce_events([a, b])
        dup = envelope.reduce_events([a, b, copy.deepcopy(a), b, copy.deepcopy(b), a])
        self.assertTrue(dup["ok"])
        self.assertEqual(once["projection"], dup["projection"])
        self.assertEqual(dup["stats"]["duplicates"], 4)
        self.assertEqual(dup["stats"]["unique"], 2)

    def test_same_event_id_different_content_rejected(self):
        a = _ev("evt-00000001", "2026-10-03T10:00:01Z", "running")
        b = _ev("evt-00000001", "2026-10-03T10:00:01Z", "failed")
        result = envelope.reduce_events([a, b])
        self.assertFalse(result["ok"])
        self.assertEqual(result["projection"], [])
        self.assertTrue(any("reused with different content" in e for e in result["errors"]))

    def test_out_of_order_converges_for_all_permutations(self):
        events = [
            _ev("evt-00000001", "2026-10-03T10:00:01Z", "pending"),
            _ev("evt-00000002", "2026-10-03T10:00:02Z", "running"),
            _ev("evt-00000003", "2026-10-03T10:00:03.5Z", "unknown", freshness="stale"),
            _ev("evt-00000004", "2026-10-03T10:00:04Z", "succeeded"),
            _ev("evt-00000005", "2026-10-03T10:00:05Z", "running", task_id="task-9"),
            _ev("evt-00000006", "2026-10-03T10:00:03Z", "running", task_id="task-9", classification="CONFIDENTIAL"),
        ]
        expected = envelope.reduce_events(events)
        self.assertTrue(expected["ok"])
        for perm in itertools.permutations(events):
            result = envelope.reduce_events(list(perm))
            self.assertEqual(result, expected)

    def test_projection_values(self):
        events = [
            _ev("evt-00000001", "2026-10-03T10:00:01Z", "running"),
            _ev("evt-00000002", "2026-10-03T10:00:02Z", "unknown", freshness="stale", classification="CONFIDENTIAL"),
        ]
        (entry,) = envelope.reduce_events(events)["projection"]
        self.assertEqual(entry["status"], "unknown")
        self.assertFalse(entry["terminal"])
        self.assertEqual(entry["freshness"], "stale")
        self.assertEqual(entry["classification"], "CONFIDENTIAL")
        self.assertEqual(entry["deciding_event_id"], "evt-00000002")
        self.assertEqual(entry["event_count"], 2)
        self.assertIsNone(entry["task_id"])
        self.assertEqual(entry["run_id"], "run-1")

    def test_terminal_not_reverted_by_late_older_or_newer_non_terminal(self):
        terminal = _ev("evt-00000003", "2026-10-03T10:00:03Z", "succeeded")
        older = _ev("evt-00000001", "2026-10-03T10:00:01Z", "running")
        newer_running = _ev("evt-00000005", "2026-10-03T10:00:05Z", "running")
        newer_unknown = _ev("evt-00000006", "2026-10-03T10:00:06Z", "unknown")
        for order in itertools.permutations([terminal, older, newer_running, newer_unknown]):
            (entry,) = envelope.reduce_events(list(order))["projection"]
            self.assertEqual(entry["status"], "succeeded")
            self.assertTrue(entry["terminal"])
            self.assertEqual(entry["deciding_event_id"], "evt-00000003")
            self.assertFalse(entry["conflicting_terminal"])

    def test_unknown_is_never_promoted_to_success(self):
        (entry,) = envelope.reduce_events([_ev("evt-00000001", "2026-10-03T10:00:01Z", "unknown")])["projection"]
        self.assertEqual(entry["status"], "unknown")

    def test_conflicting_terminal_is_flagged_and_first_terminal_wins(self):
        events = [
            _ev("evt-00000001", "2026-10-03T10:00:01Z", "failed"),
            _ev("evt-00000002", "2026-10-03T10:00:02Z", "succeeded"),
        ]
        for order in itertools.permutations(events):
            (entry,) = envelope.reduce_events(list(order))["projection"]
            self.assertEqual(entry["status"], "failed")
            self.assertTrue(entry["conflicting_terminal"])

    def test_same_instant_ties_break_by_event_id(self):
        events = [
            _ev("evt-0000000b", "2026-10-03T10:00:01Z", "running"),
            _ev("evt-0000000a", "2026-10-03T10:00:01Z", "pending"),
        ]
        for order in itertools.permutations(events):
            (entry,) = envelope.reduce_events(list(order))["projection"]
            self.assertEqual(entry["deciding_event_id"], "evt-0000000b")

    def test_fractional_seconds_order_chronologically_not_lexicographically(self):
        # "…01Z" sorts after "…01.5Z" as a string but is earlier in time.
        early = _ev("evt-00000001", "2026-10-03T10:00:01Z", "pending")
        late = _ev("evt-00000002", "2026-10-03T10:00:01.5Z", "running")
        for order in itertools.permutations([early, late]):
            (entry,) = envelope.reduce_events(list(order))["projection"]
            self.assertEqual(entry["status"], "running")

    def test_cross_tenant_substitution_rejected(self):
        a = _ev("evt-00000001", "2026-10-03T10:00:01Z", "running")
        b = _ev("evt-00000002", "2026-10-03T10:00:02Z", "succeeded", tenant_id="tenant-b")
        for order in itertools.permutations([a, b]):
            result = envelope.reduce_events(list(order))
            self.assertFalse(result["ok"])
            self.assertEqual(result["projection"], [])
            self.assertTrue(any("cross-tenant" in e for e in result["errors"]))

    def test_distinct_tenants_with_distinct_correlations_both_project(self):
        a = _ev("evt-00000001", "2026-10-03T10:00:01Z", "running")
        b = _ev("evt-00000002", "2026-10-03T10:00:02Z", "succeeded", tenant_id="tenant-b",
                correlation_id="corr-0000-0002")
        result = envelope.reduce_events([b, a])
        self.assertTrue(result["ok"])
        self.assertEqual({e["tenant_id"] for e in result["projection"]}, {"tenant-a", "tenant-b"})

    def test_run_and_task_scopes_are_independent(self):
        events = [
            _ev("evt-00000001", "2026-10-03T10:00:01Z", "succeeded"),
            _ev("evt-00000002", "2026-10-03T10:00:02Z", "running", task_id="task-1"),
            _ev("evt-00000003", "2026-10-03T10:00:03Z", "failed", run_id="run-2"),
        ]
        entries = envelope.reduce_events(events)["projection"]
        self.assertEqual(len(entries), 3)
        by_scope = {(e["run_id"], e["task_id"]): e["status"] for e in entries}
        self.assertEqual(by_scope[("run-1", None)], "succeeded")
        self.assertEqual(by_scope[("run-1", "task-1")], "running")
        self.assertEqual(by_scope[("run-2", None)], "failed")

    def test_invalid_envelope_fails_whole_reduction(self):
        good = _ev("evt-00000001", "2026-10-03T10:00:01Z", "running")
        bad = _ev("evt-00000002", "2026-10-03T10:00:02Z", "succeeded")
        del bad["classification"]
        result = envelope.reduce_events([good, bad])
        self.assertFalse(result["ok"])
        self.assertEqual(result["projection"], [])

    def test_forbidden_field_fails_whole_reduction(self):
        good = _ev("evt-00000001", "2026-10-03T10:00:01Z", "running")
        bad = _ev("evt-00000002", "2026-10-03T10:00:02Z", "succeeded", prompt="x")
        result = envelope.reduce_events([good, bad])
        self.assertFalse(result["ok"])
        self.assertTrue(any("forbidden field" in e for e in result["errors"]))

    def test_unknown_major_in_batch_fails_closed(self):
        good = _ev("evt-00000001", "2026-10-03T10:00:01Z", "running")
        future = _ev("evt-00000002", "2026-10-03T10:00:02Z", "succeeded", schema_version="2.0.0")
        result = envelope.reduce_events([good, future])
        self.assertFalse(result["ok"])
        self.assertTrue(any("unsupported major version" in e for e in result["errors"]))

    def test_errors_do_not_depend_on_input_order(self):
        good = _ev("evt-00000001", "2026-10-03T10:00:01Z", "running")
        bad1 = _ev("evt-00000002", "2026-10-03T10:00:02Z", "succeeded", prompt="x")
        bad2 = _ev("evt-00000003", "2026-10-03T10:00:03Z", "succeeded", tenant_id="tenant-b")
        results = [envelope.reduce_events(list(p)) for p in itertools.permutations([good, bad1, bad2])]
        self.assertTrue(all(r == results[0] for r in results))

    def test_reducer_does_not_mutate_input_and_is_rebuildable(self):
        events = [
            _ev("evt-00000001", "2026-10-03T10:00:01Z", "running"),
            _ev("evt-00000002", "2026-10-03T10:00:02Z", "succeeded"),
        ]
        snapshot = copy.deepcopy(events)
        first = envelope.reduce_events(events)
        second = envelope.reduce_events(events)
        self.assertEqual(events, snapshot)
        self.assertEqual(first, second)

    def test_projection_carries_no_free_text_or_forbidden_names(self):
        events = [_ev("evt-00000001", "2026-10-03T10:00:01Z", "running")]
        (entry,) = envelope.reduce_events(events)["projection"]
        self.assertEqual({k.lower() for k in entry} & envelope.FORBIDDEN_FIELD_NAMES, set())


if __name__ == "__main__":
    unittest.main()
