# Browser transfer wire contracts v1

> **Status:** proposed interfaces, not an implemented transfer service. Issue [#296](https://github.com/ahliweb/omes/issues/296), parent [#294](https://github.com/ahliweb/omes/issues/294); design PR [#307](https://github.com/ahliweb/omes/pull/307).

This directory contains **sanitized metadata-only** JSON Schema wire contracts intended to connect existing OMES health/evidence observations and AWCMS browser transfer projections. They are intentionally **not** schema definitions for credentials, page DOM, screenshots, raw source/target records, arbitrary browser commands, or business/tenant authorization.

- `browser-worker-readiness.schema.json`: optional worker host-readiness evidence. `ready` is a producer claim subject to freshness/identity verification by the authenticated consumer, not authorization to run a browser.
- `transfer-record-event.schema.json`: sanitized per-record event projection. AWCMS remains the authoritative persistent record ledger and independently verifies writes. A transition to `verified_committed` requires separate destination read-back.
- Fixtures `valid-*.json` must pass; `invalid-*.json` must fail for their recorded reason. Check with `python3 scripts/check-contracts.py`.

## Transport/security contract

1. AWCMS checks authenticated worker identity, tenant/job scope, approval, lease, version, replay and idempotency **outside JSON Schema**.
2. Every event includes only a salted/tenant-scoped or otherwise unlinkable record hash; never raw record content or a raw cookie. Salt strategy and collision/retention design belong to the application, not this wire contract.
3. The event is **observational**, not an authorization or model output and not proof of commit until destination read-back and AWCMS reconciliation confirms it.
4. Unknown schema versions, stale/missing worker evidence, unsupported enum values or extra fields are rejected.
5. Human-auth/login, browser action allowlists, model privacy and record persistence are tracked separately in #297–#306. OMES's existing host `operation-request` schema must **not** gain generic `browser_click`, `run_script` or free-form tool actions.
6. No new public listener or new model router is authorized by these files. Verify compatibility before consumer release and pin SHA.

The schema validator supports only the bounded subset documented in [contracts README](../../README.md). `$ref`, `format`, conditionals and other unsupported JSON Schema keywords are deliberately excluded.
