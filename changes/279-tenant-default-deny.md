---
issue: 279
type: security
---
`omes job submit` now enforces the documented default-deny tenant scope: when `OMES_JOBS_TENANT_ID` is unset (or empty) every submission is refused with the new `tenant_not_configured` error (`TenantNotConfiguredError`, a `CrossTenantError` subclass) before any job record, idempotency entry, or audit line is written; previously an unconfigured host accepted any `tenant_id`. Upgrade note: hosts that ran `omes job submit` without `OMES_JOBS_TENANT_ID` must now set it to their enrolled tenant. The pull worker is unaffected because it uses the tenant it was enrolled under (`omes worker enroll --tenant`). `OMES_JOBS_SERVER_ID` remains optional, and `docs/configuration.md`, `docs/jobs.md`, and `docs/security.md` no longer claim the rejection is audited.
