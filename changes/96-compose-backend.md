---
issue: 96
type: added
---
Add a rootless Docker Compose isolation backend (`spec.backend: "compose"`) for `omes agent`, alongside the existing systemd MVP, with a rootless-Docker preflight gate, deterministic compose.yaml rendering, and apply/status/health/restart/rollback/remove lifecycle support.
