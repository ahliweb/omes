---
issue: 15
type: added
---
Add a reproducible container installation/regression test matrix (`scripts/test-matrix.sh`: fresh install, idempotent re-run, offline, partial-failure, reboot-proxy, and rollback scenarios) plus a VM matrix (`tests/vm/`) for real reboot and manual Mint desktop verification, wired into `.github/workflows/compatibility.yml`, and documented in `docs/testing.md`.
