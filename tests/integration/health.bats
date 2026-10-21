#!/usr/bin/env bats
# tests/integration/health.bats - `omes health [agent|gateway]` wiring
# (issue #79). Deep layer logic is covered by
# tests/py/health/test_hermes.py's mocked subprocess/HTTP boundaries;
# these integration tests assert the bash <-> python wiring through
# tests/shims/hermes and tests/shims/systemctl.
#
# JSON assertions pass $output to python via an env var and a
# single-quoted -c script (never string-interpolated into a
# double-quoted "..." argument): the checker's JSON contains backticks
# (each layer's "proves" text quotes a command, e.g. `hermes doctor`) -
# embedded in a double-quoted bash string those would trigger command
# substitution and corrupt the JSON before python ever sees it. The env
# var handoff sidesteps that entirely.

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
  OMES_TEST_JSON="$output" run python3 -c '
import json
import os
d = json.loads(os.environ["OMES_TEST_JSON"])
assert "layers" in d
assert set(["host", "runtime", "gateway", "provider", "channel"]) <= set(d["layers"].keys())
assert "ready" in d and "connected" in d
'
  [ "$status" -eq 0 ]
}

@test "omes health agent reports runtime failure when hermes is not installed" {
  # tests/shims/hermes fails --version unless SHIM_HERMES_VERSION is set.
  omes_run_stdout_only "$OMES_BIN" health agent --json
  OMES_TEST_JSON="$output" run python3 -c '
import json
import os
d = json.loads(os.environ["OMES_TEST_JSON"])
assert d["layers"]["runtime"]["status"] == "fail"
assert d["ready"] is False
'
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
  OMES_TEST_JSON="$output" run python3 -c '
import json
import os
d = json.loads(os.environ["OMES_TEST_JSON"])
assert d["layers"]["runtime"]["status"] == "pass", d["layers"]["runtime"]
assert d["layers"]["gateway"]["status"] == "pass", d["layers"]["gateway"]
assert d["ready"] is True
'
  [ "$status" -eq 0 ]
}

@test "omes health gateway omits host/runtime layers" {
  omes_run_stdout_only "$OMES_BIN" health gateway --json
  OMES_TEST_JSON="$output" run python3 -c '
import json
import os
d = json.loads(os.environ["OMES_TEST_JSON"])
assert "host" not in d["layers"]
assert "runtime" not in d["layers"]
assert "gateway" in d["layers"]
'
  [ "$status" -eq 0 ]
}

@test "omes health agent human output includes the ready/connected summary line" {
  run "$OMES_BIN" health agent
  [[ "$output" == *"health: ready="*"connected="* ]]
}

# ---------------------------------------------------------------------------
# omes health agent-runtime (issue #270): configured Hermes delegation limits
# read through `hermes config get <key> --json`. Logic is covered by
# tests/py/health/test_agent_runtime_posture.py and test_hermes_config.py;
# these assert the bash <-> python wiring through tests/shims/hermes.
# ---------------------------------------------------------------------------

_agent_runtime_export_good_config() {
  export SHIM_HERMES_CONFIG_GET_SUPPORTED=1
  export SHIM_HERMES_CONFIG_GET_DELEGATION_MAX_CONCURRENT_CHILDREN=4
  export SHIM_HERMES_CONFIG_GET_DELEGATION_MAX_SPAWN_DEPTH=1
  export SHIM_HERMES_CONFIG_GET_DELEGATION_MAX_ITERATIONS=100
  export SHIM_HERMES_CONFIG_GET_DELEGATION_CHILD_TIMEOUT_SECONDS=600
  export SHIM_HERMES_CONFIG_GET_DELEGATION_SUBAGENT_AUTO_APPROVE=false
}

@test "omes health agent-runtime is unknown (exit 7) when Hermes cannot answer config get" {
  # Without SHIM_HERMES_CONFIG_GET_SUPPORTED the shim behaves like a Hermes
  # build that rejects `config get`: that must never read as ok.
  omes_run_stdout_only "$OMES_BIN" health agent-runtime --json
  [ "$status" -eq 7 ]
  OMES_TEST_JSON="$output" run python3 -c '
import json
import os
d = json.loads(os.environ["OMES_TEST_JSON"])
assert d["status"] == "unknown", d["status"]
assert d["scope"] == "configured"
'
  [ "$status" -eq 0 ]
}

@test "omes health agent-runtime reports ok with configured scope when every limit is read" {
  _agent_runtime_export_good_config
  omes_run_stdout_only "$OMES_BIN" health agent-runtime --json
  [ "$status" -eq 0 ]
  OMES_TEST_JSON="$output" run python3 -c '
import json
import os
d = json.loads(os.environ["OMES_TEST_JSON"])
assert d["status"] == "ok", d
assert d["scope"] == "configured"
assert "not observed" in d["scope_note"]
assert d["keys"]["delegation.max_concurrent_children"] == {"state": "value", "value": 4}
'
  [ "$status" -eq 0 ]
  # Only the supported read form was used: --json always, --raw never.
  run grep -c -- "config get delegation\." "$SHIM_LOG"
  [[ "$output" -ge 5 ]]
  run grep -c -- "--raw" "$SHIM_LOG"
  [ "$output" = "0" ]
  run grep -v -- "--json" "$SHIM_LOG"
  [ -z "$output" ]
}

@test "omes health agent-runtime warns (exit 0) when no per-child timeout is configured" {
  _agent_runtime_export_good_config
  export SHIM_HERMES_CONFIG_GET_DELEGATION_CHILD_TIMEOUT_SECONDS=0
  omes_run_stdout_only "$OMES_BIN" health agent-runtime --json
  [ "$status" -eq 0 ]
  OMES_TEST_JSON="$output" run python3 -c '
import json
import os
d = json.loads(os.environ["OMES_TEST_JSON"])
assert d["status"] == "warn", d["status"]
assert "delegation.no_child_timeout" in [f["id"] for f in d["findings"]]
'
  [ "$status" -eq 0 ]
}

@test "omes health agent-runtime rejects an unknown argument with a usage error" {
  run "$OMES_BIN" health agent-runtime --bogus
  [ "$status" -eq 2 ]
}

@test "omes health agent-runtime human output states the configured scope" {
  _agent_runtime_export_good_config
  run "$OMES_BIN" health agent-runtime
  [ "$status" -eq 0 ]
  [[ "$output" == *"health agent-runtime: status=ok scope=configured"* ]]
}

# ---------------------------------------------------------------------------
# omes health acp (issue #273): configured tool surface of Hermes' inbound ACP
# server. Logic is covered by tests/py/health/test_acp_posture.py; these assert
# the bash <-> python wiring through tests/shims/hermes. Live ACP session
# exposure is never observable and must always be reported as unknown.
# ---------------------------------------------------------------------------

@test "omes health acp is unknown (exit 7) when Hermes cannot answer config get" {
  omes_run_stdout_only "$OMES_BIN" health acp --json
  [ "$status" -eq 7 ]
  OMES_TEST_JSON="$output" run python3 -c '
import json
import os
d = json.loads(os.environ["OMES_TEST_JSON"])
assert d["status"] == "unknown", d["status"]
assert d["scope"] == "configured"
assert d["session_exposure"] == "unknown"
'
  [ "$status" -eq 0 ]
}

@test "omes health acp warns (exit 0) when platform_toolsets.acp is unset and never claims exposure" {
  export SHIM_HERMES_CONFIG_GET_SUPPORTED=1
  export SHIM_HERMES_ACP_VERSION=1.0
  omes_run_stdout_only "$OMES_BIN" health acp --json
  [ "$status" -eq 0 ]
  OMES_TEST_JSON="$output" run python3 -c '
import json
import os
d = json.loads(os.environ["OMES_TEST_JSON"])
assert d["status"] == "warn", d
assert d["scope"] == "configured"
assert d["keys"]["platform_toolsets.acp"] == {"state": "absent"}
ids = [f["id"] for f in d["findings"]]
assert "acp.default_toolset_includes_execution" in ids, ids
assert "acp.session_exposure_unobservable" in ids, ids
assert "acp.outbound_not_supported_upstream" in ids, ids
assert d["installability"]["state"] == "installed"
assert d["session_exposure"] == "unknown"
'
  [ "$status" -eq 0 ]
  # Only supported read forms were used: config get ... --json and `acp --version`; never --raw or --check.
  run grep -c -- "--raw" "$SHIM_LOG"
  [ "$output" = "0" ]
  run grep -c -- "--check" "$SHIM_LOG"
  [ "$output" = "0" ]
  run grep -c -- "^hermes acp --version$" "$SHIM_LOG"
  [ "$output" = "1" ]
  run grep -c -- "^hermes config get platform_toolsets.acp --json$" "$SHIM_LOG"
  [ "$output" = "1" ]
}

@test "omes health acp reports ok when platform_toolsets.acp is restricted, even if ACP is not installed" {
  export SHIM_HERMES_CONFIG_GET_SUPPORTED=1
  export SHIM_HERMES_CONFIG_GET_PLATFORM_TOOLSETS_ACP='["web", "file"]'
  export SHIM_HERMES_CONFIG_GET_AGENT_DISABLED_TOOLSETS='[]'
  omes_run_stdout_only "$OMES_BIN" health acp --json
  [ "$status" -eq 0 ]
  OMES_TEST_JSON="$output" run python3 -c '
import json
import os
d = json.loads(os.environ["OMES_TEST_JSON"])
assert d["status"] == "ok", d
assert d["keys"]["platform_toolsets.acp"] == {"state": "value", "value": ["web", "file"]}
assert "acp.toolset_restricted" in [f["id"] for f in d["findings"]]
assert d["installability"]["state"] == "not_installed_or_unknown"
assert d["session_exposure"] == "unknown"
'
  [ "$status" -eq 0 ]
}

@test "omes health acp rejects an unknown argument with a usage error" {
  run "$OMES_BIN" health acp --bogus
  [ "$status" -eq 2 ]
}

@test "omes health acp human output states the configured scope and unknown exposure" {
  export SHIM_HERMES_CONFIG_GET_SUPPORTED=1
  export SHIM_HERMES_CONFIG_GET_PLATFORM_TOOLSETS_ACP='["web"]'
  run "$OMES_BIN" health acp
  [ "$status" -eq 0 ]
  [[ "$output" == *"health acp: status=ok scope=configured"* ]]
  [[ "$output" == *"session exposure"*"unknown"* ]]
}
