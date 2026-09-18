# Linux Mint desktop profile

> Status: describes the actual repository state as of this document. This
> covers the `desktop` profile (issue [#8](https://github.com/ahliweb/omes/issues/8)):
> `modules/desktop-preflight/`, `modules/hyprland-session/`,
> `modules/desktop-config/`, `profiles/desktop.profile`, and the templates
> under `config/{hypr,waybar,foot,shell,nvim}/`.
>
> OMES is an independent, MIT-licensed, Omarchy-inspired compatibility
> layer for Ubuntu Server LTS and Linux Mint. It is **not** official
> Omarchy and must never be described as such — see
> [docs/scope.md](scope.md) and [docs/branding-and-trademarks.md](branding-and-trademarks.md).
> `omes backup`/`omes restore`/`omes uninstall` are implemented (issue
> [#10](https://github.com/ahliweb/omes/issues/10)), and `omes doctor`/`omes
> update` are implemented too (issue
> [#14](https://github.com/ahliweb/omes/issues/14)) — all four are used
> throughout this document. `hyprland-session`'s best-effort crash report
> (`module_doctor`, folded into `module_verify` for its own `omes
> install`/`omes check` output) is also reachable directly via `sudo omes
> doctor` once `hyprland-session` has been applied — `omes doctor` calls
> any module's optional `module_doctor` hook automatically.

## 1. Supported versions and hardware

Full detail lives in [docs/compatibility-matrix.md](compatibility-matrix.md)
(Sections 2.2, 4, 5.2); summary for the desktop profile:

| Platform | Tier |
|---|---|
| Linux Mint 22.x, amd64 | 1 (the validated desktop-profile target) |
| Ubuntu 24.04/22.04 desktop, amd64 | 2 for the desktop profile (Mint is Tier 1) |
| arm64 (either) | 3, best-effort |
| Anything else | Unsupported — `omes check` exits 3 before any mutation |

Minimums (desktop profile, `docs/compatibility-matrix.md` Section 4.1):
**8 GB RAM, 20 GB free disk.**

GPU matrix (`docs/compatibility-matrix.md` Section 4.2):

| GPU | Result |
|---|---|
| Intel (Mesa `iris`/`crocus`) | OK — Tier 1 |
| AMD (Mesa `amdgpu`) | OK — Tier 1 |
| NVIDIA proprietary driver | WARN — Tier 2; needs driver **>= 555** and `nvidia-drm.modeset=1` on the kernel command line |
| Nouveau (open-source NVIDIA) | **FAIL** — lacks the KMS/explicit-sync support Hyprland needs on NVIDIA hardware |
| Virtual GPU (virtio-gpu, QXL) | WARN — Tier 3, community/best-effort |
| Anything else/unrecognized | **FAIL** |

`omes check --profile desktop` (as your user; `desktop-preflight` is
user-scope and mutation-free) runs this whole matrix, plus display-manager,
`xdg-desktop-portal`, and package-availability checks, and prints an
`[OK]`/`[WARN]`/`[FAIL]` line for each one with a summary at the end.

## 2. What the profile installs

`profiles/desktop.profile` lists, in this order:

```
apt-base
desktop-preflight
hyprland-session
desktop-config
# hermes (optional - commented out by default, see Section 7)
```

| Module | Scope | What it does |
|---|---|---|
| `apt-base` | root | Baseline CLI tooling (curl, git, python3, jq, ufw, ...) — same as the server profile. |
| `desktop-preflight` | user | **Read-only.** GPU/driver, Mesa/Wayland libs, display manager, `xdg-desktop-portal`, RAM/disk, package availability, and the Cinnamon-session invariant. Installs nothing. |
| `hyprland-session` | root | Installs the Hyprland/Wayland toolset (Section 3) and registers an **additive** session entry. Never touches Cinnamon, LightDM's config, or the default session. |
| `desktop-config` | user | Templates `~/.config/{hypr,waybar,foot}` and a shell aliases snippet (`~/.config/omes/shell.sh`, sourced from `~/.bashrc`). Never overwrites a differing pre-existing file without `--yes`. |

Because `desktop-preflight`/`desktop-config` are user-scope and
`apt-base`/`hyprland-session` are root-scope, and OMES's module runner only
ever applies modules matching the *current* privilege level in one
`omes install` run (never both in the same invocation — see
[docs/architecture.md](architecture.md) and
`lib/omes/module.sh`'s `module_filter_by_scope`), installing the full
profile is a **two-step operation**: once as root, once as your normal
user (Section 5).

## 3. Package availability on noble (be honest about this)

Linux Mint 22.x is based on Ubuntu 24.04 "noble". **As of this writing,
Hyprland is NOT in Ubuntu 24.04's official archive**, and neither are its
close companions `hypridle`/`hyprlock` — see
[docs/omarchy-compatibility-inventory.md](omarchy-compatibility-inventory.md)
and [docs/compatibility-matrix.md](compatibility-matrix.md) Section 4.3.

`desktop-preflight`'s `module_check` calls `pkg_exists_in_repos` (from
`lib/omes/pkg.sh`) for **every** package this profile might install —
never assuming availability. On a stock noble/Mint 22 host today, that
means:

- **`hyprland`, `hypridle`, `hyprlock` will very likely FAIL preflight**
  (exit 4). The message names the exact package and says:
  1. wait for a future Ubuntu/Mint release that carries the package, or
  2. use a validated third-party source explicitly **allowlisted in
     [docs/packages.md](packages.md)** — OMES adds no PPA automatically,
     and **none is allowlisted as of this writing**. OMES never builds
     Hyprland (or anything else) from source (see
     [docs/scope.md](scope.md) non-goal #5).
- `xdg-desktop-portal` (the framework) is required; OMES prefers
  `xdg-desktop-portal-hyprland` and falls back to
  `xdg-desktop-portal-gtk` when the Hyprland-native backend is
  unavailable. Preflight only FAILs if *neither* backend is usable.
- Everything below is checked individually and, if unavailable, is
  **skipped with a WARN** (not a hard failure) — `hyprland-session` simply
  does not install it: `waybar`, `foot`, the launcher (`wofi` or
  `fuzzel`), `mako-notifier`, `wl-clipboard`, `cliphist`, `grim`, `slurp`,
  `swappy`, a PolicyKit agent (`polkit-kde-agent-1` or `lxpolkit`),
  `brightnessctl`, `playerctl`, `pavucontrol`,
  `network-manager-gnome`, `fonts-jetbrains-mono`, `fonts-noto`,
  and the Mesa/Wayland libraries `libwayland-client0`,
  `mesa-vulkan-drivers`, `libgl1-mesa-dri` (these last three are treated
  as hard requirements: FAIL, not WARN, since nothing renders without
  them).

**Marked "verify per release" (this repository does not claim to have
confirmed these against a live noble archive at the time of writing —
`pkg_exists_in_repos` decides at run time, never a hardcoded guess):**
`waybar`, `foot`, `wofi`, `fuzzel`, `mako-notifier`, `cliphist`, `swappy`,
`polkit-kde-agent-1`, `lxpolkit`, `xdg-desktop-portal-hyprland`,
`fonts-jetbrains-mono`. Everything in the "very likely FAIL" list above
(`hyprland`, `hypridle`, `hyprlock`) is also, strictly speaking, "verify
per release" — it is called out separately because it is expected to fail
today, not merely uncertain.

Once a validated package source exists (a future Ubuntu/Mint release, or a
PPA reviewed and added to `docs/packages.md`'s allowlist), this profile
starts passing preflight and installing Hyprland **with no code change** —
`desktop-preflight`/`hyprland-session` always ask `apt-cache`, never assume.

## 4. The additive-session model and the Cinnamon fallback guarantee

This is the safety invariant the whole feature depends on:

- **Cinnamon is never modified, disabled, or removed.** `hyprland-session`
  asserts `/usr/share/xsessions/cinnamon.desktop` exists — every time, in
  `module_check`, again immediately before mutating anything in
  `module_apply`, again in `module_verify`, and again in `module_rollback`
  — and refuses to proceed if it is ever missing.
- **The Hyprland session is a second, additional entry**, written to
  `/usr/share/wayland-sessions/omes-hyprland.desktop`
  (`Name=OMES Hyprland`), never to LightDM's configuration and never as
  the default session.
- **`Exec=` points at a managed wrapper**,
  `/usr/local/bin/omes-hyprland-session`, which sets
  `XDG_CURRENT_DESKTOP=Hyprland`, `XDG_SESSION_DESKTOP=Hyprland`,
  `XDG_SESSION_TYPE=wayland`, then `exec`s the `Hyprland` binary.
- Both files are registered via `omes_manage_path` (backed up before any
  future OMES re-apply touches them, and tracked so uninstall can find
  them).
- At the LightDM login screen, Cinnamon **remains the default** and is
  always selectable — OMES adds "OMES Hyprland" as an additional option,
  it does not change what is pre-selected.

## 5. Step-by-step install

```bash
# 1. Preflight only (no mutation) - as your normal user.
omes check --profile desktop

# 2. Root-scope modules: apt-base + hyprland-session (packages, session entry).
sudo omes install --profile desktop

# 3. User-scope modules: desktop-preflight (re-checked) + desktop-config
#    (your ~/.config templates and shell snippet) - as your normal user,
#    NOT root.
omes install --profile desktop

# 4. Log out, and at the LightDM login screen choose "OMES Hyprland"
#    instead of "Cinnamon".
```

Each `omes install` run only applies the modules matching the privilege
level it is run under and tells you the exact follow-up command for the
other half (`Run with sudo: ...` / `Run as your user: ...`) — this is
normal, not an error.

`--dry-run` is honored throughout: `omes check --profile desktop
--dry-run` (checks never mutate regardless) and
`sudo omes install --profile desktop --dry-run` print every planned
action (package installs, the session file, the wrapper) without writing
anything.

**Use `--profile desktop`, not `--module hyprland-session`, for install.**
`hyprland-session` declares `MODULE_REQUIRES=(desktop-preflight apt-base)`
(Section 2), and `desktop-preflight` is user-scope while `hyprland-session`
is root-scope; requesting `sudo omes install --module hyprland-session`
directly makes the CLI's explicit-module scope check see `desktop-preflight`
pulled in by that dependency and refuse with exit 5 ("requested module(s)
do not match the current privilege level"). `--profile desktop` does not
have this problem (a profile's scope mismatches are filtered, not
rejected) and is the supported way to install this profile — see Section
7 for the equivalent caveat on `omes uninstall`.

## 6. Recovery procedures

- **Hyprland fails to start / broken session:** at the LightDM login
  screen, select **"Cinnamon"** instead of "OMES Hyprland" and log in
  normally — Cinnamon was never touched. This is the documented recovery
  path required by this issue's acceptance criteria.
- **Black screen after selecting OMES Hyprland:** switch to a text VT
  (`Ctrl+Alt+F3` on most setups), log in, and check
  `~/.local/share/hyprland/` (if present) and
  `journalctl --user -b -p err | grep -i hyprland` for errors —
  `hyprland-session`'s `module_verify` already runs this best-effort check
  and prints what it finds. Then return to LightDM and choose Cinnamon.
- **NVIDIA notes:** `desktop-preflight` WARNs (does not fail) on the
  proprietary NVIDIA driver, but flags a driver version below **555** and
  a missing `nvidia-drm.modeset=1` kernel parameter individually — both
  are required for Wayland/Hyprland's explicit-sync support. Add
  `nvidia-drm.modeset=1` to your kernel command line (e.g. via
  `/etc/default/grub`'s `GRUB_CMDLINE_LINUX_DEFAULT`, then
  `update-grub`) and reboot. **Nouveau (the open-source NVIDIA driver)
  fails preflight outright** — install the proprietary driver, or use the
  Cinnamon session.
- **Portals / screen sharing not working:** confirm
  `xdg-desktop-portal-hyprland` is installed (preferred) or that
  `xdg-desktop-portal-gtk` (fallback) is; `desktop-preflight` reports
  which one it found. If neither is available, screen sharing/file
  pickers will not work under Hyprland until one is.
- **Multi-monitor:** the shipped `config/hypr/hyprland.conf` auto-detects
  every monitor at its preferred mode (`monitor=,preferred,auto,1`); edit
  `~/.config/hypr/hyprland.conf` for a fixed layout (run `hyprctl
  monitors` after first login to get exact output names).
- **Full uninstall of the session:** `sudo omes uninstall --module
  hyprland-session` (root scope, same as install) removes
  `/usr/share/wayland-sessions/omes-hyprland.desktop` and
  `/usr/local/bin/omes-hyprland-session`, prints the packages OMES
  installed without removing them (add `--purge-packages` to also remove
  those), and — per `lib/omes/restore.sh`'s generic managed-path
  rollback, which runs for every module — restores any file that existed
  *before* OMES touched it, or deletes it if OMES created it fresh.
  `hyprland-session`'s own `module_rollback` (removes the session file and
  wrapper directly, re-asserts the Cinnamon invariant, reports installed
  packages) runs first and is never allowed to touch Cinnamon, LightDM's
  config, or the default session.
- **Restoring replaced config files:** every file
  `desktop-config`/`hyprland-session` overwrite is backed up first (sha256
  manifest) via the standard OMES backup mechanism
  (`lib/omes/backup.sh`/`lib/omes/restore.sh`). Run `omes restore --list`
  to see backup sessions, then `omes restore --from <timestamp>` (or
  `omes restore` for the latest) to restore — this works fully offline
  and verifies each restored file's sha256 against the backup manifest
  before writing it back.

## 7. How to remove

> **Verified caveat:** `hyprland-session` declares `MODULE_REQUIRES=(desktop-preflight
> apt-base)` (Section 2). `lib/omes/module.sh`'s dependency resolution
> (shared, out of this issue's file scope) walks a module's full
> `MODULE_REQUIRES` chain even for a single `--module` request, so `sudo
> omes uninstall --module hyprland-session` also rolls back `apt-base` and
> `desktop-preflight` in the same run, not just `hyprland-session` — this
> was confirmed by hand against this repository's actual behavior, not
> assumed. It is **not destructive on its own**: `apt-base`'s
> `module_rollback` only warns and prints the packages it installed
> (`curl`, `git`, ...) without removing them unless you also pass
> `--purge-packages`, and Cinnamon is never touched. It **does** mark
> `apt-base`/`desktop-preflight` as `removed` in OMES's state file even
> though their packages remain installed, which can be confusing in
> `omes status` output afterward. If you only want the Hyprland session
> gone and want `apt-base`'s state to stay accurate, use `--profile
> desktop` for both install and uninstall consistently rather than a bare
> `--module hyprland-session`, or re-run `sudo omes install --profile
> desktop` afterward to re-apply `apt-base` and restore its state.

- **Session only:** `sudo omes uninstall --module hyprland-session` — see
  "Full uninstall of the session" above (and the cascading-uninstall
  caveat immediately above this list).
- **Config files:** `omes uninstall --module desktop-config` (as your
  user) removes the shell snippet (`~/.config/omes/shell.sh`) and the
  marker block in `~/.bashrc` directly, via `desktop-config`'s own
  `module_rollback`; the generic managed-path rollback that runs
  immediately after it then restores or removes every file
  `desktop-config` registered as managed — including
  `~/.config/{hypr,waybar,foot}/*` — the same way: a file that pre-dated
  OMES is restored to what it was before, a file OMES created fresh is
  removed. A file you told OMES to skip (differing, no `--yes` — see
  Section 5) was never modified in the first place, so it is left alone.
- **Packages:** never removed by default. `omes uninstall --purge-packages`
  removes exactly the packages OMES recorded as installed by the module(s)
  being uninstalled (e.g. `sudo omes uninstall --module hyprland-session
  --purge-packages`); without that flag, the exact `apt-get remove`
  command is printed instead.
- **Whole profile:** `sudo omes uninstall --profile desktop` then
  `omes uninstall --profile desktop` (as your user) rolls back every
  module `omes install --profile desktop` applied, in reverse dependency
  order — `omes uninstall` only ever touches modules OMES itself recorded
  as applied in its state file, matching the no-destructive-default policy
  in [docs/scope.md](scope.md) Section 5.

## 8. Optional: Hermes Agent alongside the desktop session

`hermes` is left commented out in `profiles/desktop.profile` — the desktop
profile does not assume you want Hermes Agent on a desktop machine.
Uncomment the `hermes` line (and `hermes-gateway` once
[#12](https://github.com/ahliweb/omes/issues/12) lands) to install it the
same way the `server`/`hermes` profiles do — see
[docs/hermes-integration.md](hermes-integration.md).

## 9. Known limitations

- `hyprland`, `hypridle`, and `hyprlock` are not in Ubuntu 24.04's official
  archive as of this writing — see Section 3. Until a validated source
  exists, `omes check --profile desktop` will report `hyprland-session`
  preflight as FAILing on a stock Mint 22 host, by design.
- `config/nvim/` ships only a README pointing at LazyVim's own installer;
  OMES does not template or install a Neovim configuration
  (`docs/omarchy-compatibility-inventory.md` categorizes this **DEFER**).
- `omes update` fast-forwards the OMES checkout itself via `git`; it does
  not update Hyprland or any package this profile installed (that stays
  on `apt`'s own update cadence, per `docs/security.md` §4 — the desktop
  profile does not enable unattended upgrades).
- Fingerprint/FIDO2 login and the Walker launcher are DEFERred (not part
  of this profile) — see
  [docs/omarchy-compatibility-inventory.md](omarchy-compatibility-inventory.md).
- No wallpaper or full visual theme ships by default; `config/hypr/` and
  `config/waybar/` are deliberately minimal, functional starting points.
