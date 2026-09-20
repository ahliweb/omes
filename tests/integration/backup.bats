#!/usr/bin/env bats
# tests/integration/backup.bats - `omes backup` integration tests.
#
# module.<name>.managed_paths is read directly from the state file (never
# via module_load), so these tests seed it by hand rather than requiring a
# real module - matching how lib/omes/module.sh's runner would have
# populated it after a real module_apply.

setup() {
  load '../test_helper.bash'
  omes_test_setup
  OMES_BIN="${OMES_TEST_ROOT}/bin/omes"

  LIVE_DIR="${OMES_TEST_TMPDIR}/live"
  mkdir -p "$LIVE_DIR"
  FILE_A="${LIVE_DIR}/a.conf"
  FILE_B="${LIVE_DIR}/b.conf"
  printf 'file a\n' >"$FILE_A"
  printf 'file b\n' >"$FILE_B"
}

teardown() {
  omes_test_teardown
}

_seed_state() {
  "$OMES_TEST_ROOT/bin/omes" version >/dev/null 2>&1 || true
  mkdir -p "$OMES_STATE_DIR"
  {
    printf 'module.demo.status=applied\n'
    printf 'module.demo.managed_paths=%s:%s\n' "$FILE_A" "$FILE_B"
  } >>"${OMES_STATE_DIR}/state"
  chmod 600 "${OMES_STATE_DIR}/state" 2>/dev/null || true
}

@test "backup with no managed paths recorded is a no-op (exit 0)" {
  run "$OMES_BIN" backup
  [ "$status" -eq 0 ]
  [[ "$output" == *"nothing to back up"* ]]
}

@test "backup creates a session covering every recorded managed path" {
  _seed_state
  run "$OMES_BIN" backup --reason "test run"
  [ "$status" -eq 0 ]

  local backups_dir="${OMES_STATE_DIR}/backups"
  run bash -c "ls -1 '$backups_dir' | wc -l"
  [ "$status" -eq 0 ]
  [ "$output" -eq 1 ]

  local session
  session="$(ls -1 "$backups_dir")"
  grep -q '^reason=test run$' "${backups_dir}/${session}/META"
  run grep -c '  ' "${backups_dir}/${session}/MANIFEST"
  [ "$output" -eq 2 ]
}

@test "backup --module targets only that module's managed paths" {
  _seed_state
  mkdir -p "${OMES_STATE_DIR}"
  printf 'module.other.managed_paths=%s\n' "${LIVE_DIR}/other.conf" >>"${OMES_STATE_DIR}/state"
  printf 'other\n' >"${LIVE_DIR}/other.conf"

  run "$OMES_BIN" backup --module demo
  [ "$status" -eq 0 ]

  local backups_dir="${OMES_STATE_DIR}/backups"
  local session
  session="$(ls -1 "$backups_dir")"
  run grep -c '  ' "${backups_dir}/${session}/MANIFEST"
  [ "$output" -eq 2 ]
  run grep -c 'other.conf' "${backups_dir}/${session}/MANIFEST"
  [ "$status" -ne 0 ] || [ "$output" -eq 0 ]
}

@test "backup --dry-run creates no session" {
  _seed_state
  OMES_DRY_RUN=1 run "$OMES_BIN" backup
  [ "$status" -eq 0 ]
  [[ "$output" == *"dry-run"* ]] || [[ "$output" == *"would back up"* ]]
  local backups_dir="${OMES_STATE_DIR}/backups"
  [ ! -d "$backups_dir" ] || [ -z "$(ls -A "$backups_dir" 2>/dev/null)" ]
}

@test "backup --json emits a single valid JSON object" {
  _seed_state
  omes_run_stdout_only "$OMES_BIN" backup --json
  [ "$status" -eq 0 ]
  run python3 -c 'import json,sys; d=json.loads(sys.stdin.read()); assert d["command"]=="backup"; assert d["ok"] is True; assert d["files_backed_up"]==2' <<<"$output"
  [ "$status" -eq 0 ]
}
