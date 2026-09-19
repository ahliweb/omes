# ADR-0016: Herman is a UX reference, not an OMES dependency

- Status: Accepted
- Date: 2026-09-19
- Related issues: [#89](https://github.com/ahliweb/omes/issues/89), [#90](https://github.com/ahliweb/omes/issues/90), [#91](https://github.com/ahliweb/omes/issues/91), [#95](https://github.com/ahliweb/omes/issues/95), [#98](https://github.com/ahliweb/omes/issues/98), [#99](https://github.com/ahliweb/omes/issues/99), [#100](https://github.com/ahliweb/omes/issues/100), [#101](https://github.com/ahliweb/omes/issues/101), [#102](https://github.com/ahliweb/omes/issues/102)
- Reference: [levay08/herman](https://github.com/levay08/herman)

## Context

Herman is a local Hermes project/panel with useful dashboard, session, usage, maintenance, access and streaming-operation patterns. OMES is developing a staged AWCMS-based Control Center while keeping Hermes as the runtime and OMES as the host/deployment authority.

A local single-user panel and a tenant-scoped control plane do not share the same trust model. Direct process/filesystem access, local token authentication, internal runtime database coupling, raw credential workflows and local-only assumptions cannot be promoted to a public or multi-tenant boundary without changing their security and ownership semantics.

## Options considered

### 1. Adopt Herman as the OMES web GUI/runtime dependency

Rejected. This would import a different source-of-truth model, increase coupling to Hermes internals, and risk direct host/credential operations from a web surface.

### 2. Fork Herman and evolve it into the OMES Control Center

Rejected for the current roadmap. A fork would combine UX work, tenant/business data, provider integrations, job execution and runtime concerns before their contracts are stable. It would also create long-term provenance and upgrade obligations.

### 3. Use Herman as a reference and reimplement selected patterns behind OMES contracts

Accepted. Adopt or adapt navigation, deployment cards, status, usage, insights, streaming job output, confirmation and maintenance UX. Keep AWCMS, OMES, Hermes and providers as separate authorities.

### 4. Build a future OMES Local Console inspired by Herman

Deferred/allowed as a later option. It may be considered for local operator and recovery workflows, but it must not replace the CLI recovery authority or become a public privileged listener.

## Decision

Herman is **reference-only**. It is not vendored, invoked, imported, or treated as a runtime dependency of OMES. The canonical fit matrix and implementation rules are in [docs/web-panel-reference-evaluation.md](../web-panel-reference-evaluation.md).

Any Herman-inspired feature must be classified as adopt, adapt, observe-only, or reject and mapped to an existing owning issue. No duplicate issue is created merely because Herman names a capability differently.

All web mutations use the existing OMES design boundary:

```text
AWCMS identity and policy
  → typed, allowlisted, tenant-scoped request
  → durable idempotent audited job
  → OMES/Hermes/provider executor
  → verification and reconciliation
  → desired/observed state in the UI
```

Raw credentials remain outside the panel and are represented only by secret references and metadata. Provider, billing, entitlement, DNS, deployment and Hermes runtime state remain separate.

If substantial Herman source is copied in the future, the contributor must preserve its MIT notice, add provenance to `THIRD_PARTY_NOTICES.md`, record the exact upstream commit/paths, review dependencies, and prove that OMES security boundaries remain intact.

## Consequences

### Positive

- OMES can benefit from Herman's practical UX patterns without importing its trust boundary.
- Existing issues #89–#102 remain the single implementation backlog.
- AWCMS remains the business/control-plane authority.
- Hermes remains the runtime authority.
- Native OMES remains the local recovery and host-state authority.
- Security, licensing and provenance obligations are explicit.

### Negative

- UX must be reimplemented rather than copied wholesale.
- Some Herman features, especially access management and direct process/file controls, require substantial adaptation or rejection.
- The first web UI will need both product UX work and durable job/event contracts.

### Operational implications

The reference evaluation must be linked from the docs index, architecture, scope, security, threat model and release gates. Until the linked implementation issues land, the Control Center and provider features remain proposed/staged and must not be advertised as implemented.
