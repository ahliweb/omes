#!/usr/bin/env bats
# tests/integration/audit-exposure.bats - `omes audit exposure` wiring
# (issue #80). Deep parsing/classification logic (loopback, LAN,
# wildcard, missing tools, approved exposure) is covered by
# tests/py/health/test_exposure.py's mocked subprocess boundaries; these
# integration tests assert the bash <-> python wiring through
# tests/shims/ss and tests/shims/ufw, plus one real "ss genuinely
# missing from PATH" case that only the bash layer can exercise.
#
# JSON assertions pass $output to python via an env var and a
# single-quoted -c script (never string-interpolated into a double-quoted
# "..." argument): the checker's own JSON can contain backticks (e.g. in
# remediation text quoting a command) and backslash-escaped quotes, both
# of which a double-quoted bash string would mangle before python ever
# sees them - the env var handoff sidesteps that entirely.

setup() {
  load '../test_helper.bash'
  omes_test_setup
  export OMES_BIN="${OMES_TEST_ROOT}/bin/omes"
  export OMES_HEALTH_TIMEOUT="3"
  unset SHIM_SS_OUTPUT SHIM_UFW_STATE_FILE OMES_EXPOSURE_ALLOW || true
}

teardown() {
  omes_test_teardown
}

@test "omes audit exposure with only loopback listeners is ok (exit 0)" {
  export SHIM_SS_OUTPUT='tcp   LISTEN 0      128    127.0.0.1:11434      0.0.0.0:*     users:(("ollama",pid=1,fd=3))'
  omes_run_stdout_only "$OMES_BIN" audit exposure --json
  [ "$status" -eq 0 ]
  OMES_TEST_JSON="$output" run python3 -c '
import json
import os
d = json.loads(os.environ["OMES_TEST_JSON"])
assert d["ok"] is True
assert d["findings"] == []
'
  [ "$status" -eq 0 ]
}

@test "omes audit exposure flags a wildcard-bound hermes gateway (exit 7)" {
  export SHIM_SS_OUTPUT='tcp   LISTEN 0      128    0.0.0.0:8642          0.0.0.0:*     users:(("hermes",pid=2,fd=10))'
  omes_run_stdout_only "$OMES_BIN" audit exposure --json
  [ "$status" -eq 7 ]
  OMES_TEST_JSON="$output" run python3 -c '
import json
import os
d = json.loads(os.environ["OMES_TEST_JSON"])
assert d["ok"] is False
assert len(d["findings"]) == 1
assert d["findings"][0]["owner"] == "hermes-gateway"
assert d["findings"][0]["bind_class"] == "wildcard"
'
  [ "$status" -eq 0 ]
}

@test "omes audit exposure flags a LAN-bound browser-control port (exit 7)" {
  export SHIM_SS_OUTPUT='tcp   LISTEN 0      128    192.168.1.5:9222       0.0.0.0:*     users:(("chrome",pid=3,fd=4))'
  omes_run_stdout_only "$OMES_BIN" audit exposure --json
  [ "$status" -eq 7 ]
  OMES_TEST_JSON="$output" run python3 -c '
import json
import os
d = json.loads(os.environ["OMES_TEST_JSON"])
assert d["findings"][0]["owner"] == "browser-control"
assert d["findings"][0]["bind_class"] == "lan"
'
  [ "$status" -eq 0 ]
}

@test "omes audit exposure approves a listener via OMES_EXPOSURE_ALLOW (exit 0)" {
  export SHIM_SS_OUTPUT='tcp   LISTEN 0      128    0.0.0.0:8642          0.0.0.0:*     users:(("hermes",pid=2,fd=10))'
  export OMES_EXPOSURE_ALLOW="0.0.0.0:8642"
  omes_run_stdout_only "$OMES_BIN" audit exposure --json
  [ "$status" -eq 0 ]
  OMES_TEST_JSON="$output" run python3 -c '
import json
import os
d = json.loads(os.environ["OMES_TEST_JSON"])
assert d["ok"] is True
assert d["findings"][0]["approved"] is True
'
  [ "$status" -eq 0 ]
}

@test "omes audit exposure exits 4 with a clear message when ss is missing" {
  # OMES_SS_BIN lets the checker's own tool-resolution be pointed at a
  # nonexistent path (see lib/omes/py/health/exposure.py's _ss_bin()) -
  # a deterministic way to simulate "ss is not installed" without
  # manipulating PATH, which would also have to keep bash/coreutils
  # reachable for the rest of `omes` to run at all.
  export OMES_SS_BIN="/nonexistent/ss-for-testing"
  omes_run_stdout_only "$OMES_BIN" audit exposure --json
  [ "$status" -eq 4 ]
  [[ "$output" == *'"ok": false'* ]]
}
