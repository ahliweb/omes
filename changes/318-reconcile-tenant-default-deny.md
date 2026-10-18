---
issue: 318
type: security
---
`omes job reconcile` is now default-deny on tenant scope, matching `omes job submit` (#279): when no local tenant is configured (`OMES_JOBS_TENANT_ID` unset or empty) both the batch run and `--job <id>` refuse with `tenant_not_configured` (`TenantNotConfiguredError`, exit 1, `{"error": "tenant_not_configured", ...}` under `--json`) before any job is loaded, read back, transitioned, saved or audited, where previously an unset variable made a batch run reconcile every running job of every tenant; jobs whose tenant does not match the configured tenant are still skipped (batch) or refused as `cross_tenant_rejected` (`--job`), tenant resolution now goes through the single `store.resolve_local_tenant()` shared with `store.submit()`, and `docs/jobs.md` documents the `enrolled_tenant_id` over `OMES_JOBS_TENANT_ID` precedence.
