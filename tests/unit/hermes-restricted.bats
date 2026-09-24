#!/usr/bin/env bats
# tests/unit/hermes-restricted.bats - modules/hermes-restricted/module.sh
# unit tests (issue #215). Root-scope behavior uses the
# OMES_TEST=1/OMES_FAKE_ROOT=1 test hook (lib/omes/core.sh: omes_is_root),
# never real privilege escalation. `hermes config get <key>` is driven by
# the tests/shims/hermes SHIM_HERMES_CONFIG_GET_<KEY> convention.

setup() {
  load '../test_helper.bash'
  omes_test_setup

  export HOME="${OMES_TEST_TMPDIR}/home"
  mkdir -p "$HOME"

  export SHIM_SYSTEM_ENABLED_FILE="${OMES_TEST_TMPDIR}/system-enabled"
  export SHIM_SYSTEM_ACTIVE_FILE="${OMES_TEST_TMPDIR}/system-active"
  export SHIM_HERMES_VERSION="1.2.3"

  # Production writes to the real /etc/systemd/system/...; tests redirect
  # to an isolated tmpdir, same pattern as
  # tests/unit/hermes-gateway-system.bats.
  export OMES_HERMES_GATEWAY_SYSTEM_DROPIN_DIR="${OMES_TEST_TMPDIR}/etc-systemd-system"

  # A well-known, non-root account that exists on virtually every Linux
  # base image (see tests/unit/hermes-gateway-system.bats's identical
  # rationale for using "nobody" rather than the invoking test uid).
  TARGET_USER="nobody"
  export TARGET_USER
  export OMES_HERMES_RESTRICTED_SYSTEM_USER="$TARGET_USER"

  # Simulate hermes-gateway-system already applied and enabled - this
  # module's module_check verifies that observable effect directly
  # rather than trusting cross-module state timing (see module.sh header
  # comment).
  printf 'hermes-gateway\n' >"$SHIM_SYSTEM_ENABLED_FILE"

  unset OMES_HERMES_RESTRICTED_PRIVATE_ENDPOINT_APPROVED OMES_HERMES_RESTRICTED_ALLOW_CIDRS OMES_HERMES_RESTRICTED_TIMEOUT || true

  # shellcheck source=../../lib/omes/core.sh
  source "${OMES_TEST_ROOT}/lib/omes/core.sh"
  # shellcheck source=../../lib/omes/log.sh
  source "${OMES_TEST_ROOT}/lib/omes/log.sh"
  # shellcheck source=../../lib/omes/detect.sh
  source "${OMES_TEST_ROOT}/lib/omes/detect.sh"
  # shellcheck source=../../lib/omes/state.sh
  source "${OMES_TEST_ROOT}/lib/omes/state.sh"
  # shellcheck source=../../lib/omes/backup.sh
  source "${OMES_TEST_ROOT}/lib/omes/backup.sh"
  # shellcheck source=../../lib/omes/module.sh
  source "${OMES_TEST_ROOT}/lib/omes/module.sh"

  module_load hermes-restricted
  declare -ga OMES_MANAGED_PATHS=()
}

teardown() {
  omes_test_teardown
}

# _set_local_endpoint: simulate a Hermes config pointing at a local
# Ollama-style endpoint (the "allow" case).
_set_local_endpoint() {
  export SHIM_HERMES_CONFIG_GET_MODEL="ollama/llama3"
  export SHIM_HERMES_CONFIG_GET_PROVIDERS_OLLAMA_BASE_URL="http://127.0.0.1:11434"
}

# _set_cloud_endpoint: simulate a Hermes config pointing at a public cloud
# provider (the "deny" case) - no base_url override needed, providers.
# <id>.base_url is simply left unconfigured, which fails closed to "unset".
_set_cloud_endpoint() {
  export SHIM_HERMES_CONFIG_GET_MODEL="anthropic/claude-opus-4"
}

# --- module_check ----------------------------------------------------------

@test "module_check requires root" {
  run module_check
  [ "$status" -eq 1 ]
}

@test "module_check requires a target user to be set" {
  export OMES_TEST=1 OMES_FAKE_ROOT=1
  unset OMES_HERMES_RESTRICTED_SYSTEM_USER
  run module_check
  [ "$status" -eq 1 ]
}

@test "module_check falls back to OMES_HERMES_GATEWAY_SYSTEM_USER" {
  export OMES_TEST=1 OMES_FAKE_ROOT=1
  unset OMES_HERMES_RESTRICTED_SYSTEM_USER
  export OMES_HERMES_GATEWAY_SYSTEM_USER="$TARGET_USER"
  _set_local_endpoint
  run module_check
  [ "$status" -eq 0 ]
}

@test "module_check refuses to target the root account" {
  export OMES_TEST=1 OMES_FAKE_ROOT=1
  export OMES_HERMES_RESTRICTED_SYSTEM_USER="root"
  run module_check
  [ "$status" -eq 1 ]
  [[ "$output" == *"refusing"* ]]
}

@test "module_check refuses a nonexistent target user" {
  export OMES_TEST=1 OMES_FAKE_ROOT=1
  export OMES_HERMES_RESTRICTED_SYSTEM_USER="omes-nonexistent-test-user"
  run module_check
  [ "$status" -eq 1 ]
}

@test "module_check fails when hermes-gateway-system's unit is not enabled" {
  export OMES_TEST=1 OMES_FAKE_ROOT=1
  : >"$SHIM_SYSTEM_ENABLED_FILE"
  _set_local_endpoint
  run module_check
  [ "$status" -eq 1 ]
  [[ "$output" == *"hermes-gateway-system"* ]]
}

@test "module_check FAILS BEFORE apply when only a cloud endpoint is configured (acceptance: restricted profile fails before execution)" {
  export OMES_TEST=1 OMES_FAKE_ROOT=1
  _set_cloud_endpoint
  run module_check
  [ "$status" -eq 1 ]
  [[ "$output" == *"FAILED"* ]]
  [[ "$output" == *"No silent cloud fallback"* ]]
}

@test "module_check fails when no endpoint is configured at all (unset fails closed, never assumed local)" {
  export OMES_TEST=1 OMES_FAKE_ROOT=1
  run module_check
  [ "$status" -eq 1 ]
}

@test "module_check fails for a public IP endpoint" {
  export OMES_TEST=1 OMES_FAKE_ROOT=1
  export SHIM_HERMES_CONFIG_GET_MODEL="local/custom"
  export SHIM_HERMES_CONFIG_GET_PROVIDERS_LOCAL_BASE_URL="http://8.8.8.8:8000"
  run module_check
  [ "$status" -eq 1 ]
}

@test "module_check fails for an unapproved private-network endpoint (approval_required is not a pass)" {
  export OMES_TEST=1 OMES_FAKE_ROOT=1
  export SHIM_HERMES_CONFIG_GET_MODEL="local/custom"
  export SHIM_HERMES_CONFIG_GET_PROVIDERS_LOCAL_BASE_URL="http://10.0.5.5:8000"
  run module_check
  [ "$status" -eq 1 ]
}

@test "module_check passes for a genuinely local loopback endpoint" {
  export OMES_TEST=1 OMES_FAKE_ROOT=1
  _set_local_endpoint
  run module_check
  [ "$status" -eq 0 ]
  [[ "$output" == *"decision=allow"* ]]
}

@test "module_check reports read-only GPU/CPU preflight facts without mutating anything" {
  export OMES_TEST=1 OMES_FAKE_ROOT=1
  _set_local_endpoint
  run module_check
  [ "$status" -eq 0 ]
  [[ "$output" == *"cpu_cores="* ]]
  [ ! -e "$OMES_HERMES_GATEWAY_SYSTEM_DROPIN_DIR" ]
}

# --- module_apply ------------------------------------------------------------

@test "module_apply refuses to write the network policy when the posture check fails (fail-closed, no partial apply)" {
  export OMES_TEST=1 OMES_FAKE_ROOT=1
  _set_cloud_endpoint
  run module_apply
  [ "$status" -eq 1 ]
  [ ! -f "${OMES_HERMES_GATEWAY_SYSTEM_DROPIN_DIR}/hermes-gateway.service.d/40-omes-restricted-network.conf" ]
}

@test "module_apply writes the deny-by-default network policy and restarts the unit when the posture allows" {
  export OMES_TEST=1 OMES_FAKE_ROOT=1 OMES_NONINTERACTIVE=1
  _set_local_endpoint
  run module_apply
  [ "$status" -eq 0 ]

  dropin="${OMES_HERMES_GATEWAY_SYSTEM_DROPIN_DIR}/hermes-gateway.service.d/40-omes-restricted-network.conf"
  [ -f "$dropin" ]
  run grep -qxF 'IPAddressDeny=any' "$dropin"
  [ "$status" -eq 0 ]
  run grep -q '^IPAddressAllow=.*localhost' "$dropin"
  [ "$status" -eq 0 ]

  run grep -qxF 'hermes-gateway' "$SHIM_SYSTEM_ACTIVE_FILE"
  [ "$status" -eq 0 ]
}

@test "module_apply performs no mutation under --dry-run" {
  export OMES_TEST=1 OMES_FAKE_ROOT=1 OMES_DRY_RUN=1
  _set_local_endpoint
  run module_apply
  [ "$status" -eq 0 ]
  [[ "$output" == *"[dry-run]"* ]]
  [ ! -f "${OMES_HERMES_GATEWAY_SYSTEM_DROPIN_DIR}/hermes-gateway.service.d/40-omes-restricted-network.conf" ]
}

@test "module_apply is idempotent: a second apply is a no-op that still succeeds" {
  export OMES_TEST=1 OMES_FAKE_ROOT=1 OMES_NONINTERACTIVE=1
  _set_local_endpoint
  run module_apply
  [ "$status" -eq 0 ]
  run module_apply
  [ "$status" -eq 0 ]

  dropin="${OMES_HERMES_GATEWAY_SYSTEM_DROPIN_DIR}/hermes-gateway.service.d/40-omes-restricted-network.conf"
  [ -f "$dropin" ]
}

# --- module_verify -----------------------------------------------------------

@test "module_verify fails when the network policy drop-in is missing" {
  export OMES_TEST=1 OMES_FAKE_ROOT=1
  _set_local_endpoint
  run module_verify
  [ "$status" -eq 1 ]
}

@test "module_verify passes after a successful apply" {
  export OMES_TEST=1 OMES_FAKE_ROOT=1 OMES_NONINTERACTIVE=1
  _set_local_endpoint
  run module_apply
  [ "$status" -eq 0 ]
  run module_verify
  [ "$status" -eq 0 ]
}

@test "module_verify detects drift to a cloud endpoint as a FAILURE, never a silent fallback" {
  export OMES_TEST=1 OMES_FAKE_ROOT=1 OMES_NONINTERACTIVE=1
  _set_local_endpoint
  run module_apply
  [ "$status" -eq 0 ]

  # Simulate the operator (or Hermes itself) reconfiguring the endpoint
  # toward a cloud provider after this module was applied.
  unset SHIM_HERMES_CONFIG_GET_PROVIDERS_OLLAMA_BASE_URL
  export SHIM_HERMES_CONFIG_GET_MODEL="anthropic/claude-opus-4"

  run module_verify
  [ "$status" -eq 1 ]
  [[ "$output" == *"DRIFT DETECTED"* ]]
  [[ "$output" == *"never a silent fallback"* ]]
}

@test "module_check fails and logs when a legacy fallback_model is configured, even with a local endpoint" {
  export OMES_TEST=1 OMES_FAKE_ROOT=1
  _set_local_endpoint
  export SHIM_HERMES_CONFIG_GET_FALLBACK_MODEL="openai/gpt-4"
  run module_check
  [ "$status" -eq 1 ]
  [[ "$output" == *"fallback_model"* ]]
  [[ "$output" == *"no silent cloud fallback"* ]]
}

# --- issue #216 integration seam (ai.local_only_posture.*, ai.privacy.expected_posture) ---
# See docs/ai-data-privacy-and-model-security.md section 10 and
# lib/omes/py/health/ai_privacy.py - #215 must write EXACTLY
# these state keys and vocabularies so `omes health ai-privacy` never
# contradicts this module's own decision.

@test "module_apply declares ai.privacy.expected_posture=restricted_local_only" {
  export OMES_TEST=1 OMES_FAKE_ROOT=1 OMES_NONINTERACTIVE=1
  _set_local_endpoint
  run module_apply
  [ "$status" -eq 0 ]
  run state_get "ai.privacy.expected_posture"
  [ "$status" -eq 0 ]
  [ "$output" = "restricted_local_only" ]
}

@test "module_apply writes ai.local_only_posture.available=true and status=pass on a local endpoint" {
  export OMES_TEST=1 OMES_FAKE_ROOT=1 OMES_NONINTERACTIVE=1
  _set_local_endpoint
  run module_apply
  [ "$status" -eq 0 ]
  run state_get "ai.local_only_posture.available"
  [ "$output" = "true" ]
  run state_get "ai.local_only_posture.status"
  [ "$output" = "pass" ]
}

@test "module_verify refreshes ai.local_only_posture.status to fail on drift (never leaves a stale pass)" {
  export OMES_TEST=1 OMES_FAKE_ROOT=1 OMES_NONINTERACTIVE=1
  _set_local_endpoint
  run module_apply
  [ "$status" -eq 0 ]
  run state_get "ai.local_only_posture.status"
  [ "$output" = "pass" ]

  unset SHIM_HERMES_CONFIG_GET_PROVIDERS_OLLAMA_BASE_URL
  export SHIM_HERMES_CONFIG_GET_MODEL="anthropic/claude-opus-4"
  run module_verify
  [ "$status" -eq 1 ]

  run state_get "ai.local_only_posture.status"
  [ "$output" = "fail" ]
  run state_get "ai.local_only_posture.available"
  [ "$output" = "true" ]
}

@test "a configured legacy fallback forces ai.local_only_posture.status=fail even when the primary endpoint is local" {
  export OMES_TEST=1 OMES_FAKE_ROOT=1 OMES_NONINTERACTIVE=1
  _set_local_endpoint
  run module_apply
  [ "$status" -eq 0 ]

  export SHIM_HERMES_CONFIG_GET_FALLBACK_MODEL="openai/gpt-4"
  run module_verify
  [ "$status" -eq 1 ]

  run state_get "ai.local_only_posture.status"
  [ "$output" = "fail" ]
}

@test "an unapproved private-network endpoint maps to ai.local_only_posture.status=fail (deny), never pass" {
  export OMES_TEST=1 OMES_FAKE_ROOT=1 OMES_NONINTERACTIVE=1
  export SHIM_HERMES_CONFIG_GET_MODEL="local/custom"
  export SHIM_HERMES_CONFIG_GET_PROVIDERS_LOCAL_BASE_URL="http://10.0.5.5:8000"
  run module_apply
  [ "$status" -eq 1 ]
  run state_get "ai.local_only_posture.status"
  [ "$output" = "fail" ]
  run state_get "ai.local_only_posture.available"
  [ "$output" = "true" ]
}

@test "module_rollback clears the ai.local_only_posture.* seam and the expected_posture it declared" {
  export OMES_TEST=1 OMES_FAKE_ROOT=1 OMES_NONINTERACTIVE=1
  _set_local_endpoint
  run module_apply
  [ "$status" -eq 0 ]

  run module_rollback
  [ "$status" -eq 0 ]

  run state_get "ai.local_only_posture.available"
  [ "$status" -eq 1 ]
  run state_get "ai.privacy.expected_posture"
  [ "$status" -eq 1 ]
}

@test "module_rollback never clears an operator-declared expected_posture it did not itself set" {
  export OMES_TEST=1 OMES_FAKE_ROOT=1 OMES_NONINTERACTIVE=1
  _set_local_endpoint
  run module_apply
  [ "$status" -eq 0 ]

  # Operator independently overrides the declared posture after apply -
  # rollback must not clobber a value this module no longer owns.
  state_set "ai.privacy.expected_posture" "unrestricted"

  run module_rollback
  [ "$status" -eq 0 ]

  run state_get "ai.privacy.expected_posture"
  [ "$status" -eq 0 ]
  [ "$output" = "unrestricted" ]
}

# --- module_rollback ----------------------------------------------------------

@test "module_rollback removes only the OMES restricted-network drop-in, never the gateway-system's own drop-ins" {
  export OMES_TEST=1 OMES_FAKE_ROOT=1 OMES_NONINTERACTIVE=1
  _set_local_endpoint
  run module_apply
  [ "$status" -eq 0 ]

  # A drop-in that belongs to hermes-gateway-system (not this module) -
  # rollback must never touch it.
  other_dropin="${OMES_HERMES_GATEWAY_SYSTEM_DROPIN_DIR}/hermes-gateway.service.d/omes-path.conf"
  mkdir -p "$(dirname "$other_dropin")"
  printf '[Service]\nEnvironment=PATH=/usr/bin\n' >"$other_dropin"

  run module_rollback
  [ "$status" -eq 0 ]

  [ ! -f "${OMES_HERMES_GATEWAY_SYSTEM_DROPIN_DIR}/hermes-gateway.service.d/40-omes-restricted-network.conf" ]
  [ -f "$other_dropin" ]
}

@test "module_rollback performs no mutation under --dry-run" {
  export OMES_TEST=1 OMES_FAKE_ROOT=1 OMES_NONINTERACTIVE=1
  _set_local_endpoint
  run module_apply
  [ "$status" -eq 0 ]

  export OMES_DRY_RUN=1
  dropin="${OMES_HERMES_GATEWAY_SYSTEM_DROPIN_DIR}/hermes-gateway.service.d/40-omes-restricted-network.conf"
  run module_rollback
  [ "$status" -eq 0 ]
  [[ "$output" == *"[dry-run]"* ]]
  [ -f "$dropin" ]
}

# --- module_doctor -------------------------------------------------------------

@test "module_doctor summarizes the last recorded posture decision" {
  export OMES_TEST=1 OMES_FAKE_ROOT=1 OMES_NONINTERACTIVE=1
  _set_local_endpoint
  run module_apply
  [ "$status" -eq 0 ]

  run module_doctor
  [ "$status" -eq 0 ]
  [[ "$output" == *"decision=allow"* ]]
}
