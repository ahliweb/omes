# Browser transfer wire contracts v1

> **Status:** proposed interfaces, not an implemented transfer service. Issue [#296](https://github.com/ahliweb/omes/issues/296), parent [#294](https://github.com/ahliweb/omes/issues/294); design PR [#307](https://github.com/ahliweb/omes/pull/307).

This directory contains **sanitized metadata-only** JSON Schema wire contracts intended to connect existing OMES health/evidence observations and AWCMS browser transfer projections. They are intentionally **not** schema definitions for credentials, page DOM, screenshots, raw source/target records, arbitrary browser commands, or business/tenant authorization.

- `browser-worker-readiness.schema.json`: optional worker host-readiness evidence. `ready` is a producer claim subject to freshness/identity verification by the authenticated consumer, not authorization to run a browser.
- `transfer-record-event.schema.json`: sanitized per-record event projection. AWCMS remains the authoritative persistent record ledger and independently verifies writes. A transition to `verified_committed` requires separate destination read-back.
- Fixtures `valid-*.json` must pass; `invalid-*.json` must fail for their recorded reason. Check with `python3 scripts/check-contracts.py`.

## Producers

| Contract | Producer | Status |
|---|---|---|
| `browser-worker-readiness` | `omes health browser-worker --json` ([`lib/omes/py/health/browser_worker.py`](../../../lib/omes/py/health/browser_worker.py), issue [#298](https://github.com/ahliweb/omes/issues/298); behaviour, state precedence and reason codes in [docs/browser-worker-readiness.md](../../../docs/browser-worker-readiness.md)) | Implemented as a read-only host-evidence producer. It validates every document against this schema before printing it and never emits an off-contract document. Because per-job isolation evidence is Not implemented yet (tracked in #298, depends on #276 / PR #282), `session_isolation` is reported `unknown` and `state` is never `ready` on a real host today. It exits `4` and prints nothing when a tenant, server or worker id is missing (it never invents one). |
| `transfer-record-event` | none in OMES | AWCMS/worker-owned projection; OMES does not produce or store these events. |

The producer fills `reason_codes` with a stable vocabulary (defined and tested in `browser_worker.REASONS`) and uses `browser_version` as a label (`chromium-<version>`, `chrome-<version>` or `playwright-chromium-<revision>`), never raw `--version` output. It does not read browser profiles, cookies or heartbeat contents.

## Transport/security contract

1. AWCMS checks authenticated worker identity, tenant/job scope, approval, lease, version, replay and idempotency **outside JSON Schema**.
2. Every event includes only a salted/tenant-scoped or otherwise unlinkable record hash; never raw record content or a raw cookie. Salt strategy and collision/retention design belong to the application, not this wire contract.
3. The event is **observational**, not an authorization or model output and not proof of commit until destination read-back and AWCMS reconciliation confirms it.
4. Unknown schema versions, stale/missing worker evidence, unsupported enum values or extra fields are rejected.
5. Human-auth/login, browser action allowlists, model privacy and record persistence are tracked separately in #297–#306. OMES's existing host `operation-request` schema must **not** gain generic `browser_click`, `run_script` or free-form tool actions.
6. No new public listener or new model router is authorized by these files. Verify compatibility before consumer release and pin SHA.

The schema validator supports only the bounded subset documented in [contracts README](../../README.md). `$ref`, `format`, conditionals and other unsupported JSON Schema keywords are deliberately excluded.
