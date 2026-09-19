#!/usr/bin/env bats
# tests/unit/versions.bats - lib/omes/versions.sh unit tests (issue #83).
#
# Deep per-component probing/classification logic lives in
# lib/omes/py/provenance/versions.py and is covered by
# tests/py/provenance/test_versions.py's mocked subprocess boundaries;
# these tests assert the bash <-> python wiring: host-facts JSON shape,
# HERMES_HOME profile isolation, and that .env is never touched.

setup() {
  load '../test_helper.bash'
  omes_test_setup
  # shellcheck source=../../lib/omes/core.sh
  source "${OMES_TEST_ROOT}/lib/omes/core.sh"
  # shellcheck source=../../lib/omes/log.sh
  source "${OMES_TEST_ROOT}/lib/omes/log.sh"
  # shellcheck source=../../lib/omes/json.sh
  source "${OMES_TEST_ROOT}/lib/omes/json.sh"
  # shellcheck source=../../lib/omes/detect.sh
  source "${OMES_TEST_ROOT}/lib/omes/detect.sh"
  # shellcheck source=../../lib/omes/state.sh
  source "${OMES_TEST_ROOT}/lib/omes/state.sh"
  # shellcheck source=../../lib/omes/runtime.sh
  source "${OMES_TEST_ROOT}/lib/omes/runtime.sh"
  # shellcheck source=../../lib/omes/versions.sh
  source "${OMES_TEST_ROOT}/lib/omes/versions.sh"
}

teardown() {
  omes_test_teardown
}

@test "versions_host_facts_json is valid JSON with the expected top-level keys" {
  run versions_host_facts_json
  [ "$status" -eq 0 ]
  OMES_TEST_JSON="$output" run python3 -c '
import json
import os
d = json.loads(os.environ["OMES_TEST_JSON"])
for key in ("omes", "os", "arch", "hermes_home", "gateway_mode"):
    assert key in d, key
assert "version" in d["omes"]
assert "id" in d["os"]
'
  [ "$status" -eq 0 ]
}

@test "versions_host_facts_json honors OMES_HERMES_HOME profile isolation" {
  export OMES_HERMES_HOME="${OMES_TEST_TMPDIR}/isolated-hermes"
  run versions_host_facts_json
  [ "$status" -eq 0 ]
  [[ "$output" == *"isolated-hermes"* ]]
}

@test "versions_collect_json produces a single JSON object with a components map" {
  run versions_collect_json
  [ "$status" -eq 0 ]
  OMES_TEST_JSON="$output" run python3 -c '
import json
import os
d = json.loads(os.environ["OMES_TEST_JSON"])
assert d["ok"] is True
components = d["components"]
for name in ("omes", "os", "arch", "hermes", "gateway_mode", "python3", "node", "browser", "ffmpeg", "docker", "ollama", "provider_config"):
    assert name in components, name
'
  [ "$status" -eq 0 ]
}

@test "versions_collect_json never reads or echoes .env content" {
  local home="${OMES_TEST_TMPDIR}/hermes-home"
  mkdir -p "$home"
  printf 'TELEGRAM_BOT_TOKEN=canary-secret-value-should-never-appear\n' > "${home}/.env"
  chmod 600 "${home}/.env"
  export OMES_HERMES_HOME="$home"

  run versions_collect_json
  [ "$status" -eq 0 ]
  [[ "$output" != *"canary-secret-value-should-never-appear"* ]]
}

@test "versions_collect_json output never contains a raw environ dump" {
  export SOME_UNRELATED_SECRET_LOOKING_VAR="should-not-leak-either"
  run versions_collect_json
  [ "$status" -eq 0 ]
  [[ "$output" != *"should-not-leak-either"* ]]
}
