---
issue: 235
type: security
---
Add a fail-closed, default-deny preflight gate (`lib/omes/py/hermesbackup/restricted_scope.py`) that refuses to create or restore a Hermes backup carrying Restricted-class prompt/session/context data (the native `portable-profile`/`full-runtime-dr` recovery classes, or the legacy `sessions`/`memory` classes) with the stable reason code `BACKUP_RESTRICTED_SCOPE_REQUIRES_OPT_IN` unless the operator passes the new, explicit `--allow-restricted-scope` opt-in, and repoint `omes agent apply`'s automatic pre-mutation backup step at the safe `omes-host`/`config`+`skills` scope so routine lifecycle commands never trigger the gate as a silent side effect.
