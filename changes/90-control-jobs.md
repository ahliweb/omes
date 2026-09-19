---
issue: 90
type: added
---
Add `omes job` (`lib/omes/py/jobs/`): an idempotent, audited control-job runner with schema validation, an approval gate for destructive operations, a fixed operation→command mapping (never a shell), retry classification, and read-back reconciliation, backed by an append-only hash-chained audit log.
