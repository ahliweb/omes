---
issue: 7
type: fixed
---
Fix `security-baseline` module_check hard-failing on hosts/containers without systemd/systemctl (WARN instead, matching the project's existing detect-and-warn pattern) — this was blocking the CI Compatibility matrix.
