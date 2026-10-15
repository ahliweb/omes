"""tests/py/architecture/test_reference_architecture.py - Unit tests for the
schema 1.1.0 semantic invariants (R1-R9), Control Center contract safety
(C1-C2), and canonical documentation invariants (D1-D2) added for issue
#247.

Each Rx/Cx/Dx invariant gets one negative test proving it fails closed and,
where meaningful, a positive test proving a compliant declaration passes.
"""
from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
PY_ROOT = REPO_ROOT / "lib" / "omes" / "py"
if str(PY_ROOT) not in sys.path:
    sys.path.insert(0, str(PY_ROOT))

from architecture import registry  # noqa: E402
from architecture import capabilities_view  # noqa: E402
from jobs import schema as schema_mod  # noqa: E402


def _base_cap(**overrides):
    cap = {
        "capability_id": "test.example.capability",
        "title": "Test Example Capability",
        "authority": "hermes",
        "upstream_project": "NousResearch/hermes-agent",
        "supported_baseline": "v2026.9.14",
        "observed_upstream_revision": "v2026.9.14",
        "maturity": "released_supported",
        "disposition": "delegate",
        "plane": "agent_runtime",
        "execution_semantics": "probabilistic",
        "implementation_status": "delegated_upstream",
        "omes_module": None,
        "duplication_allowed": False,
        "adr_reference": None,
        "removal_trigger": None,
        "evidence_urls": ["https://example.com/evidence"],
    }
    cap.update(overrides)
    return cap


def _registry_of(*caps):
    return {
        "schema_version": "1.1.0",
        "precedence": ["delegate", "port", "adapt", "defer", "reject"],
        "capabilities": list(caps),
    }


class SemanticInvariantTests(unittest.TestCase):
    # -- R1 ---------------------------------------------------------------
    def test_r1_omes_agent_runtime_plane_fails(self) -> None:
        cap = _base_cap(authority="omes", plane="agent_runtime", execution_semantics="deterministic")
        errs = registry.validate_semantic_invariants(_registry_of(cap))
        self.assertTrue(any(e.startswith("R1 ") for e in errs), errs)

    def test_r1_omes_business_control_plane_fails(self) -> None:
        cap = _base_cap(authority="omes", plane="business_control", execution_semantics="deterministic")
        errs = registry.validate_semantic_invariants(_registry_of(cap))
        self.assertTrue(any(e.startswith("R1 ") for e in errs), errs)

    def test_r1_omes_probabilistic_fails(self) -> None:
        cap = _base_cap(authority="omes", plane="host_control", execution_semantics="probabilistic")
        errs = registry.validate_semantic_invariants(_registry_of(cap))
        self.assertTrue(any(e.startswith("R1 ") for e in errs), errs)

    def test_r1_omes_host_control_deterministic_passes(self) -> None:
        cap = _base_cap(
            capability_id="omes.example.host_thing",
            authority="omes",
            plane="host_control",
            execution_semantics="deterministic",
            implementation_status="staged",
        )
        errs = registry.validate_semantic_invariants(_registry_of(cap))
        self.assertEqual([e for e in errs if e.startswith("R1 ")], [])

    # -- R2 ---------------------------------------------------------------
    def test_r2_non_hermes_agent_runtime_without_adr_fails(self) -> None:
        cap = _base_cap(
            capability_id="awcms.example.thing",
            authority="awcms",
            plane="agent_runtime",
            execution_semantics="probabilistic",
            adr_reference=None,
        )
        errs = registry.validate_semantic_invariants(_registry_of(cap))
        self.assertTrue(any(e.startswith("R2 ") for e in errs), errs)

    def test_r2_non_hermes_agent_runtime_with_adr_passes(self) -> None:
        cap = _base_cap(
            capability_id="graphify.example.thing",
            authority="graphify",
            plane="agent_runtime",
            execution_semantics="probabilistic",
            adr_reference="ADR-0099",
        )
        errs = registry.validate_semantic_invariants(_registry_of(cap))
        self.assertEqual([e for e in errs if e.startswith("R2 ")], [])

    # -- R3 ---------------------------------------------------------------
    def test_r3_business_control_wrong_authority_fails(self) -> None:
        cap = _base_cap(
            capability_id="graphify.example.thing",
            authority="graphify",
            plane="business_control",
            execution_semantics="external_authority",
        )
        errs = registry.validate_semantic_invariants(_registry_of(cap))
        self.assertTrue(any(e.startswith("R3 ") for e in errs), errs)

    def test_r3_awcms_agent_runtime_fails(self) -> None:
        cap = _base_cap(
            capability_id="awcms.example.thing",
            authority="awcms",
            plane="agent_runtime",
            execution_semantics="probabilistic",
            adr_reference="ADR-0099",
        )
        errs = registry.validate_semantic_invariants(_registry_of(cap))
        self.assertTrue(any(e.startswith("R3 ") for e in errs), errs)

    def test_r3_awcms_business_control_passes(self) -> None:
        cap = _base_cap(
            capability_id="awcms.example.thing",
            authority="awcms",
            plane="business_control",
            execution_semantics="external_authority",
        )
        errs = registry.validate_semantic_invariants(_registry_of(cap))
        self.assertEqual([e for e in errs if e.startswith("R3 ")], [])

    # -- R4 -----------------------------------------------------------
    def test_r4_omes_rag_capability_fails(self) -> None:
        cap = _base_cap(
            capability_id="omes.knowledge.retrieval_helper",
            authority="omes",
            plane="tool_data",
            execution_semantics="deterministic",
            implementation_status="staged",
        )
        errs = registry.validate_semantic_invariants(_registry_of(cap))
        self.assertTrue(any(e.startswith("R4 ") for e in errs), errs)

    def test_r4_omes_rag_title_word_fails(self) -> None:
        cap = _base_cap(
            capability_id="omes.some.helper",
            title="OMES Embeddings Cache Helper",
            authority="omes",
            plane="tool_data",
            execution_semantics="deterministic",
            implementation_status="staged",
        )
        errs = registry.validate_semantic_invariants(_registry_of(cap))
        self.assertTrue(any(e.startswith("R4 ") for e in errs), errs)

    # -- R5 -----------------------------------------------------------
    def test_r5_logical_boundary_with_omes_module_fails(self) -> None:
        cap = _base_cap(
            capability_id="hermes.example.boundary",
            authority="hermes",
            plane="tool_data",
            execution_semantics="probabilistic",
            implementation_status="logical_boundary",
            omes_module="agent",
        )
        errs = registry.validate_semantic_invariants(_registry_of(cap))
        self.assertTrue(any(e.startswith("R5 ") for e in errs), errs)

    def test_r5_logical_boundary_with_omes_authority_fails(self) -> None:
        cap = _base_cap(
            capability_id="hermes.example.boundary",
            authority="omes",
            plane="host_control",
            execution_semantics="deterministic",
            implementation_status="logical_boundary",
            omes_module=None,
        )
        errs = registry.validate_semantic_invariants(_registry_of(cap))
        self.assertTrue(any(e.startswith("R5 ") for e in errs), errs)

    def test_r5_logical_boundary_clean_passes(self) -> None:
        cap = _base_cap(
            capability_id="hermes.example.boundary",
            authority="hermes",
            plane="tool_data",
            execution_semantics="probabilistic",
            implementation_status="logical_boundary",
            omes_module=None,
        )
        errs = registry.validate_semantic_invariants(_registry_of(cap))
        self.assertEqual([e for e in errs if e.startswith("R5 ")], [])

    # -- R6 -----------------------------------------------------------
    def test_r6_omes_implemented_without_module_fails(self) -> None:
        cap = _base_cap(
            capability_id="omes.example.thing",
            authority="omes",
            plane="host_control",
            execution_semantics="deterministic",
            implementation_status="implemented",
            omes_module=None,
        )
        errs = registry.validate_semantic_invariants(_registry_of(cap))
        self.assertTrue(any(e.startswith("R6 ") for e in errs), errs)

    def test_r6_omes_implemented_without_repo_evidence_fails(self) -> None:
        cap = _base_cap(
            capability_id="omes.example.thing",
            authority="omes",
            plane="host_control",
            execution_semantics="deterministic",
            implementation_status="implemented",
            omes_module="this_module_does_not_exist_anywhere",
        )
        errs = registry.validate_semantic_invariants(_registry_of(cap), REPO_ROOT)
        self.assertTrue(any(e.startswith("R6 ") for e in errs), errs)

    def test_r6_omes_implemented_with_real_module_passes(self) -> None:
        cap = _base_cap(
            capability_id="omes.example.thing",
            authority="omes",
            plane="host_control",
            execution_semantics="deterministic",
            implementation_status="implemented",
            omes_module="architecture",
        )
        errs = registry.validate_semantic_invariants(_registry_of(cap), REPO_ROOT)
        self.assertEqual([e for e in errs if e.startswith("R6 ")], [])

    def test_r6_omes_tool_data_gateway_claim_without_module_fails(self) -> None:
        cap = _base_cap(
            capability_id="omes.universal.tool_gateway",
            title="OMES Universal Tool Gateway",
            authority="omes",
            plane="tool_data",
            execution_semantics="deterministic",
            implementation_status="staged",
            omes_module=None,
        )
        errs = registry.validate_semantic_invariants(_registry_of(cap), REPO_ROOT)
        self.assertTrue(any(e.startswith("R6 ") for e in errs), errs)

    # -- R7 -----------------------------------------------------------
    def test_r7_external_wrong_status_fails(self) -> None:
        cap = _base_cap(
            capability_id="external.example.thing",
            authority="external",
            plane="observability",
            execution_semantics="observational",
            implementation_status="implemented",
            omes_module=None,
        )
        errs = registry.validate_semantic_invariants(_registry_of(cap))
        self.assertTrue(any(e.startswith("R7 ") for e in errs), errs)

    def test_r7_siem_like_not_optional_external_fails(self) -> None:
        cap = _base_cap(
            capability_id="external.observability.wazuh_bridge",
            authority="external",
            plane="observability",
            execution_semantics="observational",
            implementation_status="staged",
            omes_module=None,
        )
        errs = registry.validate_semantic_invariants(_registry_of(cap))
        self.assertTrue(any(e.startswith("R7 ") for e in errs), errs)

    def test_r7_external_optional_external_passes(self) -> None:
        cap = _base_cap(
            capability_id="external.observability.siem_example",
            authority="external",
            plane="observability",
            execution_semantics="observational",
            implementation_status="optional_external",
            omes_module=None,
        )
        errs = registry.validate_semantic_invariants(_registry_of(cap))
        self.assertEqual([e for e in errs if e.startswith("R7 ")], [])

    def test_r7_siem_like_omes_authority_bypass_fails(self) -> None:
        # Regression for the confirmed bypass: the SIEM guard previously only
        # ran inside `if authority == "external":`, so a SIEM-like
        # capability declared with authority "omes" (or any other) slipped
        # through as [] errors. This must now fail regardless of authority.
        cap = _base_cap(
            capability_id="omes.observability.wazuh_bridge",
            title="OMES Wazuh SIEM Bridge",
            authority="omes",
            plane="observability",
            execution_semantics="observational",
            implementation_status="implemented",
            omes_module="architecture",
        )
        errs = registry.validate_semantic_invariants(_registry_of(cap))
        self.assertTrue(any(e.startswith("R7 ") for e in errs), errs)

    def test_r7_siem_like_platform_authority_also_fails(self) -> None:
        cap = _base_cap(
            capability_id="platform.observability.splunk_forwarder",
            title="Platform Splunk Forwarder",
            authority="platform",
            plane="observability",
            execution_semantics="observational",
            implementation_status="staged",
            omes_module=None,
        )
        errs = registry.validate_semantic_invariants(_registry_of(cap))
        self.assertTrue(any(e.startswith("R7 ") for e in errs), errs)

    def test_r7_non_siem_omes_capability_unaffected(self) -> None:
        # Negative control: an ordinary OMES capability with no SIEM-like
        # term must not be flagged by the widened R7 check.
        cap = _base_cap(
            capability_id="omes.example.host_thing",
            title="OMES Host Thing",
            authority="omes",
            plane="host_control",
            execution_semantics="deterministic",
            implementation_status="staged",
        )
        errs = registry.validate_semantic_invariants(_registry_of(cap))
        self.assertEqual([e for e in errs if e.startswith("R7 ")], [])

    # -- R8 -----------------------------------------------------------
    def test_r8_second_agent_framework_without_adr_fails(self) -> None:
        cap = _base_cap(
            capability_id="hermes.example.langchain_bridge",
            authority="hermes",
            plane="agent_runtime",
            execution_semantics="probabilistic",
            adr_reference=None,
        )
        errs = registry.validate_semantic_invariants(_registry_of(cap))
        self.assertTrue(any(e.startswith("R8 ") for e in errs), errs)

    def test_r8_second_agent_framework_with_omes_authority_fails(self) -> None:
        cap = _base_cap(
            capability_id="omes.example.autogen_bridge",
            authority="omes",
            plane="host_control",
            execution_semantics="deterministic",
            implementation_status="staged",
            adr_reference="ADR-0099",
        )
        errs = registry.validate_semantic_invariants(_registry_of(cap))
        self.assertTrue(any(e.startswith("R8 ") for e in errs), errs)

    def test_r8_second_agent_framework_with_adr_and_non_omes_passes(self) -> None:
        cap = _base_cap(
            capability_id="hermes.example.crewai_bridge",
            authority="hermes",
            plane="agent_runtime",
            execution_semantics="probabilistic",
            adr_reference="ADR-0099",
        )
        errs = registry.validate_semantic_invariants(_registry_of(cap))
        self.assertEqual([e for e in errs if e.startswith("R8 ")], [])

    def test_r8_standard_spelling_variants_all_match(self) -> None:
        # Regression for the confirmed bypass: the old regex required
        # "semantic[-_]?kernel" / "llama[-_]?index" (hyphen/underscore only,
        # no space), so the standard spellings "Semantic Kernel" and "Llama
        # Index" never matched. This table also covers "Crew AI", "Auto
        # Gen" and "Lang Graph" called out for the same review.
        variants = [
            "LangChain", "Lang Chain", "lang_chain", "lang-chain",
            "LangGraph", "Lang Graph", "lang_graph", "lang-graph",
            "AutoGen", "Auto Gen", "auto_gen", "auto-gen",
            "CrewAI", "Crew AI", "crew_ai", "crew-ai",
            "LlamaIndex", "Llama Index", "llama_index", "llama-index",
            "SemanticKernel", "Semantic Kernel", "semantic_kernel", "semantic-kernel",
            "Haystack",
        ]
        for idx, variant in enumerate(variants):
            with self.subTest(variant=variant):
                cap = _base_cap(
                    capability_id=f"hermes.example.thing_{idx}",
                    title=f"Bridge to {variant}",
                    authority="hermes",
                    plane="agent_runtime",
                    execution_semantics="probabilistic",
                    adr_reference=None,
                )
                errs = registry.validate_semantic_invariants(_registry_of(cap))
                self.assertTrue(
                    any(e.startswith("R8 ") for e in errs), (variant, errs)
                )

    # -- R9 -----------------------------------------------------------
    def test_r9_hermes_namespace_wrong_authority_fails(self) -> None:
        cap = _base_cap(capability_id="hermes.example.thing", authority="omarchy")
        errs = registry.validate_semantic_invariants(_registry_of(cap))
        self.assertTrue(any(e.startswith("R9 ") for e in errs), errs)

    def test_r9_omes_reasoning_term_fails(self) -> None:
        cap = _base_cap(
            capability_id="omes.agent.reasoning_helper",
            authority="omes",
            plane="host_control",
            execution_semantics="deterministic",
            implementation_status="staged",
        )
        errs = registry.validate_semantic_invariants(_registry_of(cap))
        self.assertTrue(any(e.startswith("R9 ") for e in errs), errs)

    def test_r9_omes_memory_term_fails(self) -> None:
        cap = _base_cap(
            capability_id="omes.agent.memory_store",
            authority="omes",
            plane="host_control",
            execution_semantics="deterministic",
            implementation_status="staged",
        )
        errs = registry.validate_semantic_invariants(_registry_of(cap))
        self.assertTrue(any(e.startswith("R9 ") for e in errs), errs)

    def test_r9_hermes_namespace_correct_authority_passes(self) -> None:
        cap = _base_cap(capability_id="hermes.example.thing", authority="hermes")
        errs = registry.validate_semantic_invariants(_registry_of(cap))
        self.assertEqual([e for e in errs if e.startswith("R9 ")], [])

    def test_r9_model_routing_title_bypass_fails(self) -> None:
        # Regression for the confirmed bypass: _HERMES_RESERVED_TERMS
        # contains the joined token "model_routing", but titles are split
        # on `\W+`, so the natural title "OMES Model Routing Helper" never
        # produced the token "model_routing" and the check returned [].
        cap = _base_cap(
            capability_id="omes.agent.model_router",
            title="OMES Model Routing Helper",
            authority="omes",
            plane="host_control",
            execution_semantics="deterministic",
            implementation_status="staged",
        )
        errs = registry.validate_semantic_invariants(_registry_of(cap))
        self.assertTrue(any(e.startswith("R9 ") for e in errs), errs)

    def test_r9_reserved_term_spelling_variants(self) -> None:
        variants = [
            ("omes.agent.thing1", "OMES Model Routing Helper"),
            ("omes.agent.thing2", "OMES model-routing helper"),
            ("omes.agent.model_routing_helper", "OMES Helper"),
            ("omes.agent.modelrouting_helper", "OMES Helper"),
            ("omes.agent.thing5", "OMES ModelRouting Helper"),
        ]
        for cap_id, title in variants:
            with self.subTest(cap_id=cap_id, title=title):
                cap = _base_cap(
                    capability_id=cap_id,
                    title=title,
                    authority="omes",
                    plane="host_control",
                    execution_semantics="deterministic",
                    implementation_status="staged",
                )
                errs = registry.validate_semantic_invariants(_registry_of(cap))
                self.assertTrue(
                    any(e.startswith("R9 ") for e in errs), (cap_id, title, errs)
                )

    def test_r9_similar_but_not_reserved_term_passes(self) -> None:
        # Negative control: "router"/"routing" must not be conflated with
        # unrelated words, and a title that does not contain the reserved
        # compound term must not be flagged.
        cap = _base_cap(
            capability_id="omes.agent.model_router",
            title="OMES Model Router Helper",
            authority="omes",
            plane="host_control",
            execution_semantics="deterministic",
            implementation_status="staged",
        )
        errs = registry.validate_semantic_invariants(_registry_of(cap))
        self.assertEqual([e for e in errs if e.startswith("R9 ")], [])

    def test_r9_omes_coordinator_and_acp_terms_fail(self) -> None:
        # #269 / ADR-0032: coordinator/worker delegation and the Agent
        # Client Protocol server are Hermes-owned; authority omes must not
        # own a capability whose id or title touches them.
        cases = [
            ("omes.agent.acp_proxy", "OMES ACP Proxy"),
            ("omes.agent.agent_client_protocol", "OMES Editor Bridge"),
            ("omes.agent.scheduler", "OMES Coordinator Scheduler"),
            ("omes.agent.subagent_runner", "OMES Runner"),
            ("omes.agent.runner", "OMES Subagents Runner"),
        ]
        for cap_id, title in cases:
            with self.subTest(cap_id=cap_id, title=title):
                cap = _base_cap(
                    capability_id=cap_id,
                    title=title,
                    authority="omes",
                    plane="host_control",
                    execution_semantics="deterministic",
                    implementation_status="staged",
                )
                errs = registry.validate_semantic_invariants(_registry_of(cap))
                self.assertTrue(
                    any(e.startswith("R9 ") for e in errs), (cap_id, title, errs)
                )

    def test_r9_hermes_acp_server_passes(self) -> None:
        cap = _base_cap(
            capability_id="hermes.agent.acp_server",
            title="Hermes Agent Client Protocol (ACP) Server for Editor Integration",
            authority="hermes",
            execution_semantics="deterministic",
            adr_reference="ADR-0032",
        )
        errs = registry.validate_semantic_invariants(_registry_of(cap))
        self.assertEqual([e for e in errs if e.startswith("R9 ")], [])

    def test_hermes_delegation_and_acp_registered_in_real_registry(self) -> None:
        caps = {c["capability_id"]: c for c in registry.load_registry()["capabilities"]}
        for cap_id in ("hermes.agent.delegation", "hermes.agent.acp_server"):
            with self.subTest(cap_id=cap_id):
                self.assertIn(cap_id, caps)
                self.assertEqual(caps[cap_id]["authority"], "hermes")
                self.assertEqual(caps[cap_id]["disposition"], "delegate")
                self.assertEqual(caps[cap_id]["adr_reference"], "ADR-0032")

    def test_current_repository_passes_check_all(self) -> None:
        self.assertEqual(registry.check_all(REPO_ROOT), [])

    # -- current repository ------------------------------------------
    def test_current_repository_registry_has_no_semantic_invariant_errors(self) -> None:
        reg_data = registry.load_registry()
        errs = registry.validate_semantic_invariants(reg_data, REPO_ROOT)
        self.assertEqual(errs, [], f"Expected 0 semantic invariant errors, got: {errs}")


class ControlCenterContractTests(unittest.TestCase):
    def test_c1_forbidden_shell_key_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            cc_dir = root / "contracts" / "control-center" / "v1"
            cc_dir.mkdir(parents=True)
            bad_schema = {
                "type": "object",
                "properties": {
                    "target": {
                        "type": "object",
                        "properties": {
                            "shell_command": {"type": "string"},
                        },
                    }
                },
            }
            (cc_dir / "bad.schema.json").write_text(json.dumps(bad_schema), encoding="utf-8")

            errs = registry.check_control_center_contracts(root)
            self.assertTrue(any(e.startswith("C1 ") for e in errs), errs)

    def test_c1_fixtures_directory_excluded(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            cc_dir = root / "contracts" / "control-center" / "v1"
            fixtures_dir = cc_dir / "fixtures"
            fixtures_dir.mkdir(parents=True)
            bad_schema = {"properties": {"command": {"type": "string"}}}
            (fixtures_dir / "bad.schema.json").write_text(json.dumps(bad_schema), encoding="utf-8")

            errs = registry.check_control_center_contracts(root)
            self.assertEqual([e for e in errs if e.startswith("C1 ")], [])

    def test_c2_missing_operation_request_contract_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "contracts" / "control-center" / "v1").mkdir(parents=True)

            errs = registry.check_control_center_contracts(root)
            self.assertTrue(any(e.startswith("C2 ") and "missing" in e for e in errs), errs)

    def test_c2_missing_additional_properties_false_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            cc_dir = root / "contracts" / "control-center" / "v1"
            cc_dir.mkdir(parents=True)
            op_schema = {
                "type": "object",
                "required": [
                    "tenant_id", "correlation_id", "idempotency_key",
                    "actor", "operation", "target", "permission",
                ],
                "properties": {
                    "operation": {"type": "string", "enum": ["status"]},
                },
            }
            (cc_dir / "operation-request.schema.json").write_text(
                json.dumps(op_schema), encoding="utf-8"
            )

            errs = registry.check_control_center_contracts(root)
            self.assertTrue(any(e.startswith("C2 ") for e in errs), errs)

    def test_c2_empty_operation_enum_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            cc_dir = root / "contracts" / "control-center" / "v1"
            cc_dir.mkdir(parents=True)
            op_schema = {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "tenant_id", "correlation_id", "idempotency_key",
                    "actor", "operation", "target", "permission",
                ],
                "properties": {
                    "operation": {"type": "string", "enum": []},
                },
            }
            (cc_dir / "operation-request.schema.json").write_text(
                json.dumps(op_schema), encoding="utf-8"
            )

            errs = registry.check_control_center_contracts(root)
            self.assertTrue(any(e.startswith("C2 ") for e in errs), errs)

    def test_c2_missing_required_field_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            cc_dir = root / "contracts" / "control-center" / "v1"
            cc_dir.mkdir(parents=True)
            op_schema = {
                "type": "object",
                "additionalProperties": False,
                "required": ["tenant_id", "correlation_id", "actor", "operation", "target", "permission"],
                "properties": {
                    "operation": {"type": "string", "enum": ["status"]},
                },
            }
            (cc_dir / "operation-request.schema.json").write_text(
                json.dumps(op_schema), encoding="utf-8"
            )

            errs = registry.check_control_center_contracts(root)
            self.assertTrue(any(e.startswith("C2 ") for e in errs), errs)

    def test_c2_via_refs_resolves_and_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            cc_dir = root / "contracts" / "control-center" / "v1"
            cc_dir.mkdir(parents=True)
            op_schema = {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "tenant_id", "correlation_id", "idempotency_key",
                    "actor", "operation", "target", "permission",
                ],
                "$defs": {
                    "operationEnum": {
                        "type": "string",
                        "enum": ["status", "preflight", "start", "stop"],
                    }
                },
                "properties": {
                    "operation": {"$ref": "#/$defs/operationEnum"},
                },
            }
            (cc_dir / "operation-request.schema.json").write_text(
                json.dumps(op_schema), encoding="utf-8"
            )

            errs = registry.check_control_center_contracts(root)
            self.assertEqual([e for e in errs if e.startswith("C2 ")], [])

    def test_current_repository_control_center_contracts_clean(self) -> None:
        errs = registry.check_control_center_contracts(REPO_ROOT)
        self.assertEqual(errs, [], f"Expected 0 Control Center contract errors, got: {errs}")


class CanonicalDocumentationTests(unittest.TestCase):
    def test_d1_unsupported_os_support_claim_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            docs_dir = root / "docs"
            docs_dir.mkdir(parents=True)
            (docs_dir / "scope.md").write_text(
                "OMES provides full support for Debian and Ubuntu hosts.\n",
                encoding="utf-8",
            )

            errs = registry.check_canonical_documentation(root)
            self.assertTrue(any(e.startswith("D1 ") for e in errs), errs)

    def test_d1_negated_unsupported_os_mention_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            docs_dir = root / "docs"
            docs_dir.mkdir(parents=True)
            (docs_dir / "scope.md").write_text(
                "Debian support is explicitly out of scope for OMES.\n",
                encoding="utf-8",
            )

            errs = registry.check_canonical_documentation(root)
            self.assertEqual([e for e in errs if e.startswith("D1 ")], [])

    def test_d1_arch_linux_mention_excluded(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            docs_dir = root / "docs"
            docs_dir.mkdir(parents=True)
            (docs_dir / "scope.md").write_text(
                "Arch Linux support in Omarchy inspires this project's design.\n",
                encoding="utf-8",
            )

            errs = registry.check_canonical_documentation(root)
            self.assertEqual([e for e in errs if e.startswith("D1 ")], [])

    def test_d2_missing_marker_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            docs_dir = root / "docs"
            docs_dir.mkdir(parents=True)
            (docs_dir / "architecture.md").write_text(
                "# OMES Architecture\n\nSome content without the marker.\n",
                encoding="utf-8",
            )

            errs = registry.check_canonical_documentation(root)
            self.assertTrue(any(e.startswith("D2 ") for e in errs), errs)

    def test_d2_missing_mermaid_block_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            docs_dir = root / "docs"
            docs_dir.mkdir(parents=True)
            (docs_dir / "architecture.md").write_text(
                "# OMES Architecture\n\n"
                "<!-- omes:reference-architecture:v1 -->\n\n"
                "No mermaid block here, but it does not mediate all "
                "Hermes-native tool execution.\n",
                encoding="utf-8",
            )

            errs = registry.check_canonical_documentation(root)
            self.assertTrue(any(e.startswith("D2 ") for e in errs), errs)

    def test_d2_missing_phrase_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            docs_dir = root / "docs"
            docs_dir.mkdir(parents=True)
            (docs_dir / "architecture.md").write_text(
                "# OMES Architecture\n\n"
                "<!-- omes:reference-architecture:v1 -->\n\n"
                "```mermaid\ngraph TD; A-->B;\n```\n",
                encoding="utf-8",
            )

            errs = registry.check_canonical_documentation(root)
            self.assertTrue(any(e.startswith("D2 ") for e in errs), errs)

    def test_d2_complete_marker_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            docs_dir = root / "docs"
            docs_dir.mkdir(parents=True)
            (docs_dir / "architecture.md").write_text(
                "# OMES Architecture\n\n"
                "<!-- omes:reference-architecture:v1 -->\n\n"
                "```mermaid\ngraph TD; A-->B;\n```\n\n"
                "OMES does not mediate all Hermes-native tool execution.\n",
                encoding="utf-8",
            )

            errs = registry.check_canonical_documentation(root)
            self.assertEqual([e for e in errs if e.startswith("D2 ")], [])

    def test_missing_canonical_docs_skip_gracefully(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            errs = registry.check_canonical_documentation(root)
            self.assertEqual(errs, [])

    def test_current_repository_canonical_documentation_clean(self) -> None:
        errs = registry.check_canonical_documentation(REPO_ROOT)
        self.assertEqual(errs, [], f"Expected 0 canonical documentation errors, got: {errs}")


class SchemaVersionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.schema = schema_mod.load_json(
            REPO_ROOT / "contracts" / "architecture" / "v1" / "capabilities.schema.json"
        )

    def test_schema_version_1_0_0_is_rejected(self) -> None:
        instance = {
            "schema_version": "1.0.0",
            "precedence": ["delegate", "port", "adapt", "defer", "reject"],
            "capabilities": [],
        }
        errs = schema_mod.validate(instance, self.schema)
        self.assertTrue(
            any("1.0.0" in e and "schema_version" in e for e in errs), errs
        )

    def test_schema_version_1_1_0_is_accepted(self) -> None:
        instance = {
            "schema_version": "1.1.0",
            "precedence": ["delegate", "port", "adapt", "defer", "reject"],
            "capabilities": [],
        }
        errs = schema_mod.validate(instance, self.schema)
        self.assertEqual(errs, [])

    def test_capability_missing_plane_fails_schema_validation(self) -> None:
        cap = _base_cap()
        del cap["plane"]
        instance = {
            "schema_version": "1.1.0",
            "precedence": ["delegate", "port", "adapt", "defer", "reject"],
            "capabilities": [cap],
        }
        errs = schema_mod.validate(instance, self.schema)
        self.assertTrue(
            any("plane" in e and "missing required property" in e for e in errs), errs
        )

    def test_capability_with_all_new_fields_passes_schema(self) -> None:
        cap = _base_cap()
        instance = {
            "schema_version": "1.1.0",
            "precedence": ["delegate", "port", "adapt", "defer", "reject"],
            "capabilities": [cap],
        }
        errs = schema_mod.validate(instance, self.schema)
        self.assertEqual(errs, [])

    def test_current_registry_file_validates_against_schema(self) -> None:
        reg_data = copy.deepcopy(registry.load_registry())
        errs = schema_mod.validate(reg_data, self.schema)
        self.assertEqual(errs, [])


class ArchitectureCapabilitiesViewFreshnessTests(unittest.TestCase):
    """AV1: contracts/control-center/v1/fixtures/architecture-capabilities-view/
    valid-01-generated.json must not be stale relative to
    architecture/capabilities.json (issue #246, part 3)."""

    def test_current_repository_fixture_is_fresh(self) -> None:
        errs = registry.check_architecture_capabilities_view(REPO_ROOT)
        self.assertEqual(errs, [], f"Expected 0 AV1 errors, got: {errs}")

    def test_stale_fixture_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            reg_dir = root / "architecture"
            reg_dir.mkdir(parents=True)
            reg_data = _registry_of(_base_cap())
            (reg_dir / "capabilities.json").write_text(json.dumps(reg_data), encoding="utf-8")

            fixture_dir = (
                root / "contracts" / "control-center" / "v1" / "fixtures" / "architecture-capabilities-view"
            )
            fixture_dir.mkdir(parents=True)
            (fixture_dir / "valid-01-generated.json").write_text(
                json.dumps({"stale": True}), encoding="utf-8"
            )

            errs = registry.check_architecture_capabilities_view(root, reg_data)
            self.assertTrue(any(e.startswith("AV1 ") for e in errs), errs)

    def test_regenerated_fixture_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "VERSION").write_text("0.0.0-test\n", encoding="utf-8")
            reg_dir = root / "architecture"
            reg_dir.mkdir(parents=True)
            reg_data = _registry_of(_base_cap())
            (reg_dir / "capabilities.json").write_text(json.dumps(reg_data), encoding="utf-8")

            fixture_dir = (
                root / "contracts" / "control-center" / "v1" / "fixtures" / "architecture-capabilities-view"
            )
            fixture_dir.mkdir(parents=True)
            fixture_path = fixture_dir / "valid-01-generated.json"

            expected = capabilities_view.build_fixture_view(root, reg_data)
            fixture_path.write_text(
                json.dumps(expected, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )

            errs = registry.check_architecture_capabilities_view(root, reg_data)
            self.assertEqual(errs, [], errs)

    def test_missing_fixture_skips_gracefully(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            errs = registry.check_architecture_capabilities_view(root)
            self.assertEqual(errs, [])

    def test_check_all_includes_av1(self) -> None:
        errs = registry.check_all(REPO_ROOT)
        self.assertEqual([e for e in errs if e.startswith("AV1 ")], [])


if __name__ == "__main__":
    unittest.main()
