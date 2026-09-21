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

| Recovery Class | Upstream Command | Description | Sensitive Credentials? |
| --- | --- | --- | --- |
| `portable-profile` (default) | `hermes profile export <profile> --output <path>` | Exports an isolated, portable profile archive containing skills, configuration, memories, and session state. | Excluded by upstream design |
| `full-runtime-dr` | `hermes backup --output <path>` | Full disaster recovery archive containing complete runtime state, databases, and credentials. | **Yes** — requires `--allow-sensitive-credentials` |
| `omes-host` | Managed-path backup engine | OMES host-level configuration, drop-ins, and legacy data classes (`config`, `skills`, `memory`, `sessions`, `runtime-state`, `secrets`). | Mode `0600`; requires `--allow-sensitive-credentials` or `--include-secrets` |

`omes agent-backup create` defaults to `--recovery-class portable-profile` (`profile: default`).
Existing archives without `format: native-*` are classified as `legacy-omes` and remain fully readable, verifiable, and restorable.

## 4. Commands

```
omes agent-backup create [--recovery-class CLASS] [--profile NAME]
                         [--allow-sensitive-credentials] [--include-secrets]
                         [--class NAME]... [--dry-run] [--json] [--hermes-home PATH]
omes agent-backup list [--json]
omes agent-backup inventory [--json]
omes agent-backup verify <timestamp> [--json]
omes agent-backup restore <timestamp> [--recovery-class CLASS] [--profile NAME]
                                       [--allow-sensitive-credentials] [--restore-secrets]
                                       [--class NAME]... [--dry-run] [--yes]
                                       [--force-home] [--hermes-home PATH] [--json]
```

- **`create`**: writes `<state-dir>/backups/hermes/<timestamp>/` containing:
  - For native backups: `<profile>.hermes-profile.tar.gz` or `hermes-backup.tar.gz`, `MANIFEST`, and `META` (recording `format`, `recovery_class`, `profile`, `sha256`, `artifact_size`, `sensitive`, `hermes_version`, and `timestamp`).
  - For legacy backups: `archive.tar`, `MANIFEST`, and `META`.
  Session directory permissions are set to `0700`; files are mode `0600`. Retention follows `OMES_BACKUP_KEEP` (default 10 sessions).
- **`--allow-sensitive-credentials`** (or `--include-credentials` / `--include-secrets`): required for `full-runtime-dr` backups and when restoring sensitive credentials.
- **`--dry-run`**: previews the recovery class and format without mutating the filesystem or exporting archives.
- **`list` / `inventory`**: displays all backup sessions with their format (`native-hermes-profile`, `native-hermes-runtime`, or `legacy-omes`), recovery class, target profile, and sensitivity status.
- **`verify <timestamp>`**: validates SHA-256 checksums of native artifacts and checks tar archive integrity. Detects tampered, corrupted, or missing files.
- **`restore <timestamp>`**:
  1. Validates artifact integrity and SHA-256 checksum **before** mutating any files.
  2. Refuses sensitive credential restoration unless `--allow-sensitive-credentials` is passed and confirmed.
  3. Creates an automatic pre-restore recovery point to protect existing runtime state.
  4. Delegates to `hermes profile import` or `hermes import` (or legacy tar extraction).
  5. Runs `hermes doctor` post-restore to verify runtime health across dependencies, tools, and memory backends.
  6. Refuses restore into a different `$HERMES_HOME` than recorded in `META` unless `--force-home` is passed.

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
  - the default (`config`, `skills`) never includes user-generated
  content beyond configuration and agent-authored skill code.
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
  archive/manifest/JSON output), and dry-run never opening `.env`'s
  content (proven with a `0000`-mode `.env`, where `create --dry-run`
  still succeeds because it only calls `os.stat`).
- `tests/unit/hermes-backup.bats` / `tests/integration/hermes-backup.bats`:
  the `lib/omes/cmd/agent-backup.sh` wrapper and
  `modules/hermes-backup/module.sh`'s doctor integration through
  `bin/omes`.
