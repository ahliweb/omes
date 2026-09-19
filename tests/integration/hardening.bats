#!/usr/bin/env bats
# tests/integration/hardening.bats - `omes install`/`uninstall` integration
# tests for the hermes-gateway hardening drop-in (issue #81), executing
# bin/omes end to end against tests/shims/.
#
# Unit-level behavior of modules/hermes-gateway/hardening.sh itself is
# exercised directly and exhaustively in tests/unit/hardening.bats; these
# tests focus on end-to-end wiring through bin/omes.

setup() {
  load '../test_helper.bash'
  omes_test_setup
  OMES_BIN="${OMES_TEST_ROOT}/bin/omes"

  export HOME="${OMES_TEST_TMPDIR}/home"
  mkdir -p "$HOME"
  unset XDG_DATA_HOME XDG_SESSION_TYPE || true
  export OMES_HERMES_HOME="${HOME}/.hermes"

  OMES_OS_RELEASE_FILE="$(omes_fixture_path os-release)"
  cat >"$OMES_OS_RELEASE_FILE" <<'EOF'
PRETTY_NAME="Ubuntu 24.04 LTS"
NAME="Ubuntu"
VERSION_ID="24.04"
VERSION_CODENAME=noble
ID=ubuntu
ID_LIKE=debian
UBUNTU_CODENAME=noble
EOF
  export OMES_OS_RELEASE_FILE

  export SHIM_HERMES_VERSION="1.2.3"

  export SHIM_USER_ENABLED_FILE="${OMES_TEST_TMPDIR}/user-enabled"
  export SHIM_USER_ACTIVE_FILE="${OMES_TEST_TMPDIR}/user-active"
  export SHIM_SYSTEM_ENABLED_FILE="${OMES_TEST_TMPDIR}/system-enabled"
  export SHIM_SYSTEM_ACTIVE_FILE="${OMES_TEST_TMPDIR}/system-active"
  export OMES_HERMES_GATEWAY_SYSTEM_DROPIN_DIR="${OMES_TEST_TMPDIR}/etc-systemd-system"

  unset OMES_HERMES_HARDENING OMES_HERMES_MEMORY_MAX OMES_HERMES_RW_PATHS OMES_HERMES_HARDENING_TIMEOUT SHIM_RESTART_FAILS || true
}

teardown() {
  omes_test_teardown
}

_hardening_dropin_user() {
  printf '%s/.config/systemd/user/hermes-gateway.service.d/20-omes-hardening.conf\n' "$HOME"
}

_hardening_dropin_system() {
  printf '%s/hermes-gateway.service.d/20-omes-hardening.conf\n' "$OMES_HERMES_GATEWAY_SYSTEM_DROPIN_DIR"
}

@test "off (default) writes no hardening drop-in" {
  run "$OMES_BIN" install --profile hermes --yes
  [ "$status" -eq 0 ]
  [ ! -f "$(_hardening_dropin_user)" ]
}

@test "strict is never the default profile" {
  run "$OMES_BIN" install --profile hermes --yes
  [ "$status" -eq 0 ]
  run grep -q '^module.hermes-gateway.hardening_profile=strict$' "${OMES_STATE_DIR}/state"
  [ "$status" -ne 0 ]
}

@test "conservative profile writes the managed drop-in and the unit stays active" {
  export OMES_HERMES_HARDENING=conservative
  run "$OMES_BIN" install --profile hermes --yes
  [ "$status" -eq 0 ]

  local dropin
  dropin="$(_hardening_dropin_user)"
  [ -f "$dropin" ]
  run grep -q 'Restart=on-failure' "$dropin"
  [ "$status" -eq 0 ]
  run grep -qx 'hermes-gateway' "$SHIM_USER_ACTIVE_FILE"
  [ "$status" -eq 0 ]
}

@test "failed restart under a hardening profile auto-rolls-back the drop-in and exits 6 naming the module" {
  export OMES_HERMES_HARDENING=conservative
  export OMES_HERMES_HARDENING_TIMEOUT=1
  export SHIM_RESTART_FAILS=1

  run "$OMES_BIN" install --profile hermes --yes
  [ "$status" -eq 6 ]
  [[ "$output" == *"hermes-gateway"* ]]
  [ ! -f "$(_hardening_dropin_user)" ]
}

@test "uninstall rollback removes only the hardening drop-in, not the PATH drop-in" {
  export OMES_HERMES_HARDENING=conservative
  run "$OMES_BIN" install --profile hermes --yes
  [ "$status" -eq 0 ]

  local path_dropin
  path_dropin="$(dirname "$(_hardening_dropin_user)")/omes-path.conf"
  [ -f "$path_dropin" ]

  run "$OMES_BIN" uninstall --module hermes-gateway --yes
  [ "$status" -eq 0 ]
  [ ! -f "$(_hardening_dropin_user)" ]
  [ ! -f "$path_dropin" ]
}

@test "system-mode hardening writes to the configured system drop-in directory" {
  export OMES_HERMES_HARDENING=conservative
  export OMES_HERMES_GATEWAY_SYSTEM_USER="nobody"
  OMES_TEST=1 OMES_FAKE_ROOT=1 run "$OMES_BIN" install --module hermes-gateway-system --yes
  [ "$status" -eq 0 ]

  local dropin
  dropin="$(_hardening_dropin_system)"
  [ -f "$dropin" ]
  run grep -q 'Restart=on-failure' "$dropin"
  [ "$status" -eq 0 ]
}

@test "check --profile hermes with a hardening profile requested tolerates missing optional tools" {
  export OMES_HERMES_HARDENING=conservative
  run "$OMES_BIN" check --profile hermes
  [ "$status" -eq 0 ]
}
