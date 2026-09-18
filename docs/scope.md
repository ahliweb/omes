# OMES scope, product boundary, and non-goals

> Status: accepted baseline (Phase 0 — Foundation and design decisions)
> Related: [docs/research-and-implementation-plan.md](research-and-implementation-plan.md), [Issue #1](https://github.com/ahliweb/omes/issues/1)

## 1. What OMES is

OMES ("Omarchy-inspired compatibility layer and deployment toolkit") is an
independent, MIT-licensed installer and operations toolkit that brings an
Omarchy-inspired workflow to two specific, already-installed Linux targets:

- **Ubuntu Server 24.04 LTS** (and 22.04 LTS as a tier-2 target), used
  headless, with **Hermes Agent** as the primary automation and
  agentic-operations layer.
- **Linux Mint 22.x**, used as a desktop, with an **opt-in** Hyprland/Wayland
  session layered on top of the existing Cinnamon installation.

OMES operates on a host that already has Ubuntu Server or Linux Mint
installed. It is a layer of package installation, configuration templates,
systemd services, checks, backups, and rollback — not an operating system,
not an installer image, and not a distribution.

OMES is a "compatibility layer" in the sense that it selectively reproduces
the *parts of the Omarchy workflow that make sense on apt/systemd hosts*
(terminal-first tooling, mise, themable configuration, an agent-first
operations model, explicit update/backup/rollback flows) while rejecting or
adapting the parts that depend on Arch-specific mechanisms. The full mapping
of what is ported, adapted, deferred, or rejected is tracked separately in
[docs/omarchy-compatibility-inventory.md](omarchy-compatibility-inventory.md).

## 2. What OMES is not

OMES is **not** official Omarchy, and it must never be described or marketed
as "Omarchy for Ubuntu," an Omarchy edition, or an Omarchy-affiliated
product. The distinction is architectural, not just a naming preference:

| Aspect | Official Omarchy | OMES |
|---|---|---|
| Base OS | Arch Linux | Ubuntu Server 24.04/22.04 LTS, Linux Mint 22.x (existing installs) |
| Compositor | Hyprland (mandatory, Wayland-only) | Optional, desktop-profile-only, opt-in on Mint; server profile has no GUI |
| Distribution mechanism | Bootable ISO, full-disk install | apt packages + shell installer running on an already-installed host |
| Package manager | pacman / AUR | apt only; no pacman, no AUR, no source builds by default |
| Boot/snapshot model | systemd-boot or Limine with btrfs snapshots | None; OMES does not touch the bootloader or take filesystem snapshots |
| Automation layer | Omarchy's own AI menu/tooling | Hermes Agent (a separate, Nous Research product) as the first-class automation layer |
| Relationship to upstream | Is Omarchy | Inspired by Omarchy's workflow; independent project, no affiliation |

See [docs/branding-and-trademarks.md](branding-and-trademarks.md) for the
exact naming and disclaimer rules that follow from this distinction.

## 3. The two modes

OMES ships two profiles (a third, `hermes`, exists as a shared building
block used by both — see the CLI and module contract in
[docs/research-and-implementation-plan.md](research-and-implementation-plan.md)).

### 3.1 Ubuntu Server headless mode (`server` profile)

- No GUI, no display server, no desktop packages installed.
- Hermes-first: Hermes Agent is the primary way an operator interacts with
  and automates the host after initial provisioning.
- systemd is the only service manager targeted; Hermes gateway runs as a
  systemd unit (user unit with lingering enabled, or a system unit).
- Everything installed is observable through `omes status`, `omes doctor`,
  `journalctl`, and structured (JSON) command output.
- Docker is optional and, when enabled, defaults to `sudo docker` or
  rootless Docker; membership in the `docker` group is never granted
  automatically (see the destructive-operation policy below).
- Intended primary use case: a small team's or freelancer's automation/agent
  host, or an internal server that needs a reproducible, agent-operated
  baseline.

### 3.2 Linux Mint desktop mode (`desktop` profile)

- Cinnamon is the default session on Linux Mint and is **never removed or
  disabled** by OMES.
- Hyprland is an **opt-in, additional** session entry. Enabling it does not
  remove Cinnamon, and login always offers a way back to Cinnamon.
- Desktop-only components (terminal emulator, bar/launcher, notifications,
  clipboard, idle/lock, screenshot tooling) are installed only when the
  desktop profile is selected; they do not affect the server profile.
- GPU, Mesa, Wayland, portal, and display-manager compatibility are
  preflight-checked before any Hyprland-related mutation, per
  [docs/compatibility-matrix.md](compatibility-matrix.md) (tracked in a
  parallel workstream).
- Docker on Mint is supported on a best-effort basis only; see non-goals
  below.

## 4. Non-goals

OMES explicitly does **not**:

1. Build or publish a bootable ISO or any full-disk installer image.
2. Perform disk partitioning, formatting, or filesystem provisioning.
3. Manage the bootloader, or provide Limine-style boot snapshots. OMES
   backups are file-level (see §5), not filesystem or boot snapshots.
4. Use pacman, AUR, or any Arch package/mirror mechanism.
5. Build software from source as a default installation path. Source
   builds, when unavoidable for a specific optional component, must be
   explicitly opt-in and documented as higher-risk.
6. Claim Docker support parity between Ubuntu and Linux Mint. Docker
   publishes official support for Ubuntu only; on Mint, OMES detects the
   host, warns, and uses the underlying `$UBUNTU_CODENAME` repository, but
   does not promise the same support level.
7. Act as a general configuration-management replacement (e.g., it is not
   Ansible/Puppet/Chef). OMES manages a fixed, documented set of modules and
   paths for its own profiles; it is not a general-purpose orchestration
   tool for arbitrary infrastructure.
8. Claim to be, or imply affiliation with, official Omarchy, Ubuntu
   Canonical, or Linux Mint. See
   [docs/branding-and-trademarks.md](branding-and-trademarks.md).

## 5. Destructive-operation policy

OMES follows a strict **no-destructive-default** policy:

- **No destructive action is ever the default.** Every command that can be
  destructive requires either an explicit flag (e.g. `--purge-packages`,
  `--allow-docker-group`) or explicit confirmation (`--yes` is required for
  any non-interactive mutation; interactive sessions prompt).
- **Every mutation is preceded by a preflight check and a backup.** The
  module runner contract (see
  [docs/research-and-implementation-plan.md](research-and-implementation-plan.md))
  requires `module_check` to pass for *all* modules before any
  `module_apply` runs, and every managed path is backed up (with a
  manifest and checksums) before it is modified.
- **Uninstall never deletes user data.** `omes uninstall` removes only the
  files, services, and configuration that OMES itself recorded as managed
  in its state file. Packages that OMES installed are left installed and
  merely reported, unless the operator passes `--purge-packages`. Nothing
  outside OMES-managed paths is ever touched.
- **Rollback is a first-class operation.** `omes restore` can restore the
  latest or a named backup and must work fully offline.

### Things OMES will never do without an explicit flag

- Add a user to the `docker` group (root-equivalent access) — only via
  `--allow-docker-group`, with a printed warning.
- Remove or purge a package it installed — only via `--purge-packages`.
- Delete a backup — backups are retained until an operator removes them
  manually; OMES does not prune backups automatically in the MVP.
- Disable or remove the Cinnamon session on Linux Mint.
- Overwrite a config file it manages without first writing a backup copy
  with a checksum manifest.
- Store secrets (API keys, Telegram tokens, etc.) in git-tracked files,
  command-line arguments, or plain log output. Secrets live only in
  `$HERMES_HOME/.env` (mode `0600`) or equivalent, and backups of `.env`
  files preserve that mode without echoing secret contents into logs.

## 6. Supported platform summary

Full details, tiers, and per-release caveats live in
[docs/compatibility-matrix.md](compatibility-matrix.md) (written in a
parallel workstream). Summary as of this writing:

| Target | Tier | Profile |
|---|---|---|
| Ubuntu Server 24.04 LTS, amd64 | 1 | server |
| Ubuntu Server 22.04 LTS, amd64 | 2 | server |
| Linux Mint 22.x, amd64 | 1 (desktop) | desktop |
| arm64 (either target) | 3, best-effort | server / desktop |
| Anything else (other distros, other arches) | Unsupported — `omes check` exits with code 3 before any mutation | — |

## 7. Decision log

| Decision | Rationale | Source(s) |
|---|---|---|
| OMES targets Ubuntu Server + Linux Mint, not Arch | Official Omarchy is Arch/Hyprland/ISO-based; reproducing that on Ubuntu/Mint would require pacman/AUR or source builds, which are explicitly rejected (non-goal #4, #5) | [Omarchy Manual](https://omarchy.org/manual/), [Omarchy Getting Started](https://omarchy.org/manual/getting-started/), [omacom/omarchy](https://github.com/omacom/omarchy) |
| Server profile is Hermes-first and headless | Ubuntu Server has no GUI by default; agent-driven operations reduce the need for an interactive desktop and match the target ICP (internal operators, small teams) | [docs/research-and-implementation-plan.md §2.3](research-and-implementation-plan.md), [Hermes messaging gateway docs](https://hermes-agent.nousresearch.com/docs/user-guide/messaging/) |
| Hyprland is opt-in on Mint, Cinnamon is never removed | Hyprland upstream warns that Ubuntu-family distros may lag in dependencies; a broken compositor must not leave the host unusable | [Hyprland Installation](https://wiki.hypr.land/Getting-Started/Installation/), research plan §2.2 |
| No ISO, no partitioning, no bootloader/snapshot management | OMES installs onto an existing host; taking over disk provisioning or boot would turn a reversible toolkit into an irreversible one, contradicting the no-destructive-default policy | Issue #1 acceptance criteria; research plan §3 (dependency principles) |
| No pacman/AUR, no source builds by default | apt is the only package manager OMES targets; source builds are higher-risk and non-idempotent | research plan §2.1, §2.2 |
| No Docker support-parity claim on Mint | Docker's own documentation states official support covers Ubuntu, not derivatives; OMES must not overstate what it can guarantee | [Docker Ubuntu installation](https://docs.docker.com/engine/install/ubuntu/), [Docker Linux post-install](https://docs.docker.com/install/linux/linux-postinstall) |
| Not a config-management replacement | OMES manages a fixed set of modules for its own two profiles, not arbitrary infrastructure; scope creep into general orchestration would dilute the product and the security story | Issue #1 objective; research plan §5.5 (business risks) |
| No destructive default; preflight + backup before every mutation | Matches the module contract (`check` before `apply`, backup before mutation) and the go/no-go criterion that "no destructive default exists" | research plan §3, §7 (go/no-go criteria) |
| `docker` group requires explicit opt-in flag | Docker group membership is root-equivalent; defaulting to it would silently grant root access | research plan §2.4, [Docker post-install security](https://docs.docker.com/install/linux/linux-postinstall) |
| Naming: "Omarchy-inspired," never "Omarchy for Ubuntu" | Avoids implying official affiliation or endorsement, which protects both users and the Omarchy trademark holders | [omacom/omarchy](https://github.com/omacom/omarchy), research plan §5.2 (positioning) |
| Telegram identity checks use numeric allowlists, not wildcards | Reduces the risk of unauthorized access through the Hermes gateway | [Hermes Telegram docs](https://hermes-agent.nousresearch.com/docs/user-guide/messaging/telegram) |

## 8. Related documents

- [docs/omarchy-compatibility-inventory.md](omarchy-compatibility-inventory.md) — capability-by-capability port/adapt/defer/reject decisions (Issue #2).
- [docs/compatibility-matrix.md](compatibility-matrix.md) — supported OS/hardware matrix (written in parallel).
- [docs/architecture.md](architecture.md) — module and CLI architecture (written in parallel).
- [docs/security.md](security.md) — threat model and security defaults (written in parallel).
- [docs/branding-and-trademarks.md](branding-and-trademarks.md) — naming and disclaimer rules (Issue #26).
- [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md) — licensing of referenced upstream projects (Issue #26).
