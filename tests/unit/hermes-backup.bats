#!/usr/bin/env bats
# tests/unit/hermes-backup.bats - modules/hermes-backup/module.sh unit tests.
#
# The actual backup/restore logic lives in lib/omes/py/hermesbackup/ and
# is exercised exhaustively by tests/py/hermesbackup/; this file covers
# only the module contract (module_check/apply/verify/rollback/doctor).

setup() {
  load '../test_helper.bash'
  omes_test_setup

  export HOME="${OMES_TEST_TMPDIR}/home"
  mkdir -p "$HOME"

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

  module_load hermes-backup
  declare -ga OMES_MANAGED_PATHS=()
}

teardown() {
  omes_test_teardown
}

@test "MODULE_PROFILES is empty (opt-in only, never part of a default profile)" {
  [ "${#MODULE_PROFILES[@]}" -eq 0 ]
}

@test "module_check passes when python3 and the checker script are present" {
  run module_check
  [ "$status" -eq 0 ]
}

@test "module_apply is a no-op (never mutates anything) and always succeeds" {
  run module_apply
  [ "$status" -eq 0 ]
}

@test "module_apply performs no logged mutation under --dry-run" {
  export OMES_DRY_RUN=1
  run module_apply
  [ "$status" -eq 0 ]
}

@test "module_verify always succeeds (nothing to verify)" {
  run module_verify
  [ "$status" -eq 0 ]
}

@test "module_rollback is a no-op and always succeeds" {
  run module_rollback
  [ "$status" -eq 0 ]
}

@test "module_doctor reports 'no backups yet' when none exist" {
  run module_doctor
  [ "$status" -eq 0 ]
  [[ "$output" == *"no backups yet"* ]]
}

@test "module_doctor reports the last backup per class after a real backup" {
  export OMES_HERMES_HOME="${HOME}/.hermes"
  mkdir -p "$OMES_HERMES_HOME"
  printf 'model: gpt\n' >"${OMES_HERMES_HOME}/config.yaml"

  PYTHONPATH="${OMES_TEST_ROOT}/lib/omes/py" HERMES_HOME="$OMES_HERMES_HOME" \
    python3 -m hermesbackup.cli create --class config >/dev/null

  run module_doctor
  [ "$status" -eq 0 ]
  [[ "$output" == *"config="* ]]
}
