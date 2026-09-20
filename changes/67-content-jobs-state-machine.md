---
issue: 67
type: added
---
Add the content job state machine (`lib/omes/py/content/jobs.py`): a
validated transition table, bounded exponential backoff, uncertain-vs-
confirmed-failure classification of worker results, artifact-hash-bound
approvals with staleness expiry, and `omes content approve|reject|retry|
cancel|resume|reconcile`. `resume` re-verifies jobs stuck in
`publishing`/`verifying` after a restart and never re-publishes without an
operator-confirmed retry. Adds `tests/fixtures/content/fake-worker.py`
implementing the #66 worker contract for tests.
