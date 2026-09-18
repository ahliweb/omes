#!/usr/bin/env bats
# tests/unit/hermes-gateway.bats - modules/hermes-gateway/module.sh unit tests.
#
# Sources lib/omes/*.sh and modules/hermes-gateway/module.sh directly
# (module_load), like tests/unit/hermes.bats. HOME is always overridden to
# an isolated tmpdir. tests/shims/{hermes,systemctl,loginctl,sudo} are
# already on PATH via test_helper.bash's omes_test_setup.

setup() {
  load '../test_helper.bash'
  omes_test_setup

  export HOME="${OMES_TEST_TMPDIR}/home"
  mkdir -p "$HOME"
  unset XDG_DATA_HOME XDG_SESSION_TYPE || true

  # Headless/server by default (detect_session): SHIM_DEFAULT_TARGET
  # defaults to multi-user.target (not graphical.target) and
  # XDG_SESSION_TYPE is unset above, so detect_session -> "server".
  export SHIM_USER_ENABLED_FILE="${OMES_TEST_TMPDIR}/user-enabled"
  export SHIM_USER_ACTIVE_FILE="${OMES_TEST_TMPDIR}/user-active"
  export SHIM_HERMES_VERSION="1.2.3"
  unset OMES_GATEWAY_MODE || true

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

  module_load hermes-gateway
  declare -ga OMES_MANAGED_PATHS=()
}

teardown() {
  omes_test_teardown
}

# --- module_check ------------------------------------------------------------

@test "module_check refuses to run as root" {
  export OMES_TEST=1 OMES_FAKE_ROOT=1
  run module_check
  [ "$status" -eq 1 ]
}

@test "module_check refuses OMES_GATEWAY_MODE=system" {
  export OMES_GATEWAY_MODE=system
  run module_check
  [ "$status" -eq 1 ]
  [[ "$output" == *"hermes-gateway-system"* ]]
}

@test "module_check does not hard-fail when hermes is not yet installed (check-all runs before any apply)" {
  # MODULE_REQUIRES=(hermes) only orders the APPLY phase; check-all runs
  # for every module before any module_apply, so on a first-ever install
  # hermes legitimately is not installed yet when this check runs.
  unset SHIM_HERMES_VERSION || true
  run module_check
  [ "$status" -eq 0 ]
  [[ "$output" == *"not yet installed"* ]]
}

@test "module_check passes when hermes is installed and systemctl exists" {
  run module_check
  [ "$status" -eq 0 ]
}

# --- module_apply: basic wiring ----------------------------------------------

@test "module_apply installs the gateway, writes the PATH drop-in, and enables+starts the unit" {
  export OMES_NONINTERACTIVE=1
  run module_apply
  [ "$status" -eq 0 ]

  run grep -c '^hermes gateway install$' "$SHIM_LOG"
  [ "$status" -eq 0 ]
  [ "$output" -eq 1 ]

  [ -f "${HOME}/.config/systemd/user/hermes-gateway.service.d/omes-path.conf" ]
  run grep -c 'Environment=PATH=' "${HOME}/.config/systemd/user/hermes-gateway.service.d/omes-path.conf"
  [ "$status" -eq 0 ]
  [ "$output" -eq 1 ]

  run grep -qxF 'hermes-gateway' "$SHIM_USER_ENABLED_FILE"
  [ "$status" -eq 0 ]
  run grep -qxF 'hermes-gateway' "$SHIM_USER_ACTIVE_FILE"
  [ "$status" -eq 0 ]
}

@test "module_apply includes OMES_HERMES_GATEWAY_EXTRA_PATH in the drop-in when set" {
  export OMES_NONINTERACTIVE=1
  export OMES_HERMES_GATEWAY_EXTRA_PATH="/opt/node/bin:/opt/ffmpeg/bin"
  run module_apply
  [ "$status" -eq 0 ]
  run grep -c '/opt/node/bin:/opt/ffmpeg/bin' "${HOME}/.config/systemd/user/hermes-gateway.service.d/omes-path.conf"
  [ "$status" -eq 0 ]
  [ "$output" -eq 1 ]
}

@test "module_apply performs no mutation under --dry-run" {
  export OMES_DRY_RUN=1
  export OMES_NONINTERACTIVE=1
  run module_apply
  [ "$status" -eq 0 ]
  [[ "$output" == *"[dry-run]"* ]]
  run grep -c 'hermes gateway install' "$SHIM_LOG"
  [ "$status" -ne 0 ] || [ "$output" -eq 0 ]
  [ ! -f "${HOME}/.config/systemd/user/hermes-gateway.service.d/omes-path.conf" ]
  [ ! -s "$SHIM_USER_ENABLED_FILE" ]
}

# --- module_apply: lingering consent (headless-only) --------------------------

@test "module_apply enables lingering on a headless host with --yes/OMES_NONINTERACTIVE" {
  export OMES_NONINTERACTIVE=1
  run module_apply
  [ "$status" -eq 0 ]
  run grep -c '^loginctl enable-linger' "$SHIM_LOG"
  [ "$status" -eq 0 ]
  [ "$output" -eq 1 ]
  run state_get "module.hermes-gateway.linger_enabled"
  [ "$status" -eq 0 ]
  [ "$output" = "true" ]
}

@test "module_apply does not enable lingering without consent (no tty, not noninteractive)" {
  run module_apply
  [ "$status" -eq 0 ]
  run grep -c 'loginctl enable-linger' "$SHIM_LOG"
  [ "$status" -ne 0 ] || [ "$output" -eq 0 ]
  run state_get "module.hermes-gateway.linger_enabled"
  [ "$status" -eq 0 ]
  [ "$output" = "false" ]
}

@test "module_apply never offers lingering on a desktop (non-headless) session" {
  export XDG_SESSION_TYPE=x11
  export OMES_NONINTERACTIVE=1
  run module_apply
  [ "$status" -eq 0 ]
  run grep -c 'loginctl' "$SHIM_LOG"
  [ "$status" -ne 0 ] || [ "$output" -eq 0 ]
  run state_get "module.hermes-gateway.linger_enabled"
  [ "$status" -ne 0 ]
}

@test "module_apply does not re-prompt for lingering on a second run once already enabled" {
  export OMES_NONINTERACTIVE=1
  run module_apply
  [ "$status" -eq 0 ]
  : >"$SHIM_LOG"
  run module_apply
  [ "$status" -eq 0 ]
  run grep -c 'loginctl' "$SHIM_LOG"
  [ "$status" -ne 0 ] || [ "$output" -eq 0 ]
}

# --- module_verify -------------------------------------------------------------

@test "module_verify fails when the --user unit is not enabled" {
  run module_verify
  [ "$status" -eq 1 ]
  [[ "$output" == *"not enabled"* ]]
}

@test "module_verify fails when the --user unit is enabled but not active" {
  export OMES_NONINTERACTIVE=1
  run module_apply
  [ "$status" -eq 0 ]
  # Simulate the unit crashing/stopping without being disabled.
  : >"$SHIM_USER_ACTIVE_FILE"

  run module_verify
  [ "$status" -eq 1 ]
  [[ "$output" == *"not active"* ]]
}

@test "module_verify fails when hermes gateway status fails" {
  export OMES_NONINTERACTIVE=1
  run module_apply
  [ "$status" -eq 0 ]
  export SHIM_HERMES_GATEWAY_STATUS_EXIT=1
  run module_verify
  [ "$status" -eq 1 ]
}

@test "module_verify passes and always logs the green-signal caveat" {
  export OMES_NONINTERACTIVE=1
  run module_apply
  [ "$status" -eq 0 ]
  run module_verify
  [ "$status" -eq 0 ]
  [[ "$output" == *"does NOT prove"* ]]
}

@test "module_verify warns extra when the status output looks disconnected" {
  export OMES_NONINTERACTIVE=1
  run module_apply
  [ "$status" -eq 0 ]
  export SHIM_HERMES_GATEWAY_STATUS_OUTPUT="adapter disconnected"
  run module_verify
  [ "$status" -eq 0 ]
  [[ "$output" == *"may not be connected"* ]]
}

# --- module_rollback ------------------------------------------------------------

@test "module_rollback disables lingering only when OMES itself enabled it" {
  export OMES_NONINTERACTIVE=1
  run module_apply
  [ "$status" -eq 0 ]
  : >"$SHIM_LOG"

  run module_rollback
  [ "$status" -eq 0 ]
  run grep -c '^loginctl disable-linger' "$SHIM_LOG"
  [ "$status" -eq 0 ]
  [ "$output" -eq 1 ]

  run grep -qxF 'hermes-gateway' "$SHIM_USER_ENABLED_FILE"
  [ "$status" -ne 0 ]
  [ ! -f "${HOME}/.config/systemd/user/hermes-gateway.service.d/omes-path.conf" ]
}

@test "module_rollback never calls disable-linger when OMES never enabled it" {
  run module_apply
  [ "$status" -eq 0 ]
  : >"$SHIM_LOG"

  run module_rollback
  [ "$status" -eq 0 ]
  run grep -c 'loginctl disable-linger' "$SHIM_LOG"
  [ "$status" -ne 0 ] || [ "$output" -eq 0 ]
}
