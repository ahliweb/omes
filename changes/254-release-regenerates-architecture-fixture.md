---
issue: 254
type: fixed
---
scripts/release.sh now regenerates and stages the architecture-capabilities-view fixture right after writing VERSION, so a release PR no longer fails the Architecture boundaries CI check with a stale omes_version until someone fixes it up by hand.
