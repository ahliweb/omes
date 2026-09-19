# Domain providers: abstraction, capabilities, and provider profiles

> Status: this document is written incrementally, one section per
> implementing issue (#98–#102). Each section states plainly what this
> repository actually ships versus what remains for `awcms-one` — see
> AGENTS.md §1's "if the repository state, issue, or documentation
> disagrees, stop and resolve it explicitly" rule. Nothing in this
> document is a live provider integration; OMES never calls a live
> registrar/DNS/GitHub API from this repository (rule 8, AWCMS boundary
> — see ADR-0011 and `docs/control-center-and-integrations.md`).

## 1. Provider abstraction, capability matrix, and catalog (issue #98)

### 1.1 What this section covers

`contracts/domains/v1/` defines the provider-neutral wire contracts for
domain search, authoritative availability, pricing, registration,
read/sync, renewal, transfer, contact update, DNS records, DNSSEC, and
provider-action-required states, plus the supporting contracts a Control
Center or OMES job boundary needs around them: `RegistrarCapability`,
credential references, price snapshots, the domain-order state machine,
and TLD/provider routing decisions.

`lib/omes/py/domains/` implements the OMES-side stdlib pieces that make
these contracts testable without a live provider:

- `routing.py` — resolves a `(tld, operation)` pair to a provider by
  looking up registered `RegistrarCapability` records, never by TLD
  suffix alone. When no capability matches, it returns an explicit
  `manual_fallback: true` decision.
- `states.py` — the domain-order state machine as pure transition data
  (`pending`, `action_required`, `failed`, `succeeded`, `active`,
  `expired`, `renewal_due`, `cancelled`), enforcing that, for example, a
  queued order can never jump straight to `active` without an
  intermediate reconciled `succeeded` transition.
- `fake_provider.py` — an in-memory `RegistrarAdapter`/`DnsAdapter` used
  only by tests (`tests/py/domains/`), modeling both a Cloudflare-like
  asynchronous registration flow (`mode="async"`: `register()` returns
  `in_progress`; the caller polls until a terminal outcome) and an
  SRS-X-like document-required flow (`mode="documents"`: the document
  lifecycle `documents_required → upload_pending → submitted →
  under_review → rejected|active` is modeled separately from the API
  submission status).

### 1.2 Contracts (`contracts/domains/v1/`)

| Schema | Purpose |
|---|---|
| `registrar-capability.schema.json` | `RegistrarCapability`: provider, extension pattern, account scope, supported operations, explicit `manual_fallback` flag |
| `credential-reference.schema.json` | A named reference to a provider credential (`{"store","key"}` secret_ref); never a raw token/password |
| `domain-search.request` / `.response` | Discovery-only search (`discovery_only: true`); never authoritative |
| `domain-availability.request` / `.response` | Authoritative availability check (`authoritative: true`), required before registration/checkout |
| `price-snapshot.schema.json` | Immutable price snapshot: provider cost, customer price, ISO 4217 currency code, integer minor units, tax class, term, premium flag, effective period |
| `domain-registration.request` / `.response` | Registration job request/response, including `pending`/`in_progress`/`action_required`/`failed`/`succeeded`/`blocked` |
| `domain-renewal.request` | Renewal job request |
| `domain-transfer.request` | Transfer job request; the transfer auth code is a secret reference, never a raw value |
| `domain-contact-update.request` | Contact-update job request, referencing a contact profile rather than embedding raw PII |
| `registered-domain.schema.json` | Read/sync projection of a registered domain: expiry, lock, auto-renew, privacy, provider IDs, `last_synced_at` |
| `dns-record.schema.json` | A single DNS record, with `managed_by` (`omes` vs `external`) for drift detection |
| `dnssec-status.schema.json` | DNSSEC state: `unsigned`/`signing`/`signed`/`action_required` |
| `domain-order.schema.json` | The domain-order state machine as transition data: idempotency key, correlation ID, actor, state, previous state, and an optional `reconciliation` block |
| `routing-decision.schema.json` | The output of a capability lookup: resolved provider, capability ID, and `manual_fallback` |

Every schema has at least one `valid-*.json` and one `invalid-*.json`
fixture under `contracts/domains/v1/fixtures/<schema-name>/`, validated by
the existing `scripts/check-contracts.py` (no changes to that script were
needed — it already discovers any `contracts/<area>/v<major>/` directory
generically).

### 1.3 Currency and pricing

`price-snapshot.schema.json` stores `provider_cost_minor` and
`customer_price_minor` as **integers in the currency's minor unit** (e.g.
cents for `USD`, sen for `IDR`) and `currency` as an ISO 4217 three-letter
uppercase code (`pattern: ^[A-Z]{3}$`). A snapshot is immutable once
captured — a later price change produces a *new* `snapshot_id`, never an
in-place edit (`fake_provider.PriceBook.snapshot()`/`set_price()`
demonstrate this in `tests/py/domains/test_fake_provider.py`'s price
change test).

### 1.4 Provider-neutral routing

`lib/omes/py/domains/routing.py`'s `Router.resolve(tenant_id,
correlation_id, tld, operation)` returns a `routing-decision` shape.
Resolution requires ALL of: extension pattern match, the operation being
in the capability's `supported_operations`, and the capability not being
itself a manual-only placeholder. There is no code path that infers a
provider from a bare `tld.endswith(...)` check (`test_routing.py`'s
`test_suffix_only_matching_is_insufficient_co_id_vs_id` asserts this: a
capability for `id` does not match `android` even though it "ends with"
`id`).

`routing.default_router()` is illustrative wiring for tests only — a real
deployment loads its own `RegistrarCapability` records (issue #99/#100);
this function's hardcoded Cloudflare/SRS-X-shaped list must never be
read as a live configuration source.

### 1.5 Domain-order state machine

`lib/omes/py/domains/states.py` mirrors `lib/omes/py/jobs/store.py`'s
transition-table style. Key invariants enforced by
`states.TRANSITIONS`/`apply_transition()`:

- `pending` may go to `action_required`, `failed`, `succeeded`, or
  `cancelled` — never directly to `active`.
- Only `succeeded` may transition to `active` (a provider operation must
  actually have completed and been reconciled before OMES/awcms-one may
  treat a domain as active).
- `failed` and `cancelled` are terminal; `apply_transition()` raises
  `InvalidTransitionError` for any transition attempted from a terminal
  state.
- Every transition carries an `actor` and `transitioned_at`, and may
  attach a `reconciliation` block (`last_checked_at`,
  `provider_status`, `matches_desired`) — this is transition *data*, not
  an executor; nothing in this module calls a provider.

### 1.6 Idempotency, correlation, and audit fields

Every domains request/response contract that represents a mutating
operation (`domain-availability.request`, `domain-registration.request`,
`domain-renewal.request`, `domain-transfer.request`,
`domain-contact-update.request`) requires `tenant_id`, `correlation_id`,
`idempotency_key`, and `actor`, matching the same field shapes already
used by `contracts/control-center/v1/` (#89) — this repository does not
invent a second idempotency/correlation convention for domains.
`fake_provider.FakeRegistrar` enforces idempotency in-memory
(`DuplicateRequestError` on a replayed `idempotency_key`), mirroring
`lib/omes/py/jobs/store.py`'s real idempotency index.

### 1.7 Security requirements verified for this section

- **Tenant-scoped data, default-deny:** every request/response schema
  requires `tenant_id`; there is no schema in `contracts/domains/v1/`
  that omits it.
- **Secret references only:** `credential-reference.schema.json` and
  `domain-transfer.request.schema.json`'s `auth_code_reference` use the
  same `{"store","key"}` shape as `contracts/control-center/v1/`; the
  existing stdlib secret scanner
  (`lib/omes/py/jobs/schema.py`'s `scan_for_raw_secrets()`) runs against
  every domains fixture via `scripts/check-contracts.py`, so a raw
  secret value accidentally committed to a fixture fails CI.
- **Immutable price snapshots:** see §1.3.
- **No provider calls in database transactions:** not applicable yet —
  this repository has no database; noted here so the requirement is not
  silently dropped when awcms-one implements persistence.

### 1.8 What remains in awcms-one (or a future OMES issue)

This repository does **not** ship, and issue #98 does not require it to
ship:

- a live Cloudflare or SRS-X client — that is issues #99/#100;
- an HTTP/API server, database, or tenant identity system;
- a UI for domain search, checkout, or order status;
- persistence of `domain-order`/`registered-domain` records — the
  fake-provider tests hold everything in memory for the duration of a
  single test process.

Per AGENTS.md §4 and the issue-#98 acceptance criteria: everything listed
as implemented above is genuinely implemented and tested in this
repository (`tests/py/domains/`); everything in this subsection is
explicitly left for `awcms-one` or a future issue.

## 2. Related documents

- [ADR-0011 — Control Center and external provider boundaries](adr/0011-control-center-and-provider-boundaries.md)
- [Control Center and integrations](control-center-and-integrations.md)
- [OMES control jobs](jobs.md) (#90) — the idempotency/correlation/audit conventions this document reuses
- [Security baseline](security.md) §8.2
- [Threat model](threat-model.md) T41
- [contracts/README.md](../contracts/README.md) — the fixture/versioning convention `contracts/domains/v1/` follows
