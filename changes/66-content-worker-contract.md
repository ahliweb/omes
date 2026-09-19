---
issue: 66
type: feature
---
Add the isolated browser-session worker contract for the content
distribution workflow: `lib/omes/py/content/workers/base.py` (shared
JSON transport, typed failure states `retryable|nonretryable|uncertain|
needs_login`, and a filesystem path-boundary check every worker call
enforces against an explicit `allowed_paths` allowlist), the
`generic_browser` worker skeleton (`prepare`/`bootstrap-session`/
`publish`/`verify`/`collect-evidence`/`revoke-session`) driven by a
pluggable, operator-installed browser driver (default: an evidence-only
`manual_stub`, no real browser dependency ships in OMES;
`OMES_CONTENT_BROWSER_DRIVER` selects a real one), and `omes content
plan|publish|session login|session revoke` wired to
`jobs.plan_job`/`publish_job`/`verify_job`. Each platform's browser
profile lives under `content/sessions/<platform>/` at mode `0700`,
created by the worker itself; manual login (`session login`) is a
separate operation from publishing and never publishes.
