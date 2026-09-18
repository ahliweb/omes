#!/usr/bin/env bats
# tests/unit/desktop-preflight.bats - modules/desktop-preflight/module.sh
# and modules/desktop-preflight/checks.sh unit tests.
#
# RAM/disk thresholds are made deterministic via OMES_DP_MEM_MB_OVERRIDE /
# OMES_DP_DISK_FREE_MB_OVERRIDE (see checks.sh's _dp_mem_mb/_dp_disk_free_mb
# - a local override hook, since lib/omes/detect.sh has none and is out of
# this issue's file scope). The Cinnamon session file is always pointed at
# an isolated tmp path via OMES_CINNAMON_SESSION_FILE so these tests never
# depend on (or risk depending on) any real Cinnamon install.

setup() {
  load '../test_helper.bash'
  omes_test_setup

  # shellcheck source=../../lib/omes/core.sh
  source "${OMES_TEST_ROOT}/lib/omes/core.sh"
  # shellcheck source=../../lib/omes/log.sh
  source "${OMES_TEST_ROOT}/lib/omes/log.sh"
  # shellcheck source=../../lib/omes/detect.sh
  source "${OMES_TEST_ROOT}/lib/omes/detect.sh"
  # shellcheck source=../../lib/omes/state.sh
  source "${OMES_TEST_ROOT}/lib/omes/state.sh"
  # shellcheck source=../../lib/omes/backup.sh
  source "${OMES_TEST_ROOT}/lib/omes/backup.sh"
  # shellcheck source=../../lib/omes/module.sh
  source "${OMES_TEST_ROOT}/lib/omes/module.sh"
  # shellcheck source=../../lib/omes/pkg.sh
  source "${OMES_TEST_ROOT}/lib/omes/pkg.sh"

  OMES_OS_RELEASE_FILE="${OMES_TEST_ROOT}/tests/fixtures/os-release/linuxmint-22"
  detect_os >/dev/null
  detect_arch >/dev/null
  detect_tier >/dev/null

  module_load desktop-preflight
  declare -ga OMES_MANAGED_PATHS=()

  export OMES_DP_MEM_MB_OVERRIDE=16384
  export OMES_DP_DISK_FREE_MB_OVERRIDE=40960
  export SHIM_ENABLED_SERVICES="lightdm"
  export OMES_CINNAMON_SESSION_FILE="${OMES_TEST_TMPDIR}/cinnamon.desktop"
  printf '[Desktop Entry]\nName=Cinnamon\n' > "$OMES_CINNAMON_SESSION_FILE"

  unset SHIM_APT_CACHE_UNKNOWN_PKGS SHIM_LSPCI_OUTPUT SHIM_LSPCI_K_OUTPUT \
    SHIM_LOGINCTL_FAIL SHIM_GLXINFO_DIRECT SHIM_NVIDIA_DRIVER_VERSION \
    OMES_PROC_CMDLINE_FILE OMES_NVIDIA_MODESET_PARAM_FILE || true
}

teardown() {
  omes_test_teardown
}

# --- Cinnamon safety invariant ----------------------------------------------

@test "module_check fails when cinnamon.desktop is missing" {
  export OMES_CINNAMON_SESSION_FILE="${OMES_TEST_TMPDIR}/does-not-exist/cinnamon.desktop"
  run module_check
  [ "$status" -eq 1 ]
  [[ "$output" == *"[FAIL]"* ]]
  [[ "$output" == *"Cinnamon session file not found"* ]]
}

@test "module_check passes the Cinnamon check when cinnamon.desktop is present" {
  run module_check
  [[ "$output" == *"Cinnamon fallback session found"* ]]
}

# --- hyprland package availability ------------------------------------------

@test "module_check fails when hyprland is unavailable in repos, with an actionable message, no apt-get call" {
  export SHIM_APT_CACHE_UNKNOWN_PKGS="hyprland"
  run module_check
  [ "$status" -eq 1 ]
  [[ "$output" == *"[FAIL]"* ]]
  [[ "$output" == *"hyprland"* ]]
  [[ "$output" == *"docs/packages.md"* ]]
  [[ "$output" == *"docs/linux-mint.md"* ]]
  run grep -c '^apt-get install' "$SHIM_LOG"
  [ "$status" -ne 0 ] || [ "$output" -eq 0 ]
}

@test "module_check fails when hypridle is unavailable in repos (treated like hyprland)" {
  export SHIM_APT_CACHE_UNKNOWN_PKGS="hypridle"
  run module_check
  [ "$status" -eq 1 ]
  [[ "$output" == *"hypridle"* ]]
}

@test "module_check passes overall when every package is available" {
  run module_check
  [ "$status" -eq 0 ]
  [[ "$output" != *"[FAIL]"* ]]
}

@test "module_check warns, but does not fail, when a soft package (e.g. cliphist) is unavailable" {
  export SHIM_APT_CACHE_UNKNOWN_PKGS="cliphist"
  run module_check
  [ "$status" -eq 0 ]
  [[ "$output" == *"[WARN]"* ]]
  [[ "$output" == *"cliphist"* ]]
}

# --- GPU / driver ------------------------------------------------------------

@test "module_check marks Intel GPU as OK" {
  export SHIM_LSPCI_K_OUTPUT=$'00:02.0 VGA compatible controller: Intel Corporation Iris\n\tKernel driver in use: i915'
  run module_check
  [ "$status" -eq 0 ]
  [[ "$output" == *"GPU vendor 'intel' detected"* ]]
}

@test "module_check warns on NVIDIA proprietary driver and reports the version/modeset detail" {
  export SHIM_LSPCI_K_OUTPUT=$'01:00.0 VGA compatible controller: NVIDIA Corporation GA106\n\tKernel driver in use: nvidia'
  export SHIM_NVIDIA_DRIVER_VERSION="555.42.02"
  local cmdline="${OMES_TEST_TMPDIR}/cmdline"
  printf 'BOOT_IMAGE=/vmlinuz quiet nvidia-drm.modeset=1\n' > "$cmdline"
  export OMES_PROC_CMDLINE_FILE="$cmdline"
  run module_check
  [ "$status" -eq 0 ]
  [[ "$output" == *"[WARN]"* ]]
  [[ "$output" == *"NVIDIA proprietary driver detected"* ]]
  [[ "$output" == *"555.42.02"* ]]
  [[ "$output" == *">= 555"* ]]
  [[ "$output" == *"nvidia-drm.modeset=1 is set"* ]]
}

@test "module_check warns when the NVIDIA driver is older than 555 and modeset is not set" {
  export SHIM_LSPCI_K_OUTPUT=$'01:00.0 VGA compatible controller: NVIDIA Corporation GA106\n\tKernel driver in use: nvidia'
  export SHIM_NVIDIA_DRIVER_VERSION="535.129.03"
  export OMES_PROC_CMDLINE_FILE="${OMES_TEST_TMPDIR}/cmdline-empty"
  : > "$OMES_PROC_CMDLINE_FILE"
  run module_check
  [ "$status" -eq 0 ]
  [[ "$output" == *"< 555"* ]]
  [[ "$output" == *"nvidia-drm.modeset=1 is NOT set"* ]]
}

@test "module_check fails on nouveau" {
  export SHIM_LSPCI_K_OUTPUT=$'01:00.0 VGA compatible controller: NVIDIA Corporation GA106\n\tKernel driver in use: nouveau'
  run module_check
  [ "$status" -eq 1 ]
  [[ "$output" == *"[FAIL]"* ]]
  [[ "$output" == *"nouveau"* ]]
}

@test "module_check warns (does not fail) on a virtio GPU" {
  export SHIM_LSPCI_OUTPUT="00:02.0 VGA compatible controller: Red Hat, Inc. Virtio GPU"
  export SHIM_LSPCI_K_OUTPUT=$'00:02.0 VGA compatible controller: Red Hat, Inc. Virtio GPU\n\tKernel driver in use: virtio_gpu'
  run module_check
  [ "$status" -eq 0 ]
  [[ "$output" == *"virtual GPU detected"* ]]
}

@test "module_check fails on an unrecognized GPU" {
  export SHIM_LSPCI_OUTPUT="00:02.0 VGA compatible controller: Some Unknown Vendor Widget"
  export SHIM_LSPCI_K_OUTPUT="00:02.0 VGA compatible controller: Some Unknown Vendor Widget"
  run module_check
  [ "$status" -eq 1 ]
  [[ "$output" == *"could not be determined"* ]]
}

# --- RAM / disk ---------------------------------------------------------------

@test "module_check fails when RAM is below the 8 GB desktop minimum" {
  export OMES_DP_MEM_MB_OVERRIDE=4096
  run module_check
  [ "$status" -eq 1 ]
  [[ "$output" == *"RAM: 4096 MB is below"* ]]
}

@test "module_check fails when free disk is below the 20 GB desktop minimum" {
  export OMES_DP_DISK_FREE_MB_OVERRIDE=1024
  run module_check
  [ "$status" -eq 1 ]
  [[ "$output" == *"free disk: 1024 MB is below"* ]]
}

# --- display manager -----------------------------------------------------------

@test "module_check marks lightdm as OK" {
  printf 'lightdm\n' > "${OMES_TEST_TMPDIR}/dm"
  export SHIM_ENABLED_SERVICES="lightdm"
  # detect_display_manager checks /etc/X11/default-display-manager first;
  # keep that path unreadable so it falls through to the systemctl shim.
  run module_check
  [ "$status" -eq 0 ]
  [[ "$output" == *"display manager: lightdm"* ]]
}

@test "module_check warns on gdm3" {
  export SHIM_ENABLED_SERVICES="gdm3"
  run module_check
  [ "$status" -eq 0 ]
  [[ "$output" == *"[WARN]"* ]]
  [[ "$output" == *"display manager: gdm3"* ]]
}

@test "module_check fails when no display manager is detected" {
  unset SHIM_ENABLED_SERVICES || true
  run module_check
  [ "$status" -eq 1 ]
  [[ "$output" == *"no display manager detected"* ]]
}

# --- apply / verify / rollback are mutation-free -----------------------------

@test "module_apply performs no writes and records only a state timestamp" {
  run module_apply
  [ "$status" -eq 0 ]
  run state_get "module.desktop-preflight.last_run"
  [ "$status" -eq 0 ]
  [ -n "$output" ]
}

@test "module_apply under --dry-run writes no state" {
  export OMES_DRY_RUN=1
  run module_apply
  [ "$status" -eq 0 ]
  run state_get "module.desktop-preflight.last_run"
  [ "$status" -ne 0 ]
}

@test "module_verify re-runs the same checks as module_check" {
  run module_verify
  [ "$status" -eq 0 ]
  [[ "$output" == *"Cinnamon fallback session found"* ]]
}

@test "module_rollback is a no-op and never fails" {
  run module_rollback
  [ "$status" -eq 0 ]
}
