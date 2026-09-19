---
issue: 97
type: added
---
Add contracts (`contracts/coolify/v1/`) and a fake-provider Coolify adapter (`lib/omes/py/coolify/`) for the optional multi-server Coolify deployment backend: a fail-closed OMES↔Coolify mapping, idempotent apply/status/health/redeploy/rollback, observed-state/drift/reconciliation contracts that keep Coolify's external state from ever overwriting OMES logical policy, and a network-disabled-by-default `client.py`. No live HTTP integration or manifest/`omes job` wiring yet - see `docs/coolify-adapter.md`.
