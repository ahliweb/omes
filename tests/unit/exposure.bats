#!/usr/bin/env bats
# tests/unit/exposure.bats - lib/omes/cmd/audit.sh unit tests: pure
# helpers only. Deep exposure.py parsing/classification logic is
# unit-tested in Python: tests/py/health/test_exposure.py.

setup() {
  load '../test_helper.bash'
  omes_test_setup
  # shellcheck source=../../lib/omes/core.sh
  source "${OMES_TEST_ROOT}/lib/omes/core.sh"
  # shellcheck source=../../lib/omes/log.sh
  source "${OMES_TEST_ROOT}/lib/omes/log.sh"
  # shellcheck source=../../lib/omes/json.sh
  source "${OMES_TEST_ROOT}/lib/omes/json.sh"
  # shellcheck source=../../lib/omes/cmd/audit.sh
  source "${OMES_TEST_ROOT}/lib/omes/cmd/audit.sh"
}

teardown() {
  omes_test_teardown
}

@test "_audit_py_script points at lib/omes/py/health/<name>" {
  run _audit_py_script exposure.py
  [ "$status" -eq 0 ]
  [[ "$output" == *"/lib/omes/py/health/exposure.py" ]]
}

@test "_audit_usage documents the exposure subcommand" {
  run _audit_usage
  [ "$status" -eq 0 ]
  [[ "$output" == *"exposure"* ]]
}

@test "cmd_audit with an unknown subcommand exits 2" {
  run cmd_audit nosuchsubcommand
  [ "$status" -eq 2 ]
}

@test "cmd_audit with no subcommand prints usage and exits 0" {
  run cmd_audit
  [ "$status" -eq 0 ]
  [[ "$output" == *"Usage: omes audit"* ]]
}
