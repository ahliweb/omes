#!/usr/bin/env bats
# tests/unit/state.bats - lib/omes/state.sh unit tests.

setup() {
  load '../test_helper.bash'
  omes_test_setup
  # shellcheck source=../../lib/omes/core.sh
  source "${OMES_TEST_ROOT}/lib/omes/core.sh"
  # shellcheck source=../../lib/omes/state.sh
  source "${OMES_TEST_ROOT}/lib/omes/state.sh"
}

teardown() {
  omes_test_teardown
}

@test "state_init creates the state dir at mode 0700 and the state file at 0600" {
  run state_init
  [ "$status" -eq 0 ]
  [ -d "$OMES_STATE_DIR" ]
  [ -f "${OMES_STATE_DIR}/state" ]

  local dir_mode file_mode
  dir_mode="$(stat -c '%a' "$OMES_STATE_DIR")"
  file_mode="$(stat -c '%a' "${OMES_STATE_DIR}/state")"
  [ "$dir_mode" = "700" ]
  [ "$file_mode" = "600" ]
}

@test "state_set then state_get round-trips a value" {
  state_init >/dev/null
  state_set "module.apt-base.status" "applied"
  run state_get "module.apt-base.status"
  [ "$status" -eq 0 ]
  [ "$output" = "applied" ]
}

@test "state_get returns 1 for a missing key" {
  state_init >/dev/null
  run state_get "module.does-not-exist.status"
  [ "$status" -eq 1 ]
}

@test "state_set overwrites an existing key rather than duplicating it" {
  state_init >/dev/null
  state_set "k" "v1"
  state_set "k" "v2"
  run state_get "k"
  [ "$output" = "v2" ]
  local count
  count="$(grep -c '^k=' "${OMES_STATE_DIR}/state")"
  [ "$count" -eq 1 ]
}

@test "state_set keeps the state file at mode 0600 after writing" {
  state_init >/dev/null
  state_set "a" "1"
  local mode
  mode="$(stat -c '%a' "${OMES_STATE_DIR}/state")"
  [ "$mode" = "600" ]
}

@test "state_set is atomic: readers never see a half-written file" {
  state_init >/dev/null
  state_set "k" "original"
  # Simulate a writer that has produced its temp file but not yet renamed
  # it into place; a concurrent reader must still see the last complete
  # value, never a truncated/partial one.
  local tmp
  tmp="$(mktemp "${OMES_STATE_DIR}/.state.XXXXXX")"
  printf 'k=partial-should-not-be-visible' >"$tmp"
  run state_get "k"
  [ "$output" = "original" ]
  rm -f "$tmp"
}

@test "state_unset removes a key" {
  state_init >/dev/null
  state_set "k" "v"
  state_unset "k"
  run state_get "k"
  [ "$status" -eq 1 ]
}

@test "state_unset on a missing key is a no-op" {
  state_init >/dev/null
  run state_unset "never-existed"
  [ "$status" -eq 0 ]
}

@test "state_list_modules lists distinct module names in first-seen order" {
  state_init >/dev/null
  state_set "module.apt-base.status" "applied"
  state_set "module.apt-base.applied_at" "2026-09-18T00:00:00Z"
  state_set "module.hermes.status" "pending"
  run state_list_modules
  [ "$status" -eq 0 ]
  [ "${lines[0]}" = "apt-base" ]
  [ "${lines[1]}" = "hermes" ]
  [ "${#lines[@]}" -eq 2 ]
}

@test "state_list_modules returns nothing when the state file does not exist" {
  run state_list_modules
  [ "$status" -eq 0 ]
  [ "${#lines[@]}" -eq 0 ]
}

@test "OMES_STATE_DIR override is honored" {
  local custom="${OMES_TEST_TMPDIR}/custom-state"
  OMES_STATE_DIR="$custom" state_init >/dev/null
  [ -d "$custom" ]
}
