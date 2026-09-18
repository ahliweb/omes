---
issue: 17
type: added
---
Add automated disaster-recovery tests for failed mid-profile installs, a broken Hyprland session, a failed Hermes gateway, a corrupted managed file, and a corrupted/deleted state file (`tests/integration/dr-*.bats`, plus a `dr` scenario in `scripts/test-matrix.sh`), and document every runbook (including SSH lockout, lost sudo, a deleted state directory, and off-host backup restores) with data-loss boundaries in `docs/disaster-recovery.md` and `docs/rollback.md`'s new "Tested scenarios" section.
