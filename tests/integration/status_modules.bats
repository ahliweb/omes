#!/usr/bin/env bats
# tests/integration/status_modules.bats - `omes status` / `omes modules`.

setup() {
  load '../test_helper.bash'
  omes_test_setup
  OMES_BIN="${OMES_TEST_ROOT}/bin/omes"
}

teardown() {
  omes_test_teardown
}

@test "omes modules lists apt-base with its scope and description" {
  run "$OMES_BIN" modules
  [ "$status" -eq 0 ]
  [[ "$output" == *"apt-base"* ]]
  [[ "$output" == *"scope=root"* ]]
}

@test "omes modules --json is valid JSON" {
  run "$OMES_BIN" modules --json
  [ "$status" -eq 0 ]
  run python3 -c 'import json,sys; d=json.loads(sys.stdin.read()); assert any(m["name"]=="apt-base" for m in d["modules"])' <<< "$output"
  [ "$status" -eq 0 ]
}

@test "omes status works offline and reports no modules applied on a fresh state dir" {
  run "$OMES_BIN" status
  [ "$status" -eq 0 ]
  [[ "$output" == *"none applied yet"* ]]
}

@test "omes status --json is valid JSON" {
  run "$OMES_BIN" status --json
  [ "$status" -eq 0 ]
  run python3 -c 'import json,sys; d=json.loads(sys.stdin.read()); assert d["command"]=="status"; assert d["modules"]==[]' <<< "$output"
  [ "$status" -eq 0 ]
}
