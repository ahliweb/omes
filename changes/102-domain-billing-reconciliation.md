---
issue: 102
type: added
---
Add domain product/checkout-snapshot/renewal-reminder/reconciliation-rule/billing-event-dedupe/refund-eligibility/report contracts and a stdlib billing-rules module (`lib/omes/py/domains/billing.py`) enforcing payment-before-registration, non-refundable-after-success, and cross-source duplicate-event dedupe, with end-to-end fake-provider tests for a Cloudflare international flow and an SRS-X `.id` flow.
