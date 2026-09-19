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

### `omes health` (extension command)

**Synopsis:** `omes health [agent|gateway] [--json] [--mode <user|system>]` /
`omes health ollama [--json] [--profile <name>] [--model <id>]`

`omes health` (or `omes health agent`) and `omes health gateway` run the
layered host/runtime/gateway/provider/channel readiness model for the
Hermes deployment (issue [#79](https://github.com/ahliweb/omes/issues/79),
via [`lib/omes/py/health/hermes.py`](../lib/omes/py/health/hermes.py)) and
print `{"layers": {...}, "ready": bool, "connected": bool}`. See
[docs/hermes-integration.md §17](hermes-integration.md#17-health-and-readiness-issue-79)
for what each layer proves and its remediation. Exit codes: 0 ready, 7 not
ready.

`omes health ollama` is documented below.

Implemented by [`lib/omes/cmd/health.sh`](../lib/omes/cmd/health.sh)
(issue [#71](https://github.com/ahliweb/omes/issues/71)), which delegates
to the stdlib-only [`lib/omes/py/health/ollama.py`](../lib/omes/py/health/ollama.py)
checker (ADR-0012). Runs a layered health check for the optional Ollama
local AI runtime: **service** (binary present, endpoint reachable,
`/api/version`, loopback bind-address policy), **model** (present in
`/api/tags`, loads within a bounded timeout, minimal chat smoke test,
CPU/GPU placement from `/api/ps`), and **capability** (`chat`,
`structured_output`, `tool_calling`, `embeddings`, `vision` — each
`pass`/`fail`/`not_applicable`, gated by `OMES_OLLAMA_PROFILE`). Read-only
and side-effect free; never sends real prompts or documents, only
synthetic fixtures. See [docs/ollama.md](ollama.md) for the full policy,
profiles, and remediation guidance.

`omes doctor` also runs this check (advisory, via `modules/hermes`'s
`module_doctor` hook) whenever Ollama looks configured for the install
(`OMES_OLLAMA_ENABLED=1`, or the `ollama` binary is present and
`OMES_OLLAMA_MODEL` is set) — a failure there is reported as a WARN, it
never fails `omes doctor` outright.

**Exit codes:** 0 ready, 7 not ready, 4 service missing (binary/endpoint
entirely unreachable — nothing further could be checked), 2 (usage
error, e.g. an unknown target or missing flag value).

**JSON schema:** `{"provider":"ollama-local","profile":"text","service":{"status":"pass"},"model":{"id":"...","status":"pass","processor":"..."},"capabilities":{"chat":"pass","structured_output":"not_applicable","tool_calling":"not_applicable","embeddings":"not_applicable","vision":"not_applicable"},"checks":[{"name":"...","status":"...","detail":"...","remediation":null}],"ready":true}`

**Examples:**

```bash
omes health ollama --json | jq -e '.ready'
omes health ollama --profile structured --model llama3.1
OMES_OLLAMA_MODEL=llama3.1 omes health ollama
```

## 4.12 Extension commands

Additional top-level commands are loaded from `lib/omes/cmd/<name>.sh`
(see [lib/omes/cmd/README.md](../lib/omes/cmd/README.md)). `omes help`
lists them under "Extension commands". For an extension command,
`bin/omes` parses global flags only up to the first token it does not
recognize; everything from that token on (subcommands, names,
command-specific flags) is passed to the extension verbatim. Put global
flags directly after the command word:

```bash
omes <extension> --json <subcommand> <args...>   # --json parsed by bin/omes
omes <extension> <subcommand> --json             # --json passed to the extension
```

Extension commands follow the same exit-code and JSON contract as the
built-in commands.

## 4.13 `omes content` (optional content distribution workflow)

> Status: `scan`, `rescan`, `list` implemented (#64). `approve`, `reject`,
> `retry`, `cancel`, `resume`, `reconcile` implemented (#67). `report`,
> `export`, `prune`, the append-only audit log, and archive-on-terminal-
> state implemented (#68). `plan`/`publish` as full end-to-end
> CLI verbs (driving a real platform worker) land with #66; the state
> machine and orchestration functions they will call
> (`jobs.plan_job`/`jobs.publish_job`/`jobs.verify_job`) already exist. See
> [docs/content-distribution.md](content-distribution.md) for the full
> design (job schema, state machine, worker contract, security
> boundaries) and [ADR-0015](adr/0015-content-distribution-workflow.md)
> for why this workflow exists as an OMES-owned Python orchestrator.

`omes content` is defined by `lib/omes/cmd/content.sh` (a thin wrapper,
per the extension-command contract in section 4.12) and
`lib/omes/py/content/cli.py` (stdlib-only Python, ADR-0012). **It is not
referenced by any installer profile** — see
docs/content-distribution.md section 9.

**Synopsis:**

```bash
omes content scan [--json] [--settle-seconds N]
omes content rescan [--json]
omes content list [--state STATE] [--json]
omes content approve <job-id> --actor ID [--ttl-seconds N] [--json]
omes content reject <job-id> --actor ID [--json]
omes content retry <job-id> --actor ID [--yes] [--force] [--json]
omes content cancel <job-id> --actor ID [--yes] [--json]
omes content resume [--json]
omes content reconcile [--json]
omes content report <job-id> [--md|--json]
omes content export --since YYYY-MM-DD --out DIR [--json]
omes content prune --older-than DAYS [--dry-run] [--yes] [--json]
```

- `scan` walks `$OMES_CONTENT_ROOT/inbox` (never following symlinks, never
  descending into `reports/`, `processing/`, `state/`, `sessions/`, or
  dotfiles), hashes (sha256) every file whose size/mtime have been stable
  for `--settle-seconds` (default 5) or across two consecutive scans,
  creates a job record under `state/jobs/<job-id>.json`, and moves the
  settled file to `processing/<job-id>/source.<ext>`. A file whose hash
  matches an existing job is recorded with `duplicate_of` set to that
  job's id instead of becoming a new active job. A lock file
  (`state/scan.lock`) prevents two scans from running concurrently; a
  lock held by a process that is no longer running is reclaimed
  automatically.
- `rescan` re-hashes every job's `processing/` copy and reports any job
  whose file no longer matches its recorded sha256 (corruption/tamper
  detection) — it does not touch `inbox/`.
- `list` prints job ids and states, optionally filtered by `--state`
  (one of `queued`, `planning`, `approval-required`, `approved`,
  `publishing`, `verifying`, `succeeded`, `retryable-failure`,
  `manual-review`, `failed`, `cancelled`, `archived`).
- `approve`/`reject` record an approval decision bound to the job's
  current artifact hash, with a staleness expiry (`--ttl-seconds`,
  default `OMES_CONTENT_APPROVAL_TTL_SECONDS`/3600s). This is the
  always-available MVP approval path; #65 adds a Telegram front end that
  writes the same approval record shape. `reject` moves the job to
  `cancelled`.
- `retry` moves a `retryable-failure` or `manual-review` job back to
  `publishing`. Requires `--actor`; requires `--yes` (non-interactive) or
  an interactive y/N confirmation. Refuses to run before the computed
  exponential-backoff delay has elapsed unless `--force` is also given,
  and refuses once `max_attempts` is exhausted.
- `cancel` moves any non-terminal job to `cancelled`. Requires `--actor`
  and `--yes`/confirmation.
- `resume` re-runs **verify only** for every job left in `publishing` or
  `verifying` (e.g. after a crash/restart). A job found in `publishing`
  is moved to `manual-review` instead of being re-verified, because the
  worker's publish outcome for that attempt is unknown — this is the
  rule that prevents duplicate publication across a restart
  (docs/content-distribution.md section 5.3).
- `reconcile` lists jobs that need operator attention (`manual-review`,
  `retryable-failure` with an elapsed or exhausted backoff, or an
  `approval-required` job with no/expired/mismatched approval), each
  with a human-readable reason.
- Every state transition and side effect is appended to
  `state/audit.jsonl` (one JSON object per line: `ts`, `actor`, `job`,
  `from`, `to`, `platform`, `artifact_hash`, `url`, `worker_version`,
  `note`), redacted of anything matching
  `TOKEN|KEY|SECRET|PASSWORD|COOKIE`, and hash-chained (`prev_hash`/
  `line_hash`) so a rewritten, inserted, or reordered line is detectable.
  The audit log is never read by `export`'s reports copy and never
  deleted by `prune` (see below); it is append-only by construction, not
  by filesystem permissions alone.
- As soon as a job reaches `succeeded`, `failed`, or `cancelled` (via
  `approve`/`reject`/`retry`/`cancel`/`resume`), it is automatically
  archived: its `processing/<job-id>/` evidence moves to
  `uploaded/<job-id>/` (succeeded), `failed/<job-id>/` (failed), or
  `review/<job-id>/` (cancelled/manual-review, once acted on), and a
  `reports/<job-id>/report.json` + `report.md` pair is generated.
  Archiving never overwrites existing evidence at the destination.
- `report <job-id>` prints the path to the latest report (default),
  its JSON content (`--json`), or its Markdown content (`--md`),
  generating one first if the job has none yet. Regenerating an
  existing report never overwrites `report.json`/`report.md` — it
  writes `report-2.json`/`report-2.md`, then `report-3.*`, and so on.
- `export --since DATE --out DIR` copies every job's reports and the
  portion of the audit log at/after `DATE` into `DIR`, already redacted.
  It never reads or copies `content/sessions/`.
- `prune --older-than DAYS` deletes the archived media
  (`uploaded/`/`failed/`/`review/<job-id>/`) and reports
  (`reports/<job-id>/`) of jobs in the `archived` state whose
  `updated_at` is older than `DAYS`. It never touches `content/sessions/`
  or `state/audit.jsonl` (audit rotation is a separate, not-yet-
  implemented concern). `--dry-run` reports what would be deleted
  without deleting anything; without `--dry-run`, `--yes` (or an
  interactive confirmation) is required.

**Exit codes:** 0 (success, including "nothing new to scan"), 1 (error,
e.g. `scan` while another scan holds the lock, `rescan` finding a hash
mismatch, an invalid state transition, a retry attempted before its
backoff delay, or a missing `--yes`/confirmation), 2 (usage error).

**JSON schema (`scan`):**
`{"created": ["<job-id>", ...], "duplicates": ["<job-id>", ...], "pending": ["<path>", ...]}`

**Optional systemd timer template** (not installed by OMES; an operator
copies these unit files by hand if they want scheduled scanning instead
of running `omes content scan` manually):

```ini
# ~/.config/systemd/user/omes-content-scan.service
[Unit]
Description=omes content scan (one-shot)

[Service]
Type=oneshot
ExecStart=%h/.local/bin/omes content scan --json
```

```ini
# ~/.config/systemd/user/omes-content-scan.timer
[Unit]
Description=Run omes content scan periodically

[Timer]
OnBootSec=5min
OnUnitActiveSec=15min

[Install]
WantedBy=timers.target
```

Enable with `systemctl --user enable --now omes-content-scan.timer`.
OMES does not create, enable, or reference this timer itself.

### `omes graphify` (update / uninstall / run / skill)

`lib/omes/cmd/graphify.sh` (issues #50/#51). Full synopsis, exit codes, JSON schemas, and
examples for every `omes graphify <subcommand>` live in
[docs/graphify.md](graphify.md) §2/§3, not here, to keep this shared reference file's
graphify footprint minimal — see that document for the authoritative CLI contract.

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
