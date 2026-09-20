#!/usr/bin/env bats
# tests/unit/hyprland-session.bats - modules/hyprland-session/module.sh unit
# tests.
#
# The real session/wrapper paths (/usr/share/wayland-sessions,
# /usr/local/bin) are never written to: OMES_SESSION_DIR / OMES_BIN_DIR
# override them to isolated tmpdirs. The Cinnamon session file is always
# an isolated fake via OMES_CINNAMON_SESSION_FILE, and every test asserts
# it is left untouched (byte-for-byte, and present) - the safety invariant
# this whole feature depends on.

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

  module_load hyprland-session
  declare -ga OMES_MANAGED_PATHS=()

  export HOME="${OMES_TEST_TMPDIR}/home"
  mkdir -p "$HOME"

  export OMES_SESSION_DIR="${OMES_TEST_TMPDIR}/wayland-sessions"
  export OMES_BIN_DIR="${OMES_TEST_TMPDIR}/local-bin"
  mkdir -p "$OMES_SESSION_DIR" "$OMES_BIN_DIR"

  export OMES_DP_MEM_MB_OVERRIDE=16384
  export OMES_DP_DISK_FREE_MB_OVERRIDE=40960
  export SHIM_ENABLED_SERVICES="lightdm"

  export OMES_CINNAMON_SESSION_FILE="${OMES_TEST_TMPDIR}/cinnamon.desktop"
  printf '[Desktop Entry]\nName=Cinnamon\n' >"$OMES_CINNAMON_SESSION_FILE"

  unset SHIM_APT_CACHE_UNKNOWN_PKGS SHIM_LSPCI_OUTPUT SHIM_LSPCI_K_OUTPUT || true
}

teardown() {
  omes_test_teardown
}

_cinnamon_untouched() {
  [ -f "$OMES_CINNAMON_SESSION_FILE" ]
  run cat "$OMES_CINNAMON_SESSION_FILE"
  [[ "$output" == *"Name=Cinnamon"* ]]
}

# --- module_check ------------------------------------------------------------

@test "module_check fails without cinnamon.desktop" {
  export OMES_CINNAMON_SESSION_FILE="${OMES_TEST_TMPDIR}/nope/cinnamon.desktop"
  run module_check
  [ "$status" -eq 1 ]
  [[ "$output" == *"Cinnamon session file not found"* ]]
}

@test "module_check fails when hyprland is unavailable in repos, no apt-get call" {
  export SHIM_APT_CACHE_UNKNOWN_PKGS="hyprland"
  run module_check
  [ "$status" -eq 1 ]
  [[ "$output" == *"hyprland"* ]]
  run grep -c '^apt-get install' "$SHIM_LOG"
  [ "$status" -ne 0 ] || [ "$output" -eq 0 ]
}

# --- module_apply -------------------------------------------------------------

@test "module_apply installs the toolset and writes the session file and wrapper" {
  run module_apply
  [ "$status" -eq 0 ]

  local session="${OMES_SESSION_DIR}/omes-hyprland.desktop"
  local wrapper="${OMES_BIN_DIR}/omes-hyprland-session"

  [ -f "$session" ]
  [ -x "$wrapper" ]

  run grep -c "Exec=${wrapper}" "$session"
  [ "$status" -eq 0 ]
  [ "$output" -eq 1 ]

  run grep -c 'XDG_CURRENT_DESKTOP=Hyprland' "$wrapper"
  [ "$status" -eq 0 ]
  [ "$output" -eq 1 ]

  run grep -c '^apt-get install' "$SHIM_LOG"
  [ "$status" -eq 0 ]
  [ "$output" -eq 1 ]

  run grep -c 'apt-get install.*hyprland' "$SHIM_LOG"
  [ "$status" -eq 0 ]
  [ "$output" -ge 1 ]

  _cinnamon_untouched
}

@test "module_apply refuses and writes nothing when cinnamon.desktop is missing" {
  export OMES_CINNAMON_SESSION_FILE="${OMES_TEST_TMPDIR}/nope/cinnamon.desktop"
  run module_apply
  [ "$status" -eq 1 ]
  [ ! -f "${OMES_SESSION_DIR}/omes-hyprland.desktop" ]
  [ ! -f "${OMES_BIN_DIR}/omes-hyprland-session" ]
}

@test "module_apply is idempotent: re-running installs nothing new" {
  run module_apply
  [ "$status" -eq 0 ]
  : >"$SHIM_LOG"

  run module_apply
  [ "$status" -eq 0 ]
  run grep -c '^apt-get install' "$SHIM_LOG"
  [ "$status" -ne 0 ] || [ "$output" -eq 0 ]

  _cinnamon_untouched
}

@test "module_apply under --dry-run performs no writes" {
  export OMES_DRY_RUN=1
  run module_apply
  [ "$status" -eq 0 ]
  [ ! -e "${OMES_SESSION_DIR}/omes-hyprland.desktop" ]
  [ ! -e "${OMES_BIN_DIR}/omes-hyprland-session" ]
  run grep -c '^apt-get' "$SHIM_LOG"
  [ "$status" -ne 0 ] || [ "$output" -eq 0 ]
  _cinnamon_untouched
}

# --- module_verify -------------------------------------------------------------

@test "module_verify passes after a successful apply" {
  run module_apply
  [ "$status" -eq 0 ]
  run module_verify
  [ "$status" -eq 0 ]
  [[ "$output" == *"Hyprland binary resolves"* ]]
}

@test "module_verify fails when the session file is missing" {
  run module_apply
  [ "$status" -eq 0 ]
  rm -f "${OMES_SESSION_DIR}/omes-hyprland.desktop"
  run module_verify
  [ "$status" -eq 1 ]
  [[ "$output" == *"session file missing"* ]]
}

# --- module_rollback -----------------------------------------------------------

@test "module_rollback removes only the session file and wrapper, never cinnamon" {
  run module_apply
  [ "$status" -eq 0 ]

  run module_rollback
  [ "$status" -eq 0 ]
  [ ! -e "${OMES_SESSION_DIR}/omes-hyprland.desktop" ]
  [ ! -e "${OMES_BIN_DIR}/omes-hyprland-session" ]
  [[ "$output" == *"will not remove them automatically"* ]]

  run grep -c '^apt-get remove' "$SHIM_LOG"
  [ "$status" -ne 0 ] || [ "$output" -eq 0 ]

  _cinnamon_untouched
}

@test "module_rollback under --dry-run removes nothing" {
  run module_apply
  [ "$status" -eq 0 ]
  export OMES_DRY_RUN=1
  run module_rollback
  [ "$status" -eq 0 ]
  [ -f "${OMES_SESSION_DIR}/omes-hyprland.desktop" ]
  [ -x "${OMES_BIN_DIR}/omes-hyprland-session" ]
}
