---
issue: 358
type: changed
---
Pinned the `policy-decision` envelope `reason_code` namespace: the `AI_EGRESS_` prefix is now reserved for the 20 existing codes of `lib/omes/py/privacy/egress_policy.py` (listed as an enum in `contracts/observability/v1/policy-decision.schema.json`, reused rather than duplicated), and any other `AI_EGRESS_*` value is rejected; codes outside the namespace remain an open upper-snake-case set owned by the deciding authority. Existing valid fixtures and `schema_version` `1.0.0` are unchanged. The decision and its security, compatibility and maintainability rationale are recorded in `docs/multi-agent-control-patterns.md` section 4b, with new valid and invalid fixtures and a test that keeps the schema list in sync with the egress evaluator. Emission by authorities and consumption in the job runner, worker or AWCMS are not implemented yet (tracked in #358).
