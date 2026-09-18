#!/usr/bin/env bats
# tests/unit/hermes-gateway-system.bats - modules/hermes-gateway-system/module.sh
# unit tests. Root-scope behavior uses the OMES_TEST=1/OMES_FAKE_ROOT=1 test
# hook (lib/omes/core.sh: omes_is_root), never real privilege escalation.

setup() {
  load '../test_helper.bash'
  omes_test_setup

  export HOME="${OMES_TEST_TMPDIR}/home"
  mkdir -p "$HOME"

  export SHIM_SYSTEM_ENABLED_FILE="${OMES_TEST_TMPDIR}/system-enabled"
  export SHIM_SYSTEM_ACTIVE_FILE="${OMES_TEST_TMPDIR}/system-active"
  export SHIM_HERMES_VERSION="1.2.3"

  # Production writes to the real /etc/systemd/system/...; tests redirect
  # to an isolated tmpdir (same pattern as lib/omes/pkg.sh's
  # OMES_APT_SOURCES_DIR) so no real root/filesystem access is needed.
  export OMES_HERMES_GATEWAY_SYSTEM_DROPIN_DIR="${OMES_TEST_TMPDIR}/etc-systemd-system"

  # A well-known, non-root account that exists on virtually every Linux
  # base image (including the Alpine bats/bats:latest image tests/run.sh
  # uses) - a stand-in for "an existing, non-root account" so
  # _hgws_user_exists_and_not_root and `getent passwd` resolve something
  # real without needing an actual second OS account provisioned in CI.
  # (The invoking test user's own numeric uid is deliberately NOT used
  # here: inside a `docker run --user <uid>:<gid>` container that uid has
  # no /etc/passwd entry at all, so `id -un`/`getent passwd` on it fails.)
  TARGET_USER="nobody"
  export TARGET_USER
  export OMES_HERMES_GATEWAY_SYSTEM_USER="$TARGET_USER"

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

  module_load hermes-gateway-system
  declare -ga OMES_MANAGED_PATHS=()
}

teardown() {
  omes_test_teardown
}

# --- module_check --------------------------------------------------------------

@test "module_check requires root" {
  run module_check
  [ "$status" -eq 1 ]
}

@test "module_check requires OMES_HERMES_GATEWAY_SYSTEM_USER to be set" {
  export OMES_TEST=1 OMES_FAKE_ROOT=1
  unset OMES_HERMES_GATEWAY_SYSTEM_USER
  run module_check
  [ "$status" -eq 1 ]
}

@test "module_check refuses to target the root account" {
  export OMES_TEST=1 OMES_FAKE_ROOT=1
  export OMES_HERMES_GATEWAY_SYSTEM_USER="root"
  run module_check
  [ "$status" -eq 1 ]
  [[ "$output" == *"refusing"* ]]
}

@test "module_check refuses a nonexistent target user" {
  export OMES_TEST=1 OMES_FAKE_ROOT=1
  export OMES_HERMES_GATEWAY_SYSTEM_USER="omes-nonexistent-test-user"
  run module_check
  [ "$status" -eq 1 ]
}

@test "module_check passes for a valid non-root existing user" {
  export OMES_TEST=1 OMES_FAKE_ROOT=1
  run module_check
  [ "$status" -eq 0 ]
}

# --- module_apply ----------------------------------------------------------------

@test "module_apply installs the system gateway and enables+starts the system unit" {
  export OMES_TEST=1 OMES_FAKE_ROOT=1
  run module_apply
  [ "$status" -eq 0 ]

  run grep -c '^hermes gateway install --system$' "$SHIM_LOG"
  [ "$status" -eq 0 ]
  [ "$output" -eq 1 ]

  [ -f "${OMES_HERMES_GATEWAY_SYSTEM_DROPIN_DIR}/hermes-gateway.service.d/omes-path.conf" ]
  run grep -qxF 'hermes-gateway' "$SHIM_SYSTEM_ENABLED_FILE"
  [ "$status" -eq 0 ]
  run grep -qxF 'hermes-gateway' "$SHIM_SYSTEM_ACTIVE_FILE"
  [ "$status" -eq 0 ]
}

@test "module_apply performs no mutation under --dry-run" {
  export OMES_TEST=1 OMES_FAKE_ROOT=1
  export OMES_DRY_RUN=1
  run module_apply
  [ "$status" -eq 0 ]
  [[ "$output" == *"[dry-run]"* ]]
  run grep -c 'hermes gateway install --system' "$SHIM_LOG"
  [ "$status" -ne 0 ] || [ "$output" -eq 0 ]
  [ ! -s "$SHIM_SYSTEM_ENABLED_FILE" ]
}

# --- module_verify -----------------------------------------------------------------

@test "module_verify fails when the system unit is not enabled" {
  export OMES_TEST=1 OMES_FAKE_ROOT=1
  run module_verify
  [ "$status" -eq 1 ]
}

@test "module_verify fails when the system unit is enabled but not active" {
  export OMES_TEST=1 OMES_FAKE_ROOT=1
  run module_apply
  [ "$status" -eq 0 ]
  : >"$SHIM_SYSTEM_ACTIVE_FILE"
  run module_verify
  [ "$status" -eq 1 ]
}

@test "module_verify passes and logs the green-signal caveat" {
  export OMES_TEST=1 OMES_FAKE_ROOT=1
  run module_apply
  [ "$status" -eq 0 ]
  run module_verify
  [ "$status" -eq 0 ]
  [[ "$output" == *"does NOT prove"* ]]
}

# --- module_rollback --------------------------------------------------------------

@test "module_rollback removes the drop-in and disables the system unit, never touches lingering" {
  export OMES_TEST=1 OMES_FAKE_ROOT=1
  run module_apply
  [ "$status" -eq 0 ]
  : >"$SHIM_LOG"

  run module_rollback
  [ "$status" -eq 0 ]
  run grep -c 'loginctl' "$SHIM_LOG"
  [ "$status" -ne 0 ] || [ "$output" -eq 0 ]
  run grep -qxF 'hermes-gateway' "$SHIM_SYSTEM_ENABLED_FILE"
  [ "$status" -ne 0 ]
}
