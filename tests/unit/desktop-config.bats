#!/usr/bin/env bats
# tests/unit/desktop-config.bats - modules/desktop-config/module.sh unit
# tests.
#
# HOME is always an isolated tmpdir so nothing here ever touches the real
# developer's ~/.config or ~/.bashrc.

setup() {
  load '../test_helper.bash'
  omes_test_setup

  # shellcheck source=../../lib/omes/core.sh
  source "${OMES_TEST_ROOT}/lib/omes/core.sh"
  # shellcheck source=../../lib/omes/log.sh
  source "${OMES_TEST_ROOT}/lib/omes/log.sh"
  # shellcheck source=../../lib/omes/state.sh
  source "${OMES_TEST_ROOT}/lib/omes/state.sh"
  # shellcheck source=../../lib/omes/backup.sh
  source "${OMES_TEST_ROOT}/lib/omes/backup.sh"
  # shellcheck source=../../lib/omes/module.sh
  source "${OMES_TEST_ROOT}/lib/omes/module.sh"

  module_load desktop-config
  declare -ga OMES_MANAGED_PATHS=()

  export HOME="${OMES_TEST_TMPDIR}/home"
  mkdir -p "$HOME"
  unset XDG_CONFIG_HOME || true
}

teardown() {
  omes_test_teardown
}

@test "module_check passes when templates are present and \$HOME is writable" {
  run module_check
  [ "$status" -eq 0 ]
}

@test "module_apply installs hypr/waybar/foot config files and the shell snippet" {
  run module_apply
  [ "$status" -eq 0 ]

  [ -f "${HOME}/.config/hypr/hyprland.conf" ]
  [ -f "${HOME}/.config/waybar/config.jsonc" ]
  [ -f "${HOME}/.config/waybar/style.css" ]
  [ -f "${HOME}/.config/foot/foot.ini" ]
  [ -f "${HOME}/.config/omes/shell.sh" ]

  run grep -c 'BEGIN OMES desktop shell config' "${HOME}/.bashrc"
  [ "$status" -eq 0 ]
  [ "$output" -eq 1 ]
}

@test "module_apply is idempotent across re-runs (marker block appears exactly once)" {
  run module_apply
  [ "$status" -eq 0 ]
  run module_apply
  [ "$status" -eq 0 ]

  run grep -c 'BEGIN OMES desktop shell config' "${HOME}/.bashrc"
  [ "$status" -eq 0 ]
  [ "$output" -eq 1 ]
}

@test "module_apply backs up an existing differing hyprland.conf before replacing it, with --yes" {
  mkdir -p "${HOME}/.config/hypr"
  printf '# my own custom config\n' > "${HOME}/.config/hypr/hyprland.conf"

  export OMES_NONINTERACTIVE=1
  backup_begin "desktop-config" "pre-apply" >/dev/null
  run module_apply
  [ "$status" -eq 0 ]
  backup_finish >/dev/null

  run grep -c 'mod = SUPER' "${HOME}/.config/hypr/hyprland.conf"
  [ "$status" -eq 0 ]
  [ "$output" -ge 1 ]

  local backups_dir
  backups_dir="$(omes_state_dir)/backups"
  run find "$backups_dir" -type f -name 'hyprland.conf'
  [ "$status" -eq 0 ]
  [ -n "$output" ]
  run grep -c 'my own custom config' "$output"
  [ "$status" -eq 0 ]
  [ "$output" -eq 1 ]
}

@test "module_apply skips an existing differing file without --yes and records it as skipped" {
  mkdir -p "${HOME}/.config/hypr"
  printf '# my own custom config\n' > "${HOME}/.config/hypr/hyprland.conf"

  run module_apply
  [ "$status" -eq 0 ]

  run grep -c 'my own custom config' "${HOME}/.config/hypr/hyprland.conf"
  [ "$status" -eq 0 ]
  [ "$output" -eq 1 ]

  run state_get "module.desktop-config.skipped_paths"
  [ "$status" -eq 0 ]
  [[ "$output" == *"hyprland.conf"* ]]
}

@test "module_apply under --dry-run performs no writes" {
  export OMES_DRY_RUN=1
  run module_apply
  [ "$status" -eq 0 ]
  [ ! -e "${HOME}/.config/hypr/hyprland.conf" ]
  [ ! -e "${HOME}/.bashrc" ] || {
    run grep -c 'BEGIN OMES desktop shell config' "${HOME}/.bashrc"
    [ "$status" -ne 0 ] || [ "$output" -eq 0 ]
  }
}

@test "module_verify passes after a successful apply" {
  run module_apply
  [ "$status" -eq 0 ]
  run module_verify
  [ "$status" -eq 0 ]
}

@test "module_rollback removes the shell snippet and marker block but leaves config files" {
  run module_apply
  [ "$status" -eq 0 ]

  run module_rollback
  [ "$status" -eq 0 ]
  [ ! -f "${HOME}/.config/omes/shell.sh" ]
  run grep -c 'BEGIN OMES desktop shell config' "${HOME}/.bashrc"
  [ "$status" -ne 0 ] || [ "$output" -eq 0 ]

  [ -f "${HOME}/.config/hypr/hyprland.conf" ]
}

@test "module_check fails when \$HOME is not writable" {
  chmod 500 "$HOME"
  run module_check
  chmod 700 "$HOME"
  [ "$status" -eq 1 ]
}
