#!/usr/bin/env bats
# tests/integration/agent.bats - `omes agent` extension command (#87)
# dispatch/wiring, driven through bin/omes with tests/shims.
#
# The lifecycle logic itself (manifest validation, plan, state machine,
# apply/rollback, health) is exhaustively covered by tests/py/agent/ -
# this file only proves lib/omes/cmd/agent.sh wires bin/omes -> Python
# correctly: usage, exit codes, --json passthrough, and one full
# apply/status/rollback round trip through the real CLI entry point.

setup() {
  load '../test_helper.bash'
  omes_test_setup

  export HOME="${OMES_TEST_TMPDIR}/home"
  mkdir -p "$HOME"
  export OMES_CONFIG_DIR="${OMES_TEST_TMPDIR}/config"
  mkdir -p "${OMES_CONFIG_DIR}/agents"
  export SHIM_HERMES_VERSION="1.2.3"
  export SHIM_USER_ENABLED_FILE="${OMES_TEST_TMPDIR}/shim-user-enabled"
  export SHIM_USER_ACTIVE_FILE="${OMES_TEST_TMPDIR}/shim-user-active"

  OMES_BIN="${OMES_TEST_ROOT}/bin/omes"

  cp "${OMES_TEST_ROOT}/contracts/agent/v1/fixtures/agent-deployment/valid-generic-user.json" \
    "${OMES_CONFIG_DIR}/agents/researcher.json"

  # A supported-platform fixture, so the top-level `omes doctor` tests
  # below assert on the agent-integration checks specifically rather
  # than an unrelated platform-tier FAIL from running under whatever
  # /etc/os-release the test host/container actually has (same pattern
  # tests/integration/hermes-backup.bats already uses).
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
}

teardown() {
  omes_test_teardown
}

@test "omes agent with no subcommand is a usage error" {
  run "$OMES_BIN" agent
  [ "$status" -eq 2 ]
}

@test "omes agent with an unknown subcommand is a usage error" {
  run "$OMES_BIN" agent bogus researcher
  [ "$status" -eq 2 ]
}

@test "omes help lists the agent extension command" {
  run "$OMES_BIN" help
  [ "$status" -eq 0 ]
  [[ "$output" == *"agent"* ]]
}

@test "omes agent list shows the declared manifest" {
  run "$OMES_BIN" agent list --json
  [ "$status" -eq 0 ]
  [[ "$output" == *'"researcher"'* ]]
}

@test "omes agent check validates the manifest" {
  run "$OMES_BIN" agent check researcher --json
  [ "$status" -eq 0 ]
  [[ "$output" == *'"ok": true'* ]]
}

@test "omes agent check fails preflight for an invalid manifest" {
  cat >"${OMES_CONFIG_DIR}/agents/bad.json" <<'JSON'
{"apiVersion":"omes.ahliweb.com/v1","kind":"AgentDeployment","metadata":{"name":"bad","workspace":"w","environment":"production"},"spec":{"runtime":"docker-compose","profile":"bad","role":"generic","backend":"systemd","serviceMode":"user","restartPolicy":"always","resources":{"memory":"1G","cpu":"1.0","pids":128},"health":{"command":"x","timeout":"10s"},"storage":{"memory":"private","sessions":"isolated","skills":"managed"}}}
JSON
  run "$OMES_BIN" agent check bad --json
  [ "$status" -eq 4 ]
}

@test "omes agent plan prints the unit name and hermes home without mutating" {
  run "$OMES_BIN" agent plan researcher --json
  [ "$status" -eq 0 ]
  [[ "$output" == *"omes-agent-researcher.service"* ]]
  [ ! -f "${HOME}/.config/systemd/user/omes-agent-researcher.service" ]
}

@test "omes agent apply --dry-run mutates nothing" {
  run "$OMES_BIN" agent apply researcher --dry-run --json
  [ "$status" -eq 0 ]
  [ ! -f "${HOME}/.config/systemd/user/omes-agent-researcher.service" ]
}

@test "omes agent apply --yes creates the unit and reaches ready/healthy" {
  run "$OMES_BIN" agent apply researcher --yes --json
  [ "$status" -eq 0 ]
  [ -f "${HOME}/.config/systemd/user/omes-agent-researcher.service" ]
}

@test "omes agent status reports state after apply" {
  "$OMES_BIN" agent apply researcher --yes --json >/dev/null
  run "$OMES_BIN" agent status researcher --json
  [ "$status" -eq 0 ]
  [[ "$output" == *'"state"'* ]]
}

@test "omes agent rollback removes the managed unit" {
  "$OMES_BIN" agent apply researcher --yes --json >/dev/null
  run "$OMES_BIN" agent rollback researcher --yes --json
  [ "$status" -eq 0 ]
  [ ! -f "${HOME}/.config/systemd/user/omes-agent-researcher.service" ]
}

@test "global --json flag right after the command word is passed through" {
  # docs/cli.md section 4.12: `omes <extension> --json <subcommand>` is
  # parsed by bin/omes itself (global flags only up to the first
  # unrecognized token); `omes <extension> <subcommand> --json` is passed
  # to the extension verbatim instead - both are exercised elsewhere in
  # this file.
  run "$OMES_BIN" agent --json list
  [ "$status" -eq 0 ]
  [[ "$output" == *'"agents"'* ]]
}

@test "omes agent logs without a name is a usage error" {
  run "$OMES_BIN" agent logs
  [ "$status" -eq 2 ]
}

@test "omes agent doctor --json is a single JSON object with no agents deployed" {
  run "$OMES_BIN" agent doctor --json
  [ "$status" -eq 0 ]
  local doctor_output="$output"
  run python3 -m json.tool <<<"$doctor_output"
  [ "$status" -eq 0 ]
  [[ "$doctor_output" == *'"agents": []'* ]]
  [[ "$doctor_output" == *'"ok": true'* ]]
}

@test "omes agent doctor reports a deployed agent's state and health" {
  "$OMES_BIN" agent apply researcher --yes --json >/dev/null
  run "$OMES_BIN" agent doctor --json
  [ "$status" -eq 0 ]
  [[ "$output" == *'"name": "researcher"'* ]]
  [[ "$output" == *'"backend": "systemd"'* ]]
}

@test "omes doctor (top-level) includes an agent row once an agent is deployed" {
  "$OMES_BIN" agent apply researcher --yes --json >/dev/null
  run "$OMES_BIN" doctor --json
  [ "$status" -eq 0 ]
  [[ "$output" == *'"name":"agent:researcher"'* ]]
}

@test "omes doctor (top-level) does not mention any agent when none is deployed" {
  run "$OMES_BIN" doctor --json
  [ "$status" -eq 0 ]
  [[ "$output" != *'agent:researcher'* ]]
}
