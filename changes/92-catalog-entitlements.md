---
issue: 92
type: added
---
Add OMES-side catalog (product/plan/add-on/version/price/billing-cycle/resource-policy/backend-eligibility) and subscription/entitlement contracts, a data-driven state machine (`contracts/control-center/v1/subscription.states.json` + `lib/omes/py/jobs/states.py`), and a pure, server-side entitlement evaluator (`lib/omes/py/jobs/entitlement.py`) that never stops a healthy existing deployment on suspension unless a resource policy explicitly says so.
