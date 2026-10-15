---
issue: 261
type: added
---
Add the staged rescue-ai/v1 bounded evidence contract and OpenCode Go/Ventoy integration boundary without adding an ISO builder or second OMES AI router. The contract fails closed on cloud AI egress: a completed cloud analysis is rejected for restricted data, for an unknown destination, and for public/internal/confidential data without an `egress_decision_ref` (plus `approval_id` for confidential), consistent with the AI data privacy rules and ADR-0029.
