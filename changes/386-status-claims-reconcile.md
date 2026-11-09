---
issue: 386
type: docs
---
Corrected documentation that contradicted the code: the opt-in active synthetic probe of `omes health inference-endpoint` is now described as implemented by #354 (README, inference-serving architecture, threat-model AI-12) instead of "Not implemented yet (tracked in #292)"; the architecture §18 rows for the correlation and policy-decision envelopes now point at the open follow-ups #356 and #358; documents that called the pull-worker "signed" now state that end-to-end request signing is not implemented yet (tracked in #362); ADR-0030 and the Control Center prototype data generator no longer describe shipped AWCMS work (#249, #200/#201) as open. No behaviour change.
