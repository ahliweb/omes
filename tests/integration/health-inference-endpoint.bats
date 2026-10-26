#!/usr/bin/env bats
# tests/integration/health-inference-endpoint.bats - `omes health
# inference-endpoint` wiring (issue #292). The deep SSRF/TLS/redirect/size/
# timeout logic is covered by tests/py/health/test_inference_endpoint.py's
# stub servers and fake resolvers; these tests only assert the bash <->
# python wiring: target dispatch (including a global flag placed before the
# target), --json passthrough, exit codes, redaction, and that the
# pre-existing `omes health ollama` behavior is unchanged. Everything stays
# on 127.0.0.1 / refused-before-connect destinations; no external network.
#
# JSON assertions pass $output to python via an env var and a
# single-quoted -c script (see health-ollama.bats for why).

setup() {
  load '../test_helper.bash'
  omes_test_setup
  export OMES_BIN="${OMES_TEST_ROOT}/bin/omes"
  export OMES_HEALTH_TIMEOUT="3"
  unset OMES_INFERENCE_ENDPOINT OMES_INFERENCE_MODEL OMES_INFERENCE_ENDPOINT_SCOPE OMES_INFERENCE_CA_FILE || true
}

teardown() {
  if [[ -n "${STUB_PID:-}" ]]; then
    kill "$STUB_PID" 2>/dev/null || true
  fi
  omes_test_teardown
}

@test "inference-endpoint with nothing configured is not_applicable, not ready, exit 7" {
  omes_run_stdout_only "$OMES_BIN" health inference-endpoint --json
  [ "$status" -eq 7 ]
  OMES_TEST_JSON="$output" run python3 -c '
import json
import os
d = json.loads(os.environ["OMES_TEST_JSON"])
assert d["status"] == "not_applicable"
assert d["ready"] is False
assert d["endpoint"] is None
assert d["layers"]["config"]["reason"] == "endpoint_not_configured"
assert d["layers"]["active_probe"]["status"] == "not_applicable"
'
  [ "$status" -eq 0 ]
}

@test "inference-endpoint honors the global --json flag placed before the target" {
  omes_run_stdout_only "$OMES_BIN" health --json inference-endpoint
  [ "$status" -eq 7 ]
  OMES_TEST_JSON="$output" run python3 -c 'import json, os; json.loads(os.environ["OMES_TEST_JSON"])'
  [ "$status" -eq 0 ]
}

@test "inference-endpoint refuses a public destination before any request (policy fail, exit 7)" {
  omes_run_stdout_only "$OMES_BIN" health inference-endpoint --json --endpoint http://8.8.8.8:8000 --model m
  [ "$status" -eq 7 ]
  OMES_TEST_JSON="$output" run python3 -c '
import json
import os
d = json.loads(os.environ["OMES_TEST_JSON"])
assert d["status"] == "fail"
assert d["layers"]["policy"]["reason"] == "public_address"
assert d["layers"]["network"]["status"] == "not_applicable"
'
  [ "$status" -eq 0 ]
}

@test "inference-endpoint never echoes credentials from the endpoint reference" {
  export OMES_INFERENCE_ENDPOINT='http://admin:hunter2@127.0.0.1:9/v1?key=sk-secret'
  run "$OMES_BIN" health inference-endpoint --json
  [ "$status" -eq 7 ]
  [[ "$output" != *"hunter2"* ]]
  [[ "$output" != *"sk-secret"* ]]
  [[ "$output" == *"[REDACTED]"* ]]
}

@test "inference-endpoint human output shows the layers and the not-a-generation-proof note" {
  run "$OMES_BIN" health inference-endpoint --endpoint file:///etc/passwd
  [ "$status" -eq 7 ]
  [[ "$output" == *"health inference-endpoint: status=fail"* ]]
  [[ "$output" == *"scheme_not_allowed"* ]]
  [[ "$output" == *"not that it can generate"* ]]
  [[ "$output" != *"/etc/passwd"* ]]
}

@test "inference-endpoint usage errors exit 2" {
  run "$OMES_BIN" health inference-endpoint --scope public
  [ "$status" -eq 2 ]
  run "$OMES_BIN" health inference-endpoint --endpoint
  [ "$status" -eq 2 ]
  run "$OMES_BIN" health inference-endpoint --bogus
  [ "$status" -eq 2 ]
}

@test "inference-endpoint reports ready against a local OpenAI-style stub that advertises the model" {
  mkdir -p "${OMES_TEST_TMPDIR}/stub/v1"
  printf '{"object":"list","data":[{"id":"demo-model"}]}' >"${OMES_TEST_TMPDIR}/stub/v1/models"
  local port
  port="$(python3 -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')"
  python3 -m http.server "$port" --bind 127.0.0.1 --directory "${OMES_TEST_TMPDIR}/stub" >/dev/null 2>&1 &
  STUB_PID=$!
  local i
  for i in 1 2 3 4 5 6 7 8 9 10; do
    python3 -c "import socket; socket.create_connection(('127.0.0.1', ${port}), 0.5).close()" 2>/dev/null && break
    sleep 0.3
  done
  omes_run_stdout_only "$OMES_BIN" health inference-endpoint --json --endpoint "http://127.0.0.1:${port}" --model demo-model
  [ "$status" -eq 0 ]
  OMES_TEST_JSON="$output" run python3 -c '
import json
import os
d = json.loads(os.environ["OMES_TEST_JSON"])
assert d["ready"] is True and d["status"] == "pass"
assert d["limits"]["prompt_sent"] is False and d["limits"]["credentials_sent"] is False
'
  [ "$status" -eq 0 ]
  # A model the endpoint does not advertise is a fail, never ready.
  omes_run_stdout_only "$OMES_BIN" health inference-endpoint --json --endpoint "http://127.0.0.1:${port}" --model absent-model
  [ "$status" -eq 7 ]
}

@test "regression: omes health ollama is unchanged (unreachable endpoint still exits 4 with the ollama JSON shape)" {
  export OLLAMA_HOST="127.0.0.1:1"
  export OMES_OLLAMA_MODEL="llama3"
  omes_run_stdout_only "$OMES_BIN" health ollama --json
  [ "$status" -eq 4 ]
  OMES_TEST_JSON="$output" run python3 -c '
import json
import os
d = json.loads(os.environ["OMES_TEST_JSON"])
assert d["provider"] == "ollama-local"
assert d["ready"] is False
assert d["service"]["status"] == "fail"
assert "target" not in d and "layers" not in d
'
  [ "$status" -eq 0 ]
}

@test "inference-endpoint --active-probe never sends a prompt to a refused destination (issue #354)" {
  omes_run_stdout_only "$OMES_BIN" health inference-endpoint --json --active-probe --endpoint http://169.254.169.254 --model m
  [ "$status" -eq 7 ]
  OMES_TEST_JSON="$output" run python3 -c '
import json
import os
d = json.loads(os.environ["OMES_TEST_JSON"])
assert d["ready"] is False
assert d["layers"]["policy"]["reason"] == "metadata_address"
assert d["layers"]["active_probe"]["reason"] == "skipped_prior_failure"
assert d["limits"]["prompt_sent"] is False
'
  [ "$status" -eq 0 ]
}

@test "inference-endpoint without --active-probe reports active_probe_not_run (issue #354)" {
  omes_run_stdout_only "$OMES_BIN" health inference-endpoint --json
  [ "$status" -eq 7 ]
  OMES_TEST_JSON="$output" run python3 -c '
import json
import os
d = json.loads(os.environ["OMES_TEST_JSON"])
assert d["layers"]["active_probe"]["reason"] == "active_probe_not_run"
assert d["limits"]["prompt_sent"] is False
'
  [ "$status" -eq 0 ]
}
