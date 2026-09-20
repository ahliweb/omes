#!/usr/bin/env bats
# tests/integration/status-versions.bats - `omes health versions` wiring
# (issue #83). Deep per-component collection logic is covered by
# tests/py/provenance/test_versions.py's mocked subprocess boundaries;
# these integration tests assert the bash <-> python wiring through
# tests/shims/{hermes,node,ffmpeg,chromium,docker,ollama}: target
# dispatch, --json passthrough, exit codes, and that the human-mode
# summary never contains a secret canary from .env.

setup() {
  load '../test_helper.bash'
  omes_test_setup
  export OMES_BIN="${OMES_TEST_ROOT}/bin/omes"
}

teardown() {
  omes_test_teardown
}

@test "omes health versions --json prints a single valid JSON object" {
  omes_run_stdout_only "$OMES_BIN" health versions --json
  [ "$status" -eq 0 ]
  OMES_TEST_JSON="$output" run python3 -c '
import json
import os
d = json.loads(os.environ["OMES_TEST_JSON"])
assert d["ok"] is True
assert "components" in d
assert "warnings" in d
'
  [ "$status" -eq 0 ]
}

@test "omes health --json versions accepts the global flag before the target" {
  omes_run_stdout_only "$OMES_BIN" health --json versions
  [ "$status" -eq 0 ]
  [[ "$output" == \{* ]]
}

@test "omes health versions (human mode) prints a summary line and no secret canary" {
  local home="${OMES_TEST_TMPDIR}/hermes-home"
  mkdir -p "$home"
  printf 'TELEGRAM_BOT_TOKEN=canary-should-never-appear\n' >"${home}/.env"
  chmod 600 "${home}/.env"
  export OMES_HERMES_HOME="$home"

  run "$OMES_BIN" health versions
  [ "$status" -eq 0 ]
  [[ "$output" == *"health versions:"* ]]
  [[ "$output" != *"canary-should-never-appear"* ]]
}

@test "omes health versions reflects the hermes shim's version" {
  export SHIM_HERMES_VERSION="9.9.9"
  omes_run_stdout_only "$OMES_BIN" health versions --json
  [ "$status" -eq 0 ]
  OMES_TEST_JSON="$output" run python3 -c '
import json
import os
d = json.loads(os.environ["OMES_TEST_JSON"])
assert "9.9.9" in (d["components"]["hermes"]["value"] or "")
'
  [ "$status" -eq 0 ]
}
