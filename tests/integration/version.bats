#!/usr/bin/env bats
# tests/integration/version.bats - `omes version` integration tests.

setup() {
  load '../test_helper.bash'
  omes_test_setup
  OMES_BIN="${OMES_TEST_ROOT}/bin/omes"
}

teardown() {
  omes_test_teardown
}

@test "omes version prints the VERSION file contents" {
  run "$OMES_BIN" version
  [ "$status" -eq 0 ]
  [[ "$output" == *"$(cat "${OMES_TEST_ROOT}/VERSION")"* ]]
}

@test "omes version --json emits a single valid JSON object" {
  run "$OMES_BIN" version --json
  [ "$status" -eq 0 ]
  [ "${#lines[@]}" -eq 1 ]
  run python3 -c 'import json,sys; d=json.loads(sys.stdin.read()); assert d["command"]=="version"; assert d["ok"] is True; assert d["exit_code"]==0; print(d["version"])' <<<"${lines[0]}"
  [ "$status" -eq 0 ]
}

@test "OMES_JSON=1 env var has the same effect as --json" {
  OMES_JSON=1 run "$OMES_BIN" version
  [ "$status" -eq 0 ]
  [[ "$output" == "{"* ]]
}

@test "omes help exits 0 and mentions all documented commands" {
  run "$OMES_BIN" help
  [ "$status" -eq 0 ]
  [[ "$output" == *"check"* ]]
  [[ "$output" == *"install"* ]]
  [[ "$output" == *"status"* ]]
  [[ "$output" == *"modules"* ]]
}

@test "omes with no arguments prints usage and exits 2" {
  run "$OMES_BIN"
  [ "$status" -eq 2 ]
}

@test "omes with an unknown command exits 2" {
  run "$OMES_BIN" frobnicate
  [ "$status" -eq 2 ]
}
