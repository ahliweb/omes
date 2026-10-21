---
issue: 329
type: ci
---
`scripts/ci-local.sh` now passes GNU `sed -u` to its log redaction only when the local `sed` accepts it, and falls back to plain `sed` otherwise. The runner and its bats suite therefore work under BusyBox `sed` in the Alpine-based bats image; redaction output is unchanged.
