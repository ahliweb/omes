#!/usr/bin/env bats
# tests/integration/health-ollama.bats - `omes health ollama` wiring
# (issue #71). Deep service/model/capability logic is covered by
# tests/py/health/test_ollama.py's stdlib http.server fakes; these
# integration tests only assert the bash <-> python wiring: target
# dispatch, --json passthrough, and exit codes for an unreachable
# endpoint (a real network dependency is not needed for that case - a
# closed port fails fast and deterministically).
#
# JSON assertions pass $output to python via an env var and a
# single-quoted -c script (never string-interpolated into a
# double-quoted "..." argument) - some remediation strings the checker
# emits contain backticks, which a double-quoted bash string would
# misinterpret as command substitution before python ever sees them.

setup() {
  load '../test_helper.bash'
  omes_test_setup
  export OMES_BIN="${OMES_TEST_ROOT}/bin/omes"
  # A port nothing listens on in the test sandbox; connecting fails fast
  # with "connection refused" rather than timing out.
  export OLLAMA_HOST="127.0.0.1:1"
  export OMES_OLLAMA_MODEL="llama3"
  export OMES_HEALTH_TIMEOUT="3"
}

teardown() {
  omes_test_teardown
}

@test "omes health with an unknown target exits 2" {
  run "$OMES_BIN" health nosuchtarget
  [ "$status" -eq 2 ]
}

@test "omes health ollama against an unreachable endpoint exits 4" {
  run "$OMES_BIN" health ollama
  [ "$status" -eq 4 ]
  [[ "$output" == *"health ollama:"* ]]
}

@test "omes health ollama --json against an unreachable endpoint is valid JSON with ready=false" {
  omes_run_stdout_only "$OMES_BIN" health ollama --json
  [ "$status" -eq 4 ]
  OMES_TEST_JSON="$output" run python3 -c '
import json
import os
d = json.loads(os.environ["OMES_TEST_JSON"])
assert d["ready"] is False
assert d["service"]["status"] == "fail"
assert d["provider"] == "ollama-local"
'
  [ "$status" -eq 0 ]
}

@test "omes health ollama honors the global --json flag placed after the command word" {
  # docs/cli.md §4.12: global flags for an extension command go directly
  # after the command word (`omes health --json ollama`), not before it
  # (`omes --json health ollama` would try to parse "health" itself as
  # bin/omes's command name and fail).
  omes_run_stdout_only "$OMES_BIN" health --json ollama
  [ "$status" -eq 4 ]
  OMES_TEST_JSON="$output" run python3 -c 'import json, os; json.loads(os.environ["OMES_TEST_JSON"])'
  [ "$status" -eq 0 ]
}

@test "omes health ollama --profile is passed through to the checker" {
  run "$OMES_BIN" health ollama --json --profile embeddings
  [ "$status" -eq 4 ]
  [[ "$output" == *'"profile": "embeddings"'* ]]
}
