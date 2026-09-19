---
issue: 93
type: added
---
Add OMES-side manual invoicing and billing ledger contracts (billing profile, invoice with immutable price-snapshot lines, credit, adjustment, payment record, refund record, tax metadata, currency) with integer-minor-unit money, a data-driven invoice state machine, and a stdlib reconciliation helper (`lib/omes/py/jobs/ledger.py`) that rejects currency mismatches and duplicate payment/refund confirmations.
