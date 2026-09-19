#!/usr/bin/env bats
# tests/unit/runtime.bats - lib/omes/runtime.sh unit tests: the
# runtime-neutral agent-runtime contract layer (issue #85).

setup() {
  load '../test_helper.bash'
  omes_test_setup
  # shellcheck source=../../lib/omes/core.sh
  source "${OMES_TEST_ROOT}/lib/omes/core.sh"
  # shellcheck source=../../lib/omes/log.sh
  source "${OMES_TEST_ROOT}/lib/omes/log.sh"
  # shellcheck source=../../lib/omes/json.sh
  source "${OMES_TEST_ROOT}/lib/omes/json.sh"
  # shellcheck source=../../lib/omes/runtime.sh
  source "${OMES_TEST_ROOT}/lib/omes/runtime.sh"
}

teardown() {
  omes_test_teardown
}

@test "runtime_supported is true only for hermes" {
  run runtime_supported hermes
  [ "$status" -eq 0 ]
  run runtime_supported openclaw
  [ "$status" -ne 0 ]
  run runtime_supported ""
  [ "$status" -ne 0 ]
}

@test "runtime_require dies with exit 4 for an unsupported runtime" {
  run runtime_require nope
  [ "$status" -eq 4 ]
  [[ "$output" == *"unsupported runtime"* ]]
}

@test "runtime_require is a no-op for hermes" {
  run runtime_require hermes
  [ "$status" -eq 0 ]
}

@test "runtime_describe hermes dies with exit 4 for an unsupported name" {
  run runtime_describe nope
  [ "$status" -eq 4 ]
}

@test "runtime_describe hermes matches the contract fixture" {
  run runtime_describe hermes
  [ "$status" -eq 0 ]
  echo "$output" > "${OMES_TEST_TMPDIR}/actual.json"
  run python3 -c "
import json, sys
with open('${OMES_TEST_TMPDIR}/actual.json') as f:
    actual = json.load(f)
with open('${OMES_TEST_ROOT}/tests/fixtures/runtime/hermes.json') as f:
    expected = json.load(f)
sys.exit(0 if actual == expected else 1)
"
  [ "$status" -eq 0 ]
}

@test "runtime_home resolves HERMES_HOME override" {
  export OMES_HERMES_HOME="/tmp/custom-hermes-home"
  run runtime_home hermes
  [ "$status" -eq 0 ]
  [ "$output" = "/tmp/custom-hermes-home" ]
}

@test "runtime_home falls back to \$HOME/.hermes" {
  unset OMES_HERMES_HOME || true
  run runtime_home hermes
  [ "$status" -eq 0 ]
  [ "$output" = "${HOME}/.hermes" ]
}

@test "runtime_service_unit requires a valid scope" {
  run runtime_service_unit hermes user
  [ "$status" -eq 0 ]
  [ "$output" = "hermes-gateway" ]

  run runtime_service_unit hermes system
  [ "$status" -eq 0 ]
  [ "$output" = "hermes-gateway" ]

  run runtime_service_unit hermes bogus
  [ "$status" -eq 2 ]
}
