---
issue: 168
type: ci
---
Enforce linear, verified release provenance: `scripts/release.sh` now requires a clean `main`, green CI on the release commit, exact commit-to-tag binding and a dry-run mode, and publishes GitHub Releases with read-back verification.
