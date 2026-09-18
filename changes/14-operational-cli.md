---
issue: 14
type: added
---
Implement `omes doctor` (health checks with OK/WARN/FAIL, optional per-module `module_doctor` hook) and `omes update` (git fetch + fast-forward-only, then re-runs `check`), add `--json` support to every command (`install`, `help`, and every error path across `check`/`install`/`uninstall`), polish `omes status` (profile, backup count, last log path), and add `docs/cli.md` as the full CLI reference.
