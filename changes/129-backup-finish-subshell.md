---
issue: 129
type: fixed
---
Fix `backup_finish` losing its `OMES_CURRENT_BACKUP_DIR` cleanup when
captured via `$(...)` (it now also exports `OMES_LAST_BACKUP_ID` and
writes a `.finished` marker that survives the subshell), and make
`restore_backup` refuse to restore from a still-open current session,
snapshot its MANIFEST before iterating it, and always open a fresh
pre-restore-backup session instead of reusing a stale one.
