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

## 3. SRS-X `.id` profile and document workflow (issue #100)

### 3.1 What this section covers

This section wires the provider-neutral contracts from section 1 to an
SRS-X-specific capability profile, expressed as data
(`lib/omes/py/domains/profiles/srsx.py`), plus the additional contracts a
real SRS-X adapter (not yet built) will need: a reseller config contract,
a document-lifecycle contract kept separate from the raw API result
code, a short-lived document-upload-reference contract, and a
safe-retry-classification module. No live SRS-X client exists in this
repository.

### 3.2 Verified provider facts (cited, not invented)

Fetched from SRS-X's knowledge base on 2026-09-19:

- **Available domain operations** —
  [kb.srs-x.com/en/api/domain](https://kb.srs-x.com/en/api/domain) lists
  Register/Express Register/Premium Express Register, Renew, Check
  (availability), Upload Document Link, Transfer + Transfer
  Lock/Protection, ID protection, nameserver updates, EPP code
  management, and contact modification.
- **Registration is conditionally synchronous** —
  [register-domain](https://kb.srs-x.com/en/api/domain/register-domain):
  with `autoactive` enabled, the domain activates immediately (result
  code `1000`); without it, "Domain is still waiting for the complete
  document" (result code `1001` — which the same page also uses for a
  hard creation failure; the two are **not** distinguishable by result
  code alone).
- **Renewal is synchronous** —
  [renew-domain](https://kb.srs-x.com/en/api/domain/renew-domain) takes
  `domain`, `api_id`, `periode` (years); authentication is username plus
  a SHA256-hashed password; response is `1000`/`<exDate>` or `1001`
  ("Command Failed"), with no documented pending state.
- **Document upload links expire in 10 minutes** —
  [upload-document-link](https://kb.srs-x.com/en/api/domain/upload-document-link):
  the generated URL is "available for 10 minutes only"; document types
  are described only as ".ID required prerequisites documents," with no
  enumerated list on that page.
- **`.id` is PANDI's ccTLD for Indonesia** —
  [IANA .id](https://www.iana.org/domains/root/db/.id). PANDI (via
  resellers such as SRS-X) sets the actual per-second-level-zone
  (`co.id`, `or.id`, ...) document policy; this repository does **not**
  encode that policy in detail — see §3.6.

### 3.3 Capability profile as data

`lib/omes/py/domains/profiles/srsx.py` exports `REGISTRAR_CAPABILITY`
covering `search`, `availability`, `pricing`, `registration`,
`read_sync`, and `renewal` for `id`, `co.id`, and `or.id` — **not**
`transfer`, `contact_update`, `dns_records`, or `dnssec`: SRS-X's
knowledge base documents that these operations exist, but this
repository has not verified their request/response shape against a live
or sandbox account, so they are deliberately excluded and fall through
to `manual_fallback` (docs/threat-model.md T43's sibling risk for
routing generally is T41/T42; the SRS-X-specific risk of misreading an
ambiguous result code is T43).

### 3.4 New contracts

| Schema | Purpose |
|---|---|
| `srsx-config` | Reseller ID, API username, a password **reference** (never a raw password), endpoint, `sandbox`/`live` mode, authorized egress IP |
| `document-lifecycle` | The `.id` document-required lifecycle (`documents_required` → `upload_pending` → `submitted` → `under_review` → `rejected`/`active`/`action_required`), tracked **separately** from `api_submission_status` (`auto_provisioned`/`pending_document`/`failed`) — issue #100's explicit requirement |
| `document-upload-reference` | A short-lived upload capability reference (`{"store","key"}`, never a raw URL) with `requested_at`/`expires_at` |

### 3.5 Preflight, document expiry, and safe-retry classification

- `lib/omes/py/domains/preflight.py`'s `run_srsx_preflight()` checks that
  a `srsx-config` has all required fields, that `password_reference` is
  a well-formed secret reference (never inspecting its value), and that
  `authorized_egress_ip` is a well-formed IPv4 address — a **structural**
  check only; this repository never opens a socket or performs a live
  reachability probe.
- `lib/omes/py/domains/fake_provider.py`'s `request_document_upload()`/
  `submit_documents()` model the documented 10-minute upload-link expiry
  with a deterministic integer tick clock (never wall-clock time, so
  tests are reproducible) and reject a submission after expiry.
- `lib/omes/py/domains/retry.py`'s `classify_srsx_result_code()` never
  marks the ambiguous `1001` code retryable, regardless of whether the
  caller's own `document-lifecycle` tracking believes a document is
  pending — retrying an ambiguous failure risks a duplicate/duplicate-cost
  registration. Only a distinct transient failure
  (`fake_provider.TransportError`, which does not consume the
  idempotency key) is classified retryable.

### 3.6 PANDI/.id policy assumptions and required operator validation

This repository's `REGISTRAR_TLDS` (`id`, `co.id`, `or.id`) and its
document-lifecycle contract are illustrative of the *shape* SRS-X's
document workflow takes, not a complete encoding of PANDI's actual
per-zone document requirements. Before any production use, an operator
must:

- confirm current PANDI/SRS-X document requirements per second-level
  zone (they differ between `id`, `co.id`, `or.id`, and others not
  listed here);
- confirm the exact set of accepted document types (SRS-X's own page
  does not enumerate them);
- confirm whether `autoactive` is available/appropriate for the
  reseller's account and which zones require it off by policy;
- validate the SHA256 password-hashing requirement noted on the
  renew-domain page against the reseller's actual authentication flow
  before building a live client.

### 3.7 Fake-provider test coverage (issue #100 acceptance criteria)

`tests/py/domains/test_srsx_profile.py` covers: registration then
renewal, an API failure that is retryable and does not consume the
idempotency key, a duplicate request rejection, document URL expiry
rejecting a late submission, document rejection routing to
`action_required`, and credential redaction — plus routing tests
distinguishing `id`/`co.id`/`or.id` from a naive suffix match (e.g.
`android`).

### 3.8 What remains in awcms-one (or a future OMES issue)

- A live SRS-X API client (HTTP calls, real SHA256-hashed password
  authentication, real document upload storage).
- The encrypted object storage, access audit, and retention policy for
  actual uploaded registrant documents — issue #100 and AGENTS.md §5 are
  explicit that storage implementation lives in awcms-one, not here.
- Confirmed, current PANDI per-zone document requirements (§3.6).
- Any UI for the document upload flow.

## 4. GitHub App installation, webhook verification, and provider observations (issue #101)

### 4.1 What this section covers

This section is grouped under `contracts/domains/v1/`/`lib/omes/py/domains/`
alongside the registrar work above because issue #101 is part of the
same "Domain and Integration Services" milestone as #98–#100/#102, not
because GitHub is a registrar. It defines GitHub App installation,
repository/environment mapping, webhook-envelope verification, and
provider-observation contracts, plus a stdlib HMAC-SHA256 webhook
verifier. No live GitHub App, webhook receiver, or API client exists in
this repository.

### 4.2 Verified provider facts (cited, not invented)

Fetched from GitHub's own documentation on 2026-09-19:

- **Webhook signature verification** —
  [Validating webhook deliveries](https://docs.github.com/en/webhooks/using-webhooks/validating-webhook-deliveries):
  the `X-Hub-Signature-256` header always starts with `sha256=` followed
  by an HMAC-SHA256 hex digest of the raw request body, keyed by the
  webhook secret; GitHub explicitly recommends a constant-time
  comparison (`crypto.timingSafeEqual`/`secure_compare`-style) to
  mitigate timing attacks. `lib/omes/py/domains/github.py`'s
  `verify_signature()` implements exactly this, using Python's
  `hmac.compare_digest()`.
- **GitHub Apps are installed per organization/user with their own
  granted permissions**, independent of any installing user's personal
  access — [About creating GitHub Apps](https://docs.github.com/en/apps/creating-github-apps/about-creating-github-apps).
  This is why `lib/omes/py/domains/profiles/github.py` names an explicit
  minimum permission set rather than assuming broad access.
- **Environments gate deployments** —
  [About deployments](https://docs.github.com/en/actions/deployment/about-deployments)
  describes environments as a control point for requiring approval,
  restricting branches, and limiting secret access, which is why this
  repository's `github-repository-mapping` contract ties an environment
  name to an expected branch pattern rather than trusting whatever ref a
  webhook payload claims.

### 4.3 New contracts

| Schema | Purpose |
|---|---|
| `github-app-installation` | Installation ID, tenant, account login/type, `repository_selection`, granted permissions (per-resource read/write/none), suspension state |
| `github-repository-mapping` | Links a tenant/project/installation to a repository and its environment→branch-pattern mappings |
| `github-webhook-envelope` | Delivery ID, event type, installation ID, `signature_algorithm` (pinned to `sha256`), event timestamp, received-at, and replay window |
| `github-provider-observation` | The shared shape for repository/commit/release/workflow_run/deployment/environment observations: `observation_type`, `source_url` (must be a `github.com` URL), `last_synced_at`, and a free-form `resource` payload |
| `github-disconnect.request` | Tenant-scoped revocation/disconnect request with an explicit `reason` enum |
| `github-catalog-addon` | An optional managed GitHub integration catalog add-on, referencing a price snapshot — never mirroring GitHub's own subscription ledger (AGENTS.md §2) |

### 4.4 Webhook verification and replay protection

`lib/omes/py/domains/github.py`:

- `verify_signature()`/`compute_signature()` implement GitHub's
  documented `X-Hub-Signature-256` scheme exactly, with a constant-time
  comparison.
- `ReplayGuard` tracks seen `delivery_id`s in memory and enforces a
  configurable replay window against the event timestamp — GitHub's own
  documentation covers signature validation but not replay protection,
  so this is this repository's own addition (see docs/threat-model.md
  T44 and T34, the pre-existing generic "replayed provider webhook"
  risk this makes concrete for GitHub specifically).
- `FakeGitHubApp` models installation-scoped access control
  (`AccessDeniedError` for a repository or permission the installation
  doesn't have), revocation (`RevokedInstallationError`), and
  environment/branch-pattern mismatch (`BranchMismatchError`) — all as
  typed exceptions so tests can assert on the exact rejection reason.

### 4.5 Minimum permissions

`lib/omes/py/domains/profiles/github.py`'s `MINIMUM_PERMISSIONS` requests
only `metadata:read`, `contents:read`, `actions:read`,
`deployments:write` (to report OMES-driven deployment status; never to
push code), `environments:read`, and `checks:read`.
`EXCLUDED_PERMISSIONS_RATIONALE` records, alongside it, why broader
permissions (`administration`, `contents:write`, `secrets`, `issues`,
`pull_requests`) are deliberately not requested.

### 4.6 Fake webhook/API test coverage (issue #101 acceptance criteria)

`tests/py/domains/test_github.py` covers: a valid installation accepting
a delivery, access denial for a repository outside the installation and
for a missing permission, duplicate-delivery idempotent handling, a
revoked installation rejecting every delivery, a branch-mismatch
rejection, and a deployment-status-change delivery accepted when the
branch matches — plus direct signature-verification tests (tampered
payload, wrong secret, malformed header) and replay-window boundary
tests. All HMAC secrets are generated at runtime via
`secrets.token_bytes()`, never a secret-shaped literal.

### 4.7 What remains in awcms-one (or a future OMES issue)

- A live GitHub App (registration, private key, installation access
  token exchange) and any live webhook HTTP receiver.
- Durable delivery-ID/replay-window storage — `ReplayGuard` is in-memory
  and process-lifetime only.
- Any UI for installation, repository selection, or environment mapping.
- The managed GitHub integration catalog add-on's actual billing
  integration (contract only here; see issue #102).

## 5. Domain billing and reconciliation (issue #102)

### 5.1 What this section covers

This section connects the domain catalog/orders from sections 1–4 to
OMES's own billing ledger rules: products, checkout snapshots, a
payment/approval gate, renewal reminders, duplicate-event dedupe,
non-refundable-after-success semantics, state-domain reconciliation, and
reports. Consistent with ADR-0011 §4 ("Billing is an OMES ledger, not
provider authority"), none of this repository's code calls a payment
provider, registrar, or DNS API — `lib/omes/py/domains/billing.py` is
pure rule/data logic, tested against the fake providers only.

### 5.2 New contracts

| Schema | Purpose |
|---|---|
| `domain-product` | A billable product: `kind` (`registration`/`renewal`/`transfer_manual`/`dns_management`/`dnssec`/`managed_service`), TLD pattern, currency, active flag |
| `checkout-snapshot` | `payment_required` is pinned `true` (a domain product is never modeled as free); `approval_required` is a separate, independently-settable flag |
| `renewal-reminder` | 90/30/7-day date-based reminders, plus state-triggered `payment_failure`/`action_required`/`manual_fallback` reminders |
| `reconciliation-rule` | Names a `state_domain` (`registrar`/`invoice`/`entitlement`/`dns`), its authoritative source, which other state domains it is reconciled with, and whether drift is currently detected |
| `billing-event-dedupe` | An idempotency-key record shared across every event source (`payment_provider`/`registrar`/`dns`/`github`), with `duplicate_of` pointing at the first-seen event when replayed |
| `refund-eligibility` | Per-order refund eligibility keyed by `operation` and `order_state` |
| `domain-report` | A generic report envelope (`report_type` ∈ margin/upcoming_renewals/failed_registrations/pending_documents/provider_balance_action/reconciliation_drift) with free-form `rows` |

### 5.3 Payment/approval-before-registration rule

`billing.create_checkout_snapshot()` always sets `payment_required:
true` — this repository does not model a domain product that skips
payment; a future genuinely-free tier would need its own explicitly
named product type rather than a `false` value on this field, so a
reviewer scanning `checkout-snapshot` instances can trust that
`payment_required` is never silently `false`. `assert_registration_allowed()`
raises `billing.PaymentRequiredError` — never proceeds silently — when
payment or (if the checkout requires it) approval is not confirmed.

### 5.4 Non-refundable-after-success and state separation

`billing.refund_eligibility()` treats `succeeded`, `active`, and
`renewal_due` domain-order states (`lib/omes/py/domains/states.py`) as
never refundable through this ledger — the provider operation actually
completed and (for a registrar) a resource now exists that OMES did not
create and must not silently reverse (AGENTS.md §3: "Never delete...
provider resources that OMES did not create or explicitly own").
`billing.build_reconciliation_rule()` enforces that `state_domain` and
`reconciled_with` are drawn from the same fixed set
(`registrar`/`invoice`/`entitlement`/`dns`) and rejects a rule that
claims a state domain is reconciled against itself — reconciliation is
always a comparison between two *different* sources of truth.

### 5.5 Duplicate-event dedupe

`billing.DedupeIndex` is a single idempotency-key index shared across
every billing-relevant event source. This matters because a duplicate
event for the *same* underlying order can legitimately arrive from
different sources (a payment provider's webhook and a registrar's own
status callback both referencing the same order) — the dedupe check is
keyed by `idempotency_key`, not by source, so either arrival recognizes
the other as a duplicate (see docs/threat-model.md T45).

### 5.6 End-to-end fake-provider fixtures (issue #102 acceptance criterion)

`tests/py/domains/test_e2e_billing.py` runs two full flows, no live
credentials:

- **Cloudflare international**: checkout → payment gate → async
  fake-provider registration → polling to `succeeded` → domain-order
  `pending → succeeded → active` → reconciliation rule → expiry-based
  reminder schedule → margin report row → non-refundable-after-active
  check.
- **SRS-X `.id`**: checkout → payment gate → registration immediately
  `action_required` → a `pending_documents` report row → document
  upload/submit/approve → domain-order `action_required → pending →
  succeeded → active` → cross-source duplicate-event dedupe; a second
  scenario covers a rejected document producing a `failed_registrations`
  report row and a refundable (non-`succeeded`) order state.

### 5.7 What remains in awcms-one (or a future OMES issue)

- Any live payment provider integration, invoice generation/PDF,
  dunning, or customer-facing billing UI.
- Durable storage for checkout snapshots, reminders, dedupe records, and
  reports — every structure here is a plain dict returned to the caller,
  never persisted by this repository.
- Sending an actual reminder notification (email/Telegram/etc.) — this
  repository only builds the reminder *record*.
- Cross-referencing a `domain-report`'s rows against live registrar/DNS
  state; the report builder accepts whatever rows a caller supplies.

## 6. Related documents

- [ADR-0011 — Control Center and external provider boundaries](adr/0011-control-center-and-provider-boundaries.md)
- [Control Center and integrations](control-center-and-integrations.md)
- [OMES control jobs](jobs.md) (#90) — the idempotency/correlation/audit conventions this document reuses
- [Security baseline](security.md) §8.2, §8.3, §8.4, §8.5, §8.6
- [Threat model](threat-model.md) T41, T42, T43, T44, T45
- [contracts/README.md](../contracts/README.md) — the fixture/versioning convention `contracts/domains/v1/` follows
