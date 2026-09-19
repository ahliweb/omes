# OMES CLI Reference

> Status: implemented (issue #14). Full command reference for `bin/omes` as it exists in this
> repository today. `docs/architecture.md` Sections 3, 8, and 9 are the authoritative execution
> model / logging / exit-code contracts; this document is the operator-facing reference built on
> top of them, with a synopsis, flags, JSON schema, and worked examples per command.
>
> For an end-to-end Hermes deployment runbook rather than a per-command
> reference, see [docs/hermes-deployment-guide.md](hermes-deployment-guide.md).

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

Read-only, fully offline (the `evidence` section below only ever runs read-only,
timeout-bounded local probes - it never requires network access). Platform, the profile most
recently installed against this scope's state dir, every recorded module's status/`applied_at`/
version, backup session count, the most recent log file path, and (issue #83) an `evidence`
snapshot of runtime version/compatibility evidence.

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
  "evidence": {"ok": true, "generated_at": "2026-09-19T01:00:00Z",
    "components": {"...": "..."}, "warnings": []},
  "exit_code": 0
}
```

`evidence` is exactly the same object `omes health versions --json` emits (same top-level keys:
`ok`, `generated_at`, `components`, `warnings`) - see
[docs/compatibility-evidence.md](compatibility-evidence.md) for every component it records, and
§4.12 below for the standalone `omes health versions` command. When the evidence collector
cannot run at all (python3 missing, or an internal collector error), `evidence` is instead
`{"ok": false, "error": "..."}` - the surrounding `omes status` JSON object is still exactly one
valid object either way, and `omes status`'s own `ok`/`exit_code` are unaffected. In human mode,
a short `status evidence:` summary is printed after the log line, using the same per-component
`value`/`null (<reason>)` formatting as `omes health versions`' human output.

**Example:** `omes status --json | jq '.modules[] | select(.status=="failed")'`
**Example:** `omes status --json | jq '.evidence.components.hermes.value'`

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
to the module contract in `docs/architecture.md` Section 4; not required), every **deployed**
`omes agent` (issue #87/#96 follow-up — see section 4.17 `omes agent doctor` below; skipped
entirely, not a `WARN`, when no agent has ever been declared), backup session availability, and
log directory writability. Each check reports `OK`, `WARN`, or `FAIL`.

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

The `hermes-gateway`/`hermes-gateway-system` `module_doctor` hooks additionally report the
active systemd hardening profile (`off` by default) and resolved resource limits — see
[`docs/hermes-hardening.md`](./hermes-hardening.md) for the `OMES_HERMES_HARDENING` opt-in.

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

## 4.13 `omes health` (layered health/readiness checks)

**Synopsis:** `omes health [agent|gateway] [--json] [--mode <user|system>]` /
`omes health ollama [--json] [--profile <name>] [--model <id>]`

`omes health` (or `omes health agent`) and `omes health gateway` run the
layered host/runtime/gateway/provider/channel readiness model for the
Hermes deployment (issue [#79](https://github.com/ahliweb/omes/issues/79),
via [`lib/omes/py/health/hermes.py`](../lib/omes/py/health/hermes.py)) and
print `{"layers": {...}, "ready": bool, "connected": bool}`. See
[docs/hermes-integration.md §17](hermes-integration.md#17-health-and-readiness-issue-79)
and [docs/hermes-deployment-guide.md §9](hermes-deployment-guide.md#9-health-and-readiness)
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

`omes health versions [--json]` (issue
[#83](https://github.com/ahliweb/omes/issues/83), via
[`lib/omes/versions.sh`](../lib/omes/versions.sh) and
[`lib/omes/py/provenance/versions.py`](../lib/omes/py/provenance/versions.py))
reports runtime version/compatibility evidence — OMES, OS/arch/kernel,
Hermes, gateway mode, python3, node, browser, ffmpeg, docker (client
only), Ollama, and a documented allowlist of non-secret Hermes config
keys. See [docs/compatibility-evidence.md](compatibility-evidence.md)
for the full field list and the "evidence is not a guarantee" scope note.
Exit codes: 0 (this is a read-only report, not a pass/fail gate), 1 on an
internal error only.

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

## 4.14 `omes audit` (security audits)

**Synopsis:** `omes audit exposure [--json]` / `omes audit provenance [--profile <name>] [--json]`

Implemented by [`lib/omes/cmd/audit.sh`](../lib/omes/cmd/audit.sh) (issue
[#80](https://github.com/ahliweb/omes/issues/80)), which delegates to the
stdlib-only [`lib/omes/py/health/exposure.py`](../lib/omes/py/health/exposure.py)
checker. Detects unsafe Hermes gateway, browser-control/CDP, MCP, and
Ollama listener exposure (loopback vs. LAN vs. wildcard, cross-referenced
with `ufw`) without ever opening a port, altering firewall rules, or
reading credentials. See
[docs/hermes-integration.md §18](hermes-integration.md#18-exposure-audit-issue-80)
and [docs/hermes-deployment-guide.md §10](hermes-deployment-guide.md#10-exposure-audit)
for remediation guidance.

**Exit codes:** 0 ok, 7 findings, 4 a required tool (`ss`) is missing.

**Examples:**

```bash
omes audit exposure --json | jq -e '.ok'
OMES_EXPOSURE_ALLOW="0.0.0.0:8642" omes audit exposure
```

`omes audit provenance [--profile <name>] [--json]` (issue
[#84](https://github.com/ahliweb/omes/issues/84), implemented by
[`lib/omes/cmd/audit-provenance.sh`](../lib/omes/cmd/audit-provenance.sh)
and [`lib/omes/py/provenance/audit.py`](../lib/omes/py/provenance/audit.py))
audits `<state-dir>/provenance/*.json` records written at Hermes install
time, at every `apt-base`/`containers` apply (one record per apt-managed
package, via `lib/omes/pkg.sh`'s `pkg_record_provenance`), and at every
`hermes` apply for uv-tool-/pipx-managed packages discovered on the host
(e.g. graphify, once [#50](https://github.com/ahliweb/omes/issues/50)
lands): a recorded checksum mismatch is a `FAIL` (fail-closed), an
unverified checksum status or a mutable installer-source URL
(`main`/`latest`/`HEAD`) is a `WARN` (`package_manager_verified` — apt's
own package verification — is not treated as unverified), missing
required metadata is a `WARN`, and executable files under managed
`$HERMES_HOME/{skills,plugins,mcp}/**` paths are listed (mode/size/sha256)
for review — **never executed**. See [docs/provenance.md](provenance.md)
("a provenance report is not a security certification"). Exit codes: 0
clean, 7 findings.
> Note: `omes health versions` and `omes audit provenance` (issues
> [#83](https://github.com/ahliweb/omes/issues/83) and
> [#84](https://github.com/ahliweb/omes/issues/84)) are implemented on
> the sibling branch `origin/feat/84-provenance-audit` and are **not
> present in this tree**; merging that work into this stack is tracked
> in PR [#122](https://github.com/ahliweb/omes/pull/122)/[#126](https://github.com/ahliweb/omes/pull/126).
> See [docs/hermes-deployment-guide.md §13](hermes-deployment-guide.md#13-compatibility-records-and-provenance).


## 4.12 Extension commands


## 4.15 `omes content` (optional content distribution workflow)

> Status: `scan`, `rescan`, `list` implemented (#64). `approve`, `reject`,
> `retry`, `cancel`, `resume`, `reconcile` implemented (#67). `report`,
> `export`, `prune`, the append-only audit log, and archive-on-terminal-
> state implemented (#68). `plan`, `publish`, and `session login`/
> `session revoke` implemented (#66), driving the one concrete
> `generic_browser` worker skeleton (default `manual_stub` driver; no
> real browser dependency ships in OMES — see
> [docs/content-distribution.md](content-distribution.md) section 13).
> `edit`, `notify`, `status`, and `--channel telegram` on `approve`/
> `reject`/`edit` implemented (#65) — an outbound-only Telegram approval
> front end (`lib/omes/py/content/telegram.py`); inbound chat commands are
> mapped to these CLI verbs by a Hermes skill documented at
> `skills/content/SKILL.md`, never received by OMES directly. Platform-aware
> caption/cover/policy validation implemented (#69):
> `lib/omes/py/content/validation.py` + `lib/omes/py/content/platforms/
> {generic,youtube}.json`; `publish` validates before invoking a worker and
> blocks only that one platform on a blocking issue (see
> [docs/content-distribution.md](content-distribution.md) section 15). See
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
omes content approve <job-id> --actor ID [--ttl-seconds N] [--channel cli|telegram] [--expected-hash H] [--json]
omes content reject <job-id> --actor ID [--channel cli|telegram] [--json]
omes content retry <job-id> --actor ID [--yes] [--force] [--json]
omes content cancel <job-id> --actor ID [--yes] [--json]
omes content resume [--json]
omes content reconcile [--json]
omes content report <job-id> [--md|--json]
omes content export --since YYYY-MM-DD --out DIR [--json]
omes content prune --older-than DAYS [--dry-run] [--yes] [--json]
omes content plan <job-id> [--actor ID] [--caption TEXT] [--target PLATFORM]... [--json]
omes content publish <job-id> --platform NAME [--worker-executable PATH] [--worker-version V] [--cover PATH] [--check-links] [--force-validation] [--json]
omes content session login <platform> --actor ID [--json]
omes content session revoke <platform> --actor ID [--yes] [--json]
omes content edit <job-id> --actor ID [--caption TEXT] [--target PLATFORM]... [--channel cli|telegram] [--json]
omes content edit <job-id> --actor ID --platform NAME --caption-file FILE [--json]
omes content notify <job-id> [--chat-id ID] [--json]
omes content status <job-id> [--telegram] [--chat-id ID] [--json]
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
  default `OMES_CONTENT_APPROVAL_TTL_SECONDS`/3600s). `--channel` defaults
  to `cli` (the always-available MVP approval path, no authorization list
  required). `--channel telegram` (#65) additionally requires `--actor` to
  be a numeric Telegram user id present in both `OMES_CONTENT_APPROVERS`
  and Hermes's `TELEGRAM_ALLOWED_USERS` (docs/content-distribution.md
  section 14) — an id in only one is refused. `approve --expected-hash H`
  refuses if `H` does not match the job's *current* artifact hash (guards
  against the artifact changing between a Telegram preview and the tap
  that approves it). `reject` moves the job to `cancelled`.
- `edit <job-id>` updates `--caption`/`--target` while the job is
  `approval-required` (issue #65). It never changes the job's state — a fresh
  `approve` is still required afterwards. Accepts the same `--channel telegram`
  gate as `approve`/`reject`. **With `--platform NAME --caption-file FILE`**
  (issue #69) it instead saves a new, never-overwritten
  `processing/<job-id>/variants/<platform>/caption.v<n>` — the job's generic
  `plan.caption` (the original/generated draft) is left untouched, and
  `publish`/`plan`'s validation preview both use the latest variant for that
  platform if one exists (docs/content-distribution.md section 15).
- `notify <job-id>` sends the Telegram approval-request preview (caption,
  target platforms, artifact sha256, and an ffmpeg-generated thumbnail
  when available) to `--chat-id` or `OMES_CONTENT_TELEGRAM_CHAT_ID`. Never
  reads `content/sessions/`; the bot token never appears in this command's
  output or in `state/audit.jsonl` (docs/content-distribution.md section 14).
- `status <job-id>` prints the job's state/platform/URL/attempts.
  `--telegram` additionally sends the same summary via `sendMessage` to
  `--chat-id`/`OMES_CONTENT_TELEGRAM_CHAT_ID`.
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
- `plan <job-id>` records `--caption` and any number of repeated
  `--target <platform>` values, then moves the job `queued` →
  `planning` → `approval-required` (`jobs.plan_job`). `--target` is
  advisory at plan time; `publish` refuses a platform that is not one of
  the recorded targets (unless no targets were recorded at all). The
  output also includes a `validation` object (issue #69): for every
  planned target, the caption/cover validation issues that would apply if
  `publish` ran right now (using whichever caption currently applies to
  that platform — see `edit --platform` below). This is a preview only —
  `plan` itself is never blocked by a validation issue, only `publish` is.
- `publish <job-id> --platform NAME` (issues #66/#69) is the manager side of
  the worker contract in `docs/content-distribution.md` section 6/13. **First**
  it resolves the caption that currently applies to `NAME` (the latest
  `edit --platform --caption-file` variant, or the generic `plan.caption` —
  `jobs.resolve_caption_for_platform()`) and validates it against
  `lib/omes/py/content/platforms/<NAME>.json` (`--cover PATH` and
  `--check-links` opt into the cover and link-reachability rules). A
  blocking (`error`-severity) issue moves the job straight to
  `manual-review` **without invoking any worker**, blocking only this one
  `--platform`'s publish; `--force-validation` overrides this. If validation
  passes (or is overridden), it resolves `NAME` to a worker executable via
  `lib/omes/py/content/workers/registry.py` (or uses
  `--worker-executable PATH` directly, mainly for tests), builds the
  explicit filesystem allowlist (`processing_dir`/`session_dir`/
  `evidence_dir`) for that job and platform, runs the worker's `prepare`
  operation, and — if `prepare` reports `ready: true` — runs `publish`
  then (if it returned a candidate URL) `verify`, applying whatever state
  transition the result classifies to (`succeeded`/`manual-review`/
  `retryable-failure`/`failed`). A job already in `publishing` (i.e. an
  operator just ran `retry`) is accepted directly — `publish` is also
  how a retry actually re-invokes the worker. Exits non-zero when the
  job ends in `manual-review`, `retryable-failure`, or `failed`.
- `session login <platform>` runs the worker's `bootstrap-session`
  operation — a manual, one-time login step that **never publishes**.
  The default `generic_browser` worker's `manual_stub` driver only writes
  a local marker file (no real browser, no real cookies); an
  operator-installed real driver (e.g. Playwright, or Hermes's own
  browser automation, referenced via `OMES_CONTENT_BROWSER_DRIVER`) would
  instead open a real browser window here for the operator to log in.
  Creates `content/sessions/<platform>/` at mode `0700` if it does not
  already exist.
- `session revoke <platform>` runs the worker's `revoke-session`
  operation, then clears the local session directory's contents (the
  directory itself is kept, still at mode `0700`). Requires `--yes` (or
  an interactive confirmation).

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

### `omes graphify` (update / uninstall / run / skill / mcp health / export / sync / status / init-ignore / purge)

`lib/omes/cmd/graphify.sh` (issues #50/#51/#52/#53/#54/#55). Full synopsis, exit codes, JSON
schemas, and examples for every `omes graphify <subcommand>` live in
[docs/graphify.md](graphify.md) §2/§3/§4/§5/§6 and [docs/graphify-privacy.md](graphify-privacy.md)
(`init-ignore`/`purge`), not here, to keep this shared reference file's graphify footprint
minimal — see those documents for the authoritative CLI contract. The optional `graphify-mcp`
server itself is installed via `omes install --module graphify-mcp`, like any other OMES module
(docs/graphify.md §4.2). `omes graphify export` (docs/graphify.md §5) renders a prior extraction
into vault-ready Markdown under an operator-supplied Obsidian vault's own managed subdirectory
only — it never installs or manages Obsidian itself. `omes graphify sync`/`status`
(docs/graphify.md §6) re-run extraction only when the source tree actually changed (an OMES-owned
manifest, not an upstream flag — see docs/graphify.md §6.1 for the corrected upstream
`update`/`watch` facts). `omes graphify init-ignore`/`purge` (docs/graphify-privacy.md) ship safe
`.graphifyignore`/`.gitignore` defaults and remove only marker/manifest-owned OMES artifacts,
never user-authored content.
## 4.16 `omes agent-backup` (opt-in Hermes data-class backup/restore)


## 4.14 `omes agent-backup` (opt-in Hermes data-class backup/restore)


`omes agent-backup create|list|verify|restore` (issue #82,
`lib/omes/cmd/agent-backup.sh`) is a separate tool from `omes
backup`/`omes restore` scoped to Hermes's own data under `$HERMES_HOME`
(config, skills, and opt-in memory/sessions/runtime-state/secrets). See
[docs/hermes-backup.md](hermes-backup.md) for the class-to-path mapping,
manifest format, and command reference.

## 4.17 `omes agent` (native OMES + Hermes agent deployment lifecycle: systemd MVP + rootless Docker Compose)


> Status: systemd MVP implemented on `feat/87-agent-lifecycle` (issue
> #87). The rootless Docker Compose backend (`spec.backend: "compose"`)
> is implemented on this branch (issue #96,
> [docs/agent-orchestration-roadmap.md section 2.2](agent-orchestration-roadmap.md)).
> `omes doctor` integration, `omes agent logs` for the compose backend,
> containerized-Hermes health reuse, and `lib/omes/runtime.sh` unit-name
> integration are implemented as `Part of #87`/`Part of #96` follow-up
> work - see [docs/agent-deployment.md](agent-deployment.md) section 11.
> Ubuntu Server 24.04 VM/real-Docker evidence is still not included for
> either backend. The Coolify backend
> ([section 2.3](agent-orchestration-roadmap.md)) remains unimplemented.

`omes agent list|doctor|check|plan|apply|status|health|restart|logs|rollback|remove`
(`lib/omes/cmd/agent.sh`, `lib/omes/py/agent/`) declares, plans, applies,
verifies, and rolls back a Hermes-runtime agent deployment, from a
versioned JSON manifest at `$OMES_CONFIG_DIR/agents/<name>.json`.
`spec.backend` selects the isolation strategy: `"systemd"` (the MVP - an
isolated `systemctl` unit) or `"compose"` (issue #96 - a rootless Docker
Compose project, for agents that need stronger filesystem/dependency/
network isolation than a systemd service provides). Full design,
manifest schema, lifecycle states, isolation model, and secret handling:
[docs/agent-deployment.md](agent-deployment.md).

## 4.14 `omes job` (Control Center control jobs)

> Status: implemented (#90). `lib/omes/cmd/job.sh` (a thin wrapper, per
> the extension-command contract in section 4.12) and
> `lib/omes/py/jobs/` (stdlib-only Python, ADR-0012). Never referenced by
> any installer profile. See [docs/jobs.md](jobs.md) for the full design
> (state machine, approval policy, operation→command table, retry and
> reconciliation semantics) and
> [docs/control-center-contracts.md](control-center-contracts.md) (#89)
> for the wire contract a submitted request must satisfy.


**Synopsis:**

```bash
omes agent list [--json]
omes agent doctor [--json]                       # every deployed agent's state + health, read-only
omes agent check <name> [--json]
omes agent plan <name> [--json]
omes agent apply <name> [--dry-run] [--yes] [--json]
omes agent status <name> [--json]
omes agent health <name> [--json]
omes agent restart <name> [--json]
omes agent logs <name> [journalctl-args...]      # systemd backend
omes agent logs <name> [--tail N] [--follow]     # compose backend (docker compose logs)
omes agent rollback <name> [--yes] [--json]
omes agent remove <name> [--yes] [--json]        # compose backend only
```

**Exit codes:** 0 ok; 2 usage error (including `remove` against a
non-compose backend, which is not implemented); 4 preflight failed
(missing/invalid manifest, or - for `backend: "compose"` - a rootful
Docker daemon/socket/docker-group-only access); 5 privilege error
(`serviceMode` vs. current EUID mismatch); 6 apply/mutation failed; 7
verification/health failed; 9 backup step failed; 10 rollback/remove
teardown failed; 1 other errors (e.g. an unconfirmed mutating call).
`doctor` always exits 0 (it is a read-only report; per-agent failure is
surfaced in its `ok`/`error` fields, not the process exit code - see
`omes doctor`'s own integration below).

### `omes agent doctor`

Reports every **deployed** agent (i.e. every name under the agent state
directory, `<state-dir>/agents/<name>/`) with its current lifecycle state
and a health summary, reusing `lib/omes/py/agent/health.py` (systemd
backend) or `lib/omes/py/agent/compose_health.py` (compose backend) -
never a second, ad hoc health check. Every check is read-only and bounded
by `OMES_HEALTH_TIMEOUT` (default 10s) per agent; a missing/invalid
manifest is reported as `"ok": false` with an `error` field rather than
crashing the whole report. `--json` shape:

```json
{"agents": [{"name": "researcher", "state": "healthy", "backend": "systemd", "serviceMode": "user", "ok": true, "ready": true, "connected": true, "health": {"layers": {"...": "..."}}}], "ok": true}
```

`omes doctor` (the top-level platform doctor, section 4.10) calls this
automatically whenever `lib/omes/cmd/agent.sh` is present and at least
one agent has been declared, adding one `agent:<name>` row per agent
(`OK` when the agent's health reports ready, `WARN` otherwise) to its own
check list - it never fails the overall `omes doctor` exit code by
itself (a degraded agent is a `WARN`, matching the existing
`module_doctor` convention), so a broken agent deployment cannot block
routine host `omes doctor` runs from reporting everything else.

### `omes agent logs` for `backend: "compose"`

Runs `docker compose -p <project> -f <compose file> logs --no-color
--tail <n>` (default `--tail 200`); `--follow` is accepted but **never**
the default - a plain `omes agent logs <name>` always returns rather than
streaming forever. Any other arguments are passed through to `docker
compose logs` verbatim. The systemd backend is unchanged: a plain
`journalctl` passthrough scoped to the agent's own unit.

`apply` is `check -> plan -> backup -> mutate -> verify`, idempotent,
and bounded: `--dry-run` performs only `check`+`plan` and prints the
planned unit name (systemd) or image digest/project/network/volumes
(compose), `HERMES_HOME`, secret **reference names** (never values), and
resource limits, mutating nothing. `rollback` removes only the
OMES-managed unit+drop-in (systemd) or tears the compose project down
and restores the previously-rendered `compose.yaml` if one exists
(compose) - never Hermes's own data under `HERMES_HOME`. `remove`
(compose backend only) additionally runs `docker compose down --volumes`
and deletes the agent's own OMES-managed compose directory.

For `backend: "compose"`, preflight (`check`, and the start of `apply`,
before any mutation) requires a **rootless** Docker daemon: it refuses
if `docker context show`/`docker info --format '{{.SecurityOptions}}'`
does not report `name=rootless`, if the active context's socket is the
well-known rootful path (`/var/run/docker.sock` or `/run/docker.sock`),
or if the invoking user's only path to that access is `docker`-group
membership on a rootful daemon. OMES never adds a user to the `docker`
group and never runs `sudo` on the agent's behalf; the agent container
is never given Docker socket access.

omes job submit --file <deployment-request.json> [--json]
omes job approve <job-id> --actor <id> [--json]
omes job run <job-id> [--actor <id>] [--json]
omes job status <job-id> [--json]
omes job list [--state STATE] [--json]
omes job cancel <job-id> --actor <id> [--json]
omes job expire [--json]

- `submit` validates `<file>` against
  `contracts/control-center/v1/deployment.request.schema.json` (rejecting,
  among other things, any free-form `command` field or unlisted
  `operation`) and creates (or, for a repeated `idempotency_key`,
  replays) a job in `queued`.
- `approve` moves a `queued` job to `approved`. Required before `run` for
  a destructive operation (`restore`, `rollback`, `stop`, `configure`)
  unless its operation name is in `OMES_JOBS_AUTO_APPROVE`.
- `run` executes the job by mapping its operation to a fixed `bin/omes`
  argv (never a shell, never a field from the request), then re-runs the
  corresponding read-back command and compares desired vs. observed state
  before reporting `succeeded`/`rolled_back`. A non-destructive job in
  `queued` is auto-approved as part of `run` when its operation is in
  `OMES_JOBS_AUTO_APPROVE`.
- `status`/`list` are read-only.
- `cancel` moves a `queued`/`approved` job to `cancelled` (not `running`
  — see docs/jobs.md's state machine for why).
- `expire` moves every `queued`/`approved` job older than
  `OMES_JOBS_TTL_SECONDS` to `expired`.

**Exit codes:** 0 (success; for `run`, only when the job reached
`succeeded`/`rolled_back`), 1 (error — schema validation failure,
cross-tenant rejection, missing approval, invalid state transition,
exhausted retries, job failed), 2 (usage error).



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
