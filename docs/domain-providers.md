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

## 2. Cloudflare Registrar and DNS profile (issue #99)

### 2.1 What this section covers

This section wires the provider-neutral contracts from section 1 to a
Cloudflare-specific capability profile, expressed **as data**
(`lib/omes/py/domains/profiles/cloudflare.py`), plus the additional
contracts a real Cloudflare adapter (issue #99, not yet built) will need:
a preflight contract for the scoped API token, a registration polling
contract that never treats a timeout as success, and a DNS drift-report
contract. No live Cloudflare client exists in this repository — this
section documents contracts and fake-provider test coverage only.

### 2.2 Verified provider facts (cited, not invented)

Fetched from Cloudflare's own documentation on 2026-09-19:

- **Registrar API is beta and covers only search, availability, and
  registration** — [Cloudflare Registrar API](https://developers.cloudflare.com/registrar/registrar-api/)
  states "Only a subset of supported Cloudflare Registrar extensions are
  available through the API beta" and that renewals, transfers, and
  contact updates are **not yet available** via the API. A request for
  an unsupported extension returns `extension_not_supported_via_api`.
- **Dashboard support ≠ API support** — the same page: "Some extensions
  supported in the dashboard are not yet available for programmatic
  registration."
- **Registration terms run up to 10 years**, auto-renew defaults on, and
  **Internationalized Domain Names (IDNs) are not supported** —
  [Register a domain](https://developers.cloudflare.com/registrar/get-started/register-domain/).
- **A domain registered through Cloudflare Registrar is locked to
  Cloudflare DNS** — the same page: "will not be able to change to
  another DNS provider's nameservers while using Cloudflare Registrar."
  This is why `lib/omes/py/domains/profiles/cloudflare.py` defines the
  registrar and DNS capabilities over the same TLD set rather than as
  independently configurable scopes.
- **DNSSEC is offered as a follow-on step after registration**, not
  described as available at registration time.

Cloudflare has not published the exact list of extensions supported
through the API (the page contains an unfilled placeholder for it as of
this writing). `cloudflare.REGISTRAR_TLDS` is therefore an explicitly
"illustrative, not authoritative" small subset (`com`, `net`, `org`) —
the module docstring states this must be verified against the live
`extension_not_supported_via_api` response before production use.

### 2.3 Capability profile as data

`lib/omes/py/domains/profiles/cloudflare.py` exports:

- `REGISTRAR_CAPABILITY` — a `RegistrarCapability` dict whose
  `supported_operations` is `["search", "availability", "pricing",
  "registration", "read_sync"]` — **`renewal`, `transfer`, and
  `contact_update` are deliberately absent**, so
  `lib/omes/py/domains/routing.py`'s capability match fails for them and
  routing falls through to `manual_fallback`, per AGENTS.md #99: "do not
  claim automated renewal, transfer, or contact update until the
  provider API capability is verified."
- `DNS_CAPABILITY` — a separate `RegistrarCapability` for
  `dns_records`/`dnssec`, over the same TLD set (see §2.2's DNS-lock
  fact).
- `REQUIRED_TOKEN_SCOPES` — the scopes OMES expects a scoped API token
  to declare (`registrar:read`, `registrar:write`, `dns:edit`); these are
  OMES's own naming for the access it needs, not a literal Cloudflare
  permission-group identifier.
- `MAX_TERM_YEARS`, `SUPPORTS_IDN`, `DNS_LOCKED_TO_PROVIDER` — small
  constants sourced directly from §2.2, so a future adapter cannot
  silently drift from the documented facts without a diff being visible
  in this file.

### 2.4 New contracts

| Schema | Purpose |
|---|---|
| `provider-preflight.request` / `.response` | Structural preflight of a `credential-reference`: provider match, secret-reference resolvability, and required-scope coverage — never a live network call from this repository |
| `registration-poll.response` | The result of one poll of an in-flight registration: `status`, `attempt`, `polled_at`, and an explicit `timed_out` flag so a caller can never mistake a timed-out poll for `succeeded` |
| `dns-drift-report` | A zone-level report of DNS records that are missing at the provider, mismatched, or unmanaged (present at the provider but not desired by OMES/awcms-one) |

`lib/omes/py/domains/preflight.py`'s `run_preflight()` implements the
preflight check against these contracts; it inspects only a
credential's metadata (`provider`, `kind`, `scopes`) and never reads
`credential["reference"]`'s contents, since that value is a `{"store",
"key"}` pointer, not the secret itself.

### 2.5 Fake-provider test coverage (issue #99 acceptance criteria)

`tests/py/domains/test_cloudflare_profile.py` and
`test_preflight.py` cover, against the Cloudflare profile specifically
(not just the generic #98 fake-provider tests): async polling to a
terminal state, duplicate-job rejection, a price change between search
and checkout producing a new snapshot ID, an unsupported-TLD request
routing to `manual_fallback`, DNS drift detection for a Cloudflare zone,
and secret redaction of credential-shaped evidence fields.

### 2.6 What remains in awcms-one (or a future OMES issue)

- A live Cloudflare API client (HTTP calls, real scoped-token
  authentication, real DNS zone management) — this repository ships
  contracts and a fake provider only.
- The actual verified list of API-supported extensions — an operator
  must confirm this against Cloudflare's current API behavior before
  widening `REGISTRAR_TLDS` beyond its current illustrative subset.
- Any UI for showing registration/renewal pricing, premium status, or
  provider terms before customer confirmation.
- Persistence of poll results, drift reports, or DNSSEC status.

## 3. Related documents

- [ADR-0011 — Control Center and external provider boundaries](adr/0011-control-center-and-provider-boundaries.md)
- [Control Center and integrations](control-center-and-integrations.md)
- [OMES control jobs](jobs.md) (#90) — the idempotency/correlation/audit conventions this document reuses
- [Security baseline](security.md) §8.2, §8.3
- [Threat model](threat-model.md) T41, T42
- [contracts/README.md](../contracts/README.md) — the fixture/versioning convention `contracts/domains/v1/` follows
