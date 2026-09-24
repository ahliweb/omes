---
issue: 216
type: added
---
Add `omes health ai-privacy`, a read-only AI privacy posture/egress evidence report (bounded
policy/destination/isolation metadata, PASS/FAIL/WARN/BLOCKED with stable reason codes) that never
captures raw prompts, responses, or credentials, and reports FAIL on drift from a restricted
local-only posture to a cloud-capable destination.
