---
issue: 15
type: fixed
---
`module.<name>.applied_at` now records the first successful apply and stays stable across idempotent re-runs; a new `module.<name>.last_run_at` key moves on every run. The container matrix's `rerun` scenario now gates on `applied_at` being unchanged.
