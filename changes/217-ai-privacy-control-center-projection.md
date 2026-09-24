---
issue: 217
type: added
---
Add Control Center contracts (`ai-privacy-posture-view`, `ai-egress-approval.request/response`,
and two events) and `lib/omes/py/privacy/posture_projection.py` so AWCMS can display and govern AI
privacy posture and policy decisions from sanitized #214/#216 evidence — classification mode,
destination class, decision, reason code, evidence freshness, and authority — without raw
prompts, transcripts, restricted data, or provider credentials. Cross-tenant reads and the one
owner-approval path are gated by a pure, independent authorization backstop; `RESTRICTED ->
cloud_sanitized` has no approval path. AWCMS-side consumption is not implemented yet (tracked in
#217).
