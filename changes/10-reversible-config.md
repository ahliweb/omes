---
issue: 10
type: added
---
Implement `omes backup`, `omes restore` (checksum-verified, offline-safe, refuses a corrupt MANIFEST with exit 9), and `omes uninstall` (reverse-dependency-order rollback that restores files OMES modified, removes files OMES created fresh, and only purges packages with `--purge-packages`), plus `lib/omes/restore.sh` and `docs/rollback.md`.
