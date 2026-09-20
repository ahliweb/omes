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
  run python3 -c 'import json,sys; d=json.loads(sys.stdin.read()); assert any(m["name"]=="apt-base" for m in d["modules"])' <<<"$output"
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
  run python3 -c 'import json,sys; d=json.loads(sys.stdin.read()); assert d["command"]=="status"; assert d["modules"]==[]' <<<"$output"
  [ "$status" -eq 0 ]
}

@test "omes status --json embeds an evidence object with the same keys as 'omes health versions --json' (issue #83)" {
  run "$OMES_BIN" status --json
  [ "$status" -eq 0 ]
  OMES_TEST_JSON="$output" run python3 -c '
import json
import os
d = json.loads(os.environ["OMES_TEST_JSON"])
assert d["command"] == "status"
evidence = d["evidence"]
assert evidence["ok"] is True
assert "generated_at" in evidence
assert "components" in evidence
assert "warnings" in evidence
assert "omes" in evidence["components"]
'
  [ "$status" -eq 0 ]
}

@test "omes status (human mode) prints an evidence summary and never a secret canary from .env" {
  local home="${OMES_TEST_TMPDIR}/hermes-home"
  mkdir -p "$home"
  printf 'TELEGRAM_BOT_TOKEN=canary-should-never-appear\n' >"${home}/.env"
  chmod 600 "${home}/.env"
  export OMES_HERMES_HOME="$home"

  run "$OMES_BIN" status
  [ "$status" -eq 0 ]
  [[ "$output" == *"status evidence:"* ]]
  [[ "$output" != *"canary-should-never-appear"* ]]
}
