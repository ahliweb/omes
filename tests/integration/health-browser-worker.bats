#!/usr/bin/env bats
# tests/integration/health-browser-worker.bats - `omes health browser-worker`
# wiring (issue #298). Deep probe/state logic (every reason code, the state
# precedence, read-only guarantees) is covered by
# tests/py/health/test_browser_worker.py's injected-dependency suite; these
# tests drive the real `bin/omes` entry point through the repository's
# shims (tests/shims/{ss,chromium,hermes,ollama}) and a canned `ps` stub, so
# they double as the shim/container smoke test: no real browser, Ollama,
# Hermes or network is involved, and every emitted document is validated
# against the published contract with the repository's own validator.
#
# JSON assertions pass $output to python via an env var and a single-quoted
# -c script (never string-interpolated into a double-quoted "..." argument).

setup() {
  load '../test_helper.bash'
  omes_test_setup
  export OMES_BIN="${OMES_TEST_ROOT}/bin/omes"
  export OMES_HEALTH_TIMEOUT="3"

  OMES_OS_RELEASE_FILE="$(omes_fixture_path os-release)"
  cat >"$OMES_OS_RELEASE_FILE" <<'EOF'
PRETTY_NAME="Ubuntu 24.04 LTS"
NAME="Ubuntu"
VERSION_ID="24.04"
VERSION_CODENAME=noble
ID=ubuntu
ID_LIKE=debian
UBUNTU_CODENAME=noble
EOF
  export OMES_OS_RELEASE_FILE

  # Deterministic host: an empty HOME and Playwright cache, an empty socket
  # table, an empty process table, and every declaration a worker needs
  # except the per-job isolation evidence that is not integrated yet.
  export HOME="${OMES_TEST_TMPDIR}/home"
  mkdir -p "$HOME"
  export PLAYWRIGHT_BROWSERS_PATH="${OMES_TEST_TMPDIR}/no-playwright-cache"
  unset SHIM_SS_OUTPUT SHIM_SS_EXIT SHIM_PS_TABLE SHIM_HERMES_VERSION OLLAMA_HOST OMES_OLLAMA_MODEL OMES_EXPOSURE_ALLOW || true

  PS_STUB="${OMES_TEST_TMPDIR}/ps-stub"
  cat >"$PS_STUB" <<'EOF'
#!/usr/bin/env bash
# Canned `ps -ww -eo pid=,etimes=,args=` table for the bats tests.
printf '%s\n' "${SHIM_PS_TABLE:-}"
exit 0
EOF
  chmod +x "$PS_STUB"
  export OMES_PS_BIN="$PS_STUB"

  PROFILE_ROOT="${OMES_TEST_TMPDIR}/profiles"
  mkdir -m 700 "$PROFILE_ROOT"
  HEARTBEAT="${OMES_TEST_TMPDIR}/worker.heartbeat"
  : >"$HEARTBEAT"
  chmod 600 "$HEARTBEAT"

  export OMES_JOBS_TENANT_ID="tenant-fixture"
  export OMES_JOBS_SERVER_ID="server-fixture"
  export OMES_BROWSER_WORKER_ID="worker-fixture"
  export OMES_BROWSER_WORKER_CAPABILITY_PROFILE="deterministic"
  export OMES_BROWSER_WORKER_ADAPTER_VERSION="adapter-v1"
  export OMES_BROWSER_WORKER_BROWSER_BIN="${OMES_TEST_ROOT}/tests/shims/chromium"
  export OMES_BROWSER_WORKER_BROWSER_PIN="chromium-122.0.6261.94"
  export OMES_BROWSER_WORKER_PROFILE_ROOT="$PROFILE_ROOT"
  export OMES_BROWSER_WORKER_HEARTBEAT_FILE="$HEARTBEAT"
  export OMES_BROWSER_WORKER_DISK_MIN_MB="1"
  export OMES_BROWSER_WORKER_MEM_MIN_MB="1"
  export OMES_BROWSER_WORKER_CPU_MIN="1"
}

teardown() {
  omes_test_teardown
}

# assert_contract <json>: validates against the published schema using the
# repository's own stdlib validator (the same one scripts/check-contracts.py uses).
assert_contract() {
  OMES_TEST_JSON="$1" python3 -c '
import json
import os
import pathlib
import sys

root = os.environ["OMES_TEST_ROOT"]
sys.path.insert(0, root + "/lib/omes/py")
from jobs import schema

doc = json.loads(os.environ["OMES_TEST_JSON"])
sch = schema.load_json(pathlib.Path(root + "/contracts/browser-transfer/v1/browser-worker-readiness.schema.json"))
errors = schema.validate(doc, sch)
assert not errors, errors
'
}

# json_field <json> <python-expression-on-d>
json_field() {
  OMES_TEST_JSON="$1" OMES_TEST_EXPR="$2" python3 -c '
import json
import os

d = json.loads(os.environ["OMES_TEST_JSON"])
print(eval(os.environ["OMES_TEST_EXPR"]))
'
}

@test "browser-worker with every declaration but no isolation evidence is unknown (never ready), exit 7, valid contract" {
  omes_run_stdout_only "$OMES_BIN" health browser-worker --json
  [ "$status" -eq 7 ]
  assert_contract "$output"
  [ "$(json_field "$output" 'd["state"]')" = "unknown" ]
  [ "$(json_field "$output" 'd["reason_codes"]')" = "['ISOLATION_EVIDENCE_NOT_INTEGRATED']" ]
  [ "$(json_field "$output" 'd["session_isolation"]')" = "unknown" ]
  [ "$(json_field "$output" 'd["exposure"]')" = "loopback" ]
  [ "$(json_field "$output" 'd["browser_version"]')" = "chromium-122.0.6261.94" ]
  [ "$(json_field "$output" 'd["model_provider"] + "/" + d["model_ready"]')" = "none/not_required" ]
  [ "$(json_field "$output" 'd["tenant_id"] + "/" + d["server_id"] + "/" + d["worker_id"]')" = "tenant-fixture/server-fixture/worker-fixture" ]
}

@test "browser-worker stdout is exactly one JSON object and logs stay on stderr" {
  omes_run_stdout_only "$OMES_BIN" health browser-worker --json
  [ "$(printf '%s\n' "$output" | wc -l)" -eq 1 ]
  OMES_TEST_JSON="$output" run python3 -c 'import json, os; json.loads(os.environ["OMES_TEST_JSON"])'
  [ "$status" -eq 0 ]
}

@test "browser-worker without a tenant id is not configured: exit 4, nothing on stdout, id never invented" {
  unset OMES_JOBS_TENANT_ID
  omes_run_stdout_only "$OMES_BIN" health browser-worker --json
  [ "$status" -eq 4 ]
  [ -z "$output" ]
  run "$OMES_BIN" health browser-worker --json
  [ "$status" -eq 4 ]
  [[ "$output" == *"not configured"* ]]
  [[ "$output" == *"OMES_JOBS_TENANT_ID"* ]]
}

@test "browser-worker without a server id or worker id is not configured (exit 4)" {
  unset OMES_JOBS_SERVER_ID
  run "$OMES_BIN" health browser-worker --json
  [ "$status" -eq 4 ]
  [[ "$output" == *"OMES_JOBS_SERVER_ID"* ]]
  export OMES_JOBS_SERVER_ID="server-fixture"
  unset OMES_BROWSER_WORKER_ID
  run "$OMES_BIN" health browser-worker --json
  [ "$status" -eq 4 ]
  [[ "$output" == *"OMES_BROWSER_WORKER_ID"* ]]
}

@test "browser-worker --worker-id supplies the worker id" {
  unset OMES_BROWSER_WORKER_ID
  omes_run_stdout_only "$OMES_BIN" health browser-worker --json --worker-id worker-from-flag
  [ "$status" -eq 7 ]
  assert_contract "$output"
  [ "$(json_field "$output" 'd["worker_id"]')" = "worker-from-flag" ]
}

@test "browser-worker rejects an id that is not contract-safe (exit 2, nothing on stdout)" {
  omes_run_stdout_only "$OMES_BIN" health browser-worker --json --worker-id 'bad worker!'
  [ "$status" -eq 2 ]
  [ -z "$output" ]
}

@test "browser-worker usage errors exit 2" {
  run "$OMES_BIN" health browser-worker --bogus
  [ "$status" -eq 2 ]
  run "$OMES_BIN" health browser-worker --worker-id
  [ "$status" -eq 2 ]
  export OMES_BROWSER_WORKER_DISK_MIN_MB="not-a-number"
  run "$OMES_BIN" health browser-worker --json
  [ "$status" -eq 2 ]
}

@test "browser-worker --help documents the target" {
  run "$OMES_BIN" health browser-worker --help
  [ "$status" -eq 0 ]
  [[ "$output" == *"browser-worker"* ]]
  [[ "$output" == *"browser-worker-readiness.schema.json"* ]]
}

@test "a wildcard-bound CDP listener makes the worker blocked and public_blocked (exit 7)" {
  export SHIM_SS_OUTPUT='tcp   LISTEN 0      128    0.0.0.0:9222          0.0.0.0:*     users:(("chrome",pid=3,fd=4))'
  omes_run_stdout_only "$OMES_BIN" health browser-worker --json
  [ "$status" -eq 7 ]
  assert_contract "$output"
  [ "$(json_field "$output" 'd["state"]')" = "blocked" ]
  [ "$(json_field "$output" 'd["exposure"]')" = "public_blocked" ]
  [[ "$(json_field "$output" 'd["reason_codes"]')" == *"CDP_LISTENER_NONLOOPBACK"* ]]
}

@test "OMES_EXPOSURE_ALLOW cannot approve a LAN-bound CDP listener (private_boundary, still blocked)" {
  export OMES_EXPOSURE_ALLOW="192.168.1.5:9222"
  export SHIM_SS_OUTPUT='tcp   LISTEN 0      128    192.168.1.5:9222      0.0.0.0:*     users:(("chrome",pid=3,fd=4))'
  omes_run_stdout_only "$OMES_BIN" health browser-worker --json
  [ "$status" -eq 7 ]
  assert_contract "$output"
  [ "$(json_field "$output" 'd["state"]')" = "blocked" ]
  [ "$(json_field "$output" 'd["exposure"]')" = "private_boundary" ]
}

@test "MCP and Ollama listeners bound beyond loopback are blocked" {
  export SHIM_SS_OUTPUT='tcp   LISTEN 0      128    0.0.0.0:8931          0.0.0.0:*     users:(("node",pid=5,fd=6))
tcp   LISTEN 0      128    0.0.0.0:11434         0.0.0.0:*     users:(("ollama",pid=6,fd=3))'
  omes_run_stdout_only "$OMES_BIN" health browser-worker --json
  [ "$status" -eq 7 ]
  [ "$(json_field "$output" 'd["state"]')" = "blocked" ]
  [[ "$(json_field "$output" 'd["reason_codes"]')" == *"MCP_LISTENER_NONLOOPBACK"* ]]
  [[ "$(json_field "$output" 'd["reason_codes"]')" == *"OLLAMA_LISTENER_NONLOOPBACK"* ]]
}

@test "loopback-only listeners keep exposure at loopback" {
  export SHIM_SS_OUTPUT='tcp   LISTEN 0      128    127.0.0.1:9222        0.0.0.0:*     users:(("chrome",pid=3,fd=4))'
  omes_run_stdout_only "$OMES_BIN" health browser-worker --json
  [ "$(json_field "$output" 'd["exposure"]')" = "loopback" ]
}

@test "an unreadable socket table is unknown, never silently loopback" {
  export SHIM_SS_EXIT=1
  omes_run_stdout_only "$OMES_BIN" health browser-worker --json
  assert_contract "$output"
  [ "$(json_field "$output" 'd["exposure"]')" = "unknown" ]
  [[ "$(json_field "$output" 'd["reason_codes"]')" == *"EXPOSURE_AUDIT_UNAVAILABLE"* ]]
}

@test "a missing declared browser binary is blocked as not_installed" {
  export OMES_BROWSER_WORKER_BROWSER_BIN="${OMES_TEST_TMPDIR}/no-such-browser"
  omes_run_stdout_only "$OMES_BIN" health browser-worker --json
  [ "$status" -eq 7 ]
  assert_contract "$output"
  [ "$(json_field "$output" 'd["state"]')" = "blocked" ]
  [ "$(json_field "$output" 'd["browser_engine"]')" = "not_installed" ]
  [[ "$(json_field "$output" 'd["reason_codes"]')" == *"BROWSER_BINARY_MISSING"* ]]
}

@test "as root, a browser binary owned by another account is refused and not executed (BROWSER_BINARY_UNSAFE)" {
  # Real-uid test: it can only exercise the root path when the suite itself
  # runs as root. ci-local.sh runs bats as a non-root uid, where the unit
  # suite (tests/py/health/test_browser_worker.py) covers this with an
  # injected uid and stat.
  [ "$(id -u)" -eq 0 ] || skip "needs to run as root (euid 0)"
  local unsafe="${OMES_TEST_TMPDIR}/unsafe-chromium" marker="${OMES_TEST_TMPDIR}/was-executed"
  printf '#!/bin/sh\ntouch "%s"\necho "Chromium 122.0.6261.94 unofficial"\n' "$marker" >"$unsafe"
  chmod 0755 "$unsafe"
  chown 65534:65534 "$unsafe"
  export OMES_BROWSER_WORKER_BROWSER_BIN="$unsafe"
  omes_run_stdout_only "$OMES_BIN" health browser-worker --json
  [ "$status" -eq 7 ]
  assert_contract "$output"
  [ "$(json_field "$output" 'd["state"]')" = "blocked" ]
  [ "$(json_field "$output" 'd["browser_engine"]')" = "unknown" ]
  [[ "$(json_field "$output" 'd["reason_codes"]')" == *"BROWSER_BINARY_UNSAFE"* ]]
  [ ! -e "$marker" ]
}

@test "a browser version that differs from the pin is blocked" {
  export SHIM_CHROMIUM_VERSION="Chromium 123.0.0.1 unofficial"
  omes_run_stdout_only "$OMES_BIN" health browser-worker --json
  [ "$(json_field "$output" 'd["state"]')" = "blocked" ]
  [[ "$(json_field "$output" 'd["reason_codes"]')" == *"BROWSER_PIN_MISMATCH"* ]]
}

@test "the Hermes-bundled Playwright Chromium is found read-only in the Playwright cache" {
  unset OMES_BROWSER_WORKER_BROWSER_BIN
  mkdir -p "${PLAYWRIGHT_BROWSERS_PATH}/chromium-1194/chrome-linux"
  : >"${PLAYWRIGHT_BROWSERS_PATH}/chromium-1194/INSTALLATION_COMPLETE"
  printf '#!/bin/sh\necho SHOULD_NEVER_RUN >&2\nexit 9\n' >"${PLAYWRIGHT_BROWSERS_PATH}/chromium-1194/chrome-linux/chrome"
  chmod +x "${PLAYWRIGHT_BROWSERS_PATH}/chromium-1194/chrome-linux/chrome"
  export OMES_BROWSER_WORKER_BROWSER_PIN="playwright-chromium-1194"
  run "$OMES_BIN" health browser-worker --json
  [ "$status" -eq 7 ]
  [[ "$output" != *"SHOULD_NEVER_RUN"* ]]
  omes_run_stdout_only "$OMES_BIN" health browser-worker --json
  assert_contract "$output"
  [ "$(json_field "$output" 'd["browser_version"]')" = "playwright-chromium-1194" ]
  [ "$(json_field "$output" 'd["reason_codes"]')" = "['ISOLATION_EVIDENCE_NOT_INTEGRATED']" ]
}

@test "a long-running automation browser is an orphan session and blocks" {
  export SHIM_PS_TABLE=" 4242      7300 /opt/pw/chrome-linux/chrome --headless=new --remote-debugging-pipe --proxy-server=http://user:PROXY_CANARY@host:1"
  omes_run_stdout_only "$OMES_BIN" health browser-worker --json
  [ "$status" -eq 7 ]
  assert_contract "$output"
  [ "$(json_field "$output" 'd["state"]')" = "blocked" ]
  [[ "$(json_field "$output" 'd["reason_codes"]')" == *"ORPHAN_BROWSER_PROCESS"* ]]
  [[ "$output" != *"PROXY_CANARY"* ]]
}

@test "a leftover per-job profile directory blocks, and its contents are never read or echoed" {
  mkdir -p "${PROFILE_ROOT}/job-old/Default"
  printf 'COOKIE_CANARY_VALUE' >"${PROFILE_ROOT}/job-old/Default/Cookies"
  touch -d '5 hours ago' "${PROFILE_ROOT}/job-old"
  omes_run_stdout_only "$OMES_BIN" health browser-worker --json
  [ "$status" -eq 7 ]
  assert_contract "$output"
  [ "$(json_field "$output" 'd["state"]')" = "blocked" ]
  [[ "$(json_field "$output" 'd["reason_codes"]')" == *"STALE_PROFILE_DIRECTORY"* ]]
  [[ "$output" != *"COOKIE_CANARY_VALUE"* ]]
  [[ "$output" != *"Cookies"* ]]
  # Read-only: the leftover profile is still there (OMES never cleans it up).
  [ -f "${PROFILE_ROOT}/job-old/Default/Cookies" ]
}

@test "a stale heartbeat degrades, a missing one degrades, an undeclared one is unknown" {
  touch -d '1 hour ago' "$HEARTBEAT"
  omes_run_stdout_only "$OMES_BIN" health browser-worker --json
  [[ "$(json_field "$output" 'd["reason_codes"]')" == *"HEARTBEAT_STALE"* ]]
  rm -f "$HEARTBEAT"
  omes_run_stdout_only "$OMES_BIN" health browser-worker --json
  [[ "$(json_field "$output" 'd["reason_codes"]')" == *"HEARTBEAT_MISSING"* ]]
  unset OMES_BROWSER_WORKER_HEARTBEAT_FILE
  omes_run_stdout_only "$OMES_BIN" health browser-worker --json
  [[ "$(json_field "$output" 'd["reason_codes"]')" == *"HEARTBEAT_NOT_CONFIGURED"* ]]
}

@test "resource floors above the host's capacity degrade the worker" {
  export OMES_BROWSER_WORKER_CPU_MIN="100000"
  omes_run_stdout_only "$OMES_BIN" health browser-worker --json
  [[ "$(json_field "$output" 'd["reason_codes"]')" == *"RESOURCE_CPU_LOW"* ]]
}

@test "ai_assisted with an unreachable Ollama reports an unavailable model" {
  export OMES_BROWSER_WORKER_CAPABILITY_PROFILE="ai_assisted"
  export OMES_OLLAMA_MODEL="qwen3.5:9b"
  export OLLAMA_HOST="127.0.0.1:1"
  export SHIM_HERMES_VERSION="0.21.5"
  export SHIM_HERMES_CONFIG_GET_MODEL_PROVIDER="ollama"
  omes_run_stdout_only "$OMES_BIN" health browser-worker --json
  [ "$status" -eq 7 ]
  assert_contract "$output"
  [ "$(json_field "$output" 'd["model_provider"] + "/" + d["model_ready"] + "/" + d["model_id"]')" = "ollama/unavailable/qwen3.5:9b" ]
  [ "$(json_field "$output" 'd["hermes_version"]')" = "0.21.5" ]
  [[ "$(json_field "$output" 'd["reason_codes"]')" == *"MODEL_UNAVAILABLE"* ]]
}

@test "ai_assisted with Hermes routed to a cloud provider is blocked and never reports a healthy ollama" {
  export OMES_BROWSER_WORKER_CAPABILITY_PROFILE="ai_assisted"
  export OMES_OLLAMA_MODEL="qwen3.5:9b"
  export OLLAMA_HOST="127.0.0.1:1"
  export SHIM_HERMES_CONFIG_GET_MODEL_PROVIDER="anthropic"
  omes_run_stdout_only "$OMES_BIN" health browser-worker --json
  assert_contract "$output"
  [ "$(json_field "$output" 'd["state"]')" = "blocked" ]
  [ "$(json_field "$output" 'd["model_provider"]')" = "unknown" ]
  [[ "$(json_field "$output" 'd["reason_codes"]')" == *"EGRESS_MODEL_DESTINATION_CLOUD"* ]]
}

@test "ai_assisted without a model id is unknown" {
  export OMES_BROWSER_WORKER_CAPABILITY_PROFILE="ai_assisted"
  export SHIM_HERMES_CONFIG_GET_MODEL_PROVIDER="ollama"
  omes_run_stdout_only "$OMES_BIN" health browser-worker --json
  [[ "$(json_field "$output" 'd["reason_codes"]')" == *"MODEL_NOT_CONFIGURED"* ]]
  [ "$(json_field "$output" 'd["model_ready"]')" = "unknown" ]
}

@test "an unsupported platform reports state unsupported (exit 7) and probes nothing else" {
  cat >"$OMES_OS_RELEASE_FILE" <<'EOF'
PRETTY_NAME="Fedora Linux 40"
NAME="Fedora Linux"
VERSION_ID="40"
ID=fedora
EOF
  export SHIM_SS_OUTPUT='tcp   LISTEN 0      128    0.0.0.0:9222          0.0.0.0:*     users:(("chrome",pid=3,fd=4))'
  omes_run_stdout_only "$OMES_BIN" health browser-worker --json
  [ "$status" -eq 7 ]
  assert_contract "$output"
  [ "$(json_field "$output" 'd["state"]')" = "unsupported" ]
  [ "$(json_field "$output" 'd["reason_codes"]')" = "['PLATFORM_UNSUPPORTED']" ]
  [ "$(json_field "$output" 'd["exposure"]')" = "unknown" ]
}

@test "human output names the state and a remediation without --json" {
  run "$OMES_BIN" health browser-worker
  [ "$status" -eq 7 ]
  [[ "$output" == *"state=unknown"* ]]
  [[ "$output" == *"ISOLATION_EVIDENCE_NOT_INTEGRATED"* ]]
  [[ "$output" == *"Not implemented yet (tracked in #298"* ]]
}

@test "browser-worker is read-only: it writes nothing to the OMES state directory or the profile root" {
  before="$(find "$OMES_STATE_DIR" "$PROFILE_ROOT" -mindepth 1 | sort | tr '\n' ' ')"
  omes_run_stdout_only "$OMES_BIN" health browser-worker --json
  after="$(find "$OMES_STATE_DIR" "$PROFILE_ROOT" -mindepth 1 | sort | tr '\n' ' ')"
  [ "$before" = "$after" ]
}

@test "the shim commands the probes may run are fixed-argv and recorded (browser: --version only)" {
  omes_run_stdout_only "$OMES_BIN" health browser-worker --json
  grep -q '^chromium --version$' "$SHIM_LOG"
  ! grep -E '^chromium (--headless|--remote-debugging|http)' "$SHIM_LOG"
}

@test "omes health ollama output is unchanged by the browser-worker target" {
  export OLLAMA_HOST="127.0.0.1:1"
  export OMES_OLLAMA_MODEL="llama3"
  omes_run_stdout_only "$OMES_BIN" health ollama --json
  [ "$status" -eq 4 ]
  [ "$(json_field "$output" 'sorted(d)')" = "['capabilities', 'checks', 'model', 'profile', 'provider', 'ready', 'service']" ]
}
