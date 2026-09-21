---
issue: 176
type: minor
---
Delegate profile and full-runtime backup to Hermes native commands (`hermes profile export/import`, `hermes backup/import`, ADR-0020). Support recovery classes (`portable-profile`, `full-runtime-dr`, `omes-host`), sensitive credential opt-in enforcement (`--allow-sensitive-credentials`), pre-restore recovery point snapshotting, SHA-256 integrity verification, post-restore health verification via `hermes doctor`, and backward compatibility for legacy archives.
