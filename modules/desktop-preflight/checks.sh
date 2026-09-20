#!/usr/bin/env bash
# shellcheck shell=bash
# modules/desktop-preflight/checks.sh - shared, read-only desktop-readiness
# checks for the opt-in Hyprland/Wayland session.
#
# This is a LOCAL HELPER (not a lib/omes/*.sh addition - out of this issue's
# file scope). It is sourced by both modules/desktop-preflight/module.sh
# (which runs it as the user-scope preflight) and
# modules/hyprland-session/module.sh (which independently re-runs the same
# hard checks in its own module_check, because MODULE_REQUIRES ordering only
# applies within a single privilege scope - see modules/hyprland-session's
# header comment for why a root-scope module cannot simply trust a
# user-scope module's state).
#
# Every function here is read-only: no mutation, safe to call from any
# module_check/module_verify. Callers get pkg_map/pkg_is_installed/
# pkg_exists_in_repos/log_*/detect_* from the already-sourced lib/omes/*.sh
# files (module_load sources module.sh after core/log/detect/state/backup/
# module/pkg are already loaded - see bin/omes and tests/test_helper.bash
# patterns).

if [[ -n "${OMES_DESKTOP_PREFLIGHT_CHECKS_SH_LOADED:-}" ]]; then
  # shellcheck disable=SC2317
  return 0 2>/dev/null || exit 0
fi
OMES_DESKTOP_PREFLIGHT_CHECKS_SH_LOADED=1

# ---------------------------------------------------------------------------
# Thresholds (docs/compatibility-matrix.md Section 4.1)
# ---------------------------------------------------------------------------

DP_MIN_RAM_MB=8192
DP_MIN_DISK_MB=20480

# ---------------------------------------------------------------------------
# Package lists (logical names; pkg_map resolves the real per-OS package)
# ---------------------------------------------------------------------------
#
# "Hard" packages: without these the Hyprland session cannot function at
# all. Unavailability of any of these is a preflight FAIL (exit 4), never a
# guess - see docs/linux-mint.md "Package availability on noble".
#
# Hyprland itself, and its companion idle/lock daemons, are NOT in Ubuntu
# 24.04 "noble"'s official archive as of this writing (verified 2026-09-18,
# see docs/omarchy-compatibility-inventory.md and docs/compatibility-matrix.md
# Section 4.3) - they are listed here anyway, and checked via
# pkg_exists_in_repos exactly like everything else, so that the moment a
# validated source makes them available (an allowlisted PPA added to
# docs/packages.md, or a future Ubuntu/Mint release), this module starts
# passing with NO code change.
# shellcheck disable=SC2034
DP_HARD_PACKAGES=(hyprland hypridle hyprlock)

# Mesa/Wayland core libraries (docs/compatibility-matrix.md Section 5.2).
# shellcheck disable=SC2034
DP_MESA_PACKAGES=(libwayland-client0 mesa-vulkan-drivers libgl1-mesa-dri)

# The xdg-desktop-portal framework package itself is hard-required; its two
# candidate backends are handled separately (dp_check_portal) because either
# one satisfies the "a portal is present" requirement.
# shellcheck disable=SC2034
DP_PORTAL_FRAMEWORK=xdg-desktop-portal
# shellcheck disable=SC2034
DP_PORTAL_HYPRLAND=xdg-desktop-portal-hyprland
# shellcheck disable=SC2034
DP_PORTAL_GTK=xdg-desktop-portal-gtk

# Candidate PolicyKit authentication agents; either satisfies the
# requirement (module_apply installs whichever is available).
# shellcheck disable=SC2034
DP_POLKIT_AGENTS=(polkit-kde-agent-1 lxpolkit)

# Candidate application launcher; either satisfies the requirement. wofi is
# the longer-established package in Debian/Ubuntu universe; fuzzel is a
# newer alternative. Availability of BOTH must be verified per release (see
# docs/linux-mint.md) - this module never assumes either is present, it
# always asks apt-cache via pkg_exists_in_repos.
# shellcheck disable=SC2034
DP_LAUNCHER_CANDIDATES=(wofi fuzzel)

# "Soft" toolset packages: convenience/UX pieces of the Hyprland session.
# Unavailability of any of these is a WARN, not a FAIL - hyprland-session
# skips installing whichever of these are unavailable (mirrors the `eza`
# precedent in docs/packages.md Section 2/4) rather than blocking the whole
# profile on a non-essential package.
# shellcheck disable=SC2034
DP_SOFT_PACKAGES=(
  waybar
  foot
  mako-notifier
  wl-clipboard
  cliphist
  grim
  slurp
  swappy
  brightnessctl
  playerctl
  pavucontrol
  network-manager-gnome
  fonts-jetbrains-mono
  fonts-noto
)

# ---------------------------------------------------------------------------
# Result collection
# ---------------------------------------------------------------------------

# Callers must reset this before running a batch of dp_check_* /
# dp_mark_* calls: `DP_HAS_FAIL=0`.
DP_HAS_FAIL=0

dp_mark_ok() {
  log_info "[OK]   $*"
}

dp_mark_warn() {
  log_warn "[WARN] $*"
}

dp_mark_fail() {
  log_error "[FAIL] $*"
  DP_HAS_FAIL=1
}

# ---------------------------------------------------------------------------
# Package availability (calls pkg_exists_in_repos for every package, per
# this issue's mandatory design constraint - never assumes availability)
# ---------------------------------------------------------------------------

# _dp_pkg_available <real-pkg>
# Silent boolean: true if already installed OR available in the configured
# repositories. Does not mark/log anything itself (used by OR-style
# candidate selection, e.g. the launcher/polkit-agent choices, where only
# the winning candidate should be logged).
_dp_pkg_available() {
  local real="$1"
  pkg_is_installed "$real" && return 0
  pkg_exists_in_repos "$real" >/dev/null 2>&1
}

# _dp_check_package <logical> <hard|soft> [label]
# Resolves <logical> via pkg_map, then: already installed -> OK; not
# installed but available in repos -> OK (will be installed by
# hyprland-session's module_apply); unavailable -> FAIL (hard) or WARN
# (soft), with an actionable message on hard failures. Returns 0 for
# OK/soft-WARN outcomes it wants the caller to treat as "usable", 1 for a
# hard FAIL.
_dp_check_package() {
  local logical="$1" severity="$2" label="${3:-$1}"
  local real
  real="$(pkg_map "$logical")"

  if pkg_is_installed "$real"; then
    dp_mark_ok "${label} (${real}) already installed"
    return 0
  fi

  local rc=0
  pkg_exists_in_repos "$real" >/dev/null 2>&1 || rc=$?

  if [[ "$rc" -eq 0 ]]; then
    dp_mark_ok "${label} (${real}) available in the configured repositories (not yet installed)"
    return 0
  fi

  if [[ "$severity" == "hard" ]]; then
    dp_mark_fail "$(_dp_hard_unavailable_message "$real" "$label")"
    return 1
  fi

  dp_mark_warn "${label} (${real}) is not available in the configured repositories; hyprland-session will skip installing it (verify per release - see docs/linux-mint.md)"
  return 0
}

# _dp_hard_unavailable_message <real-pkg> <label>
_dp_hard_unavailable_message() {
  local real="$1" label="$2"
  printf '%s is not available in the configured repositories (Ubuntu 24.04 "noble" base, Linux Mint 22.x'\''s upstream). %s\n%s\n%s\n%s' \
    "${label} (${real})" \
    "Hyprland and its companion packages (hypridle, hyprlock) are not in the official noble archive as of this writing." \
    "Options: (1) wait for a future Ubuntu/Mint release that carries these packages, or" \
    "(2) use a validated third-party source explicitly allowlisted in docs/packages.md (OMES adds no PPA automatically, and none is allowlisted as of this writing - see docs/packages.md \"Repository policy\"; never a source build)." \
    "See docs/linux-mint.md for the current status and recovery guidance."
}

# dp_check_hard_toolset
# Checks every DP_HARD_PACKAGES + DP_MESA_PACKAGES entry (hard/FAIL) plus
# every DP_SOFT_PACKAGES entry (soft/WARN). Also resolves the launcher and
# polkit-agent OR-candidates.
dp_check_toolset() {
  local logical
  for logical in "${DP_HARD_PACKAGES[@]}"; do
    _dp_check_package "$logical" hard "Hyprland package"
  done
  for logical in "${DP_MESA_PACKAGES[@]}"; do
    _dp_check_package "$logical" hard "Mesa/Wayland library"
  done
  for logical in "${DP_SOFT_PACKAGES[@]}"; do
    _dp_check_package "$logical" soft "Desktop toolset package"
  done

  dp_check_or_candidates "application launcher" "${DP_LAUNCHER_CANDIDATES[@]}"
  dp_check_or_candidates "PolicyKit authentication agent" "${DP_POLKIT_AGENTS[@]}"
}

# dp_check_or_candidates <description> <logical...>
# At least one of <logical...> must be installed/available; if none are,
# this is a WARN (hyprland-session installs whichever is available at apply
# time; the OS-level xdg-desktop-portal/polkit framework checks are the
# hard gates, not the specific agent/launcher choice).
dp_check_or_candidates() {
  local description="$1"
  shift
  local logical real
  for logical in "$@"; do
    real="$(pkg_map "$logical")"
    if _dp_pkg_available "$real"; then
      dp_mark_ok "${description}: ${real} available (candidates checked: $*)"
      return 0
    fi
  done
  dp_mark_warn "${description}: none of the candidates (${*}) are installed or available in the configured repositories; hyprland-session will skip this piece (verify per release - see docs/linux-mint.md)"
  return 0
}

# dp_check_portal
# xdg-desktop-portal framework is hard-required; at least one backend
# (hyprland-native preferred, gtk fallback) must be usable, per
# docs/compatibility-matrix.md Section 5.2.
dp_check_portal() {
  _dp_check_package "$DP_PORTAL_FRAMEWORK" hard "xdg-desktop-portal framework"

  local hypr_real gtk_real hypr_ok=0 gtk_ok=0
  hypr_real="$(pkg_map "$DP_PORTAL_HYPRLAND")"
  gtk_real="$(pkg_map "$DP_PORTAL_GTK")"

  if _dp_pkg_available "$hypr_real"; then
    hypr_ok=1
    dp_mark_ok "Hyprland-native portal backend (${hypr_real}) available"
  else
    dp_mark_warn "Hyprland-native portal backend (${hypr_real}) not available; falling back to ${gtk_real} (verify per release)"
  fi

  if _dp_pkg_available "$gtk_real"; then
    gtk_ok=1
    dp_mark_ok "GTK portal backend fallback (${gtk_real}) available"
  fi

  if [[ "$hypr_ok" -eq 0 ]] && [[ "$gtk_ok" -eq 0 ]]; then
    dp_mark_fail "neither the Hyprland-native (${hypr_real}) nor the GTK fallback (${gtk_real}) xdg-desktop-portal backend is available; screen sharing and file pickers would not work under Hyprland - see docs/linux-mint.md"
  fi
}

# ---------------------------------------------------------------------------
# GPU / driver
# ---------------------------------------------------------------------------

# _dp_lspci_driver
# Prints the "Kernel driver in use" value reported by `lspci -k` for the
# first VGA/3D/display controller, or empty when undetermined.
_dp_lspci_driver() {
  command -v lspci >/dev/null 2>&1 || return 0
  lspci -k 2>/dev/null | awk '
    /VGA compatible controller|3D controller|Display controller/ { invga=1; next }
    invga && /^\tKernel driver in use:/ {
      sub(/^\tKernel driver in use: */, "");
      print;
      exit
    }
    invga && /^[0-9a-f]{2}:[0-9a-f]{2}\.[0-9a-f]/ { invga=0 }
  '
}

# _dp_gpu_vendor
# Classifies the primary GPU as one of: intel, amd, nvidia, nouveau,
# virtio, unknown - from the `lspci` VGA/3D/display line plus (when
# distinguishable) the in-use kernel driver from `lspci -k`.
_dp_gpu_vendor() {
  local line driver
  line="$(detect_gpu)"
  driver="$(_dp_lspci_driver)"

  case "$driver" in
    nouveau)
      printf 'nouveau\n'
      return 0
      ;;
    nvidia)
      printf 'nvidia\n'
      return 0
      ;;
    i915 | xe)
      printf 'intel\n'
      return 0
      ;;
    amdgpu | radeon)
      printf 'amd\n'
      return 0
      ;;
    virtio_gpu | qxl | vmwgfx)
      printf 'virtio\n'
      return 0
      ;;
  esac

  case "$line" in
    *[Ii]ntel*) printf 'intel\n' ;;
    *"Advanced Micro Devices"* | *AMD* | *ATI*) printf 'amd\n' ;;
    *NVIDIA*) printf 'nvidia\n' ;;
    *[Vv]irtio* | *QXL* | *"VMware SVGA"*) printf 'virtio\n' ;;
    *) printf 'unknown\n' ;;
  esac
}

# _dp_nvidia_driver_version
# Prints the NVIDIA proprietary driver version via `nvidia-smi`, or empty
# when unavailable/undeterminable.
_dp_nvidia_driver_version() {
  command -v nvidia-smi >/dev/null 2>&1 || return 0
  nvidia-smi --query-gpu=driver_version --format=csv,noheader 2>/dev/null | head -n1
}

# _dp_nvidia_modeset_enabled
# True when nvidia-drm.modeset=1 is present on the kernel command line, or
# the nvidia_drm module's `modeset` parameter reports Y. Overridable via
# OMES_PROC_CMDLINE_FILE / OMES_NVIDIA_MODESET_PARAM_FILE for testing (the
# real paths, /proc/cmdline and /sys/module/nvidia_drm/parameters/modeset,
# are never writable in a bats container).
_dp_nvidia_modeset_enabled() {
  local cmdline_file="${OMES_PROC_CMDLINE_FILE:-/proc/cmdline}"
  local param_file="${OMES_NVIDIA_MODESET_PARAM_FILE:-/sys/module/nvidia_drm/parameters/modeset}"

  if [[ -r "$cmdline_file" ]] && grep -qw 'nvidia-drm.modeset=1' "$cmdline_file" 2>/dev/null; then
    return 0
  fi
  if [[ -r "$param_file" ]]; then
    local val
    val="$(tr -d '[:space:]' <"$param_file" 2>/dev/null || true)"
    [[ "$val" == "Y" ]] && return 0
  fi
  return 1
}

# dp_check_gpu
# Implements docs/compatibility-matrix.md Section 4.2: Intel/AMD OK;
# NVIDIA proprietary WARN (with driver>=555 / modeset=1 sub-checks);
# nouveau FAIL; virtio/QXL WARN (tier3); unrecognized FAIL (per Section
# 5.2, "Nouveau or unrecognized GPUs fail this check").
dp_check_gpu() {
  local vendor line
  vendor="$(_dp_gpu_vendor)"
  line="$(detect_gpu)"

  case "$vendor" in
    intel | amd)
      dp_mark_ok "GPU vendor '${vendor}' detected (${line}); open-source Mesa driver, Tier 1 for Hyprland"
      ;;
    nvidia)
      local version modeset_ok=1
      version="$(_dp_nvidia_driver_version)"
      _dp_nvidia_modeset_enabled || modeset_ok=0

      local detail="NVIDIA proprietary driver detected (${line})"
      if [[ -n "$version" ]]; then
        detail="${detail}, driver version ${version}"
        if _dp_version_ge "$version" "555"; then
          detail="${detail} (>= 555, explicit sync supported)"
        else
          detail="${detail} (< 555 - upgrade recommended for Wayland explicit sync support)"
        fi
      else
        detail="${detail}, driver version could not be determined (nvidia-smi unavailable)"
      fi
      if [[ "$modeset_ok" -eq 1 ]]; then
        detail="${detail}; nvidia-drm.modeset=1 is set"
      else
        detail="${detail}; nvidia-drm.modeset=1 is NOT set (add it to the kernel command line, then reboot)"
      fi
      dp_mark_warn "${detail} - NVIDIA is Tier 2 for Hyprland (see docs/compatibility-matrix.md Section 4.2)"
      ;;
    nouveau)
      dp_mark_fail "nouveau (open-source NVIDIA) driver detected (${line}); nouveau lacks the KMS/atomic-modesetting and explicit-sync support Hyprland requires on NVIDIA hardware - install the proprietary NVIDIA driver (>= 555) or use the Cinnamon fallback session"
      ;;
    virtio)
      dp_mark_warn "virtual GPU detected (${line}); Hyprland over virtio-gpu/QXL is community/best-effort only (Tier 3) - 3D acceleration and explicit sync vary by hypervisor"
      ;;
    *)
      dp_mark_fail "GPU vendor could not be determined (${line}); unrecognized GPUs fail Hyprland preflight per docs/compatibility-matrix.md Section 5.2 - use the Cinnamon fallback session"
      ;;
  esac
}

# _dp_version_ge <version> <min-major>
# Minimal, dependency-free "is the leading numeric component >= min-major"
# comparison (good enough to distinguish NVIDIA driver 555+ from older).
_dp_version_ge() {
  local version="$1" min="$2"
  local major="${version%%.*}"
  [[ "$major" =~ ^[0-9]+$ ]] || return 1
  [[ "$major" -ge "$min" ]]
}

# dp_check_render
# Secondary, non-fatal cross-check of direct rendering via `glxinfo -B`
# (mesa-utils). Never gates FAIL by itself - the GPU vendor/driver check
# (dp_check_gpu) already makes the authoritative Tier decision; this is
# informational confirmation for when glxinfo happens to be installed.
dp_check_render() {
  if ! command -v glxinfo >/dev/null 2>&1; then
    dp_mark_warn "glxinfo not found (part of mesa-utils, not an OMES-managed package); skipping the direct-rendering cross-check"
    return 0
  fi

  local out direct
  out="$(glxinfo -B 2>/dev/null || true)"
  direct="$(printf '%s\n' "$out" | awk -F': ' '/^direct rendering:/ {print $2; exit}')"

  case "$direct" in
    Yes* | yes*)
      dp_mark_ok "glxinfo reports direct rendering: ${direct}"
      ;;
    "")
      dp_mark_warn "glxinfo produced no 'direct rendering' line; could not confirm hardware acceleration"
      ;;
    *)
      dp_mark_warn "glxinfo reports direct rendering: ${direct} (software rendering fallback - Hyprland will be slow or may not run at all)"
      ;;
  esac
}

# dp_check_logind
# systemd-logind is required for Wayland session/seat management and for
# xdg-desktop-portal/polkit to work correctly. Checked via
# `loginctl list-sessions`, which requires nothing more than logind being
# reachable over D-Bus. Non-fatal: on any supported tier1/tier2 target,
# logind is effectively always present, but a WARN is still useful signal.
dp_check_logind() {
  if ! command -v loginctl >/dev/null 2>&1; then
    dp_mark_warn "loginctl not found; could not confirm systemd-logind is reachable"
    return 0
  fi
  if loginctl list-sessions >/dev/null 2>&1; then
    dp_mark_ok "systemd-logind is reachable (loginctl list-sessions succeeded)"
  else
    dp_mark_warn "loginctl list-sessions failed; systemd-logind may be unreachable - Wayland session/seat management may not work correctly"
  fi
}

# ---------------------------------------------------------------------------
# Display manager / resources / Cinnamon
# ---------------------------------------------------------------------------

dp_check_display_manager() {
  local dm
  dm="$(detect_display_manager)"
  case "$dm" in
    lightdm)
      dp_mark_ok "display manager: lightdm (Linux Mint default)"
      ;;
    gdm | gdm3 | sddm)
      dp_mark_warn "display manager: ${dm} (not Mint's default; the OMES Hyprland session entry is validated against lightdm - it should still appear, but is less tested here)"
      ;;
    none)
      dp_mark_fail "no display manager detected; a display manager is required to log into either Cinnamon or the OMES Hyprland session"
      ;;
    *)
      dp_mark_warn "display manager: ${dm} (unrecognized; proceed with caution)"
      ;;
  esac
}

# _dp_mem_mb / _dp_disk_free_mb
# Thin wrappers around lib/omes/detect.sh's detect_mem_mb/
# detect_disk_free_mb that add a test-only override hook
# (OMES_DP_MEM_MB_OVERRIDE / OMES_DP_DISK_FREE_MB_OVERRIDE) - detect.sh
# itself has no such hook and is out of this issue's file scope (lib/omes/*
# is owned by another workstream), so bats tests need a deterministic way
# to exercise both the pass and fail sides of the RAM/disk thresholds
# without depending on the real host/container's actual memory and free
# disk space.
_dp_mem_mb() {
  if [[ -n "${OMES_DP_MEM_MB_OVERRIDE:-}" ]]; then
    printf '%s\n' "$OMES_DP_MEM_MB_OVERRIDE"
    return 0
  fi
  detect_mem_mb
}

_dp_disk_free_mb() {
  if [[ -n "${OMES_DP_DISK_FREE_MB_OVERRIDE:-}" ]]; then
    printf '%s\n' "$OMES_DP_DISK_FREE_MB_OVERRIDE"
    return 0
  fi
  detect_disk_free_mb /
}

dp_check_resources() {
  local mem disk
  mem="$(_dp_mem_mb)"
  disk="$(_dp_disk_free_mb)"

  if [[ "$mem" -ge "$DP_MIN_RAM_MB" ]]; then
    dp_mark_ok "RAM: ${mem} MB (>= ${DP_MIN_RAM_MB} MB required)"
  else
    dp_mark_fail "RAM: ${mem} MB is below the ${DP_MIN_RAM_MB} MB (8 GB) desktop-profile minimum (docs/compatibility-matrix.md Section 4.1)"
  fi

  if [[ "$disk" -ge "$DP_MIN_DISK_MB" ]]; then
    dp_mark_ok "free disk: ${disk} MB (>= ${DP_MIN_DISK_MB} MB required)"
  else
    dp_mark_fail "free disk: ${disk} MB is below the ${DP_MIN_DISK_MB} MB (20 GB) desktop-profile minimum (docs/compatibility-matrix.md Section 4.1)"
  fi
}

# _dp_cinnamon_session_file
# Overridable via OMES_CINNAMON_SESSION_FILE (test hook only - production
# always uses the real path).
_dp_cinnamon_session_file() {
  printf '%s\n' "${OMES_CINNAMON_SESSION_FILE:-/usr/share/xsessions/cinnamon.desktop}"
}

# dp_check_cinnamon
# The safety invariant this whole feature depends on: Hyprland is never
# offered as anything but an ADDITIONAL session, so Cinnamon must exist.
# Called from every module_check/module_verify in this feature (desktop-
# preflight AND hyprland-session), every time - never assumed from a prior
# run.
dp_check_cinnamon() {
  local f
  f="$(_dp_cinnamon_session_file)"
  if [[ -r "$f" ]]; then
    dp_mark_ok "Cinnamon fallback session found: ${f}"
  else
    dp_mark_fail "Cinnamon session file not found at ${f} - OMES refuses to proceed without a guaranteed way back to Cinnamon; install/repair Cinnamon before enabling the Hyprland session"
  fi
}

# dp_check_tier
# desktop-profile-specific tier framing (docs/compatibility-matrix.md
# Section 2.2): Linux Mint 22.x is Tier 1 for desktop; Ubuntu (any
# supported version) is Tier 2 for the desktop profile specifically (Mint
# is the validated desktop target). Unsupported platforms are already
# rejected before any module runs (bin/omes cmd_check/cmd_install exit 3),
# so this never needs to handle "unsupported" itself, but stays defensive.
dp_check_tier() {
  case "${OMES_OS_ID:-}" in
    linuxmint)
      dp_mark_ok "platform: Linux Mint ${OMES_OS_VERSION_ID:-} (tier=${OMES_OS_TIER:-unknown}) - the validated desktop-profile target"
      ;;
    ubuntu)
      dp_mark_warn "platform: Ubuntu ${OMES_OS_VERSION_ID:-} desktop (tier=${OMES_OS_TIER:-unknown} for server; Tier 2 for the desktop profile) - Linux Mint 22.x is the Tier 1 desktop target"
      ;;
    *)
      dp_mark_fail "platform '${OMES_OS_ID:-unknown}' is not a supported desktop-profile target"
      ;;
  esac
}

# dp_run_all_checks
# Runs every check in this file, resetting DP_HAS_FAIL first. Returns 0
# when nothing FAILed, 1 otherwise. Callers still see every OK/WARN/FAIL
# line via log_info/log_warn/log_error (captured by lib/omes/module.sh's
# run_checks via `module_check 2>&1`).
dp_run_all_checks() {
  DP_HAS_FAIL=0

  dp_check_tier
  dp_check_gpu
  dp_check_render
  dp_check_logind
  dp_check_display_manager
  dp_check_portal
  dp_check_toolset
  dp_check_resources
  dp_check_cinnamon

  if [[ "$DP_HAS_FAIL" -eq 1 ]]; then
    log_error "desktop-preflight: summary: one or more checks FAILED - see [FAIL] lines above"
    return 1
  fi
  log_info "desktop-preflight: summary: all checks passed (see [WARN] lines above, if any, for non-fatal notes)"
  return 0
}
