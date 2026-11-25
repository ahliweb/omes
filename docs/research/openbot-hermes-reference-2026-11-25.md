# OpenBot reference evaluation — host operations and Hermes boundary

## Metadata
- Version: v0.1.0
- Last updated: 2026-11-25
- Changeset: initial independent concept research and gap assessment.
- Status: research proposal; not an accepted ADR, implementation or gate approval.
- Tracking: [#405](https://github.com/ahliweb/omes/issues/405).

## Local fit and non-goals

Read [runtime boundary](../agent-runtime-boundary.md), ADR-0013/0017, and existing [#269](https://github.com/ahliweb/omes/issues/269), [#276](https://github.com/ahliweb/omes/issues/276), [#277](https://github.com/ahliweb/omes/issues/277), [#344](https://github.com/ahliweb/omes/issues/344). Hermes is sole supported runtime at documented baseline v2026.9.24; upstream main capability must not be assumed present at baseline.

| Pattern | Disposition | Verification |
|---|---|---|
| Dedicated workspace/team host | Adapt host profile isolation, not agent workspace semantics | distinct service users, filesystem permissions, no credential sharing |
| Runtime adapter | Reuse existing runtime_supported/runtime_describe; no plugin system or new runtime | unknown name rejected, pinned fixture |
| ACP | Delegate native Hermes session behavior, assess version compatibility | synthetic handshake, cancellation, permission failure |
| Durable board | Delegate Hermes Kanban; existing OMES mission control metadata only | kill/restart, restore evidence, no duplicated queue |
| Health / backup / rollback | Extend existing host lifecycle evidence if a gap exists | native CLI and health endpoint, restore drill |
| Approval/agent memory | Runtime or business owner; OMES only host-side allowlisted operations | no arbitrary shell, no direct access to .hermes databases |

Research follows DELEGATE → PORT → ADAPT → DEFER → REJECT; here PORT of OpenBot code is prohibited. No feature claim or second runtime admission. Report gap to existing issue owners; if a boundary changes, submit proposed ADR and Gate 9 review. Test host recovery with synthetic workloads and least-privilege profiles; capture version, commands, exit status and sanitized evidence. No live host changes.

## Evidence and decision (2026-11-25)

| Primary evidence | Finding | Disposition |
|---|---|---|
| [Nightly Labs OpenBot LICENSE](https://github.com/nightly-labs/openbot/blob/main/LICENSE), [README](https://github.com/nightly-labs/openbot/blob/main/README.md) | PolyForm Noncommercial 1.0.0; dedicated agent workspaces, team coordination and provider integration including Hermes ACP | Conceptual reference only; no code, binaries, skills, assets or proprietary service |
| [PolyForm terms](https://polyformproject.org/licenses/noncommercial/1.0.0) | Personal research permission is bounded by absence of anticipated commercial application | Do not install or test restricted software for commercial product development without separate authorization |
| [CopilotKit OpenBot LICENSE](https://github.com/CopilotKit/OpenBot/blob/main/LICENSE), [configuration](https://github.com/CopilotKit/OpenBot/blob/main/docs/configuration.md), [architecture](https://github.com/CopilotKit/OpenBot/blob/main/docs/architecture.md) | MIT application template; Intelligence requires a license or project key; documented browser default is broad allow | Concept only; reject permissive default and dependency on Intelligence |
| [Hermes Agent](https://github.com/NousResearch/hermes-agent), [Kanban](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/kanban.md) | MIT repository, native learning, skills, persistent agent and durable board | Keep approved upstream integration; evaluate pinned version and native functionality before any duplication |

Historical license claims about older OpenBot tags are not accepted as evidence of rights; no historical version will be imported. Public descriptions establish advertised behavior, not security assurance, production readiness or performance. No product installed, benchmarked or audited here. License scope must be checked at the immutable commit, dependency, model, asset and service level before adoption.

## Independent industry references

| Reference | Role and implication |
|---|---|
| [ACP](https://agentclientprotocol.com/get-started/introduction) | Agent/client session lifecycle, capability negotiation, permission requests and cancellation; not an authorization engine |
| [AG-UI](https://docs.ag-ui.com/introduction) | Event-oriented agent/user application interaction; use typed sanitized projections, not raw privileged execution |
| [MCP security](https://modelcontextprotocol.io/docs/2026-07-28/tutorials/security/security_best_practices) | Tool/context interface; prevent token passthrough, confused deputy, SSRF and malicious tool output |
| [A2A](https://a2a-protocol.org/v1.0.0/) | Inter-agent collaboration; cross-boundary identity, audience, purpose, expiry and revocation still required |
| [Temporal workflow execution](https://docs.temporal.io/workflow-execution) | Durable workflow reference; use restart-safe state, idempotent effects and reconciliation; not a dependency decision |
| [OpenTelemetry GenAI](https://opentelemetry.io/docs/specs/semconv/gen-ai/) | Correlation and observability reference; pin stable fields; redact sensitive payloads |
| [OWASP Agentic Top 10 2026](https://genai.owasp.org/top-10-for-agentic-applications-2026/) | Goal hijack, tool misuse, identity abuse, supply chain, execution, memory poisoning, inter-agent communication, cascade failure, trust exploitation and rogue agents |
| [NIST AI 600-1](https://www.nist.gov/publications/artificial-intelligence-risk-management-framework-generative-artificial-intelligence) | Govern, map, measure, manage; evidence-based risk review |
| [OpenChain](https://openchainproject.org/get-started), [SPDX](https://spdx.dev/), [CycloneDX](https://cyclonedx.org/) | ISO/IEC 5230 license compliance and 18974 security assurance, SPDX identifiers/SBOM; inventory and review |

References retrieved 2026-11-25. Protocol specifications and SDKs are distinct licensing surfaces; no SDK is admitted by this document. Pin protocol revision and test interoperability before proposing a dependency.

## Proposed cross-cutting controls (not implementation)

- Default deny at server/host, explicit principal + tenant/domain + capability + resource + purpose; never accept agent output as authorization.
- High-impact action preview, immutable action digest, approver independent of agent, short expiry, single-use approval bound to actor/action/target/version, reauthorize at execution, deny on revocation or mismatch.
- Isolated worker profiles, minimal read-only scopes, separate secrets, egress allowlist, bounded tool budgets, cancellation and emergency stop; treat documents, web content, tool results and generated skills as untrusted.
- Durable state belongs to the owner of the workflow: queued/running/waiting_approval/succeeded/failed/cancelled/unknown; lease/fencing, bounded retry, idempotency key, reconciliation for ambiguous external effects; never promise exactly-once external execution.
- Audit event proposal: event_id, correlation_id, actor_ref, tenant_ref, capability, resource_ref, policy_version, decision, approval_ref, outcome, timestamps; sanitized metadata only, retention/classification under applicable policy; no credentials, raw secrets or chain-of-thought.
- Memory/skills proposals require provenance, scoped review, expiry/revocation and regression tests; preserve native runtime authority.
- Before production: test cross-tenant denial, stale/replayed approval, injection in tool output, unexpected egress, crash/restart, concurrent retry, cancellation, corrupted memory, recovery and audit linkage. Evidence must distinguish docs, mocks, unit tests, real integration and production.

## Licensing and governance

Owner direction: independently implement relevant concepts, never copy OpenBot source, documentation expression, branding, screen assets or skills. Maintain original functional specification and implementation provenance, exact source/version/license, dependency tree and commercial-use review, SBOM, notices and exceptions. MIT/Apache/BSD are candidates, not automatic approval; copyleft and source-available restrictions require review. Do not infer patent or trademark rights. Indonesia UU 28/2014 (especially distinction of ideas and expression in Art. 41) and UU 27/2022 require contextual legal/privacy review; see [copyright](https://peraturan.bpk.go.id/Details/38690) and [PDP](https://peraturan.bpk.go.id/Details/229798/uu-no-27-tahun-2022). No legal certification implied.

Control mapping: ISO/IEC 42001 and 23894 (AI governance/risk), 27001/27002/27005 (ISMS/controls/risk), 27017/27018 (cloud), 27034 (application security), 27701 (privacy), 15408 (evaluation where scoped), 20000-1 (service lifecycle), ISO 22301 (continuity), 5230/18974 (supply chain). Mapping is guidance, not certification. Preserve current technology and authority, no new package/runtime/production change without reviewed ADR and applicable gates.

## Changelog
- v0.1.0 (2026-11-25): validated public documentation, recorded boundaries, proposed tests.
