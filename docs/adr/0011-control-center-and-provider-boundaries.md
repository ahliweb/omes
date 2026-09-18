# ADR-0011 — Control Center and external provider boundaries

- **Status:** Accepted as design boundary; implementation tracked in issues [#89](https://github.com/ahliweb/omes/issues/89)–[#102](https://github.com/ahliweb/omes/issues/102)
- **Date:** 2026-09-19
- **Decision maker:** @ahliweb
- **Related:** ADR-0002 (explicit state), ADR-0003 (module lifecycle), ADR-0004 (check before mutate), ADR-0005 (root/user separation), ADR-0007 (Docker policy), [Control Center and integrations](../control-center-and-integrations.md), [agent orchestration roadmap](../agent-orchestration-roadmap.md)

## Context

OMES is a Bash-based host compatibility and deployment toolkit. The project backlog now includes a possible AWCMS/awcms-one-based web Control Center, managed service catalog and billing, domain registration through Cloudflare and SRS-X, GitHub integration, and optional multi-server backends.

These capabilities introduce a second class of state and new external trust boundaries. If the web application is allowed to execute arbitrary host commands, or if it becomes a second Hermes runtime, OMES would lose its least-privilege and single-authority design. If provider state is copied into billing without reconciliation, customers could be charged for operations that never completed.

## Decision

### 1. The Control Center is a companion control plane, not the OMES runtime

An AWCMS-based Control Center may own tenant, customer, catalog, subscription, invoice, payment, entitlement, approval, support, and reporting state. It requests operations through a versioned OMES API/job boundary. It does not replace the OMES CLI, local recovery path, OMES host state, or Hermes runtime.

Hermes remains the owner of reasoning, messaging, memory, sessions, skills, delegation, and model/provider routing. OMES remains the owner of host deployment, lifecycle, health, hardening, backup, rollback, compatibility, and provenance.

### 2. External operations use allowlisted, idempotent jobs

Every provider or host mutation is represented by an audited job with a tenant, actor, target, operation, contract version, correlation ID, and idempotency key. External calls occur outside database transactions. A timeout or accepted asynchronous operation is not treated as success until provider state is reconciled.

The web boundary must never expose arbitrary shell execution. The preferred transport is a local socket, authenticated mTLS channel, or pull worker; a public privileged host listener is not the default.

### 3. Registrar and DNS are separate provider contracts

`RegistrarAdapter` and `DnsAdapter` are separate. Provider capabilities are resolved by account, extension, and operation. TLD suffix matching alone is insufficient. Unsupported operations go to an explicit manual fallback.

Cloudflare is the preferred registrar candidate for supported international extensions. SRS-X is the candidate for supported Indonesian `.id` extensions and document workflows. These are adapters, not hardcoded assumptions about every TLD or reseller account.

### 4. Billing is an OMES ledger, not provider authority

OMES stores immutable price and invoice snapshots and links them to provider operations. Provider registrar, DNS, GitHub, and payment state remains provider-sourced and is reconciled asynchronously. Payment success does not prove registration success; registration success does not prove DNS or deployment health.

Domain documents and contact data are sensitive data and require encryption, tenant scope, access audit, retention, and deletion/legal-hold rules.

### 5. GitHub is an integration provider, not a billing source of truth

The preferred GitHub authentication is a least-privilege GitHub App. OMES may manage repository/deployment metadata, webhooks, Actions observations, and provenance. It may sell a managed GitHub integration add-on, but it does not mirror or replace GitHub's own subscription ledger.

### 6. The MVP remains local and native

The web Control Center and provider integrations are not prerequisites for the native OMES + Hermes + systemd MVP. Rootless Docker Compose, Coolify, and any web surface remain staged work. Nomad/Kubernetes remain evidence-gated evaluation only.

## Consequences

### Positive

- Keeps host execution, agent runtime, business billing, and provider state under explicit authorities.
- Makes retry, reconciliation, rollback, audit, and manual fallback first-class.
- Allows Cloudflare, SRS-X, GitHub, and future providers to evolve behind adapters.
- Avoids exposing the OMES host as an arbitrary remote-shell service.
- Preserves a usable local CLI when the Control Center or an external provider is unavailable.

### Trade-offs

- There are more contracts and projections than a direct provider-to-UI integration.
- Provider capability drift must be monitored and tested.
- Domain documents and PII require operational storage and retention controls.
- Billing and provider operations are asynchronous; the UI must expose pending and reconciliation states instead of pretending every operation is synchronous.

## Rejected alternatives

- **Make AWCMS execute shell commands over SSH:** rejected because it creates an arbitrary remote execution boundary and duplicates OMES policy.
- **Make the Control Center a second Hermes runtime:** rejected because gateway, memory, skills, sessions, messaging, and provider routing already belong to Hermes.
- **Use Cloudflare for every domain:** rejected because Registrar API coverage and `.id`/registry workflows are not universal.
- **Use a single domain adapter with hardcoded `.id` suffix routing:** rejected because capability depends on provider account, extension, operation, and policy.
- **Treat paid invoices as successful deployments/registrations:** rejected because external operations are asynchronous and can fail after payment.
- **Mirror GitHub billing into OMES:** rejected because GitHub remains the billing authority for GitHub subscriptions.

## Evidence and implementation tracking

- Boundary and contracts: [#89](https://github.com/ahliweb/omes/issues/89)
- Idempotent jobs: [#90](https://github.com/ahliweb/omes/issues/90)
- Control Center: [#91](https://github.com/ahliweb/omes/issues/91)
- Catalog and entitlements: [#92](https://github.com/ahliweb/omes/issues/92)
- Billing: [#93](https://github.com/ahliweb/omes/issues/93), [#94](https://github.com/ahliweb/omes/issues/94), [#95](https://github.com/ahliweb/omes/issues/95)
- Isolation and multi-server: [#96](https://github.com/ahliweb/omes/issues/96), [#97](https://github.com/ahliweb/omes/issues/97)
- Domain and provider integration: [#98](https://github.com/ahliweb/omes/issues/98)–[#102](https://github.com/ahliweb/omes/issues/102)
- Existing runtime contracts consumed by this design: [#79](https://github.com/ahliweb/omes/issues/79), [#81](https://github.com/ahliweb/omes/issues/81), [#82](https://github.com/ahliweb/omes/issues/82), [#83](https://github.com/ahliweb/omes/issues/83), [#84](https://github.com/ahliweb/omes/issues/84), [#85](https://github.com/ahliweb/omes/issues/85), [#87](https://github.com/ahliweb/omes/issues/87)
