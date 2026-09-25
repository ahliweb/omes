# Hermes data-class backup and restore

> Status: describes the actual repository state. This document covers
> `omes agent-backup` (issue [#82](https://github.com/ahliweb/omes/issues/82)),
> implemented in `lib/omes/py/hermesbackup/` and wired in via
> `lib/omes/cmd/agent-backup.sh` (the extension-command pattern in
> `lib/omes/cmd/README.md`). It complements the general backup/restore
> model in [docs/rollback.md](./rollback.md), which covers OMES's own
> managed-path backups; this document is specific to Hermes's own data
> under `$HERMES_HOME`. See
> [docs/hermes-deployment-guide.md §12](hermes-deployment-guide.md#12-backup-classes)
> for the condensed runbook version.

## 1. Why a separate tool from `omes backup`/`omes restore`

`omes backup`/`omes restore` (`lib/omes/backup.sh`, `lib/omes/restore.sh`)
back up the files a **module** registers with `omes_manage_path` -
config/PATH drop-ins, package lists, etc. They deliberately never touch
`$HERMES_HOME/.env` (docs/architecture.md's secret boundary) and have no
concept of Hermes's internal data classes.

`$HERMES_HOME` holds several kinds of data with very different
sensitivity and recovery needs - some of it (skills, config) is safe and
useful to back up by default; some of it (session transcripts, long-term
memory) an operator should opt into deliberately; and some of it
(credentials) must never be swept into a generic backup. `omes
agent-backup` exists to make that distinction explicit rather than
leaving it to `omes backup`'s generic, all-managed-paths model.

## 2. Verified `$HERMES_HOME` layout (source: upstream Hermes docs)

Fetched from
[hermes-agent.nousresearch.com/docs/user-guide/configuration](https://hermes-agent.nousresearch.com/docs/user-guide/configuration)
and [hermes-agent.nousresearch.com/docs/](https://hermes-agent.nousresearch.com/docs/)
on 2026-09-19:

| Path (relative to `$HERMES_HOME`) | What it is |
| --- | --- |
| `config.yaml` | Primary settings: model, terminal backend, compression, memory limits, toolsets |
| `.env` | API keys, secrets, environment variables |
| `auth.json` | OAuth provider credentials (Nous Portal, etc.) |
| `SOUL.md` | Primary agent identity (slot #1 in system prompt) |
| `memories/` | Persistent memory storage, including `MEMORY.md` and `USER.md` |
| `skills/` | Agent-created skills (managed via the `skill_manage` tool) |
| `cron/` | Scheduled jobs |
| `sessions/` | Gateway sessions storage |
| `state.db` | SQLite: sessions, messages, gateway routing |
| `logs/` | `errors.log`, `gateway.log` (secrets auto-redacted upstream) |
| `cache/` | e.g. `cache/terminal` - session temporary artifacts |
| `state-snapshots/` | Hermes's own pre-update backup snapshots |
| `backups/` | Hermes's own full-`HERMES_HOME` zips + config history - **not** covered by any OMES class (see §3) |
| `modal_snapshots.json` | Modal backend filesystem snapshots |
| `verification_evidence.db` | Coding verification ledger (when `verify_on_stop` is enabled) |

Every path in `lib/omes/py/hermesbackup/classes.py` is one of the above;
none are guessed. If a path does not exist on a given install (e.g. no
`cron/` yet), it is silently skipped rather than treated as an error.

## 3. Recovery classes and upstream delegation (ADR-0020)

Under ADR-0020 (issue [#176](https://github.com/ahliweb/omes/issues/176)), OMES delegates profile and runtime backup directly to upstream Hermes CLI commands, while retaining host-level recovery and backward compatibility for legacy archives:

| Recovery Class | Upstream Command | Description | Sensitive Credentials? | Restricted-scope session data? |
| --- | --- | --- | --- | --- |
| `portable-profile` | `hermes profile export <profile> --output <path>` | Exports an isolated, portable profile archive containing skills, configuration, memories, and session state. | Excluded by upstream design | **Always included** by upstream design — requires `--allow-restricted-scope` (issue #235) |
| `full-runtime-dr` | `hermes backup --output <path>` | Full disaster recovery archive containing complete runtime state, databases, and credentials. | **Yes** — requires `--allow-sensitive-credentials` | **Always included** (superset of portable-profile) — requires `--allow-restricted-scope` |
| `omes-host` (default) | Managed-path backup engine | OMES host-level configuration, drop-ins, and legacy data classes (`config`, `skills`, `memory`, `sessions`, `runtime-state`, `secrets`). | Mode `0600`; requires `--allow-sensitive-credentials` or `--include-secrets` | Only if `--class memory`/`--class sessions` explicitly requested — same `--allow-restricted-scope` gate |

`omes agent-backup create` with no arguments at all uses the `omes-host` engine's own default classes (`config`, `skills` — see `classes.py` `DEFAULT_CLASSES`), which never contain prompt/session/context data and therefore never require `--allow-restricted-scope`.
`--recovery-class portable-profile`/`full-runtime-dr` remain available, but (issue #235) both always require the separate `--allow-restricted-scope` opt-in described in section 3a below — they are no longer creatable (or restorable) as a silent default, because upstream Hermes documents both as always including session state.
Existing archives without `format: native-*` are classified as `legacy-omes` and remain fully readable, verifiable, and restorable.

### 3a. Restricted-scope preflight gate (issue #235)

`docs/ai-data-privacy-and-model-security.md` section 12 states that Restricted-class prompt/session/context data must never silently enter a default backup. This is now an enforced, fail-closed control in `lib/omes/py/hermesbackup/restricted_scope.py`, not just operating guidance:

- **What is gated.** Two closed, upstream-documented lists (never guessed, never content-inspected):
  - Native recovery classes that upstream Hermes documents as always including session state: `portable-profile`, `full-runtime-dr` (`RESTRICTED_RECOVERY_CLASSES`).
  - Legacy classes whose declared paths hold prompt/session/context data per section 2's table: `sessions` (`sessions/`, `state.db`), `memory` (`memories/`) (`RESTRICTED_LEGACY_CLASSES`).
- **What is NOT gated by this control**: `secrets` (already required `--include-secrets` before #235 — a separate, pre-existing control for credentials, not prompt/session data), and `runtime-state`/`config`/`skills` (not primarily prompt/session data by design).
- **Enforcement point.** `create()`/`create_legacy()`/`create_native()` check BEFORE any mutation (including `--dry-run`, which reports the same refusal a real create would). `restore()` checks the same way before importing an archive back onto a live `$HERMES_HOME`.
- **The opt-in.** `--allow-restricted-scope` (CLI) / `allow_restricted_scope=True` (Python). Distinct from `--allow-sensitive-credentials`, which covers a different concern (credentials) — passing one never satisfies the other.
- **Stable reason code.** `BACKUP_RESTRICTED_SCOPE_REQUIRES_OPT_IN`, returned in the `BackupError` message and safe to grep in scripts/CI (never a generic string).
- **Fail-closed, not silent exclusion.** A gated request is refused outright (raises `BackupError`) rather than silently dropping the Restricted-scope class and continuing — the operator must re-run with the explicit flag or a narrower class list.
- **`restricted_scope_included`** is recorded in every session's `META`/`--json` output and `list`/`inventory` rows (`true`/`false`), so an operator or Control Center projection can audit which existing backups carry this data without re-deriving it.
- **Enforcement is by declared scope only** — never by opening/parsing archive content. OMES never reads `.hermes/` or `messages.db` directly (AGENTS.md constraint); the native recovery classes are gated because ADR-0020 itself documents what `hermes profile export`/`hermes backup` produce, not because OMES inspected the resulting artifact.
- **`omes agent apply`'s automatic pre-mutation backup step** (`lib/omes/py/agent/cli.py`'s `_maybe_backup`) now explicitly pins `--recovery-class omes-host` rather than falling through to the native default — this was the actual "default backup" gap: every `apply`/`update`/`rollback` silently created a `portable-profile` backup (including session state) as a side effect. An operator who wants `apply` to also snapshot session state must run `omes agent-backup create --recovery-class portable-profile --allow-restricted-scope` manually — a separate, explicit, reviewed action, never a side effect of routine lifecycle commands.

## 4. Commands

```
omes agent-backup create [--recovery-class CLASS] [--profile NAME]
                         [--allow-sensitive-credentials] [--include-secrets]
                         [--allow-restricted-scope]
                         [--class NAME]... [--dry-run] [--json] [--hermes-home PATH]
omes agent-backup list [--json]
omes agent-backup inventory [--json]
omes agent-backup verify <timestamp> [--json]
omes agent-backup restore <timestamp> [--recovery-class CLASS] [--profile NAME]
                                       [--allow-sensitive-credentials] [--restore-secrets]
                                       [--allow-restricted-scope]
                                       [--class NAME]... [--dry-run] [--yes]
                                       [--force-home] [--hermes-home PATH] [--json]
```

- **`create`**: writes `<state-dir>/backups/hermes/<timestamp>/` containing:
  - For native backups: `<profile>.hermes-profile.tar.gz` or `hermes-backup.tar.gz`, `MANIFEST`, and `META` (recording `format`, `recovery_class`, `profile`, `sha256`, `artifact_size`, `sensitive`, `restricted_scope_included`, `hermes_version`, and `timestamp`).
  - For legacy backups: `archive.tar`, `MANIFEST`, and `META` (also recording `restricted_scope_included`).
  Session directory permissions are set to `0700`; files are mode `0600`. Retention follows `OMES_BACKUP_KEEP` (default 10 sessions).
- **`--allow-sensitive-credentials`** (or `--include-credentials` / `--include-secrets`): required for `full-runtime-dr` backups and when restoring sensitive credentials.
- **`--allow-restricted-scope`** (issue #235): required to create or restore `portable-profile`/`full-runtime-dr`, or the legacy `sessions`/`memory` classes — see section 3a. A separate, distinct opt-in from `--allow-sensitive-credentials`; passing one never satisfies the other.
- **`--dry-run`**: previews the recovery class and format without mutating the filesystem or exporting archives. A `--dry-run` of a Restricted-scope backup without `--allow-restricted-scope` is refused with the same reason code as a real create, not silently previewed.
- **`list` / `inventory`**: displays all backup sessions with their format (`native-hermes-profile`, `native-hermes-runtime`, or `legacy-omes`), recovery class, target profile, sensitivity status, and `restricted_scope_included`.
- **`verify <timestamp>`**: validates SHA-256 checksums of native artifacts and checks tar archive integrity. Detects tampered, corrupted, or missing files.
- **`restore <timestamp>`**:
  1. Validates artifact integrity and SHA-256 checksum **before** mutating any files.
  2. Refuses sensitive credential restoration unless `--allow-sensitive-credentials` is passed and confirmed.
  3. Refuses restoring a Restricted-scope session onto a live `$HERMES_HOME` unless `--allow-restricted-scope` is passed and confirmed (issue #235).
  4. Creates an automatic pre-restore recovery point to protect existing runtime state.
  5. Delegates to `hermes profile import` or `hermes import` (or legacy tar extraction).
  6. Runs `hermes doctor` post-restore to verify runtime health across dependencies, tools, and memory backends.
  7. Refuses restore into a different `$HERMES_HOME` than recorded in `META` unless `--force-home` is passed.

## 5. Secrets handling

`secrets` (`.env`, `auth.json`) is excluded from every default operation:

- `create` without `--include-secrets`: never reads, hashes, or archives
  secret files, even if `--class secrets` is passed (refused outright).
- `restore` without `--restore-secrets`: any `secrets`-class entries in
  the selected backup are silently skipped (reported as
  `skipped_secrets: true` in `--json` output) rather than overwriting a
  live `.env`/`auth.json`.
- A canary value planted in `.env` never appears in `archive.tar`,
  `MANIFEST`, or any `--json` output of a default (non-`--include-secrets`)
  backup - see `tests/py/hermesbackup/test_backup.py`.

## 6. Privacy implications of retention and deletion

- Session (`sessions`), memory (`memories`), and log (`runtime-state`)
  classes can contain user conversation content, transcripts, and
  personally identifying information the operator's users provided to
  the agent. Opting into these classes means that data now also exists
  in `<state-dir>/backups/hermes/<timestamp>/`, subject to the same
  retention window as any other OMES backup (`OMES_BACKUP_KEEP`, default
  10 sessions) rather than Hermes's own retention policy.
- **To purge a specific backup's data early** (e.g. a user requested
  deletion of their conversation history), remove that session's
  directory directly: `rm -rf <state-dir>/backups/hermes/<timestamp>/`.
  There is no separate "redact one file from an existing archive"
  operation - the archive is deleted as a whole session, matching how
  `lib/omes/backup.sh` treats its own sessions.
- **To stop future backups from including sensitive classes**, simply
  stop passing `--class memory`/`--class sessions`/`--class runtime-state`
  (and, since issue #235, `--allow-restricted-scope`) - the default
  (`config`, `skills`) never includes user-generated content beyond
  configuration and agent-authored skill code, and is the only thing a
  bare `omes agent-backup create` or `omes agent apply` produces.
- Backups of these classes are not automatically encrypted at rest beyond
  the standard `0700`/`0600` filesystem permissions already applied
  everywhere in this tool; operators handling regulated data should apply
  disk-level encryption or move backups to a controlled location that
  provides it.

## 7. Doctor integration

`modules/hermes-backup/module.sh` is an opt-in, no-op module (never part
of any default profile) whose only purpose is an additive `module_doctor`
hook: once applied (`omes install --module hermes-backup`), `omes doctor`
reports the most recent backup timestamp per class and its age. It never
installs, mutates, or manages any file itself.

## 8. Testing

- `tests/py/hermesbackup/` (stdlib `unittest`): missing data class,
  corrupted `MANIFEST`/`archive.tar` (checksum mismatch refused),
  partial restore by class, profile isolation between two different
  `HERMES_HOME` values, secret exclusion (canary in `.env` absent from
  archive/manifest/JSON output), dry-run never opening `.env`'s
  content (proven with a `0000`-mode `.env`, where `create --dry-run`
  still succeeds because it only calls `os.stat`), and (issue #235) the
  `--allow-restricted-scope` gate for every native recovery class and
  legacy `sessions`/`memory` class, on both create and restore, including
  dry-run (`test_native_backup.py`'s `TestNativeCreateRestrictedScopeGate`
  and `test_backup.py`'s `TestRestrictedScopeGateLegacy`).
- `tests/py/privacy/test_privacy_boundary_regression.py`'s
  `TestDefaultBackupsCannotCaptureRestrictedScopeData` (issue #235,
  extending #218's suite): the cross-cutting invariant that a bare
  `hermesbackup.backup.create()` call with no arguments - the literal
  default - must refuse rather than silently produce a backup containing
  session state, that every recovery/legacy class ADR-0020/section 2
  document as Restricted-scope is gated, that the safe defaults
  (`config`/`skills`) are never gated, and that a refused attempt leaves
  nothing on disk (including no canary content from `sessions`/`memories`).
- `tests/unit/hermes-backup.bats` / `tests/integration/hermes-backup.bats`:
  the `lib/omes/cmd/agent-backup.sh` wrapper,
  `modules/hermes-backup/module.sh`'s doctor integration through
  `bin/omes`, and (issue #235) `--allow-restricted-scope` end-to-end
  through the real CLI for both create and restore.
