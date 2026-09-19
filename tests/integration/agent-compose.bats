#!/usr/bin/env bats
# tests/integration/agent-compose.bats - `omes agent` rootless Docker
# Compose isolation backend (#96), driven through bin/omes with
# tests/shims/docker (never a real docker daemon or network).
#
# The exhaustive validation/rendering/state-machine coverage lives in
# tests/py/agent/test_compose*.py and tests/py/agent/test_cli_compose.py;
# this file proves the bin/omes -> lib/omes/cmd/agent.sh -> agent.cli
# wiring for the compose backend specifically: preflight refusal
# (rootful daemon / rootful socket path / privileged-equivalent config),
# dry-run, apply/idempotent re-apply, failed-update rollback, resource
# limits, and remove.

setup() {
  load '../test_helper.bash'
  omes_test_setup

  export HOME="${OMES_TEST_TMPDIR}/home"
  mkdir -p "$HOME"
  export OMES_CONFIG_DIR="${OMES_TEST_TMPDIR}/config"
  mkdir -p "${OMES_CONFIG_DIR}/agents"

  export OMES_FAKE_GROUPS="users"
  export SHIM_DOCKER_CONTEXT="rootless"
  export SHIM_DOCKER_ENDPOINT="unix:///run/user/1000/docker.sock"
  export SHIM_DOCKER_SECURITY_OPTIONS="[name=seccomp,profile=default name=rootless]"

  OMES_BIN="${OMES_TEST_ROOT}/bin/omes"

  cp "${OMES_TEST_ROOT}/contracts/agent/v1/fixtures/valid-compose-generic.json" \
    "${OMES_CONFIG_DIR}/agents/compose-worker.json"

  COMPOSE_FILE="${OMES_STATE_DIR}/agents/compose-worker/compose/compose.yaml"
}

teardown() {
  omes_test_teardown
}

@test "omes agent check reports backend=compose" {
  run "$OMES_BIN" agent check compose-worker --json
  [ "$status" -eq 0 ]
  [[ "$output" == *'"backend": "compose"'* ]]
}

@test "preflight refuses a rootful daemon before any mutation" {
  export SHIM_DOCKER_CONTEXT="default"
  export SHIM_DOCKER_ENDPOINT="unix:///var/run/docker.sock"
  export SHIM_DOCKER_SECURITY_OPTIONS="[name=seccomp,profile=default]"
  run "$OMES_BIN" agent apply compose-worker --yes --json
  [ "$status" -eq 4 ]
  [ ! -f "$COMPOSE_FILE" ]
}

@test "preflight refuses when the socket is the well-known rootful path" {
  export SHIM_DOCKER_ENDPOINT="unix:///var/run/docker.sock"
  run "$OMES_BIN" agent apply compose-worker --yes --json
  [ "$status" -eq 4 ]
  [ ! -f "$COMPOSE_FILE" ]
}

@test "preflight refuses docker-group-only access on a rootful daemon" {
  export OMES_FAKE_GROUPS="users,docker"
  export SHIM_DOCKER_SECURITY_OPTIONS="[name=seccomp,profile=default]"
  run "$OMES_BIN" agent apply compose-worker --yes --json
  [ "$status" -eq 4 ]
  [[ "$output" == *"docker"*"group"* ]]
}

@test "omes agent apply --dry-run shows the plan without mutation" {
  run "$OMES_BIN" agent apply compose-worker --dry-run --json
  [ "$status" -eq 0 ]
  [[ "$output" == *"sha256:aaaa"* ]]
  [ ! -f "$COMPOSE_FILE" ]
}

@test "omes agent apply --yes renders compose.yaml and reaches healthy" {
  run "$OMES_BIN" agent apply compose-worker --yes --json
  [ "$status" -eq 0 ]
  [[ "$output" == *'"state": "healthy"'* ]]
  [ -f "$COMPOSE_FILE" ]
  grep -q "cap_drop:" "$COMPOSE_FILE"
  grep -q "read_only: true" "$COMPOSE_FILE"
  ! grep -q "provider-primary" "$COMPOSE_FILE"
}

@test "omes agent apply is idempotent on re-apply" {
  "$OMES_BIN" agent apply compose-worker --yes --json >/dev/null
  run "$OMES_BIN" agent apply compose-worker --yes --json
  [ "$status" -eq 0 ]
  [[ "$output" == *'"state": "healthy"'* ]]
}

@test "resource limits are rendered into compose.yaml" {
  "$OMES_BIN" agent apply compose-worker --yes --json >/dev/null
  grep -q "mem_limit:" "$COMPOSE_FILE"
  grep -q "cpus:" "$COMPOSE_FILE"
  grep -q "pids_limit: 64" "$COMPOSE_FILE"
}

@test "a failed update can be rolled back to the previous rendered file" {
  "$OMES_BIN" agent apply compose-worker --yes --json >/dev/null
  cp "$COMPOSE_FILE" "${OMES_TEST_TMPDIR}/before.yaml"

  export SHIM_DOCKER_COMPOSE_UP_EXIT=1
  run "$OMES_BIN" agent apply compose-worker --yes --json
  [ "$status" -eq 6 ]
  unset SHIM_DOCKER_COMPOSE_UP_EXIT

  run "$OMES_BIN" agent rollback compose-worker --yes --json
  [ "$status" -eq 0 ]
  diff "${OMES_TEST_TMPDIR}/before.yaml" "$COMPOSE_FILE"
}

@test "omes agent status reports container state after apply" {
  "$OMES_BIN" agent apply compose-worker --yes --json >/dev/null
  run "$OMES_BIN" agent status compose-worker --json
  [ "$status" -eq 0 ]
  [[ "$output" == *'"backend": "compose"'* ]]
}

@test "omes agent health reflects an unhealthy container" {
  "$OMES_BIN" agent apply compose-worker --yes --json >/dev/null
  export SHIM_DOCKER_COMPOSE_PS_OUTPUT='{"Name":"x","State":"exited","Health":""}'
  run "$OMES_BIN" agent health compose-worker --json
  [ "$status" -eq 7 ]
}

@test "omes agent remove tears down containers and OMES-managed compose dir" {
  "$OMES_BIN" agent apply compose-worker --yes --json >/dev/null
  run "$OMES_BIN" agent remove compose-worker --yes --json
  [ "$status" -eq 0 ]
  [ ! -d "$(dirname "$COMPOSE_FILE")" ]
}

@test "omes agent remove is not implemented for the systemd backend" {
  cp "${OMES_TEST_ROOT}/contracts/agent/v1/fixtures/valid-generic-user.json" \
    "${OMES_CONFIG_DIR}/agents/researcher.json"
  run "$OMES_BIN" agent remove researcher --yes --json
  [ "$status" -eq 2 ]
}
