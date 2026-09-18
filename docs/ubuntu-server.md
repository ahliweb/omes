# Ubuntu Server Headless Profile

> Status: describes the actual repository state. Sections marked **Not
> implemented yet** are tracked by the linked issue; everything else
> reflects `profiles/server.profile`, `modules/security-baseline/module.sh`,
> and `modules/containers/module.sh` as they exist today (issue
> [#7](https://github.com/ahliweb/omes/issues/7)).
>
> OMES is an independent, MIT-licensed, Omarchy-inspired compatibility
> layer for Ubuntu Server 24.04/22.04 LTS and Linux Mint 22.x. It is not
> official Omarchy. This document covers the **server** profile: a
> headless host with Hermes Agent as the primary operations surface.
>
> **Verification legend:** each behavior below is marked either
> **(tested)** - covered by `tests/unit/{security-baseline,containers}.bats`
> and/or `tests/integration/server.bats`, exercised in CI against shims,
> never against a real host - or **(VM matrix)** - only verifiable against
> a real Ubuntu Server/Linux Mint installation, tracked by the VM test
> matrix, issue [#15](https://github.com/ahliweb/omes/issues/15), **not
> implemented yet**.

## 1. What the server profile installs

`profiles/server.profile` currently resolves to, in `MODULE_REQUIRES`
order:

| Module | Scope | Purpose |
|---|---|---|
| `apt-base` | root | Base CLI tooling (curl, git, python3, jq, ufw, ...) |
| `security-baseline` | root | Firewall (ufw), unattended security upgrades, journald log hygiene, NTP check |
| `hermes` | user | Hermes Agent CLI, installed per-user |
| `hermes-gateway` | user | Hermes gateway as a per-user systemd service (boot-persistent via `loginctl enable-linger`) |

`containers` (Docker Engine) is listed in the profile file but **commented
out** - it is optional and never applies unless explicitly requested (see
Section 5). `hermes-gateway-system` (a system-wide gateway alternative) is
also never applied by the default profile; see `docs/hermes-integration.md`
part 2 for that decision.

Because root-scope and user-scope modules cannot run in the same `omes`
invocation (the module contract refuses a root-scope module as non-root and
a user-scope module as root - `docs/architecture.md` Section 4.5), a full
server profile install is always two commands: one as root, one as the
operator's own user.

## 2. End-to-end walkthrough

Run on a freshly provisioned Ubuntu Server 24.04 LTS (or 22.04 LTS, or
Linux Mint 22.x used headless) host, as the operator's own non-root user
with `sudo` access.

### 2.1 Bootstrap

```console
$ curl -fsSL https://raw.githubusercontent.com/ahliweb/omes/main/install/bootstrap.sh | bash
```

**Privileged commands this may run, and why:** `install/bootstrap.sh` only
escalates once, and only if `git` is missing: `sudo apt-get update` and
`sudo DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends git`
(both printed before they run). Everything else - cloning the repository to
`~/.local/share/omes` and symlinking `~/.local/bin/omes` - runs as the
invoking user. The script never mutates further than that; it hands off to
`omes check` and prints the next commands. **(VM matrix)** - a real network
fetch and a real git install.

### 2.2 Check (no mutation)

```console
$ omes check --profile server
```

Detects the OS/arch/tier, and for every module in the profile: root-scope
modules report `requires root privilege` and are listed as **skipped, not
failed** (this is normal for a first, non-root run - see
`docs/architecture.md` Section 4.5); user-scope modules run their real
`module_check`. Exit code `4` here means a real preflight problem (e.g. a
package unavailable in the configured repositories); exit code `0` with
skipped root-scope modules is the expected non-root result. **(tested)** -
`tests/integration/server.bats` ("check --profile server as root includes
security-baseline and passes").

### 2.3 Install as root

```console
$ sudo omes install --profile server --dry-run --yes   # preview, no mutation
$ sudo omes install --profile server --yes
```

**What runs as root, and why:** `apt-base` and `security-baseline` are
`MODULE_SCOPE=root` - they install/configure system-wide packages
(`ufw`, `unattended-upgrades`), the firewall, and `/etc`-rooted config
files, none of which a non-root user can do. `sudo omes install` refuses to
touch anything outside those two modules; `hermes`/`hermes-gateway` are
silently skipped (with a printed follow-up command) because they are
user-scope. `--dry-run` first is the recommended habit: it prints every
planned command via `omes_run` (prefixed `[dry-run] would run:`) and
mutates nothing - **(tested)**, `tests/integration/server.bats` ("install
--profile server --dry-run as root makes no ufw or apt-get install
calls").

### 2.4 Install as your own user

```console
$ omes install --profile server --yes
```

Installs Hermes Agent and its gateway for the invoking user (never as
root - see `docs/hermes-integration.md`). This is the command
`sudo omes install --profile server` itself prints as the exact follow-up.

### 2.5 Diagnose

```console
$ omes doctor
$ sudo omes doctor   # also verifies root-scope modules (apt-base, security-baseline, containers)
```

`omes doctor` (issue [#14](https://github.com/ahliweb/omes/issues/14)) is
implemented: OK/WARN/FAIL per check, exit code `0` unless something is
`FAIL`. For every module recorded as applied, it runs that module's
`module_verify` (OK/FAIL) plus - when the module defines one - its
optional `module_doctor` hook, treating a non-zero return as `WARN`.
`security-baseline` and `containers` both implement `module_doctor`:

- `security-baseline`: current `ufw status verbose` firewall state, a
  simulated pending-security-updates count (`apt-get -s upgrade`), and
  NTP sync state - `WARN` if the firewall is not active or the clock is
  not NTP-synchronized.
- `containers`: `docker version` reachability, whether `docker.service`
  is enabled, and the current access policy - `WARN` if docker is
  unreachable, the service is disabled, **or** OMES granted `docker`
  group membership (per `docs/threat-model.md` T06, that root-equivalent
  grant must stay visible on every run, not just at install time).

Run `omes doctor` as your own user first (verifies `hermes`/
`hermes-gateway`), then `sudo omes doctor` (verifies `apt-base`/
`security-baseline`/`containers`) - each privilege level can only verify
the modules that match its own scope; the other modules are reported as
`WARN "requires root to verify"` / `WARN "must not run as root"`, not a
failure. **(tested)** - `tests/unit/security-baseline.bats`,
`tests/unit/containers.bats` exercise both `module_doctor` functions
directly; `bin/omes cmd_doctor`'s own OK/WARN/FAIL wiring is covered by
`tests/integration/doctor.bats` (outside this module's file scope, not
modified here beyond the same test-isolation setup as Section 2.3).

Complementary manual commands:

```console
$ sudo ufw status verbose
$ systemctl is-enabled --now unattended-upgrades.service 2>/dev/null; systemctl list-timers apt-daily-upgrade.timer
$ timedatectl status
```

### 2.6 Reboot test

Confirm the boot-persistent pieces actually survive a reboot: `ufw`
(enabled via `systemctl enable` as a side effect of the `ufw` package
itself, independent of any single `omes` run), the `apt-daily.timer`/
`apt-daily-upgrade.timer` systemd timers that drive unattended-upgrades,
and (if installed) `hermes-gateway`'s per-user service plus
`loginctl enable-linger` (see `docs/hermes-integration.md` Section 12.3).

```console
$ sudo reboot
# after reboot:
$ sudo ufw status verbose
$ systemctl list-timers apt-daily-upgrade.timer
$ systemctl --user status hermes-gateway   # as the operator's user
```

**(VM matrix)** - a real reboot cannot be exercised in the bats/shim test
environment.

### 2.7 Status

```console
$ omes status
$ omes status --json
```

Reports the detected platform and, for every module with any recorded
state, its status (`applied`/`removed`/`unknown`) and `applied_at`
timestamp - including `security-baseline` and, if installed, `containers`.
**(tested)** - `tests/integration/server.bats`.

## 3. Firewall and the SSH lockout guard

`security-baseline` configures `ufw` with `default deny incoming` /
`default allow outgoing` (`docs/security.md` Section 3). Before it ever
runs `ufw --force enable`, it checks - in this order - whether SSH access
must be kept open, and if so adds the allow rule **first**, logging a
`WARN` naming exactly why:

1. `OMES_ENABLE_SSH=1` is set (this module's environment-variable stand-in
   for the brief's future `--enable-ssh` CLI flag - `bin/omes` flag parsing
   is outside this module's file scope, so there is no `--enable-ssh` flag
   yet; set the env var instead: `OMES_ENABLE_SSH=1 sudo omes install
   --profile server --yes`).
2. The current process is already inside an SSH session
   (`$SSH_CONNECTION`/`$SSH_TTY` set).
3. `who am i` reports a remote host in parentheses (covers `su -`-style
   invocations that lose `$SSH_CONNECTION`).
4. The `ssh`/`sshd` systemd service is active or enabled.

If none apply, no SSH rule is added and the firewall comes up with SSH
closed - the documented default for a profile that does not assume SSH
should be open just because it is headless (`docs/security.md` Section 3).
**OMES never removes or denies an existing SSH allowance** once granted,
on any later run (including `module_rollback`).

The allowed port is auto-detected: `sshd -T`'s `port` line, falling back to
`Port` in `sshd_config`, falling back to `22`. A non-default port gets an
explicit `<port>/tcp` rule instead of the named `OpenSSH` profile.

Every run is idempotent: re-applying makes no further `ufw` calls once the
firewall already matches the desired state (active, default deny incoming,
default allow outgoing, SSH rule present if required).

**(tested)** - `tests/unit/security-baseline.bats` covers all four
detection paths, the before-enable ordering, the idempotent re-run, the
custom-port case, and that dry-run never calls `ufw --force enable`.
**(VM matrix)** - that a real `sshd`/`ufw` on a real host behave the same
way end to end.

## 4. Update policy

`security-baseline` installs `unattended-upgrades` and manages its own
config fragment, `/etc/apt/apt.conf.d/52omes-unattended-upgrades` - a
**separate, OMES-owned file**. It never edits the distro's own
`/etc/apt/apt.conf.d/50unattended-upgrades` or `20auto-upgrades`. Since
`apt.conf.d` files are cumulative and a later-sorting file's settings win,
`52omes-unattended-upgrades` enables the periodic timers itself
(`APT::Periodic::Update-Package-Lists "1"`,
`APT::Periodic::Unattended-Upgrade "1"`) and restricts the allowed origin
to the **security pocket only**:

```
Unattended-Upgrade::Allowed-Origins {
    "${distro_id}:${distro_codename}-security";
};
Unattended-Upgrade::Automatic-Reboot "false";
```

No automatic reboot by default. Opt in with
`OMES_UNATTENDED_AUTO_REBOOT=1` (writes
`Unattended-Upgrade::Automatic-Reboot "true"` plus a reboot time, default
`02:00`, overridable via `OMES_UNATTENDED_REBOOT_TIME`).

Journald log hygiene is managed separately, at
`/etc/systemd/journald.conf.d/omes.conf`
(`SystemMaxUse=500M` by default, overridable via `OMES_JOURNALD_MAX_USE`;
`Compress=yes`, fixed). `systemd-journald` is only restarted when this
file's content actually changes.

`security-baseline` also checks (never mutates unless asked)
`timedatectl`'s NTP-synchronized state, logging a `WARN` if the clock is
not synchronized. It never installs `chrony` unless `OMES_INSTALL_CHRONY=1`
is set.

**(tested)** - content of both config files, the "never touch
50unattended-upgrades/20auto-upgrades" invariant, journald-restart-only-
on-change, the `OMES_UNATTENDED_AUTO_REBOOT`/`OMES_INSTALL_CHRONY` opt-ins,
and the NTP warning path (`tests/unit/security-baseline.bats`).
**(VM matrix)** - that `apt-daily-upgrade.service` actually performs a
security-only upgrade on a real host.

## 5. Docker (optional): `containers` module

Docker is **never installed by default**. Enable it explicitly:

```console
$ sudo omes install --module containers --yes
```

or uncomment the `containers` line in `profiles/server.profile`. This
follows `docs/adr/0007-docker-access-policy.md`: Docker group membership is
root-equivalent, so nothing about it is a silent default.

`module_apply` downloads Docker's official signing key to
`/etc/apt/keyrings/docker.asc` (verified ASCII-armored/PEM, mode `0644`),
adds Docker's official apt repo as a deb822 `.sources` file via `https://`
+ `Signed-By`, and installs `docker-ce docker-ce-cli containerd.io
docker-buildx-plugin docker-compose-plugin`. On **Linux Mint**, Docker does
not officially support Mint - OMES uses `$UBUNTU_CODENAME` for the apt
suite instead and logs an explicit non-parity `WARN`; no support parity is
claimed.

### 5.1 The three access levels and their risk

| Level | How to enable | Risk |
|---|---|---|
| **`sudo docker`** (default) | Nothing to do - this is what you get by just installing the module | Lowest. Every Docker command needs `sudo`; no standing elevated access. |
| **Rootless Docker** | `OMES_DOCKER_ROOTLESS=1 sudo omes install --module containers --yes`, then run `dockerd-rootless-setuptool.sh install` **as your own user, never as root** | Low-medium. The daemon itself does not run as root. Requires prerequisites (see below); OMES installs `docker-ce-rootless-extras` and prints the exact user-level command - it never runs that command for you. |
| **`docker` group membership** | `sudo omes install --module containers --allow-docker-group --yes` (requires **both** the flag **and** `--yes`/interactive confirmation) | **High - root-equivalent.** A member of the `docker` group can bind-mount the host root filesystem into a container and escape any container boundary, with no `sudo` needed. OMES prints this warning at the exact moment it makes the change and records it in state so `omes status`/rollback can see it. |

Rootless prerequisites (all must hold, or OMES reports exactly which are
missing and skips the rootless-extras install, leaving Docker itself
installed and functional via `sudo docker`): the `uidmap` and
`dbus-user-session` packages, `SUDO_USER` resolvable to the invoking
non-root user, `/etc/subuid`/`/etc/subgid` entries for that user, and an
active systemd user session for that user.

Group membership is refused outright if `SUDO_USER` is empty (never adds
`root`), and is only ever granted with an explicit `--allow-docker-group`
**and** `--yes`/interactive confirmation - declining the confirmation
(or running non-interactively without `--yes`) makes no change.
`module_rollback` removes a group grant OMES itself made (`gpasswd -d
<user> docker`), stops/disables `docker.service`, and removes the repo
file and signing key - it never removes the Docker packages themselves
(prints the exact `apt-get remove` command instead).

**(tested)** - repo/keyring correctness, the Mint codename + WARN path,
all three access levels (default no-op, rootless success/failure
reporting, group grant/refusal/rollback), and dry-run touching neither
`curl` nor `apt-get install` nor `usermod`
(`tests/unit/containers.bats`). **(VM matrix)** - a real `dockerd`, a real
rootless setup, and a real escape-the-container demonstration are
intentionally not exercised by any automated test.

## 6. What "no GUI by default" means

The server profile never installs a display manager, a compositor, or any
desktop package. `detect_session` (`lib/omes/detect.sh`) reports `server`
unless a graphical target or `XDG_SESSION_TYPE` is detected, and nothing in
`apt-base`, `security-baseline`, `hermes`, `hermes-gateway`, or `containers`
depends on or pulls in graphical packages. Operators wanting a desktop use
the separate `desktop` profile (Linux Mint, opt-in Hyprland - see the
desktop-profile documentation) - the two are not mixed.

## 7. Status and diagnostic commands

| Command | What it shows |
|---|---|
| `omes check --profile server` | Preflight only, no mutation. |
| `omes status` / `omes status --json` | Platform + per-module applied/removed state. |
| `omes modules` | Every discovered module, its scope, and current status. |
| `sudo ufw status verbose` | Live firewall state (what `security-baseline`'s own idempotency check reads). |
| `systemctl list-timers apt-daily-upgrade.timer` | Whether the security-upgrade timer is scheduled. |
| `timedatectl status` | NTP sync state. |
| `journalctl -u apt-daily-upgrade.service -n 20` | Recent unattended-upgrades run log. |
| `docker version` (if `containers` installed) | Confirms the daemon is reachable. |

## 8. Troubleshooting

### 8.1 "I ran install and now I'm locked out over SSH"

This should not happen - the lockout guard (Section 3) is a hard invariant,
not an opt-out default, and it is added **before** `ufw` is ever enabled.
If it does happen anyway (e.g. a non-standard sshd port that
`_security_sshd_port` could not detect):

1. Use the **cloud/VM provider's serial/VNC console** (out-of-band from
   SSH) to log in locally.
2. Check what's actually configured: `sudo ufw status verbose`.
3. Allow the correct port: `sudo ufw allow <port>/tcp`, or reset entirely
   (Section 8.2) and re-run `sudo omes install --profile server --yes`
   with `OMES_ENABLE_SSH=1` set, or with an active SSH session already
   open (condition 2/3 in Section 3).

### 8.2 Resetting ufw entirely

```console
$ sudo ufw --force reset
$ sudo omes install --profile server --yes
```

`ufw --force reset` clears all rules and returns to ufw's install
defaults; re-running `omes install` re-applies OMES's policy from scratch,
including the SSH guard.

### 8.3 unattended-upgrades not running / logs

```console
$ systemctl list-timers apt-daily-upgrade.timer   # is it scheduled?
$ journalctl -u apt-daily-upgrade.service -n 50   # what did the last run do?
$ journalctl -u unattended-upgrades.service -n 50 # if present on this release
$ sudo cat /etc/apt/apt.conf.d/52omes-unattended-upgrades   # OMES's own config
```

If the timer is not enabled at all, `security-baseline`'s package install
step may not have completed - re-run `sudo omes install --profile server
--yes` and check its output for errors.

### 8.4 Docker rootless setup fails

Re-run `sudo omes install --module containers --dry-run --yes` with
`OMES_DOCKER_ROOTLESS=1` set and read the reported missing prerequisites
exactly - OMES lists each one by name (missing package, missing
subuid/subgid entry, or no active user session) rather than a generic
failure.

## 9. Known limitations

- There is no `--enable-ssh` / `--rootless` CLI flag yet; both are
  environment-variable opt-ins (`OMES_ENABLE_SSH=1`,
  `OMES_DOCKER_ROOTLESS=1`) pending `bin/omes` flag-parsing changes, which
  are outside this module's file scope.
- `ss` and `journalctl` test shims exist for future diagnostics but are not
  yet exercised by any required check path beyond `module_doctor`.
- The VM test matrix (real reboot, real `sshd`/`ufw` interaction, real
  `apt-daily-upgrade` runs, real rootless Docker) is tracked in
  [#15](https://github.com/ahliweb/omes/issues/15) and **not implemented
  yet** - everything marked **(VM matrix)** above is unverified beyond the
  shim-based test suite.
