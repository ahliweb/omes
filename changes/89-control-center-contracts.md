---
issue: 89
type: added
---
Define the OMES Control Center integration boundary: an ownership matrix, versioned JSON Schema API/event contracts under `contracts/control-center/v1/` with valid/invalid fixtures, a stdlib-only fixture validator (`scripts/check-contracts.py`, wired into CI and `scripts/lint.sh`/`tests/run.sh`), and a STRIDE threat model for the new trust boundary.
