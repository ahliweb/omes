---
issue: 70
type: security
---
Add `docs/content-threat-model.md`: a STRIDE threat table for the content
distribution workflow (session/cookie theft, log/artifact leakage,
approval spoofing, stale approval, artifact tampering between approval
and publish, duplicate publish on restart, partial platform failure,
rate limits/ToS), plus operator-responsibility and platform-ToS notes.
One cross-reference row each added to `docs/threat-model.md` (T40) and
`docs/security.md` (§1). Add
`tests/py/content/test_orchestrator_e2e.py` and
`tests/integration/content_orchestrator.bats`: end-to-end orchestrator
tests against mocked workers only (`tests/fixtures/content/fake-worker.py`,
the `generic_browser` worker's `manual_stub` driver) covering duplicate
detection, restart/resume, partial platform failure, worker timeout,
rate-limit backoff, manual-review (never auto-retried), approval
authorization/staleness/hash-mismatch, cancellation, session-directory
permissions, and a planted-canary-secret check across state/reports/
exports/audit. Fixes a latent bug where `jobs.publish_job()` raised
`InvalidTransitionError` instead of the intended `ApprovalError` when an
`approved` job's approval was invalid, and where `omes content publish`'s
exit code was computed after auto-archiving masked a `failed` outcome as
`archived`. Real-platform tests remain opt-in
(`OMES_TEST_REAL_PLATFORM=1`) and are skipped by default; none exist yet.
