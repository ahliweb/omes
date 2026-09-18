# OMES Control Center and External Integrations

> Status: proposed architecture; implementation is tracked in [milestone `Domain and Integration Services`](https://github.com/ahliweb/omes/milestone/8) and issues [#89](https://github.com/ahliweb/omes/issues/89)–[#102](https://github.com/ahliweb/omes/issues/102).
>
> This document is normative for the planned web control-plane and provider integrations. It does **not** claim that the web GUI, billing service, registrar adapters, or GitHub adapter exist in the current OMES CLI branch. Every unimplemented capability is explicitly tracked below.

## 1. Purpose and boundary

OMES may provide a web-based **Control Center** based on the AWCMS/awcms-one foundation. The Control Center is an operator and customer-facing control plane for:

- tenants, users, and permissions;
- service catalog, subscriptions, invoices, and entitlements;
- server and deployment inventory;
- idempotent OMES control jobs;
- health/readiness evidence;
- backup and rollback requests;
- domain orders and provider reconciliation;
- GitHub repository and deployment metadata;
- support and operational reporting.

The Control Center does not replace the three existing authorities:

| Concern | Authority |
|---|---|
| Host installation, service lifecycle, hardening, backup, rollback, compatibility, and provenance | OMES |
| Agent reasoning, messaging, sessions, memory, skills, delegation, and model/provider routing | Hermes Agent |
| Tenant, customer, catalog, subscription, invoice, payment, entitlement, and portal state | Control Center/AWCMS, when implemented |
| Registrar state | Cloudflare Registrar or SRS-X |
| DNS state | The selected DNS provider |
| Repository, workflow, release, and deployment observations | GitHub |
| Delegated Coolify resource state | Coolify for the external resource; OMES for logical intent and policy |

The Control Center must never become a second Hermes runtime, arbitrary remote shell, or replacement for the OMES local recovery path.

## 2. Component model

```text
Browser / operator / customer
             │ HTTPS
             ▼
     AWCMS-based Control Center
     - identity and tenant scope
     - catalog and billing
     - approvals and audit
     - portal and reports
             │ versioned API/events
             ▼
       OMES Control API / job boundary
       - allowlisted operations
       - idempotency and correlation
       - preflight/plan/apply/verify
       - desired/observed reconciliation
             │ local socket, mTLS, or pull worker
             ▼
          OMES installation
          - systemd
          - rootless Compose
          - optional Coolify adapter
             │
             ▼
       Hermes runtime and host services

Control Center provider adapters:
  ├── Cloudflare Registrar + Cloudflare DNS
  ├── SRS-X Registrar and optional DNS/DNSSEC
  └── GitHub App / webhooks / Actions observations
```

The production implementation must prefer an authenticated local or pull-based OMES boundary over exposing a privileged host API to the public internet. A CMS request must never translate directly into an arbitrary shell command.

## 3. Integration contract

The versioned boundary is owned by issue [#89](https://github.com/ahliweb/omes/issues/89). The first implementation must define:

- tenant and resource scope;
- authenticated actor or service identity;
- operation name from an allowlist;
- target resource identity;
- desired state or provider action;
- correlation ID;
- idempotency key;
- contract version;
- timeout and retry class;
- sanitized outcome;
- observed state and evidence reference.

A mutation follows this shape:

```text
request
  → authenticate and authorize
  → validate tenant and target
  → check idempotency
  → create audited job
  → preflight / plan
  → approval where required
  → execute one allowlisted operation
  → verify provider/host state
  → reconcile desired and observed state
  → emit result and audit event
```

The external provider is never called inside the database transaction that creates the order, invoice, or job. The database records intent and the outbox/job runner performs the external call.

## 4. Idempotent job rules

Issue [#90](https://github.com/ahliweb/omes/issues/90) owns the job contract. All provider and deployment operations must:

- have an idempotency key;
- return the original result for a replayed request;
- distinguish queued, running, succeeded, failed, cancelled, expired, and rolled-back states;
- classify errors as retryable, non-retryable, or action-required;
- record actor, tenant, target, operation, correlation ID, and timestamps;
- redact secrets and sensitive provider responses;
- verify the target after a mutation;
- never report an unknown timeout as success;
- preserve a manual fallback path when provider capability is absent.

A provider request that may already have been accepted must not be retried blindly. The adapter must first reconcile the provider-side operation or require an operator action.

## 5. Domain provider abstraction

Issue [#98](https://github.com/ahliweb/omes/issues/98) owns the provider-neutral domain contract. Registrar and DNS are separate adapters:

```text
RegistrarAdapter
  search
  checkAvailability
  getPrice
  register
  getRegistration
  renew
  transfer
  updateContacts
  reconcile

DnsAdapter
  createZone
  listRecords
  upsertRecord
  deleteRecord
  getDnssec
  enableDnssec
  reconcile
```

Capabilities are provider- and extension-specific. OMES must not route by suffix alone. It must resolve a capability record using provider account, extension, operation, and current provider configuration.

A domain order stores an immutable checkout snapshot:

```text
tenant_id
normalized_domain
extension
provider
term
currency
provider_cost
customer_price
tax_class
premium_flag
capability_snapshot
idempotency_key
```

The following states must remain separate:

```text
registrar_state
billing_state
entitlement_state
dns_state
deployment_state
```

A paid invoice is not proof that registration succeeded. A successful registration is not proof that DNS or deployment is healthy.

## 6. Cloudflare integration

Issue [#99](https://github.com/ahliweb/omes/issues/99) owns Cloudflare Registrar and DNS integration for supported international extensions.

Required sequence:

```text
search (discovery only)
  → authoritative availability and price check
  → customer confirmation
  → invoice/payment/approval
  → idempotent registration job
  → synchronous or asynchronous status polling
  → provider read/reconciliation
  → DNS zone/record operation, if requested
  → DNSSEC operation, if supported
```

Cloudflare Registrar API limitations must remain visible in the UI and documentation:

- the API is beta and supports only a subset of dashboard extensions;
- dashboard support does not guarantee API registration support;
- renewals, transfers, and contact updates must not be advertised as automated until the current API capability is verified;
- registration can return an asynchronous operation;
- Cloudflare Registrar domains use Cloudflare nameservers;
- Cloudflare currently documents restrictions around IDN/Unicode and contact data.

The adapter must support `extension_not_supported_via_api` and route the order to manual handling rather than attempting an unapproved alternative.

Cloudflare credentials are secret references with least-privilege scope. A customer browser never receives the registrar token. Registration requires an explicit final confirmation of domain, term, price, registrant contact, and provider terms because a completed registration may be non-refundable.

Authoritative sources:

- [Cloudflare Registrar API](https://developers.cloudflare.com/registrar/registrar-api/)
- [Cloudflare Registrar API reference](https://developers.cloudflare.com/api/resources/registrar/)
- [Register a new domain](https://developers.cloudflare.com/registrar/get-started/register-domain/)

## 7. SRS-X integration for `.id`

Issue [#100](https://github.com/ahliweb/omes/issues/100) owns the SRS-X adapter for supported Indonesian extensions.

The adapter configuration is a secret reference plus non-secret provider metadata:

```text
provider: srsx
reseller_id
api_endpoint
sandbox_or_live
credential_reference
authorized_egress_ip
```

SRS-X access requires reseller API configuration and authorized source IP address. Production operations must run from a stable, approved egress path. The adapter must use HTTPS and must never log the API password, request body containing credentials, or raw provider response containing personal data.

A `.id` registration may require a document workflow. The Control Center must distinguish:

```text
registered_pending_documents
 documents_required
 upload_pending
 documents_submitted
 under_review
 rejected
 active
 action_required
```

Document handling requires encrypted approved object storage, short-lived upload capability, tenant-scoped access, access audit, retention, and deletion/legal-hold rules. A short-lived SRS-X upload URL is a capability, not a permanent record.

SRS-X capability and policy must be verified against the actual reseller account before production enablement. Public documentation is not evidence that every `.id` extension, renewal, transfer, DNS, or DNSSEC operation is available for every account.

Authoritative sources:

- [SRS-X API documentation](https://kb.srs-x.com/en/)
- [SRS-X domain API](https://kb.srs-x.com/en/api/domain)
- [SRS-X register domain](https://kb.srs-x.com/en/api/domain/register-domain)
- [SRS-X renew domain](https://kb.srs-x.com/en/api/domain/renew-domain)
- [SRS-X upload document link](https://kb.srs-x.com/en/api/domain/upload-document-link)
- [IANA `.id` delegation data](https://www.iana.org/domains/root/db/.id)

## 8. GitHub integration

Issue [#101](https://github.com/ahliweb/omes/issues/101) owns the GitHub integration. GitHub is an external repository and CI/CD provider, not the OMES billing source of truth.

The preferred authentication model is a GitHub App with minimum organization/repository permissions. OAuth may be used where user identity or consent requires it. The adapter must support:

- installation and removal;
- repository and organization selection;
- environment and branch mapping;
- webhook signature and delivery replay verification;
- repository, commit, release, workflow-run, and deployment metadata;
- linkage to OMES project, domain, and deployment;
- revoked-App and permission-loss handling;
- redacted audit events.

OMES may sell a managed GitHub integration add-on, but it must not mirror GitHub's subscription ledger or claim control of GitHub billing. Provider billing observations, if added later, remain explicitly provider-sourced.

Authoritative sources:

- [GitHub Apps](https://docs.github.com/en/apps/creating-github-apps/about-creating-github-apps)
- [Validating webhook deliveries](https://docs.github.com/en/webhooks/using-webhooks/validating-webhook-deliveries)
- [GitHub deployments](https://docs.github.com/en/actions/deployment/about-deployments)

## 9. Billing and reconciliation

Issue [#102](https://github.com/ahliweb/omes/issues/102) extends issues [#92](https://github.com/ahliweb/omes/issues/92), [#93](https://github.com/ahliweb/omes/issues/93), [#94](https://github.com/ahliweb/omes/issues/94), and [#95](https://github.com/ahliweb/omes/issues/95).

Domain products may include:

- registration;
- renewal;
- transfer/manual handling;
- DNS management;
- DNSSEC;
- managed deployment linkage;
- GitHub deployment integration.

The invoice must preserve the provider/customer price snapshot, term, currency, premium status, tax class, and provider capability at checkout. Renewal reminders should be generated at policy-defined windows such as 90, 30, and 7 days, but the exact schedule is a product policy and must be configurable.

Billing state, registrar state, and entitlement state reconcile asynchronously:

```text
invoice paid
  ≠ registration succeeded
registration succeeded
  ≠ DNS configured
DNS configured
  ≠ deployment healthy
```

Unsupported automation must become a visible manual task. A provider success that is non-refundable must be reflected in cancellation/refund policy before checkout.

## 10. Control Center security rules

The following are mandatory design gates for issues #89–#102:

- no arbitrary shell endpoint;
- no public privileged OMES listener by default;
- local socket, mTLS, or pull worker preferred;
- tenant and resource scope validated server-side;
- AWCMS RLS/ABAC is reused rather than duplicated;
- registrar, DNS, GitHub, payment, and OMES credentials are secret references;
- no secrets in browser payloads, logs, audit bodies, issue text, or backups;
- PII and `.id` documents are encrypted, minimized, retained, and access-audited;
- registration and destructive deployment require explicit confirmation/approval;
- webhook signatures and provider event IDs are verified;
- all external calls are outside database transactions;
- all mutations are idempotent and reconciled;
- provider state cannot overwrite OMES policy or entitlement state;
- service suspension does not automatically stop healthy deployments unless an explicit policy and approval gate permits it.

The Control Center adds a new network-facing trust boundary. It must be added to the threat model before a public pilot.

## 11. Delivery sequence and status

| Phase | Deliverable | Issue | Status in current OMES branch |
|---|---|---:|---|
| D1 | provider abstraction, capabilities, catalog, price snapshot, fallback | #98 | Not implemented yet |
| D2 | Cloudflare Registrar and DNS | #99 | Not implemented yet |
| D2 | SRS-X `.id` and documents | #100 | Not implemented yet |
| D2 | GitHub App/repository integration | #101 | Not implemented yet |
| D3 | domain billing and reconciliation | #102 | Not implemented yet |
| Foundation | Control Center boundary | #89 | Not implemented yet |
| Foundation | idempotent audited jobs | #90 | Not implemented yet |
| Foundation | AWCMS Control Center | #91 | Not implemented yet |
| Foundation | service catalog/entitlements | #92 | Not implemented yet |
| Billing | manual billing ledger | #93 | Not implemented yet |
| Billing | recurring billing and webhooks | #94 | Not implemented yet |
| Billing | reporting projections | #95 | Not implemented yet |
| Isolation | rootless Docker Compose | #96 | Not implemented yet |
| Multi-server | optional Coolify adapter | #97 | Not implemented yet |

The current OMES repository remains a Bash CLI and host toolkit. This document is a design and traceability artifact, not evidence that the web or provider features have landed.

## 12. Related documents

- [Architecture](architecture.md)
- [Scope](scope.md)
- [Agent orchestration roadmap](agent-orchestration-roadmap.md)
- [Security baseline](security.md)
- [Threat model](threat-model.md)
- [Disaster recovery](disaster-recovery.md)
- [Business model](business/business-model.md)
- [Release gates](business/release-gates.md)
- [ADR-0011](adr/0011-control-center-and-provider-boundaries.md)
