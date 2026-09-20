#!/usr/bin/env bats
# tests/integration/dr-gateway.bats - DR scenario (c), issue #17: a failed
# Hermes gateway. `hermes-gateway`'s module_verify fails when the --user
# unit is inactive; `omes doctor` reports it; `omes uninstall --module
# hermes-gateway` (rollback) disables the unit and disables lingering if
# OMES had enabled it; re-install recovers.

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
  export SHIM_SUDO_AVAILABLE=1
  export SHIM_LOGINCTL_ENABLE_EXIT=0
  export SHIM_LOGINCTL_DISABLE_EXIT=0

  # No SHIM_DEFAULT_TARGET / XDG_SESSION_TYPE set -> detect_session()
  # reports "server" (headless), so hermes-gateway's module_apply offers
  # (and, via --yes, auto-confirms) enabling lingering.
  run "$OMES_BIN" install --profile hermes --yes
  [ "$status" -eq 0 ]
  run grep -q '^module.hermes-gateway.linger_enabled=true$' "${OMES_STATE_DIR}/state"
  [ "$status" -eq 0 ]
}

teardown() {
  omes_test_teardown
}

@test "the gateway unit is initially enabled and active" {
  run grep -qxF "hermes-gateway" "$SHIM_USER_ENABLED_FILE"
  [ "$status" -eq 0 ]
  run grep -qxF "hermes-gateway" "$SHIM_USER_ACTIVE_FILE"
  [ "$status" -eq 0 ]
}

@test "simulated failure: unit becomes inactive -> module_verify fails via omes doctor (FAIL, not WARN)" {
  # Simulate the process having died without the unit being disabled (the
  # symptom docs/disaster-recovery.md's gateway runbook describes): drop
  # it from the "active" list, leave it enabled.
  sed -i '/^hermes-gateway$/d' "$SHIM_USER_ACTIVE_FILE"

  run "$OMES_BIN" doctor --json
  [ "$status" -eq 1 ]
  run python3 -c 'import json,sys; d=json.loads(sys.argv[1]); c=[x for x in d["checks"] if x["name"]=="module:hermes-gateway"]; sys.exit(0 if c and c[0]["level"]=="FAIL" else 1)' "$output"
  [ "$status" -eq 0 ]
}

@test "recovery: uninstall --module hermes-gateway disables the unit and disables lingering OMES had enabled" {
  sed -i '/^hermes-gateway$/d' "$SHIM_USER_ACTIVE_FILE"

  run "$OMES_BIN" uninstall --module hermes-gateway --yes
  [ "$status" -eq 0 ]

  run grep -qxF "hermes-gateway" "$SHIM_USER_ENABLED_FILE"
  [ "$status" -ne 0 ]

  run grep -q 'disable-linger' "$SHIM_LOG"
  [ "$status" -eq 0 ]

  run grep -q '^module.hermes-gateway.status=removed$' "${OMES_STATE_DIR}/state"
  [ "$status" -eq 0 ]
  run grep -c '^module.hermes-gateway.linger_enabled=' "${OMES_STATE_DIR}/state"
  [ "$status" -ne 0 ] || [ "$output" -eq 0 ]
}

@test "recovery: re-install recovers - the unit is enabled and active again, doctor is clean" {
  sed -i '/^hermes-gateway$/d' "$SHIM_USER_ACTIVE_FILE"
  run "$OMES_BIN" uninstall --module hermes-gateway --yes
  [ "$status" -eq 0 ]

  run "$OMES_BIN" install --module hermes --module hermes-gateway --yes
  [ "$status" -eq 0 ]

  run grep -qxF "hermes-gateway" "$SHIM_USER_ACTIVE_FILE"
  [ "$status" -eq 0 ]

  run "$OMES_BIN" doctor --json
  [ "$status" -eq 0 ]
}
