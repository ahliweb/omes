---
issue: 216
type: added
---
Add `omes health ai-privacy`, a read-only AI privacy posture/egress evidence report (bounded
policy/destination/isolation metadata, PASS/FAIL/WARN/BLOCKED with stable reason codes) that never
captures raw prompts, responses, or credentials, and reports FAIL on drift from a restricted
local-only posture to a cloud-capable destination. `cloud_fallback_enabled` is detected from the
non-secret `fallback_model` / `fallback_providers` Hermes config keys via the existing vetted
`hermes config get` allowlist: a cloud-destined `fallback_model` reports `enabled`, both keys
confirmed unset reports `disabled`, and anything ambiguous — including a present but deliberately
unparsed `fallback_providers` list — reports `unknown`, never `disabled`.
