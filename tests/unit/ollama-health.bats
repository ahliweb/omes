#!/usr/bin/env bats
# tests/unit/ollama-health.bats - modules/hermes/module.sh's
# module_doctor Ollama hook (issue #71). Deep Ollama health logic is
# covered by tests/py/health/test_ollama.py; this suite only asserts the
# module_doctor wiring: detection, and pass-through of the checker's
# result/exit code as an advisory (never-fatal) doctor signal.

setup() {
  load '../test_helper.bash'
  omes_test_setup

  export HOME="${OMES_TEST_TMPDIR}/home"
  mkdir -p "$HOME"
  unset XDG_DATA_HOME OMES_OLLAMA_ENABLED OMES_OLLAMA_MODEL OLLAMA_HOST || true

  # shellcheck source=../../lib/omes/core.sh
  source "${OMES_TEST_ROOT}/lib/omes/core.sh"
  # shellcheck source=../../lib/omes/log.sh
  source "${OMES_TEST_ROOT}/lib/omes/log.sh"
  # shellcheck source=../../lib/omes/state.sh
  source "${OMES_TEST_ROOT}/lib/omes/state.sh"
  # shellcheck source=../../lib/omes/backup.sh
  source "${OMES_TEST_ROOT}/lib/omes/backup.sh"
  # shellcheck source=../../lib/omes/module.sh
  source "${OMES_TEST_ROOT}/lib/omes/module.sh"

  module_load hermes
  declare -ga OMES_MANAGED_PATHS=()
}

teardown() {
  omes_test_teardown
}

@test "module_doctor is a no-op success when Ollama is not configured" {
  run module_doctor
  [ "$status" -eq 0 ]
  [[ "$output" == *"not configured"* ]]
}

@test "_hermes_ollama_configured is true when OMES_OLLAMA_ENABLED=1" {
  export OMES_OLLAMA_ENABLED=1
  run _hermes_ollama_configured
  [ "$status" -eq 0 ]
}

@test "_hermes_ollama_configured is true when the ollama binary is present and a model is set" {
  export OMES_OLLAMA_MODEL="llama3"
  run _hermes_ollama_configured
  [ "$status" -eq 0 ]
}

@test "_hermes_ollama_configured is false when only the binary is present (no model configured)" {
  run _hermes_ollama_configured
  [ "$status" -ne 0 ]
}

@test "module_doctor runs the health checker and reports a summary when configured" {
  export OMES_OLLAMA_MODEL="llama3"
  export OLLAMA_HOST="127.0.0.1:1"
  export OMES_HEALTH_TIMEOUT="3"
  run module_doctor
  [[ "$output" == *"ollama: ready="* ]]
  # An unreachable endpoint means the checker itself is unhealthy;
  # module_doctor surfaces that as a non-zero (WARN-level) return,
  # never a fatal error.
  [ "$status" -ne 0 ]
}
