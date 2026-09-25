---
issue: 234
type: security
---
Add opt-in retention and rotation for AI-privacy posture evidence OMES itself persists: `omes health ai-privacy` remains a point-in-time report that persists nothing by default, `--persist` re-validates the bounded evidence object against the published posture-evidence schema (which also runs the existing secret-value/secret-field scan) and refuses to write anything that fails, writing only a validated record to the OMES-owned `<state-dir>/ai-privacy-evidence/` directory (mode 0700/0600), and the new `omes health ai-privacy prune [--max-age-days] [--max-count] [--dry-run] [--json]` command deletes expired or excess records confined strictly to that directory, refusing symlinks and any file not matching its own naming pattern, idempotently and with dry-run support.
