# OMES host posture and Hermes boundaries — FastPlaz concepts, repository-specific architecture

## Document control
- **Version:** v1.0.0
- **Updated:** 2026-11-10
- **Status:** Proposed architecture / research only; not a production feature.
- **Document Changeset:** First reference and implementation-boundary mapping.
- **Owning issue:** [#393](https://github.com/ahliweb/omes/issues/393).

## Summary and confirmed local baseline
OMES pre-alpha controls Ubuntu/Mint host compatibility, install/check/verify/rollback, and integrates Hermes. `omes health acp` is read-only; Hermes owns runtime, ACP and MCP tools.

## Intent and candidate adaptation
Take only concepts for local loopback security, configured capability exposure, approval, egress and toolset checks. Extend existing read-only posture evidence, not execution authority; explicitly report absent data `unknown`.

## Authority and prohibitions
OMES MUST NOT implement new Hermes memory/router/delegation, MCP/ACP proxy/server/client, generalized command execution, or business approval API; AWCMS owns approved business jobs and audit.

## Existing related issues and anti-duplication
Architecture [#393](https://github.com/ahliweb/omes/issues/393), existing [#269](https://github.com/ahliweb/omes/issues/269), closed inbound ACP [#273](https://github.com/ahliweb/omes/issues/273), open posture follow-up [#357](https://github.com/ahliweb/omes/issues/357), privacy research [#375](https://github.com/ahliweb/omes/issues/375).

## Six concrete validation scenarios
1. Disabled inbound ACP is reported without starting network listener.
2. MCP bound to wildcard interface is flagged as risky exposure.
3. Missing egress profile is unknown, never healthy.
4. Unknown tool permission cannot be assumed read-only.
5. Unsafe host mutation is rejected by typed operation boundary.
6. Direct Hermes access is not incorrectly claimed to be intercepted by OMES.

## Proposed checks and test evidence
Use `omes health acp`, lint/architecture registry checks, test fake config and known/unknown negative cases; no direct access to internal Hermes databases or live production data.

## Rollout / handoff
Do not duplicate #357; add only missing posture test cases via existing issues and review supported Hermes version.

## Source reference and limits
This document takes **concepts only** from [FastPlaz](https://github.com/fastplaz/fastplaz): [MCP/AI Agent Plane](https://github.com/fastplaz/fastplaz/blob/master/docs/MCP.md), [IDE workflow](https://github.com/fastplaz/fastplaz/blob/master/docs/AI_AGENT_by_FASTPLAZ.md), and [standalone operator concepts](https://github.com/fastplaz/fastplaz/blob/master/docs/STANDALONE-deployment.md). No Pascal, Lazarus, CGI, MVC/theme engine, or source-code copying. [FastPlaz license text](https://github.com/fastplaz/fastplaz/blob/master/LICENSE.txt) does not demonstrate explicit source-code permissions; independent implementation only.

## Cross-portfolio engineering invariants
Applies the **internal cross-portfolio standard**, alongside this repository's own AGENTS, ADR, CONTRIBUTING and current code reality. Human-approved **Gate 9 (cross-spec Definition of Ready)** precedes production feature code; human-approved **Gate 15 (release readiness)** precedes production. One issue/branch/PR per atomic scope. Never present a specification, green documentation check, or research as working production functionality.

Local MCP/tool connector, if ever required: loopback or authenticated IPC, host/Origin restrictions for HTTP, rotating scoped credentials, canonical-path and symlink protections, read/write/destructive approval tiers, no arbitrary shell, no default SQL/raw DB/secret access, bounded resource/time/cancellation, policy at actual tool/OS runtime, fail-closed unknown and redacted logs. Human sign-off for changes to authorization, production configuration or sensitive data.

## Relevant regulatory and control references
Evaluate applicability, not compliance by assertion: Indonesian UU 27/2022 (PDP), PP 71/2019 (PSTE), Perpres 95/2018 where SPBE applies; ISO/IEC 27001, 27002, 27005, 27017, 27018, 27034, 27701, ISO 22301, ISO/IEC 20000-1, ISO/IEC 42001, ISO/IEC 15408; OWASP LLM/Agentic Security, NIST AI RMF. Obtain independent control evidence and applicable edition before any claim of certification.

## Acceptance evidence rubric
For any follow-on: link spec/ADR and owning issue; record source commit, toolchain/runtime version, commands executed, actual error/result, denied access tests and evidence SHA. Mark unrun checks **NOT EXECUTED**. Approvals and publication/deployment status are distinct. No sensitive personal data or production secrets in prompts, logs or GitHub artifacts.


## Changelog
- **v1.0.0 — 2026-11-10:** Documented evidence-backed, repo-specific adoption boundary, six practical cases and acceptance rubric.
