---
issue: 259
type: security
---
Fixed the automated upstream-drift report's Hermes finding always showing "Current Baseline: n/a" (a lexicographic-sort bug that let a logical-boundary capability's `n/a` placeholder outrank a real pinned version), raised the verified-and-pinned Hermes Agent installer baseline to v2026.9.24 (v0.21.5, verified installer SHA-256 recorded in `lib/omes/versions.sh` after reviewing the downloaded installer for new remote fetches, sudo, or unverified execution), raised the Graphify baseline to 0.9.71 after re-verifying extraction, export, hook, and Hermes skill-install behavior against a live install, and raised the documented-but-not-live-tested Coolify adapter baseline to v4.3.23 after confirming via the upstream changelog that none of its `POST`/`GET` breaking changes or the `host_path` removal affect the read/deploy/rollback endpoints OMES's Coolify client actually calls.
