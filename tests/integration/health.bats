#!/usr/bin/env bats
# tests/integration/health.bats - `omes health [agent|gateway]` wiring
# (issue #79). Deep layer logic is covered by
# tests/py/health/test_hermes.py's mocked subprocess/HTTP boundaries;
# these integration tests assert the bash <-> python wiring through
# tests/shims/hermes and tests/shims/systemctl.

setup() {
  load '../test_helper.bash'
  omes_test_setup
  export OMES_BIN="${OMES_TEST_ROOT}/bin/omes"
  export HOME="${OMES_TEST_TMPDIR}/home"
  mkdir -p "$HOME"
  export OMES_HEALTH_TIMEOUT="3"
  unset SHIM_HERMES_VERSION SHIM_HERMES_DOCTOR_EXIT SHIM_HERMES_GATEWAY_STATUS_EXIT \
    SHIM_USER_ENABLED_FILE SHIM_USER_ACTIVE_FILE || true
}

teardown() {
  omes_test_teardown
}

@test "omes health (bare) defaults to the agent target and is valid JSON" {
  omes_run_stdout_only "$OMES_BIN" health --json
  run python3 -c "
import json
d = json.loads('''$output''')
assert 'layers' in d
assert set(['host', 'runtime', 'gateway', 'provider', 'channel']) <= set(d['layers'].keys())
assert 'ready' in d and 'connected' in d
"
  [ "$status" -eq 0 ]
}

@test "omes health agent reports runtime failure when hermes is not installed" {
  # tests/shims/hermes fails --version unless SHIM_HERMES_VERSION is set.
  omes_run_stdout_only "$OMES_BIN" health agent --json
  run python3 -c "
import json
d = json.loads('''$output''')
assert d['layers']['runtime']['status'] == 'fail'
assert d['ready'] is False
"
  [ "$status" -eq 0 ]
}

@test "omes health agent is ready when hermes and the gateway unit are healthy" {
  export SHIM_HERMES_VERSION="1.0.0"
  export SHIM_HERMES_DOCTOR_EXIT="0"
  export SHIM_HERMES_GATEWAY_STATUS_EXIT="0"
  export SHIM_USER_ENABLED_FILE="${OMES_TEST_TMPDIR}/enabled"
  export SHIM_USER_ACTIVE_FILE="${OMES_TEST_TMPDIR}/active"
  printf 'hermes-gateway\n' >"$SHIM_USER_ENABLED_FILE"
  printf 'hermes-gateway\n' >"$SHIM_USER_ACTIVE_FILE"

  omes_run_stdout_only "$OMES_BIN" health agent --json
  run python3 -c "
import json
d = json.loads('''$output''')
assert d['layers']['runtime']['status'] == 'pass', d['layers']['runtime']
assert d['layers']['gateway']['status'] == 'pass', d['layers']['gateway']
assert d['ready'] is True
"
  [ "$status" -eq 0 ]
}

@test "omes health gateway omits host/runtime layers" {
  omes_run_stdout_only "$OMES_BIN" health gateway --json
  run python3 -c "
import json
d = json.loads('''$output''')
assert 'host' not in d['layers']
assert 'runtime' not in d['layers']
assert 'gateway' in d['layers']
"
  [ "$status" -eq 0 ]
}

@test "omes health agent human output includes the ready/connected summary line" {
  run "$OMES_BIN" health agent
  [[ "$output" == *"health: ready="*"connected="* ]]
}
