---
issue: 276
type: added
---
`omes agent doctor` now runs the existing read-only isolation-drift check for compose-backend agents and reports it as an `isolationDrift` object (`status` of `ok`, `drift`, `unknown` or `not_applicable`, plus findings and reason) in `--json` output and as an `isolation=` suffix in text output. `drift` and `unknown` (daemon unreachable or inspect failed, never reported as ok) mark the agent not ok with a `warning` field and a `WARN` row in `omes doctor`; non-compose agents report `not_applicable`; doctor's exit code is unchanged and no scheduled run is added yet (tracked in #276).
