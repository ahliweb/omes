---
issue: 370
type: changed
---
`AGENTS.md` §4 now places product and feature work on the portfolio development flow v2.0.0 (network/infrastructure profile): the operator-facing interface (CLI commands and flags, `--json` output, configuration schema, Control Center screens) is defined and validated with `--dry-run` or a stub before implementation, then implemented as interface → host logic → integration in the disposable matrix. Documentation only; no behaviour change.
