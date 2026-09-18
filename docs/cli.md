# OMES CLI Reference

> Status: implemented (issue #14). Full command reference for `bin/omes` as it exists in this
> repository today. `docs/architecture.md` Sections 3, 8, and 9 are the authoritative execution
> model / logging / exit-code contracts; this document is the operator-facing reference built on
> top of them, with a synopsis, flags, JSON schema, and worked examples per command.

## 1. Global flags and environment variables

Every command accepts these flags, in any order, after the command name:

| Flag | Env var equivalent | Meaning |
|---|---|---|
| `--profile <name>` | — | `server`, `desktop`, or `hermes`. Selects a profile's module set (`check`/`install`/`uninstall`). |
| `--module <name>` | — | Select one module (repeatable). Combine with `check`/`install`/`backup`/`uninstall`. |
| `--dry-run` | `OMES_DRY_RUN=1` | Print planned actions, mutate nothing. |
| `--json` | `OMES_JSON=1` | Emit exactly one JSON object on stdout; all logging moves to stderr/the log file. |
| `--yes` | `OMES_NONINTERACTIVE=1` | Auto-confirm a mutating action instead of prompting interactively. |
| `--verbose` | `OMES_VERBOSE=1` | Also print `DEBUG`-level lines to the console (they always go to the log file regardless). |
| `--log-file <path>` | `OMES_LOG_FILE` | Override the log file location (default: `<state-dir>/logs/omes-<UTC timestamp>.log`). |
| `--allow-docker-group` | — | Opt in to granting root-equivalent `docker` group access (prints a warning; `containers` module, not yet implemented). |
| `--from <timestamp>` | — | (`restore`) target a specific backup session instead of the latest. |
| `--list` | — | (`restore`) list available backup sessions, then exit; no restore happens. |
| `--reason <text>` | — | (`backup`) short reason recorded in the session's `META`. Default: `manual`. |
| `--purge-packages` | — | (`uninstall`) also remove the packages OMES recorded as installed by the target module(s). |

Other environment variables: `OMES_STATE_DIR` (override the state directory for either scope),
`OMES_ROOT` (repository root; normally auto-detected from `bin/omes`'s own path),
`OMES_BACKUP_KEEP` (retention count for `backup_prune`, default 10),
`OMES_ASSUME_ONLINE=1` / `OMES_ASSUME_OFFLINE=1` (force `detect_network`'s result, mainly for
testing), `NO_COLOR` (disable ANSI color in human output per https://no-color.org/).

**Root/user scope.** Every module declares `MODULE_SCOPE=root` or `MODULE_SCOPE=user`.
`check`/`install`/`uninstall` run only the modules matching the *current* effective privilege
and skip the rest, printing the exact follow-up command. `--module <name>` naming a module
whose scope does not match the current privilege is a privilege usage error (**exit 5**,
no mutation) — see `docs/architecture.md` Section 4.5 and
[ADR-0005](adr/0005-root-and-user-scope-separation.md).

## 2. Exit codes

**Contract — stable across releases** (`docs/architecture.md` Section 9):

| Code | Meaning |
|---|---|
| 0 | Success |
| 1 | General/unexpected error (includes a failed `omes doctor` check, i.e. at least one `FAIL`) |
| 2 | Usage error (bad flags, missing dependency, `MODULE_REQUIRES` cycle) |
| 3 | Unsupported platform (OS/arch) — detected BEFORE any mutation |
| 4 | Preflight failed (one or more `module_check` failed, including a package not found in the configured repos or network absent during check — `docs/packages.md` Section 5; no mutation occurred) |
| 5 | Privilege error (an explicit `--module <name>` request whose scope does not match the current privilege) |
| 6 | Module apply failed (message names the module) |
| 7 | Verification failed (message names the module) |
| 8 | Network required but unavailable (mid-`module_apply`, or a command that legitimately needs it — e.g. `omes update`'s fetch) |
| 9 | Backup/restore failed (corrupt `MANIFEST`, checksum mismatch, no backup found — message names the reason/path) |
| 10 | Rollback failed (`omes uninstall`'s `module_rollback`/managed-path rollback failed; message names the module) |

Exit codes 0–10 are reserved and stable; a future addition uses the next unused integer.

## 3. JSON output contract

With `--json` (or `OMES_JSON=1`), every command below emits **exactly one** JSON object on
stdout for the whole invocation, e.g. `{"command":"check","ok":true,...,"exit_code":0}`. No
other stdout noise is permitted — anything that would normally go to human stdout (progress
lines, warnings) instead goes to stderr and/or the log file, so `omes ... --json | jq .` always
works. Every object has at least `command` (string), `ok` (boolean), and `exit_code` (number,
mirroring the process's actual exit code); this is the schema piece common to all of them.
Field names below are stable; new fields may be added, existing ones are not removed or
repurposed without a major-version note in `CHANGELOG.md`.

## 4. Commands

### `omes check`

**Synopsis:** `omes check [--profile <name> | --module <name>...] [--json]`

Runs platform detection and every resolved module's `module_check` — **no mutation**. Safe to
run any time, as any user, offline (network absence is reported as a named check, not a crash).

**Exit codes:** 0 (ok), 3 (unsupported platform), 4 (a `module_check` failed), 5 (explicit
`--module` wrong-scope request).

**JSON schema:**

```json
{
  "command": "check",
  "ok": true,
  "platform": {"os_id": "ubuntu", "os_version_id": "24.04", "os_codename": "noble",
    "os_pretty": "Ubuntu 24.04.1 LTS", "arch": "amd64", "tier": "tier1", "virt": "none",
    "session": "server", "privilege": "root", "network": "online"},
  "checks": [{"name": "os", "ok": true, "detail": "Ubuntu 24.04.1 LTS tier=tier1"}],
  "exit_code": 0
}
```

**Examples:**

```bash
omes check                              # platform-only checks (no --profile/--module)
omes check --profile server             # + every server-profile module's module_check
sudo omes check --profile server --json | jq '.checks[] | select(.ok==false)'
```

### `omes install`

**Synopsis:** `omes install (--profile <name> | --module <name>...) [--dry-run] [--yes] [--json]`

Check-all-then-apply: runs every resolved module's `module_check`; if any fails, exits 4 with
no mutation. Otherwise backs up, applies, and verifies each module in `MODULE_REQUIRES` order.
Requires `--yes` (or an interactive TTY confirmation) to mutate; `--dry-run` prints the plan and
touches nothing. On success with `--profile`, records `omes.profile` in state (visible in
`omes status`).

**Exit codes:** 0, 2 (no `--profile`/`--module`, or nothing resolved), 3, 4, 5, 6 (a module's
`module_apply` failed — or exit 8 specifically when it failed because network became
unavailable mid-apply, `docs/packages.md` Section 5), 7 (a module's `module_verify` failed).

**JSON schema (success):**

```json
{"command": "install", "ok": true,
 "modules": [{"name": "apt-base", "status": "applied"}], "exit_code": 0}
```

On a preflight failure: `{"command":"install","ok":false,"failed_checks":[{"name":"apt-base","detail":"..."}],"exit_code":4}`.

**Examples:**

```bash
sudo omes install --profile server --dry-run    # see exactly what would happen
sudo omes install --profile server --yes        # apply for real
omes install --module hermes --yes              # apply a single user-scope module
```

### `omes status`

**Synopsis:** `omes status [--json]`

Read-only, fully offline. Platform, the profile most recently installed against this scope's
state dir, every recorded module's status/`applied_at`/version, backup session count, and the
most recent log file path.

**Exit codes:** 0 only (status never fails on its own).

**JSON schema:**

```json
{
  "command": "status", "ok": true,
  "platform": {"...": "..."},
  "profile": "server",
  "state_dir": "/var/lib/omes",
  "modules": [{"name": "apt-base", "status": "applied",
    "applied_at": "2026-09-18T10:00:00Z", "version": "0.1.0"}],
  "backup_count": 3,
  "last_log": "/var/lib/omes/logs/omes-20260918T100000Z.log",
  "exit_code": 0
}
```

**Example:** `omes status --json | jq '.modules[] | select(.status=="failed")'`

### `omes modules`

**Synopsis:** `omes modules [--json]`

Lists every module under `modules/` with its scope, description, and current status.

**Exit codes:** 0 only.

**JSON schema:** `{"command":"modules","ok":true,"modules":[{"name":"apt-base","description":"...","scope":"root","status":"not-applied"}],"exit_code":0}`

### `omes doctor`

**Synopsis:** `omes doctor [--json]`

Health checks, read-only and offline: platform tier, required commands (`bash`, `sha256sum`,
`dpkg-query`, `apt-get`, `apt-cache`, `git`), state dir/file integrity and permissions
(`0700`/`0600`), every `applied` module's `module_verify` (skipped with `WARN` if the current
privilege can't run it, or if the module was removed from disk), an optional per-module
`module_doctor` hook (any module MAY define this function for deeper self-diagnosis — additive
to the module contract in `docs/architecture.md` Section 4; not required), backup session
availability, and log directory writability. Each check reports `OK`, `WARN`, or `FAIL`.

**Exit codes:** 0 when no check is `FAIL` (a `WARN` alone does not fail the run); 1 when at
least one check is `FAIL`.

**JSON schema:**

```json
{
  "command": "doctor", "ok": true,
  "checks": [
    {"name": "platform", "level": "OK", "detail": "Ubuntu 24.04.1 LTS tier=tier1"},
    {"name": "cmd:git", "level": "OK", "detail": "found on PATH"},
    {"name": "state_dir", "level": "OK", "detail": "/var/lib/omes (mode 700)"},
    {"name": "module:apt-base", "level": "OK", "detail": "verified"},
    {"name": "backups", "level": "OK", "detail": "3 session(s) available"},
    {"name": "log_writable", "level": "OK", "detail": "/var/lib/omes/logs"}
  ],
  "exit_code": 0
}
```

**Example:** `sudo omes doctor --json | jq '.checks[] | select(.level!="OK")'`

### `omes update`

**Synopsis:** `omes update [--dry-run] [--json]`

Updates the OMES checkout itself via `git` (fetch, then a fast-forward-only merge of the
current branch's upstream) — **never** applies any module. Refuses on a dirty working tree, a
detached `HEAD`, or a diverged/missing upstream. On success, re-runs `omes check` (never
`install`) and exits with **check's own result**, so `omes update`'s final human/JSON output on
a successful update IS `omes check`'s output for the freshly-updated checkout — this is
intentional (documented here, not duplicated as a second envelope).

**Exit codes:** 0 (up to date or fast-forwarded, `check` then exits 0), 1 (not a git checkout,
dirty tree, detached `HEAD`, fetch/merge failed), 3/4 (whatever `check` returns after a
successful update), 8 (network unavailable for the fetch).

**JSON schema (dry-run):** `{"command":"update","ok":true,"branch":"main","exit_code":0}`.
**On a real, successful update:** `check`'s schema (Section 4 above), `"command":"check"`.

**Examples:**

```bash
omes update --dry-run     # see whether a fast-forward is even possible
omes update               # fetch + ff-only merge, then re-run `omes check`
```

### `omes backup`

**Synopsis:** `omes backup [--module <name>] [--reason <text>] [--dry-run] [--json]`

Creates an on-demand backup session covering every path in `module.<name>.managed_paths` for
the given `--module`, or every currently-recorded module's managed paths when `--module` is
omitted. Prunes to the last `OMES_BACKUP_KEEP` sessions (default 10) afterward.

**Exit codes:** 0 only (nothing to back up is not an error).

**JSON schema:** `{"command":"backup","ok":true,"backup_dir":"/var/lib/omes/backups/20260918T100000Z","files_backed_up":2,"exit_code":0}`

**Examples:**

```bash
omes backup --module hermes --reason "before manual edit"
omes backup --dry-run       # see what would be backed up
```

### `omes restore`

**Synopsis:** `omes restore [--from <timestamp>] [--list] [--dry-run] [--yes] [--json]`

Restores files from a backup session (the given `--from <timestamp>`, or the latest), verifying
each file's sha256 against the session's `MANIFEST` before writing, restoring mode/ownership
(`cp -a`), and taking a fresh `pre-restore-backup` of anything it is about to overwrite.
**Works fully offline.** `--list` prints every available session (timestamp, module, reason,
started_at) and exits without restoring anything. See `docs/rollback.md` for the full recovery
walkthroughs.

**Exit codes:** 0, 1 (declined confirmation without `--yes`), 9 (no backup found, corrupt
`MANIFEST`, or a checksum mismatch — message names the reason/path).

**JSON schema:** `{"command":"restore","ok":true,"exit_code":0}` (restore), or with `--list`:
`{"command":"restore","ok":true,"backups":[{"timestamp":"20260918T100000Z","module":"apt-base","reason":"pre-apply","started_at":"2026-09-18T10:00:00Z"}],"exit_code":0}`.

**Examples:**

```bash
omes restore --list                      # see what's available (read-only)
omes restore --yes                       # restore the latest session
omes restore --from 20260918T100000Z --yes --dry-run   # preview a specific one
```

### `omes uninstall`

**Synopsis:** `omes uninstall [--profile <name>] [--module <name>...] [--purge-packages] [--dry-run] [--yes] [--json]`

Resolves target modules **from state only** (never a module OMES never recorded as applied),
runs `module_rollback` plus the generic managed-path rollback (restore a path some backup
captured as pre-existing; remove a path no backup ever captured, i.e. OMES created it fresh) in
**reverse** dependency order. Always prints the packages a module installed and the exact
`apt-get remove` command; removes them only with `--purge-packages`, and only the ones recorded
for that module. See `docs/rollback.md` for the full policy and recovery walkthroughs.

**Exit codes:** 0, 1 (declined confirmation), 5 (explicit `--module` wrong-scope request), 10
(a module's rollback failed — message names the module).

**JSON schema:** `{"command":"uninstall","ok":true,"modules":[{"name":"apt-base","status":"removed"}],"exit_code":0}`

**Examples:**

```bash
sudo omes uninstall --dry-run                  # preview
sudo omes uninstall --yes                      # roll back every applied module (this scope)
sudo omes uninstall --module apt-base --purge-packages --yes
```

### `omes version`

**Synopsis:** `omes version [--json]`

Prints the `VERSION` file contents. **Exit codes:** 0 only.
**JSON schema:** `{"command":"version","ok":true,"version":"0.1.0","exit_code":0}`

### `omes help`

**Synopsis:** `omes help` / `omes --help` / `omes -h` / `omes` (no arguments; exits 2 in this
last form only, since no command was given)

Prints the full usage text (commands, global flags, exit codes). **Exit codes:** 0 (`omes
help`), 2 (no command given at all).
**JSON schema:** `{"command":"help","ok":true,"usage":"<the same text as human mode>","exit_code":0}`

## 5. Worked examples

**First run on a fresh Ubuntu Server 24.04 host:**

```bash
omes check                                   # read-only, any user
sudo omes install --profile server --dry-run # preview root-scope modules
sudo omes install --profile server --yes     # apply for real
omes install --profile server --yes          # apply the profile's user-scope modules too
omes status                                  # confirm what's applied
sudo omes doctor                             # health check after install
```

**Recovering from a bad config change:**

```bash
omes restore --list
omes restore --from <timestamp> --yes
```

**Fully removing OMES's changes:**

```bash
sudo omes uninstall --dry-run
sudo omes uninstall --purge-packages --yes
omes uninstall --yes    # the profile's user-scope modules, as your own user
```

**Automation-friendly (CI, monitoring):**

```bash
sudo omes doctor --json | jq -e '.ok'          # non-zero exit if any FAIL
omes status --json | jq '.modules[].status'
sudo omes install --profile server --yes --json | jq '.modules'
```
