# OMES Control Center threat model (issue #89)

> Status: design-stage threat model for a boundary that does not have a
> running implementation in this repository yet, except the OMES-side job
> runner (issue #90, `lib/omes/py/jobs/`). See
> [docs/threat-model.md](threat-model.md) for the existing, implemented
> OMES/Hermes/Telegram threat model; this document is scoped to the *new*
> trust boundary the Control Center adds, per
> [docs/control-center-and-integrations.md](control-center-and-integrations.md)
> §10 ("The Control Center adds a new network-facing trust boundary. It
> must be added to the threat model before a public pilot."). This
> document does not redefine the contracts owned by #79, #82, #83, #84, or
> #87; it links to them.

## 1. System description

```text
 Browser / operator / customer
             │ HTTPS (AWCMS-owned; not implemented in this repository)
             ▼
     AWCMS-based Control Center
     - identity, tenant scope, RBAC/ABAC
     - catalog, billing, entitlements
     - approvals, audit, portal
             │ contracts/control-center/v1/*  (this document's boundary)
             ▼
   OMES job boundary  (lib/omes/py/jobs/, issue #90)
   - schema validation (rejects free-form command)
   - idempotency, correlation, audit
   - approval gate for destructive operations
   - allowlisted operation -> fixed argv mapping
             │ local socket / mTLS / pull worker (not a public listener)
             ▼
        OMES installation (bin/omes, lib/omes/*.sh, modules/*)
             │
             ▼
        Hermes runtime and host services
```

## 2. Trust boundaries

| Boundary | Crossed by | Trust change |
|---|---|---|
| B1: Browser ↔ Control Center | HTTPS | Untrusted internet client → authenticated tenant session (AWCMS-owned; not in this repository) |
| B2: Control Center ↔ OMES job boundary | Local socket / mTLS / pull worker | A remote-controlled service process → a process with host-mutation capability |
| B3: OMES job runner ↔ `bin/omes` | In-process argv construction from an allowlisted operation | Validated job intent → actual host command execution |
| B4: OMES ↔ Hermes runtime | Existing OMES/Hermes boundary (docs/threat-model.md) | Unchanged by this document |

B2 is the boundary this document adds. B1, B3 (already governed by
AGENTS.md §3's "no arbitrary shell" rule and issue #90's allowlist), and
B4 (docs/threat-model.md) are referenced, not redefined.

## 3. Assets

Reuses `docs/threat-model.md` §4's asset list (A1–A9) plus:

- **A10**: Control Center contract payloads (requests, responses, events)
  in transit and in any OMES-side audit log.
- **A11**: the job idempotency/audit store (`<state-dir>/jobs/`,
  `audit.jsonl`) — issue #90.
- **A12**: tenant-scope correctness (a request must never mutate a target
  outside its stated tenant).

## 4. STRIDE threat table

| ID | Threat | STRIDE | Asset(s) | Likelihood | Impact | Mitigation | OMES control | Implementing issue |
|---|---|---|---|---|---|---|---|---|
| CC01 | A `deployment.request` carries a free-form `command`/`args` field that gets executed as shell | Elevation of Privilege, Tampering | A4, A10 | M | H | `deployment.request.schema.json` uses `additionalProperties: false` with a closed `operation` enum; no schema in this contract set has a command/argv field; `scripts/check-contracts.py` fixture `invalid-free-form-command.json` asserts this is rejected specifically for "additional properties not allowed" | implemented-in-OMES (contract); job runner never builds argv from request fields other than the allowlisted operation name (issue #90) | #89, #90 |
| CC02 | A replayed or duplicated request (network retry, double-click, webhook redelivery) executes a destructive operation twice | Tampering | A11, A7 | H | H | Every mutating request requires `idempotency_key`; the job store returns the original job and records a `replayed` audit event on a repeat key, never re-executing (issue #90) | implemented-in-OMES (job runner) | #90 |
| CC03 | A request targets a server/deployment belonging to a different tenant than the caller | Tampering, Elevation of Privilege | A12, A11 | M | H | `tenant_id` is required on every mutating request; the job runner validates the target's recorded tenant against `OMES_JOBS_TENANT_ID`/local state before executing and audits a rejection on mismatch (issue #90) | implemented-in-OMES (job runner) | #90 |
| CC04 | A raw secret (API token, password) is embedded directly in a request/response/event body, log, or audit line | Information Disclosure | A1, A2, A10, A11 | M | H | `secret_ref` object indirection is the only allowed shape behind a secret-like field name; `lib/omes/py/jobs/schema.py`'s `scan_for_raw_secrets()` runs unconditionally on every validated instance; job runner redacts logs/errors before writing audit entries (reuses the redaction pattern of `lib/omes/py/content/reports.py`, issue #68/#84 lineage) | implemented-in-OMES (contract + job runner) | #89, #90, #84 |
| CC05 | The OMES side of this boundary is exposed as a public, unauthenticated listener | Spoofing, Elevation of Privilege | A4, A11 | M | H | §5 of docs/control-center-contracts.md and ADR-0011 decision 2 require local socket / mTLS / pull worker; no listener exists in this repository | not-implemented-yet (no transport exists); documented requirement for whoever implements the transport | #89 |
| CC06 | A destructive operation (`restore`, `rollback`, a stop that removes availability) executes without an operator approval step | Elevation of Privilege, Repudiation | A7, A11 | M | H | Job runner requires `omes job approve <id> --actor <id>` for destructive operations before `run`, unless the operation name is in the documented `OMES_JOBS_AUTO_APPROVE` allowlist policy; approval is audited with actor attribution | implemented-in-OMES (job runner) | #90 |
| CC07 | A job reports success after a timeout or an unconfirmed asynchronous provider/host operation | Repudiation, Tampering | A11, A7 | M | H | Job runner performs a read-back verification (re-running the corresponding status/health command) and compares desired vs. observed state before reporting `succeeded`; a timeout is classified as retryable-or-failed, never success | implemented-in-OMES (job runner) | #90 |
| CC08 | The audit log (`audit.jsonl`) is edited after the fact to hide a rejected/failed operation | Repudiation, Tampering | A11 | L | M | Append-only, hash-chained audit log (same pattern as `lib/omes/py/content/reports.py`'s `audit_append`/`_line_hash`); a chain-integrity check can detect any rewritten line | implemented-in-OMES (job runner) | #90, #84 |
| CC09 | An event (`entitlement.changed`, `backup.completed`, ...) is spoofed or replayed to make OMES believe a state changed that did not | Spoofing, Tampering | A10, A12 | L | M | Every event carries `idempotency_key` and `correlation_id`; OMES treats `entitlement.changed` as consumed-only (never authoritative for its own state, per the ownership matrix) — a spoofed event can at most be ignored, never elevate privilege, because OMES never grants a capability solely because an event arrived | not-implemented-yet (no event consumer exists); documented requirement | #89 |
| CC10 | Job status/health responses leak host filesystem paths, environment details, or raw command stdout/stderr beyond what is necessary | Information Disclosure | A1, A2, A10 | M | M | Job runner caps and redacts any command output surfaced in errors (a bounded, redacted tail, never raw command output); `health-readiness.response` only carries the layers/signals/proves shape from issue #79, never raw process output | implemented-in-OMES (job runner) | #90, #79 |
| CC11 | Mission Control composes records across tenants, or a viewer sees another tenant's servers, jobs, or agents in the 3D scene (issue #264, ADR-0031) | Information Disclosure, Elevation of Privilege | A4, A10, A12 | M | H | A scene is single-tenant by contract (`tenant_id` required; no node or relation spans tenants); AWCMS must authorize per request and apply FORCE RLS on every source query before composing; the scene carries references and bounded fields only (source-map `allowed_fields`) | contract and source map only; AWCMS composition and RLS enforcement are not implemented yet (tracked in #265) | #264, #265 |
| CC12 | Stored XSS or UI injection through a label, Hermes goal, or summary rendered in the scene or object list (e.g. an `<img onerror>` payload) | Tampering, Elevation of Privilege | A4, A10 | M | H | Every label/goal/summary is untrusted text: schema-bounded length, output-encoded, never injected as HTML; the strict CSP (`script-src 'self'`) is not weakened; `fixtures/mission-control-scene-view/valid-04-xss-label.json` carries an `<img onerror>` label so consumers can test encoding | contract and fixture implemented; renderer encoding not implemented yet (tracked in #265) | #264, #265 |
| CC13 | A stale or unreachable source is drawn as healthy (a last-known success shown as current, or a missing source shown as an empty healthy scene) | Spoofing, Repudiation | A7, A11 | M | H | The deterministic state rule: unmapped state is `unknown`; freshness `unknown` is `unknown`; stale keeps `failed`/`warning` and otherwise becomes `stale`; `sources[]` reports `stale`/`unavailable` explicitly; animation never carries state; guard MC4/MC8 reject unmapped upstream states and mis-derived fixtures | implemented-in-OMES (map, rule, guard); AWCMS derivation not implemented yet (tracked in #265) | #264, #265 |
| CC14 | Fabricated or back-dated replay: historical mode presents a state the retained evidence does not prove | Repudiation, Tampering | A11 | M | M | Every replay event references retained evidence (`evidence_kind` + `evidence_id`), is deduplicated and deterministically ordered, is labelled `late_arrival` when it revises history, and gaps are explicit (`not_retained`, `retention_expired`, `source_unavailable`, `before_first_observation`) and never interpolated; `current_only` sources are never back-dated; no retention extension | contract implemented; replay composition not implemented yet (tracked in #266) | #264, #266 |
| CC15 | A forged, replayed, or cross-tenant client action issued from the 3D scene (a crafted request that claims an action the viewer may not take) | Spoofing, Tampering, Elevation of Privilege | A4, A7, A10 | M | H | Action availability is not in the scene contract; AWCMS re-authorizes server-side per selected object and per request, every mutation goes through the existing endpoint with its own `Idempotency-Key`, audit record, and workflow approval, only candidate actions listed in the source map exist, no new operation name is allowed (guard MC7), and historical mode exposes no actions | source map and guard implemented; contextual actions not implemented yet (tracked in #267) | #264, #267 |
| CC16 | Approval bypass: a destructive operation (stop, rollback, backup restore) is approved or executed from the scene without the workflow approval | Elevation of Privilege, Repudiation | A7, A11 | M | H | Approval decisions are made only in the canonical `/admin/approvals` inbox (workflow key `omes_control.destructive_operation`); the scene may only deep-link to it (`approval.open_in_inbox`); there is no second approval surface and Hermes agents are read-only | source map implemented; AWCMS behavior not implemented yet (tracked in #267) | #264, #267 |
| CC17 | Raw sensitive data (prompts, transcripts, reasoning, tool arguments or results, tokens, secrets, shell commands) reaches the renderer through a composed field | Information Disclosure | A1, A2, A10 | M | H | Per-kind `allowed_fields` allowlist, schema `additionalProperties: false` at every object level, registry guard C1 (no command/shell/exec property keys), and guard MC9 (forbidden field terms) | implemented-in-OMES (contracts, map, guard); AWCMS projection not implemented yet (tracked in #265) | #264, #265 |
| CC18 | Denial of service through unbounded history or scene size (an unbounded replay range or a very large node/relation set) | Denial of Service | A10 | M | M | Scene `nodes` max 500 and `relations` max 1000 with explicit `truncated` counts; replay pages max 500 events with a keyset cursor and a capped window; `evidence_gaps` max 64; the browser never loads unbounded history | contract bounds implemented; AWCMS caps and pagination not implemented yet (tracked in #265, #266) | #264, #265, #266 |
| CC19 | The CSP is weakened to make WebGL or a 3D library work (`unsafe-eval`, `blob:` workers, remote CDN scripts or assets) | Elevation of Privilege, Tampering | A4, A10 | L | H | Forbidden by ADR-0031: the renderer must work under the existing strict CSP (`script-src 'self'`, no `blob:`/`worker-src`), assets are bundled same-origin and procedural, a no-WebGL fallback is mandatory, and any client-asset budget change must be measured and justified in the AWCMS repository, never silent | decision recorded; renderer not implemented yet (tracked in #265) | #264, #265 |

## 5. Residual risk

- The transport for B2 (local socket / mTLS / pull worker) is not
  implemented in this repository. Every control above that depends on
  "no public listener" is a documented requirement, not yet an enforced
  one, until a transport lands.
- Tenant isolation (CC03) depends on the Control Center correctly scoping
  `tenant_id` on outbound requests; OMES can only reject a mismatch it can
  detect against its own local state, not prove the Control Center itself
  is honest. This mirrors ADR-0011's rejected-alternative note that OMES
  cannot and should not trust a remote caller's tenant claim without
  local verification.
- This document does not evaluate the AWCMS/awcms-one codebase's own
  security posture (out of scope: it is a separate repository); it only
  evaluates the contract and the OMES-side job runner.

## 6. Related documents

- [docs/threat-model.md](threat-model.md) — the existing OMES/Hermes/Telegram threat model this document extends.
- [docs/control-center-contracts.md](control-center-contracts.md) — the contracts these threats apply to.
- [docs/control-center-and-integrations.md](control-center-and-integrations.md) §10 — the mandatory design gates this document evidences.
- [ADR-0011](adr/0011-control-center-and-provider-boundaries.md).
- [`docs/jobs.md`](jobs.md) — the implemented OMES-side job runner (issue #90), referenced throughout the mitigation column. The HTTP/API transport, tenant database, web RBAC/ABAC, and UI remain outside this repository.
