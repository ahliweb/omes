# Hermes data-class backup and restore

> Status: describes the actual repository state. This document covers
> `omes agent-backup` (issue [#82](https://github.com/ahliweb/omes/issues/82)),
> implemented in `lib/omes/py/hermesbackup/` and wired in via
> `lib/omes/cmd/agent-backup.sh` (the extension-command pattern in
> `lib/omes/cmd/README.md`). It complements the general backup/restore
> model in [docs/rollback.md](./rollback.md), which covers OMES's own
> managed-path backups; this document is specific to Hermes's own data
> under `$HERMES_HOME`.

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

## 3. Backup classes

| Class | Paths | Default? |
| --- | --- | --- |
| `config` | `config.yaml`, `SOUL.md` | Yes |
| `skills` | `skills/` | Yes |
| `memory` | `memories/` | No - explicit `--class memory` |
| `sessions` | `sessions/`, `state.db` | No - explicit `--class sessions` |
| `runtime-state` | `logs/`, `cache/`, `cron/`, `state-snapshots/`, `modal_snapshots.json`, `verification_evidence.db` | No - explicit `--class runtime-state` |
| `secrets` | `.env`, `auth.json` | **Never** - requires `--include-secrets` |

`omes agent-backup create` with no `--class` flag backs up only `config`
and `skills`. Hermes's own `backups/` directory is intentionally excluded
from every class (backing up Hermes's own backups would duplicate data
across nested generations).

## 4. Commands

```
omes agent-backup create [--class NAME]... [--include-secrets] [--dry-run] [--json] [--hermes-home PATH]
omes agent-backup list [--json]
omes agent-backup verify <timestamp> [--json]
omes agent-backup restore <timestamp> [--class NAME]... [--dry-run] [--yes]
                                       [--restore-secrets] [--force-home]
                                       [--hermes-home PATH] [--json]
```

- **`create`**: writes `<state-dir>/backups/hermes/<timestamp>/` containing
  `archive.tar` (a plain tar of the selected files, member paths relative
  to `$HERMES_HOME`), `MANIFEST` (one JSON object per file: `category`,
  `path`, `sha256`, `size`, `mode`), and `META` (`hermes_version`
  (from `hermes --version`, best-effort), `hermes_home`, `classes`,
  `include_secrets`, `timestamp`, `omes_version`). The session directory
  is mode `0700`; `MANIFEST`, `META`, and `archive.tar` are mode `0600`.
  Retention follows the same `OMES_BACKUP_KEEP` semantics as
  `lib/omes/backup.sh` (default 10, applied after every successful
  create).
- **`--include-secrets`**: required before `--class secrets` is accepted;
  without it, requesting the `secrets` class is refused with a clear
  error. When used, a warning is printed before the backup is written.
- **`--dry-run`**: previews classes/paths/sizes via `os.stat` only -
  **no file's content is ever opened** during a dry run, even for
  `secrets` with `--include-secrets`, so a dry run cannot leak secret
  content through an error path or timing side channel.
- **`list`**: lists sessions with their classes and whether they include
  secrets.
- **`verify <timestamp>`**: re-hashes every archive member against its
  `MANIFEST` entry; reports `OK` or lists every mismatched/missing file.
  A structurally corrupt `MANIFEST`/`META` (bad JSON, missing fields) or
  an unreadable tar container is refused with a clear error rather than
  silently treated as empty.
- **`restore <timestamp>`**: validates checksums for every file it is
  about to write **before** writing any of them; creates a pre-restore
  backup of the classes being restored (from the live `$HERMES_HOME`,
  before any file is overwritten); preserves the original file
  permissions recorded in `MANIFEST`; works fully offline (no network
  call anywhere in this tool); never restores the `secrets` class unless
  `--restore-secrets` is passed (and confirmed, unless `--yes`); refuses
  to restore into a different `$HERMES_HOME` than the one recorded in
  `META` unless `--force-home` is passed.

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
