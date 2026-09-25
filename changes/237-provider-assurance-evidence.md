---
issue: 237
type: security
---
Add a versioned `provider_assurance` schema recording the section 5 provider due-diligence checklist (retention, human access, subprocessors, residency/transfer, and the rest) as structured status-plus-evidence-reference fields, and require it in `lib/omes/py/privacy/egress_policy.py` so a missing or incomplete record fails closed to `deny` for `cloud_sanitized` regardless of provider posture or sanitization evidence, per threat AI-07 (#237).
