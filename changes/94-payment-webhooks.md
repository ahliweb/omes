---
issue: 94
type: added
---
Add OMES-side provider-neutral payment gateway adapter, webhook envelope, proration, suspension-automation-policy, and outbox-event contracts, plus a stdlib reference (`lib/omes/py/jobs/payments.py`) implementing HMAC-SHA256 signature verification, replay/timestamp checks, half-up-rounding proration, and approval-gated automation for destructive webhook-triggered actions.
