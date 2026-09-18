# OMES

OMES is an **independent, Omarchy-inspired** compatibility layer and deployment
toolkit for **Ubuntu Server 24.04 LTS** and **Linux Mint 22.x**, with
[Hermes Agent](https://hermes-agent.nousresearch.com/) as the integrated
automation layer.

> **Disclaimer.** OMES is an independent, Omarchy-inspired project. It is
> **not affiliated with or endorsed by Omarchy/DHH or Nous Research**. It does
> not install official Omarchy (an Arch/Hyprland-based distribution shipped as
> an ISO); it brings an Omarchy-inspired, reversible workflow to Ubuntu Server
> and Linux Mint instead.

## Status

**Pre-alpha.** The bootstrap installer, preflight checks, state/backup model,
and the `apt-base` module are implemented and tested (this repository's
`bin/omes`, `lib/omes/*`, `modules/apt-base/`). Most modules referenced by the
`server`/`desktop`/`hermes` profiles are placeholders tracked by their own
issues — see [Project layout](#project-layout) and
[docs/architecture.md](docs/architecture.md) §5 for what is implemented today
versus planned.

## Supported platforms

| Platform | Tier | Notes |
|---|---|---|
| Ubuntu Server 24.04 LTS, amd64 | tier1 | Primary target |
| Linux Mint 22.x, amd64 | tier1 | Desktop target |
| Ubuntu Server 22.04 LTS, amd64 | tier2 | Supported, lower priority |
| Any supported OS above, arm64 | tier3 | Best-effort |
| Anything else (Debian, Fedora, etc.) | unsupported | `omes check`/`omes install` exit 3 before any mutation |

See [docs/compatibility-matrix.md](docs/compatibility-matrix.md) for the full
matrix and detection rules.

## Quick start

```bash
# 1. Bootstrap: clones OMES and symlinks ~/.local/bin/omes (no other mutation)
curl -fsSL https://raw.githubusercontent.com/ahliweb/omes/main/install/bootstrap.sh | bash

# 2. Preflight: read-only checks, safe to run any time, as any user
omes check

# 3. See exactly what would happen - no mutation
sudo omes install --profile server --dry-run

# 4. Apply for real (asks for confirmation unless --yes is given)
sudo omes install --profile server
```

`install/bootstrap.sh` never mutates the system beyond installing `git` (via
apt, with an explicit sudo prompt printed first) and cloning/symlinking OMES
itself; it never installs an OMES module. Every module apply is preceded by a
read-only check phase, and idempotent: re-running `omes install` on an
unchanged system makes no further changes.

## CLI overview

```text
omes check      Run platform + module preflight checks (no mutation)
omes install    Check-all-then-apply the requested profile/modules
omes status     Show platform + applied-module state summary
omes modules    List available modules (scope, description, status)
omes version    Print the OMES version
omes help       Show usage

omes doctor, backup, restore, update, uninstall
                 Not implemented yet (tracked in #10 / #14)
```

Global flags: `--profile <server|desktop|hermes>`, `--module <name>`
(repeatable), `--dry-run`, `--json`, `--yes`, `--verbose`, `--log-file <path>`,
`--allow-docker-group`.

Env overrides: `OMES_DRY_RUN=1`, `OMES_JSON=1`, `OMES_STATE_DIR`,
`OMES_LOG_FILE`, `OMES_ROOT`, `OMES_NONINTERACTIVE=1`.

**Root/user scope (no auto-escalation).** Every module declares
`MODULE_SCOPE=root` or `MODULE_SCOPE=user`. `omes check`/`omes install` only
run the modules matching the *current* effective privilege and **skip** the
rest, printing the exact follow-up command
(e.g. `sudo omes install --profile server` prints `Run as your user: omes
install --profile server` for the profile's user-scope modules once its
root-scope modules are done). OMES never calls `sudo` or `sudo -u` on your
behalf. See [ADR-0005](docs/adr/0005-root-and-user-scope-separation.md).

### Exit codes

| Code | Meaning |
|---|---|
| 0 | Success |
| 1 | General/unexpected error |
| 2 | Usage error |
| 3 | Unsupported platform (OS/arch) — detected before any mutation |
| 4 | Preflight failed (a `module_check` failed; no mutation occurred) |
| 5 | Privilege error — reserved for an explicit `--module <name>` request whose scope does not match the current privilege |
| 6 | Module apply failed (names the module) |
| 7 | Verification failed (names the module) |
| 8 | Network required but unavailable |
| 9 | Backup/restore failed |
| 10 | Rollback failed |

## Project layout

```text
bin/omes                 CLI entry point
lib/omes/                core.sh, log.sh, json.sh, detect.sh, state.sh, backup.sh, module.sh
modules/apt-base/        implemented: base CLI tooling (curl, git, python3, jq, ufw, ...)
profiles/                server.profile, desktop.profile, hermes.profile
install/bootstrap.sh     curl-able bootstrap entry point
install/preflight.sh     thin wrapper for `omes check`
tests/                   bats unit + integration tests, shims, tests/run.sh
docs/                    architecture, compatibility matrix, security, scope, ADRs, research plan
```

Modules other than `apt-base` (`security-baseline`, `hermes`,
`hermes-gateway`, `containers`, the desktop modules, etc.) are referenced by
the profile files as placeholders and implemented in later issues; see
[docs/architecture.md](docs/architecture.md) §5 for the module-to-issue
mapping.

## Further reading

- [docs/architecture.md](docs/architecture.md) — execution model, module
  contract, state/backup model, exit codes, privilege model
- [docs/compatibility-matrix.md](docs/compatibility-matrix.md) — supported
  platforms and detection rules
- [docs/security.md](docs/security.md) and
  [docs/threat-model.md](docs/threat-model.md) — security baseline and threat
  model
- [docs/scope.md](docs/scope.md) — product scope and non-goals
- [docs/research-and-implementation-plan.md](docs/research-and-implementation-plan.md) —
  research baseline and phased implementation plan
- [docs/adr/](docs/adr/) — architecture decision records
- [CONTRIBUTING.md](CONTRIBUTING.md) and
  [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)

## License

MIT. See [LICENSE](LICENSE).
