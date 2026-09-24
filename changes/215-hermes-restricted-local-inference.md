---
issue: 215
type: added
---
Add an opt-in `hermes-restricted` OMES module that enforces a restricted/local-only Hermes
deployment posture: `module_check`/`module_apply` refuse before execution if the configured
model endpoint is not local/private (per the #214 egress-policy contract, evaluated via
`lib/omes/py/privacy/restricted_posture.py`, never a second decision matrix), `module_apply`
denies outbound network access from the Hermes system-gateway unit by default, and
`module_verify` reports drift toward a cloud endpoint as a hard failure rather than a silent
fallback. Also detects a configured legacy Hermes `fallback_model` as an independent restricted-
posture violation, and writes exactly the `ai.local_only_posture.*`/`ai.privacy.expected_posture`
state keys and vocabulary issue #216's evidence surface (`omes health ai-privacy`) expects.
