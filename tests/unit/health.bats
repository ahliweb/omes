#!/usr/bin/env bats
# tests/unit/health.bats - lib/omes/cmd/health.sh unit tests: the pure
# helpers (host-facts JSON, target scanning is covered end-to-end by
# tests/integration/health.bats and health-ollama.bats since cmd_health
# itself always exits). The layered model's own logic is unit-tested in
# Python: tests/py/health/test_hermes.py.

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
  # shellcheck source=../../lib/omes/cmd/health.sh
  source "${OMES_TEST_ROOT}/lib/omes/cmd/health.sh"
}

teardown() {
  omes_test_teardown
}

@test "_health_host_facts_json is a valid JSON object with the expected keys" {
  run _health_host_facts_json
  [ "$status" -eq 0 ]
  run python3 -c "
import json
d = json.loads('''$output''')
assert set(['systemd_present', 'disk_free_mb', 'mem_mb']) <= set(d.keys())
assert isinstance(d['systemd_present'], bool)
"
  [ "$status" -eq 0 ]
}

@test "_health_py_script points at lib/omes/py/health/<name>" {
  run _health_py_script hermes.py
  [ "$status" -eq 0 ]
  [[ "$output" == *"/lib/omes/py/health/hermes.py" ]]
}

@test "_health_usage documents all three targets" {
  # bin/omes's own global flag parser intercepts a literal -h/--help
  # anywhere in argv (turning the command into the general `omes help`)
  # before any extension command like `health` ever runs - see bin/omes's
  # main() while loop - so this text is unreachable via `omes health
  # --help` itself; it is unit-tested directly here instead.
  run _health_usage
  [ "$status" -eq 0 ]
  [[ "$output" == *"agent"* ]]
  [[ "$output" == *"gateway"* ]]
  [[ "$output" == *"ollama"* ]]
}
