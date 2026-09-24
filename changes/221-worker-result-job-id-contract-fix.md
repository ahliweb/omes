---
issue: 221
type: fixed
summary: remove the unimplementable job_id from worker-result.request and give worker-poll.response.job a real schema
---

### Summary of changes

`worker-result.request.schema.json` required a `job_id` that nothing in
the `v1` contract set ever gave the worker to send (`worker-poll.response.job`
was an untyped object with no shape; `operation-request.schema.json` has
no `job_id` property). `lib/omes/py/jobs/worker.py` fabricated one
(`f"job_auto_{secrets.token_hex(8)}"`) purely to satisfy the schema.

1. **`contracts/control-center/v1/worker-result.request.schema.json`** —
   removed `job_id` from both `required` and `properties`. Correlation
   with the Control Center is documented as binding on `idempotency_key`
   (plus `correlation_id`, `tenant_id`, `server_id`, and the leasing
   `worker_id`) only.
2. **`contracts/control-center/v1/worker-poll.response.schema.json`** —
   gave `job` a real, closed schema (properties for `tenant_id`,
   `correlation_id`, `idempotency_key`, `actor`, `operation`, `target`,
   `permission`, plus optional `backup_id`/`rollback_ref`/`parameters`;
   `additionalProperties: false`; explicit `required`), matching the
   shape `lib/omes/py/jobs/worker.py` already validates `job` against at
   runtime (`operation-request.schema.json`, duplicated inline since this
   repository's validator has no `$ref` support).
3. **`lib/omes/py/jobs/worker.py`** — `_submit_result` no longer fabricates
   or sends a `job_id` in the `worker-result.request` wire payload; the
   local job-store id (used only for local audit/logging and this
   function's own return value) is now kept strictly separate from what
   is sent to the Control Center.
4. **Fixtures** (`contracts/control-center/v1/fixtures/`) — updated
   `worker-result.request/valid-01.json` and `invalid-operation.json` to
   drop `job_id`; added `worker-result.request/invalid-job-id-present.json`
   (a result that still sends `job_id` now fails
   `additionalProperties: false`) and
   `worker-poll.response/valid-02-job-available.json` /
   `invalid-job-missing-idempotency-key.json` (a `job_available` response
   whose `job` lacks `idempotency_key` now fails validation).
5. **Docs** — `docs/control-center-contracts.md` §2.5a documents the new
   `job` shape and the `job_id` removal; `contracts/README.md`
   "Compatibility rules" documents this as an explicit, maintainer-approved
   exception to the additive-only `v1` policy, amending `v1` in place
   rather than cutting `v2` because `contracts/control-center/v1` has
   exactly one known consumer (AWCMS), which pins this repository's
   contracts by commit hash and runs a CI drift gate on that pin (issue
   #197) — the break is detectable and cheap to re-pin.
6. **Tests** (`tests/py/jobs/test_worker.py`) — updated the mocked
   `/api/v1/worker/result` handlers (the Control Center's own
   acknowledgement still has its own, server-minted `job_id`, unrelated
   to the request) and added assertions that the submitted result payload
   never carries `job_id`.

Consumers pinning `contracts/control-center/v1/` by commit hash (AWCMS)
must re-pin/re-vendor after this change and drop any code that sends
`job_id` on `worker-result.request`.
