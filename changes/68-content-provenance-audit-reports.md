---
issue: 68
type: feature
---
Add `lib/omes/py/content/reports.py`: an append-only, hash-chained audit
log (`state/audit.jsonl`, redacted of `TOKEN|KEY|SECRET|PASSWORD|COOKIE`
patterns, with `omes content` never reading `content/sessions/`),
never-overwritten JSON/Markdown job reports (`report.json`/`report.md`,
then `report-2.*`, `report-3.*`, ...), automatic archive-on-terminal-state
(`uploaded/`, `failed/`, `review/<job-id>/`), and `omes content
report|export|prune`. `prune` deletes only archived media/reports and
never touches `sessions/` or the audit log.
