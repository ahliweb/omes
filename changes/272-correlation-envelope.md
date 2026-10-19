---
issue: 272
type: added
---
Added the versioned cross-plane correlation/event envelope as evidence, never authority (ADR-0032 rule 5, epic #269): the `contracts/observability/v1/correlation-envelope.schema.json` contract (closed status, freshness, ADR-0029 classification, redaction state, causation and W3C-shaped trace identifiers, no free-text, command or credential field) with valid and invalid fixtures, the stdlib-only `lib/omes/py/observability/envelope.py` validator that fails closed on unknown major versions and reuses the existing schema validator and secret scan, a pure order-independent convergence reducer (dedupe by event id, cross-tenant rejection, terminal status never reverted) and the registered `omes.observability.correlation_envelope` capability; propagation of the identifiers through AWCMS, job and worker flows and any envelope producer are not implemented yet (tracked in #272).
