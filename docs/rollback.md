# OMES Rollback, Backup, and Uninstall

> Status: implemented (issue #10). Describes `lib/omes/backup.sh`, `lib/omes/restore.sh`,
> `omes backup`, `omes restore`, and `omes uninstall` as they exist in this repository today.
> `docs/architecture.md` Section 7 is the authoritative backup-model contract; this document
> is the operator-facing recovery guide built on top of it, plus what is and is not covered by
> automated tests today.

## 1. Backup layout

Every backup session lives under `<state-dir>/backups/<UTC timestamp>/` (mode `0700`):

```text
<state-dir>/backups/2026-09-18T101530Z/
  MANIFEST                 # sha256  /absolute/original/path  (one line per file)
  META                      # omes_version=, module=, reason=, started_at=, finished_at=
  home/u/.hermes/config.yaml   # the backed-up file, at its original path relative to /
```

- One session per triggering event: a module's pre-apply backup (`reason=pre-apply`), an
  on-demand `omes backup` (`reason=manual`, or `--reason <text>`), or a restore's own
  safety copy of whatever it is about to overwrite (`reason=pre-restore-backup`).
- `.env`/secret files are copied at mode `0600` regardless of their source mode; every other
  file preserves its original mode (`cp -a`, both backing up and restoring).
- Retention: the last `OMES_BACKUP_KEEP` sessions are kept (default 10, `lib/omes/backup.sh`
  `backup_prune`), pruned after every successful `module_apply`'s backup step and after every
  `omes backup`. A backup is never pruned mid-recovery — pruning only ever runs right after a
  *new* session is successfully created.
- Backups are local only, under the state directory (root scope: `/var/lib/omes/backups/`;
  user scope: `${XDG_STATE_HOME:-$HOME/.local/state}/omes/backups/`). OMES never transmits a
  backup off-host.

## 2. What is reversible, and what is not

**Reversible (OMES tracks and can undo it):**

- Any file a module registered via `omes_manage_path` before writing it — this is exactly
  what `module.<name>.managed_paths` records, and it is what `omes restore` and
  `omes uninstall` operate on. Nothing else.
- Packages a module recorded in `module.<name>.installed_packages` — `omes uninstall
  --purge-packages` removes exactly that list, never more.
- Third-party apt repository files added via `lib/omes/pkg.sh`'s `repo_add` (they go through
  `omes_manage_path` too — see `docs/packages.md`).

**Not reversible by OMES (explicit boundaries, matching `docs/scope.md` Section 5):**

- Anything outside a path a module explicitly registered via `omes_manage_path`. OMES does
  not scan the filesystem for "things that look related" to undo — only what it recorded.
- Packages a module did **not** install (already present before OMES ran, or installed by the
  operator/another tool). `module.<name>.installed_packages` only ever contains packages
  `pkg_install` (issue #9) actually installed on this host.
- Data a service/package created after OMES finished (e.g. a database an installed service
  populated, log files, user-generated content). OMES's backup granularity is "the file(s) a
  module wrote", captured once, right before that module wrote them — it is not continuous
  data protection.
- Anything changed by a manual operator action outside `omes` (e.g. hand-editing a config file
  OMES manages, then expecting `omes restore` to know about that specific edit as a
  checkpoint — it only knows about the backup sessions that actually exist).
- OS-level state OMES never touches: disk partitioning, the bootloader, kernel configuration
  (see `docs/scope.md` Section 4 — these are non-goals, not gaps).

## 3. How `omes restore` decides what to restore

`omes restore [--from <timestamp>] [--list] [--dry-run] [--yes]` (`lib/omes/restore.sh`):

1. Resolves the target session: the given `--from <timestamp>`, or the latest one.
2. Validates the session's `MANIFEST` structurally first (`backup_manifest_validate`) — every
   line must be `<64-hex-sha256>  /absolute/path`. A malformed `MANIFEST` is refused outright,
   **exit 9**, before touching anything.
3. For each entry: verifies the *backed-up copy's* current sha256 against the recorded hash
   (detects corruption of the backup itself). Any mismatch aborts immediately, **exit 9**,
   naming the failing path; files already restored earlier in this same invocation are left
   restored (a partial restore is reported, never silently rolled further back).
4. Before overwriting a file that currently exists at the destination, takes a **new**
   `reason=pre-restore-backup` backup of it first — restoring never destroys the pre-restore
   state without its own recovery path.
5. Copies the backed-up file back (`cp -a`, preserving mode/ownership) to its original
   absolute path.
6. Never touches the network. Every step above is local file I/O only.

`omes restore --list` prints every backup session (timestamp, module, reason, started_at)
without restoring anything.

## 4. How `omes uninstall` decides "restore" vs "remove"

`omes uninstall [--profile <name>] [--module <name>] [--purge-packages] [--dry-run] [--yes]`
resolves its target modules **from state only** (never blindly from a profile/module name —
a module OMES never recorded as applied is never touched), runs each target module's
`module_rollback` (module-specific extras: stopping services, printing package info) in
**reverse dependency order**, then `module_rollback_managed_paths` (`lib/omes/restore.sh`) —
the generic, path-level undo:

- If **some** backup session for that module ever captured a *pre-existing* copy of the path
  (i.e. the path existed before OMES's first `module_apply` touched it), the path is
  **restored** from the OLDEST such backup — reconstructing exactly what was there before
  OMES ever ran.
- If **no** backup session ever captured it, OMES created that path itself, fresh — it is
  **removed** outright.
- A path never listed in `module.<name>.managed_paths` is never touched, restored, or
  removed — this is the file-level mechanism behind "uninstall never deletes user data"
  (`docs/scope.md` Section 5).

Packages: `omes uninstall` always **prints** (`log_info`) the packages the target module
recorded as installed, and the exact `apt-get remove <pkgs>` command an operator could run
themselves. It removes them only with `--purge-packages`, and even then only the packages
recorded for that specific module — never a package it did not install. On success, the
module's `installed_packages` state key is cleared (nothing is left to purge next time); the
module's status becomes `removed`.

Any `module_rollback` or managed-path-rollback failure stops the whole `uninstall` invocation
immediately, **exit 10**, naming the module.

## 5. Step-by-step recovery

### (a) A failed install

`omes install` stops at the first failing module (exit 6 apply failure, exit 7 verify
failure) and leaves earlier modules' state untouched. To recover:

```bash
omes status                       # see which module failed and when
omes restore --list               # see the pre-apply backup(s) taken for it
omes restore --yes                # restore the latest backup (the failed module's pre-apply state)
```

If the failure was package-related (see `docs/packages.md`'s failure-mode matrix), re-running
`omes install` after fixing the underlying cause (network, repository) is usually simpler than
a restore, since `module_apply` is idempotent and nothing was left half-written to disk that
`omes restore` would need to fix — the backup-before-mutate step means the module's own files
were never touched until the specific write that then succeeded or was skipped.

### (b) A corrupted/misconfigured file that OMES manages

```bash
omes restore --list               # find the session before the change you want to undo
omes restore --from <timestamp> --yes
```

`omes restore` always takes a fresh backup of the file it is about to overwrite first, so this
step is itself reversible if you picked the wrong timestamp.

### (c) A full uninstall

```bash
omes uninstall --dry-run                     # see exactly what would happen first
omes uninstall --yes                         # roll back every applied module (this scope's state dir)
omes uninstall --purge-packages --yes        # ...and also remove the packages OMES installed
```

Scope: `omes uninstall` only ever acts within the current effective privilege's own state
directory (root scope vs. user scope are entirely separate — Section 6.1 of
`docs/architecture.md`), exactly like `install`/`check`. Run it once as root and once as your
user to fully uninstall a mixed-scope profile.

## 6. Data-loss boundaries

- **Backups themselves are not backed up.** If the state directory (and its `backups/`
  subdirectory) is lost — disk failure, `rm -rf` of the state dir — there is nothing left to
  restore from. OMES's backups protect against *OMES's own* mutations, not host-level data
  loss.
- **Retention is finite.** Once a session ages past the last `OMES_BACKUP_KEEP` (default 10),
  it is deleted. If you need a specific historical state kept indefinitely, copy that one
  session directory out of `backups/` yourself before it ages out.
- **Purging packages can remove more than OMES's own files depend on** if something else on
  the host started depending on a package OMES installed after the fact — apt's own dependency
  resolution runs during `apt-get remove`, and OMES does not model or block that; the printed
  command is always visible before `--purge-packages` runs it, precisely so an operator can
  review it first.
- **Uninstall does not undo data a running service created** (Section 2) — only the files
  OMES itself wrote.

## 7. Restoring with no network

Every step in Sections 3 and 4 above is local file I/O and local state-file reads/writes only
— no code path in `lib/omes/restore.sh`, `omes restore`, or `omes uninstall` makes a network
call. `tests/integration/restore.bats` proves this directly with `OMES_ASSUME_OFFLINE=1`.

## 8. What has automated test coverage today

| Scenario | Covered by |
|---|---|
| Backup → modify → restore round-trip, verified by checksum | `tests/unit/restore.bats`, `tests/integration/restore.bats` |
| Corrupt-`MANIFEST` refusal (exit 9) | `tests/unit/restore.bats`, `tests/integration/restore.bats` |
| Checksum-mismatch-on-backed-up-copy refusal (exit 9) | `tests/unit/restore.bats` |
| `omes restore` works fully offline | `tests/unit/restore.bats`, `tests/integration/restore.bats` |
| `omes restore --dry-run` mutates nothing | `tests/unit/restore.bats`, `tests/integration/restore.bats` |
| `omes restore --from <timestamp>` targets a specific session | `tests/integration/restore.bats` |
| `omes restore --list` (human and `--json`) | `tests/integration/restore.bats` |
| Restore takes a fresh pre-restore-backup before overwriting | `tests/unit/restore.bats` |
| `module_rollback_managed_paths`: restore-if-pre-existing vs. remove-if-OMES-created, from the OLDEST matching backup, never touching an unrelated path | `tests/unit/restore.bats` |
| `backup_prune` retention (`OMES_BACKUP_KEEP`) | `tests/unit/backup.bats` |
| `omes backup` (all managed paths, `--module`, `--reason`, `--dry-run`, `--json`) | `tests/integration/backup.bats` |
| `omes uninstall --dry-run` prints packages without removing them or mutating state | `tests/integration/uninstall.bats` |
| `omes uninstall --purge-packages` removes exactly the recorded packages, once, and clears the record | `tests/integration/uninstall.bats` |
| `omes uninstall` without `--purge-packages` never calls `apt-get remove` | `tests/integration/uninstall.bats` |
| `omes uninstall` rollback failure → exit 10, naming the module | `tests/integration/uninstall.bats` |
| Explicit wrong-scope `--module` request to `uninstall` → exit 5 | `tests/integration/uninstall.bats` |

**Not covered by an automated test (manual verification only, or deferred):** restoring a
directory (as opposed to a single file) captured by `backup_path`'s recursive mode; a
full mixed-scope (`server` profile with both root- and user-scope modules) uninstall running
as two separate invocations — no user-scope module exists yet to exercise this (tracked
alongside the modules that will add one, e.g. #11).

## 9. Tested scenarios (disaster recovery, issue #17)

`docs/disaster-recovery.md` is the operator-facing runbook set (symptoms, exact commands,
expected output, data-loss boundary, and verification per scenario); this section is the index
of which automated test proves which of its claims.

| Disaster-recovery scenario (`docs/disaster-recovery.md` section) | Automated test |
|---|---|
| 2.1 Failed installation mid-profile — earlier module's managed file restored, re-install converges | `tests/integration/dr-failed-install.bats` |
| 2.2 Broken desktop session — `uninstall --module hyprland-session` removes only the OMES session entry/wrapper, Cinnamon untouched, user config restored | `tests/integration/dr-desktop-session.bats` (skips with a clear message if issue #8's modules are absent — never faked) |
| 2.3 Failed Hermes gateway — `module_verify` fails when inactive (`omes doctor` FAIL not WARN), rollback disables the unit and OMES-enabled lingering, re-install recovers | `tests/integration/dr-gateway.bats` |
| 2.4 Corrupted configuration — `omes restore --from <ts>` repairs a truncated/garbled managed file; a corrupt `MANIFEST` is refused, exit 9 | `tests/integration/dr-corrupted-config.bats` |
| 2.5 Corrupted/deleted state file — `omes restore` still works from the backup directory alone | `tests/integration/dr-corrupted-state.bats` |
| 2.6 SSH locked out by firewall | Manual only — requires real console/out-of-band access; the underlying "SSH rule added before `ufw enable`" ordering is covered by `tests/unit/security-baseline.bats` / `tests/integration/server.bats` |
| 2.7 Lost sudo/root | Manual only — OS account recovery is outside OMES's own scope (`docs/scope.md` section 4) |
| 2.8 OMES state directory deleted entirely (no backups left) | Manual only for the "nothing left to restore" end state; the closely related "state file corrupted/deleted, `backups/` intact" case is `tests/integration/dr-corrupted-state.bats` |
| 2.9 Restoring from a backup copied off-host | Manual only for the literal two-host case; the underlying restore mechanism (backup-session-location-independent) is the same code path as `tests/unit/restore.bats` / `tests/integration/restore.bats` |

Two confirmed gaps were found and are tested (not silently assumed) rather than papered over —
see `docs/disaster-recovery.md` sections 2.4 and 2.5 for the full explanation and a suggested
follow-up for each:

- `omes doctor` has no checksum-based check that a managed file's *content* still matches what
  OMES last wrote — only functional checks (package present, unit active, binary on `PATH`).
- `omes status` does not fail with a clear "state file corrupted" message; a garbled state file
  is silently under-reported rather than flagged.

Evidence from running the container-matrix side of these scenarios (`scripts/test-matrix.sh`'s
`dr-*` scenarios, where applicable) is archived the same way `docs/testing.md` section 3
describes — not committed to the repository, referenced from a PR body / release review instead.
