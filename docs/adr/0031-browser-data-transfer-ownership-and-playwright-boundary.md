# ADR-0031: Delegate AI-assisted browser data transfer to Hermes and a constrained Playwright adapter

- **Status**: Proposed
- **Date**: 2026-10-12
- **Owner**: OMES architecture (review required)
- **Related**: #294, #295; ADR-0017, ADR-0024, ADR-0027, ADR-0029; #71, #80, #90, #192, #214-#217, #263, #269, #276

## Context

Operators need to transfer records between unrelated web applications that do not expose interoperable APIs, using natural-language instructions, local model interpretation, robust browser automation, progress and audit reports. OMES already integrates Hermes for reasoning, browser tools and model routing; OMES also ships host health, security and deployment primitives, while AWCMS owns the authenticated Control Center and durable domain workflow state.

The design must not create a second Hermes runtime, second Ollama model gateway, unscoped execution endpoint or second dashboard. Browser-origin page contents are untrusted and should not be allowed to redefine permissions. A click or toast is not evidence of persisted data.

## Options and tradeoffs

| Option | Security | Performance | Maintainability | Compatibility / operations |
|---|---|---|---|---|
| A. Build Playwright+LLM+business queue directly inside OMES | High risk of credential/control-plane coupling | Low overhead for one host, poor fleet fit | Duplicates Hermes/AWCMS; rejected | Contradicts ADR-0017 and ADR-0024 |
| B. Let an unconstrained browser AI agent click everything | Unacceptable unbounded action/egress risk | Higher inference cost and variable latency | Quick demos, brittle production | No deterministic audit/replay guarantee |
| **C. Delegate to Hermes and policy-gated Playwright worker; AWCMS manages business controls; OMES manages host evidence** | **Strongest separation and testability** | Fast deterministic mode; AI invoked only when uncertain | Reuses existing authority and lifecycle | **Selected recommendation, subject to baseline tests** |
| D. Use API-to-API integration where both services permit it | Usually strongest data integrity | Usually fastest | Low UI maintenance | Preferred over browser RPA when available |

## Decision (proposed)

Choose **C for browser-only use cases** and **D wherever a supported API exists**.

1. **Hermes authority**: inference/model selection, agent planning, browser tool invocation and per-session agent semantics. Native Hermes browser is the first candidate; a Playwright SDK or MCP adapter is introduced only on evidence of a real capability gap. Pin verified versions.
2. **AWCMS authority**: site/template catalog, approval and authorization, durable record/job ledger, mapping version, business reports and existing `/admin/omes` views. `ahliweb/awcms` is canonical; `awcms-one` is a reference/template deployment.
3. **OMES authority**: host provisioning, isolation, exposure and local inference readiness, resource controls, provenance and **sanitized** execution evidence. Extend #71/#80/#192/#276 instead of implementing the business worker in OMES.
4. **Policy boundary**: model output is a proposal, never a permission. A deterministic, validated and versioned allowlist mediates all browser actions in the constrained adapter. No arbitrary browser JS, shell, HTTP, local file access or cloud fallback for restricted data.
5. **Consistency boundary**: writes must have unique source identity and independent destination read-back. If submit state is unknown, reconcile before retrying. UI-only systems cannot promise exactly-once effects without destination support; report this limitation.
6. **Observability**: project redacted job/record events into existing AWCMS Jobs/Operations/Audit and the existing Mission Control read model, avoiding a parallel dashboard.

The workflow is **not** a global security intercept for Hermes native tools outside the dedicated constrained execution profile.

## Consequences

**Benefits:** operator-friendly language control, faster deterministic repeated jobs, minimized LLM token traffic, local privacy options, robust auditability, standardized recovery and reduced duplicated product architecture.

**Costs/risks:** UI selectors can break, browser automation can be limited by MFA and site terms, model outputs can be wrong, external sites may not expose reliable read-back, and multi-repository integration introduces version compatibility obligations. These require pilot fixtures, human intervention states, explicit provider permission, threat modeling and staged release gates.

**Non-goals:** bypassing MFA/CAPTCHA, silently scraping unauthorized sites, unrestricted remote browser control, auto-committing arbitrary writes suggested by an LLM, or representing a prospective feature as already shipped.

## Implementation and verification

Detailed normative design and synthetic acceptance matrix: [browser-data-transfer-integration.md](../browser-data-transfer-integration.md). Parent tracking epic #294; technical documentation issue #295. Subsequent implementation must be split into owner-specific issues/PRs and include read-back verification, replay/crash tests, local-only inference testing, and privacy-preserving audit.

**Status reminder:** proposed ADR and docs only. No new Playwright executable, AI browser workflow or Control Center screen is delivered by this decision record.
