---
issue: 218
type: security
---
Add a cross-cutting AI privacy boundary regression and exfiltration-resistance suite
(`tests/py/privacy/test_privacy_boundary_regression.py`, 50 negative tests) covering
restricted-to-cloud denial, fail-closed unknowns, credential rejection/redaction,
prompt/transcript fields being refused by the AI contracts, silent cloud fallback, stale
evidence, canary-free logs/state/backups, prompt-injection invariance, and the jobs operation
allowlist; the suite found and this change fixes three real gaps it exposed — a
`sk_live_`-shaped value could be echoed through `hermes_version_reference.value`, a malformed
`observed_at` was echoed verbatim as a healthy `last_verified_at` (violating the evidence
schema's own pattern), and a field literally named `private_key` was not covered by the secret
name gate in `lib/omes/py/jobs/schema.py`/`audit.py`. Documented in `docs/testing.md` section 7,
including the RAG/embedding coverage that is not implemented yet.
