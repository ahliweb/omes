#!/usr/bin/env bats
# tests/integration/hermes-gateway.bats - `omes install` integration tests for
# modules/hermes-gateway (user, default) and modules/hermes-gateway-system
# (root, opt-in-only) executing bin/omes end to end against tests/shims/.
#
# module_check/module_apply/module_verify/module_rollback's own logic is
# exercised directly and exhaustively in tests/unit/hermes-gateway*.bats;
# these tests focus on end-to-end wiring through bin/omes: profile
# resolution + MODULE_REQUIRES ordering (hermes -> hermes-gateway), scope
# filtering, and exit code mapping.

setup() {
  load '../test_helper.bash'
  omes_test_setup
  OMES_BIN="${OMES_TEST_ROOT}/bin/omes"

  export HOME="${OMES_TEST_TMPDIR}/home"
  mkdir -p "$HOME"
  unset XDG_DATA_HOME XDG_SESSION_TYPE || true
  export OMES_HERMES_HOME="${HOME}/.hermes"

  OMES_OS_RELEASE_FILE="$(omes_fixture_path os-release)"
  cat > "$OMES_OS_RELEASE_FILE" <<'EOF'
PRETTY_NAME="Ubuntu 24.04 LTS"
NAME="Ubuntu"
VERSION_ID="24.04"
VERSION_CODENAME=noble
ID=ubuntu
ID_LIKE=debian
UBUNTU_CODENAME=noble
EOF
  export OMES_OS_RELEASE_FILE

  # hermes already "installed" (see tests/integration/hermes.bats) so the
  # hermes module is a trivial no-op and these tests focus on the gateway.
  export SHIM_HERMES_VERSION="1.2.3"

  export SHIM_USER_ENABLED_FILE="${OMES_TEST_TMPDIR}/user-enabled"
  export SHIM_USER_ACTIVE_FILE="${OMES_TEST_TMPDIR}/user-active"
  export SHIM_SYSTEM_ENABLED_FILE="${OMES_TEST_TMPDIR}/system-enabled"
  export SHIM_SYSTEM_ACTIVE_FILE="${OMES_TEST_TMPDIR}/system-active"

  # Production's modules/hermes-gateway-system writes to the real
  # /etc/systemd/system/...; tests redirect to an isolated tmpdir.
  export OMES_HERMES_GATEWAY_SYSTEM_DROPIN_DIR="${OMES_TEST_TMPDIR}/etc-systemd-system"
}

teardown() {
  omes_test_teardown
}

@test "install --profile hermes as non-root applies hermes then hermes-gateway" {
  run "$OMES_BIN" install --profile hermes --yes
  [ "$status" -eq 0 ]
  run grep -q '^module.hermes.status=applied$' "${OMES_STATE_DIR}/state"
  [ "$status" -eq 0 ]
  run grep -q '^module.hermes-gateway.status=applied$' "${OMES_STATE_DIR}/state"
  [ "$status" -eq 0 ]
}

@test "install --profile hermes as (simulated) root skips both hermes and hermes-gateway" {
  OMES_TEST=1 OMES_FAKE_ROOT=1 run "$OMES_BIN" install --profile hermes --yes
  [ "$status" -eq 0 ]
  [[ "$output" == *"Run as your user"* ]]
  run grep -c '^hermes ' "$SHIM_LOG"
  [ "$status" -ne 0 ] || [ "$output" -eq 0 ]
}

@test "install --profile hermes --dry-run touches no systemctl/loginctl state" {
  run "$OMES_BIN" install --profile hermes --dry-run --yes
  [ "$status" -eq 0 ]
  [ ! -s "$SHIM_USER_ENABLED_FILE" ]
  run grep -c 'loginctl' "$SHIM_LOG"
  [ "$status" -ne 0 ] || [ "$output" -eq 0 ]
}

@test "install --profile hermes exits 7 when hermes gateway status fails verification" {
  export SHIM_HERMES_GATEWAY_STATUS_EXIT=1
  run "$OMES_BIN" install --profile hermes --yes
  [ "$status" -eq 7 ]
}

@test "install --module hermes-gateway-system as (simulated) root without a target user fails preflight (exit 4)" {
  OMES_TEST=1 OMES_FAKE_ROOT=1 run "$OMES_BIN" install --module hermes-gateway-system --yes
  [ "$status" -eq 4 ]
}

@test "install --module hermes-gateway-system as (simulated) root with a valid target user succeeds" {
  export OMES_HERMES_GATEWAY_SYSTEM_USER="nobody"
  OMES_TEST=1 OMES_FAKE_ROOT=1 run "$OMES_BIN" install --module hermes-gateway-system --yes
  [ "$status" -eq 0 ]
  run grep -q '^module.hermes-gateway-system.status=applied$' "${OMES_STATE_DIR}/state"
  [ "$status" -eq 0 ]
}

@test "install --module hermes-gateway-system as non-root (explicit wrong-scope request) exits 5" {
  export OMES_HERMES_GATEWAY_SYSTEM_USER="nobody"
  run "$OMES_BIN" install --module hermes-gateway-system --yes
  [ "$status" -eq 5 ]
}

@test "hermes-gateway-system is never applied by --profile server (opt-in only via --module)" {
  OMES_TEST=1 OMES_FAKE_ROOT=1 run "$OMES_BIN" install --profile server --yes
  [ "$status" -eq 0 ]
  run grep -c '^module.hermes-gateway-system' "${OMES_STATE_DIR}/state"
  [ "$status" -ne 0 ] || [ "$output" -eq 0 ]
}
