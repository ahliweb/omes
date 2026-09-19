# Hermes gateway systemd hardening

> Status: describes the actual repository state. This document covers
> `modules/hermes-gateway/hardening.sh` (issue
> [#81](https://github.com/ahliweb/omes/issues/81)), shared by
> `modules/hermes-gateway/module.sh` (user mode) and
> `modules/hermes-gateway-system/module.sh` (system mode). It complements
> [docs/hermes-integration.md](hermes-integration.md), which covers the
> gateway modules' non-hardening behavior.

## 1. Summary

Hardening is **opt-in and off by default**. It is controlled by
`OMES_HERMES_HARDENING=off|conservative|strict` (default `off`).
`conservative` is the recommended opt-in profile; `strict` is never
chosen automatically and trades some compatibility (browser automation,
some MCP subprocess tools) for a materially smaller attack surface -
operators choose it knowingly, after reading section 4 below.

Hardening never edits an operator-owned unit file. It writes a single
managed systemd drop-in, separate from the existing PATH drop-in that
both gateway modules already manage:

| Mode | PATH drop-in (existing) | Hardening drop-in (this document) |
| --- | --- | --- |
| user | `~/.config/systemd/user/hermes-gateway.service.d/omes-path.conf` | `~/.config/systemd/user/hermes-gateway.service.d/20-omes-hardening.conf` |
| system | `/etc/systemd/system/hermes-gateway.service.d/omes-path.conf` | `/etc/systemd/system/hermes-gateway.service.d/20-omes-hardening.conf` |

Both files live in the same `*.service.d` directory (systemd drop-ins
merge; ordering is by filename, `20-` sorts after the PATH drop-in's
implicit ordering) and are registered with `omes_manage_path` so they
are covered by the standard backup/rollback machinery
(`docs/rollback.md`).

## 2. Profiles

### `off` (default)

No drop-in is written. If a previous run left the hardening drop-in in
place and the profile is changed back to `off`, the next apply removes
it and reloads the daemon.

### `conservative` (recommended)

| Directive | Value | Compatibility note |
| --- | --- | --- |
| `Restart=` | `on-failure` | Standard crash-recovery; does not affect tool compatibility. |
| `RestartSec=` | `5` | Backoff between restarts. |
| `StartLimitIntervalSec=` / `StartLimitBurst=` | `60` / `5` | Caps restart storms; a genuinely crash-looping gateway stops retrying after 5 restarts in 60s instead of spinning forever. |
| `TasksMax=` | `512` | High enough for MCP subprocess trees and browser automation's helper/renderer processes; raise via env if a workload needs more. |
| `MemoryMax=` / `MemoryHigh=` | `${OMES_HERMES_MEMORY_MAX:-2G}` | Configurable; Chromium-based browser automation can be memory-hungry, hence the 2G default rather than a smaller value. |
| `CPUQuota=` | unset unless `OMES_HERMES_CPU_QUOTA` is set | Optional; left unset by default so it never silently throttles a legitimate workload. |
| `NoNewPrivileges=yes` | | Blocks privilege escalation via setuid binaries; does not affect Hermes shell tools, browser automation, or MCP subprocesses (none of those need to gain privileges they don't already have). |
| `UMask=0077` | | Only affects newly created files' default permissions; Hermes already writes `.env` at `0600` explicitly. |
| `PrivateTmp=yes` | | Gives the unit its own `/tmp`; compatible with browser automation and MCP tools, which use `$HERMES_HOME` or `$TMPDIR`, not a shared `/tmp` with other services. |
| `ProtectSystem=full` | | Read-only `/usr`, `/boot`, `/etc`; `$HOME` and `$HERMES_HOME` remain writable. **Deliberately not `ProtectSystem=strict`** (which would also make `/etc` config changes and most of the filesystem read-only in ways that are harder to reason about for a first opt-in profile). |

`conservative` intentionally does **not** set `RestrictNamespaces`
(Chromium's sandbox needs user namespaces), does **not** set
`PrivateDevices` (which would hide `/dev/snd` and `/dev/dri`, breaking
audio/video tools), and does not touch Docker CLI socket access (that
remains entirely governed by the operator's own group membership /
mount, per `docs/security.md`).

### `strict`

Adds, on top of everything in `conservative`:

| Directive | Value | What it breaks |
| --- | --- | --- |
| `ProtectHome=read-only` | | `$HOME` becomes read-only *except* the paths listed in `ReadWritePaths=` (see below). Any Hermes skill or MCP tool that writes somewhere under `$HOME` outside `$HERMES_HOME` will fail until added to `OMES_HERMES_RW_PATHS`. |
| `ReadWritePaths=` | `$HERMES_HOME` plus `OMES_HERMES_RW_PATHS` (colon-separated) | The escape hatch for the line above. |
| `ProtectKernelTunables=yes` | | Blocks writes to `/proc/sys`, `/sys`; no known Hermes tool needs this. |
| `ProtectKernelModules=yes` | | Blocks module loading; no known Hermes tool needs this. |
| `ProtectLogs=yes` | | Blocks direct journal manipulation; does not affect `journalctl` reads. |
| `RestrictSUIDSGID=yes` | | Blocks creating new setuid/setgid files; no known legitimate use in this workload. |
| `LockPersonality=yes` | | Blocks changing the process execution domain; no known legitimate use. |
| `SystemCallArchitectures=native` | | Blocks non-native syscall ABIs; can break some sandboxed browser helper binaries that ship a foreign-arch compatibility shim - this is the single most likely strict-mode browser-automation breakage. |
| `CapabilityBoundingSet=` (empty) | | Drops every Linux capability. Breaks any tool that legitimately needs one (rare in this workload, but MCP tools that shell out to `ping`, low port binds, etc. will fail). |

`strict` still does **not** set `ProtectSystem=strict` or
`PrivateDevices=` - those are considered out of scope for this profile
without a specific, documented reason (see `hardening_render` in
`modules/hermes-gateway/hardening.sh`), because they would break
audio/video device access and more of the filesystem than the
`ProtectHome`/capability changes above already do.

## 3. Environment variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `OMES_HERMES_HARDENING` | `off` | `off`, `conservative`, or `strict`. Any other value is treated as `off` with a warning. |
| `OMES_HERMES_MEMORY_MAX` | `2G` | `MemoryMax=`/`MemoryHigh=` value (systemd byte-size syntax, e.g. `1500M`, `4G`). |
| `OMES_HERMES_CPU_QUOTA` | unset | `CPUQuota=` value (e.g. `50%`); omitted entirely from the drop-in when unset. |
| `OMES_HERMES_RW_PATHS` | unset | Colon-separated extra paths added to strict mode's `ReadWritePaths=`, alongside `$HERMES_HOME`. |
| `OMES_HERMES_HARDENING_TIMEOUT` | `15` | Seconds to wait for the unit to report active after a hardening-triggered restart before automatically rolling the drop-in back. |

None of these variables carry secrets; `.env` is never read or written
by hardening.

## 4. `module_check` incompatibility prediction

Before any mutation, `hardening_check` (called from both gateway
modules' `module_check`) logs advisory warnings - it never fails the
check on its own, because these are compatibility predictions for a
profile the operator explicitly requested, not blocking preflight
failures:

- **systemd version**: parses `systemctl --version`; warns if the major
  version is below 240, since `ProtectHome=`/`ProtectSystem=` support and
  behavior on `--user` units is best verified on newer systemd (verify
  locally with `systemd-analyze verify` and `systemd-analyze cat-config`
  after applying).
- **Browser presence**: detects `chromium`, `chromium-browser`,
  `google-chrome` on `PATH`, or a Playwright cache
  (`~/.cache/ms-playwright`) - if found alongside a `strict` request,
  warns that `ProtectHome=read-only` and the empty
  `CapabilityBoundingSet=` may break browser-automation sandboxing.
- **Docker socket group membership**: reports (informationally) who is
  in the `docker` group; hardening never grants or revokes this
  membership.
- **Missing optional tools**: if `systemd-analyze` is not installed, the
  best-effort verification step is skipped silently rather than failing
  the check.

## 5. Apply, verify, doctor, rollback

- **`module_apply`**: writes the drop-in (skipped entirely for `off`,
  which instead removes any previously-written hardening drop-in),
  `daemon-reload`s, and - only after an explicit confirmation prompt
  (auto-confirmed under `--yes`/`OMES_NONINTERACTIVE=1`) - restarts the
  gateway unit. It then polls `systemctl [--user] is-active` for up to
  `OMES_HERMES_HARDENING_TIMEOUT` seconds. **If the unit does not come
  back active within that window, the hardening drop-in is automatically
  removed, the daemon is reloaded, and the unit is restarted again** -
  this is the one place in the gateway modules where automatic rollback
  is justified, because it undoes only the change this same `module_apply`
  call just made, within the same operation, before control returns to
  the operator, and the alternative is leaving the gateway down.
- **`module_verify`**: in addition to the existing unit
  enabled/active/`hermes gateway status` checks, logs
  `systemctl [--user] show hermes-gateway -p MemoryMax -p TasksMax -p
  NoNewPrivileges -p ProtectHome -p ProtectSystem` (advisory; does not
  fail verification on its own).
- **`omes doctor`** (`module_doctor`): reports one line with the active
  profile, whether the unit is currently active, and its resolved
  `MemoryMax`/`TasksMax`.
- **`module_rollback`**: removes only the OMES hardening drop-in (never
  the PATH drop-in, never an operator-owned unit file), `daemon-reload`s,
  and restarts with confirmation.

## 6. Never running as root

Neither profile changes which account runs the gateway process. User
mode never runs it as root; system mode continues to require
`OMES_HERMES_GATEWAY_SYSTEM_USER` to be a real, non-root account
(`modules/hermes-gateway-system/module.sh`'s existing invariant).
Hardening only tightens the sandbox around that same, already
unprivileged process.

## 7. Testing

- `tests/unit/hardening.bats` exercises `hardening_render`,
  `hardening_check`, `hardening_apply`, and `hardening_rollback` directly
  against `tests/shims/systemctl` and `tests/shims/systemd-analyze`
  (`off` writes nothing; `conservative`/`strict` render the directives
  above; a forced failed restart triggers the automatic rollback and
  `module_apply` surfaces it as exit 6 naming the module; rollback
  removes only the hardening drop-in; the system-mode path is exercised
  via `OMES_HERMES_GATEWAY_SYSTEM_DROPIN_DIR`).
- `tests/integration/hardening.bats` exercises the same behavior through
  `bin/omes` end to end under `OMES_DRY_RUN=1` and against the real
  module wiring.
