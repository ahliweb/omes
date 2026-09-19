#!/usr/bin/env bats
# tests/unit/restore.bats - lib/omes/restore.sh unit tests.

setup() {
  load '../test_helper.bash'
  omes_test_setup
  # shellcheck source=../../lib/omes/core.sh
  source "${OMES_TEST_ROOT}/lib/omes/core.sh"
  # shellcheck source=../../lib/omes/log.sh
  source "${OMES_TEST_ROOT}/lib/omes/log.sh"
  # shellcheck source=../../lib/omes/state.sh
  source "${OMES_TEST_ROOT}/lib/omes/state.sh"
  # shellcheck source=../../lib/omes/backup.sh
  source "${OMES_TEST_ROOT}/lib/omes/backup.sh"
  # shellcheck source=../../lib/omes/restore.sh
  source "${OMES_TEST_ROOT}/lib/omes/restore.sh"
  state_init >/dev/null

  SRC="${OMES_TEST_TMPDIR}/live/config.yaml"
  mkdir -p "$(dirname "$SRC")"
}

teardown() {
  omes_test_teardown
}

# --- backup_target_dir -------------------------------------------------------

@test "backup_target_dir prints the latest session when no timestamp is given" {
  backup_begin "m" "r1" >/dev/null
  local d1="$OMES_CURRENT_BACKUP_DIR"
  backup_finish >/dev/null
  backup_begin "m" "r2" >/dev/null
  local d2="$OMES_CURRENT_BACKUP_DIR"
  backup_finish >/dev/null

  run backup_target_dir
  [ "$status" -eq 0 ]
  [ "$output" = "$d2" ]
  [ "$output" != "$d1" ]
}

@test "backup_target_dir prints the named session when a timestamp is given" {
  backup_begin "m" "r1" >/dev/null
  local d1="$OMES_CURRENT_BACKUP_DIR"
  backup_finish >/dev/null
  backup_begin "m" "r2" >/dev/null
  backup_finish >/dev/null

  run backup_target_dir "$(basename "$d1")"
  [ "$status" -eq 0 ]
  [ "$output" = "$d1" ]
}

@test "backup_target_dir fails when no backups exist" {
  run backup_target_dir
  [ "$status" -eq 1 ]
}

@test "backup_target_dir fails for an unknown timestamp" {
  backup_begin "m" "r1" >/dev/null
  backup_finish >/dev/null
  run backup_target_dir "20000101T000000Z"
  [ "$status" -eq 1 ]
}

# --- backup_manifest_validate ------------------------------------------------

@test "backup_manifest_validate accepts a well-formed MANIFEST" {
  printf 'hello\n' > "$SRC"
  backup_begin "m" "r" >/dev/null
  local dir="$OMES_CURRENT_BACKUP_DIR"
  backup_path "$SRC"
  backup_finish >/dev/null

  run backup_manifest_validate "$dir"
  [ "$status" -eq 0 ]
}

@test "backup_manifest_validate rejects a missing MANIFEST" {
  local dir="${OMES_TEST_TMPDIR}/no-manifest"
  mkdir -p "$dir"
  run backup_manifest_validate "$dir"
  [ "$status" -eq 1 ]
}

@test "backup_manifest_validate rejects a malformed MANIFEST line" {
  printf 'hello\n' > "$SRC"
  backup_begin "m" "r" >/dev/null
  local dir="$OMES_CURRENT_BACKUP_DIR"
  backup_path "$SRC"
  backup_finish >/dev/null

  printf 'not-a-valid-manifest-line\n' >> "${dir}/MANIFEST"

  run backup_manifest_validate "$dir"
  [ "$status" -eq 1 ]
}

# --- restore_backup: round-trip with checksum verification ------------------

@test "restore_backup round-trips a modified file back to its backed-up content" {
  printf 'original content\n' > "$SRC"
  local original_sum
  original_sum="$(sha256sum "$SRC" | awk '{print $1}')"

  backup_begin "demo" "pre-apply" >/dev/null
  backup_path "$SRC"
  backup_finish >/dev/null

  printf 'modified content\n' > "$SRC"
  [ "$(cat "$SRC")" = "modified content" ]

  run restore_backup ""
  [ "$status" -eq 0 ]

  [ "$(cat "$SRC")" = "original content" ]
  local restored_sum
  restored_sum="$(sha256sum "$SRC" | awk '{print $1}')"
  [ "$restored_sum" = "$original_sum" ]
}

@test "restore_backup takes a fresh pre-restore-backup of the file it is about to overwrite" {
  printf 'original\n' > "$SRC"
  backup_begin "demo" "pre-apply" >/dev/null
  backup_path "$SRC"
  backup_finish >/dev/null

  printf 'modified\n' > "$SRC"

  local before
  before="$(backup_list | wc -l)"
  restore_backup "" >/dev/null
  local after
  after="$(backup_list | wc -l)"
  [ "$after" -eq $((before + 1)) ]

  local latest
  latest="$(backup_list | tail -n1)"
  grep -q '^reason=pre-restore-backup$' "$(omes_state_dir)/backups/${latest}/META"
}

@test "restore_backup honors dry-run: file is not modified" {
  printf 'original\n' > "$SRC"
  backup_begin "demo" "pre-apply" >/dev/null
  backup_path "$SRC"
  backup_finish >/dev/null

  printf 'modified\n' > "$SRC"

  OMES_DRY_RUN=1 run restore_backup ""
  [ "$status" -eq 0 ]
  [ "$(cat "$SRC")" = "modified" ]
}

@test "restore_backup restores a file that no longer exists at its original path" {
  printf 'original\n' > "$SRC"
  backup_begin "demo" "pre-apply" >/dev/null
  backup_path "$SRC"
  backup_finish >/dev/null

  rm -f "$SRC"
  [ ! -e "$SRC" ]

  run restore_backup ""
  [ "$status" -eq 0 ]
  [ "$(cat "$SRC")" = "original" ]
}

@test "restore_backup fails when the requested timestamp does not exist" {
  run restore_backup "20000101T000000Z"
  [ "$status" -eq 1 ]
}

@test "restore_backup fails when no backups exist at all" {
  run restore_backup ""
  [ "$status" -eq 1 ]
}

@test "restore_backup refuses a corrupt MANIFEST and writes nothing" {
  printf 'original\n' > "$SRC"
  backup_begin "demo" "pre-apply" >/dev/null
  local dir="$OMES_CURRENT_BACKUP_DIR"
  backup_path "$SRC"
  backup_finish >/dev/null

  printf 'corrupt-line-not-a-manifest-entry\n' >> "${dir}/MANIFEST"
  printf 'modified\n' > "$SRC"

  run restore_backup ""
  [ "$status" -eq 1 ]
  [ "$(cat "$SRC")" = "modified" ]
}

@test "restore_backup detects a checksum mismatch on the backed-up copy and aborts" {
  printf 'original\n' > "$SRC"
  backup_begin "demo" "pre-apply" >/dev/null
  local dir="$OMES_CURRENT_BACKUP_DIR"
  backup_path "$SRC"
  backup_finish >/dev/null

  local rel="${SRC#/}"
  printf 'tampered\n' > "${dir}/${rel}"

  run restore_backup ""
  [ "$status" -eq 1 ]
  [[ "$output" == *"checksum mismatch"* ]]
}

@test "restore_backup works fully offline (OMES_ASSUME_OFFLINE=1)" {
  printf 'original\n' > "$SRC"
  backup_begin "demo" "pre-apply" >/dev/null
  backup_path "$SRC"
  backup_finish >/dev/null

  printf 'modified\n' > "$SRC"

  OMES_ASSUME_ONLINE=0 OMES_ASSUME_OFFLINE=1 run restore_backup ""
  [ "$status" -eq 0 ]
  [ "$(cat "$SRC")" = "original" ]
}

# --- #129 regressions: backup_finish via $(...), restore self-manifest loop -

@test "#129: ts=\"\$(backup_finish)\" leaves a stale OMES_CURRENT_BACKUP_DIR, but restore_backup still terminates and restores correctly" {
  printf 'original content\n' > "$SRC"

  backup_begin "demo" "pre-apply" >/dev/null
  backup_path "$SRC"
  local ts
  ts="$(backup_finish)"

  # Confirm the bug's precondition is actually reproduced here: capturing
  # backup_finish via command substitution must NOT have cleared
  # OMES_CURRENT_BACKUP_DIR in this (the caller's) shell, because the
  # `unset` ran in the subshell backup_finish executed inside. The same is
  # true of OMES_LAST_BACKUP_ID's export - no variable assignment inside a
  # `$(...)` subshell can ever reach the parent shell, which is exactly
  # why OMES_LAST_BACKUP_ID is only useful to callers that invoke
  # backup_finish as a plain statement (see the next assertions and the
  # separate plain-statement test below).
  [ -n "${OMES_CURRENT_BACKUP_DIR:-}" ]
  [ "$OMES_CURRENT_BACKUP_DIR" = "$ts" ]
  [ -z "${OMES_LAST_BACKUP_ID:-}" ]

  printf 'modified content\n' > "$SRC"

  # Run the restore in a child process under `timeout` so this test fails
  # fast instead of hanging the whole suite if the self-manifest loop ever
  # regresses. The stale OMES_CURRENT_BACKUP_DIR from above is inherited by
  # the child exactly as it would be by any real caller.
  run timeout 20 bash -c '
    set -Eeuo pipefail
    source "${OMES_TEST_ROOT}/lib/omes/core.sh"
    source "${OMES_TEST_ROOT}/lib/omes/log.sh"
    source "${OMES_TEST_ROOT}/lib/omes/state.sh"
    source "${OMES_TEST_ROOT}/lib/omes/backup.sh"
    source "${OMES_TEST_ROOT}/lib/omes/restore.sh"
    state_init >/dev/null
    restore_backup ""
  '
  [ "$status" -eq 0 ]
  [ "$(cat "$SRC")" = "original content" ]
}

@test "#129: restore_backup never appends into the MANIFEST it is restoring from" {
  printf 'original\n' > "$SRC"

  backup_begin "demo" "pre-apply" >/dev/null
  local dir="$OMES_CURRENT_BACKUP_DIR"
  backup_path "$SRC"
  backup_finish >/dev/null

  local before_lines
  before_lines="$(wc -l < "${dir}/MANIFEST")"

  printf 'modified\n' > "$SRC"

  # Simulate the stale-current-session precondition explicitly (as
  # ts="$(backup_finish)" would leave it) so the pre-restore-backup step
  # below is exercised under the exact condition #129 describes.
  export OMES_CURRENT_BACKUP_DIR="$dir"

  run timeout 20 bash -c '
    set -Eeuo pipefail
    source "${OMES_TEST_ROOT}/lib/omes/core.sh"
    source "${OMES_TEST_ROOT}/lib/omes/log.sh"
    source "${OMES_TEST_ROOT}/lib/omes/state.sh"
    source "${OMES_TEST_ROOT}/lib/omes/backup.sh"
    source "${OMES_TEST_ROOT}/lib/omes/restore.sh"
    state_init >/dev/null
    restore_backup ""
  '
  [ "$status" -eq 0 ]

  local after_lines
  after_lines="$(wc -l < "${dir}/MANIFEST")"
  [ "$after_lines" -eq "$before_lines" ]
}

@test "#129: restore_backup refuses to restore from a session that is still current (never finished)" {
  printf 'original\n' > "$SRC"

  backup_begin "demo" "pre-apply" >/dev/null
  local dir="$OMES_CURRENT_BACKUP_DIR"
  backup_path "$SRC"
  # No backup_finish: this session is genuinely still open, unlike the
  # stale-but-finished case covered above.

  local ts
  ts="$(basename "$dir")"

  run timeout 20 bash -c '
    set -Eeuo pipefail
    source "${OMES_TEST_ROOT}/lib/omes/core.sh"
    source "${OMES_TEST_ROOT}/lib/omes/log.sh"
    source "${OMES_TEST_ROOT}/lib/omes/state.sh"
    source "${OMES_TEST_ROOT}/lib/omes/backup.sh"
    source "${OMES_TEST_ROOT}/lib/omes/restore.sh"
    state_init >/dev/null
    restore_backup "'"$ts"'"
  '
  [ "$status" -eq 1 ]
  [[ "$output" == *"still-open"* ]]
  [ "$(cat "$SRC")" = "original" ]
}

@test "#129: restore_backup preserves the caller's own already-open outer backup session" {
  printf 'original\n' > "$SRC"

  backup_begin "demo" "pre-apply" >/dev/null
  local dir_a="$OMES_CURRENT_BACKUP_DIR"
  backup_path "$SRC"
  backup_finish >/dev/null
  local ts_a
  ts_a="$(basename "$dir_a")"

  printf 'modified\n' > "$SRC"

  # The caller has its own outer backup session open (genuinely in
  # progress, not finished) when it calls restore_backup - e.g. a module
  # that wraps a restore inside its own backup_begin/backup_finish pair
  # (feature branches such as lib/omes/cmd/graphify.sh call restore_backup
  # this way). restore_backup's internal pre-restore-backup calls
  # (backup_begin/backup_path/backup_finish) must not clobber or drop this
  # outer session.
  backup_begin "outer" "outer-session" >/dev/null
  local outer_dir="$OMES_CURRENT_BACKUP_DIR"

  run restore_backup "$ts_a"
  [ "$status" -eq 0 ]
  [ "$(cat "$SRC")" = "original" ]

  [ -n "${OMES_CURRENT_BACKUP_DIR:-}" ]
  [ "$OMES_CURRENT_BACKUP_DIR" = "$outer_dir" ]
  [ ! -e "${outer_dir}/.finished" ]
}

# --- module_rollback_managed_paths -------------------------------------------

@test "module_rollback_managed_paths restores a path that pre-existed OMES's first touch" {
  printf 'pre-existing content\n' > "$SRC"
  backup_begin "demo" "pre-apply" >/dev/null
  backup_path "$SRC"
  backup_finish >/dev/null

  printf 'omes-modified content\n' > "$SRC"
  state_set "module.demo.managed_paths" "$SRC"

  run module_rollback_managed_paths "demo"
  [ "$status" -eq 0 ]
  [ "$(cat "$SRC")" = "pre-existing content" ]
}

@test "module_rollback_managed_paths removes a path OMES created fresh (never backed up as pre-existing)" {
  printf 'omes-created content\n' > "$SRC"
  state_set "module.demo.managed_paths" "$SRC"

  run module_rollback_managed_paths "demo"
  [ "$status" -eq 0 ]
  [ ! -e "$SRC" ]
}

@test "module_rollback_managed_paths never touches a path outside managed_paths" {
  local other="${OMES_TEST_TMPDIR}/live/unrelated.txt"
  printf 'unrelated\n' > "$other"
  state_set "module.demo.managed_paths" "$SRC"

  run module_rollback_managed_paths "demo"
  [ "$status" -eq 0 ]
  [ -e "$other" ]
  [ "$(cat "$other")" = "unrelated" ]
}

@test "module_rollback_managed_paths honors dry-run: nothing is restored or removed" {
  printf 'omes-created content\n' > "$SRC"
  state_set "module.demo.managed_paths" "$SRC"

  OMES_DRY_RUN=1 run module_rollback_managed_paths "demo"
  [ "$status" -eq 0 ]
  [ -e "$SRC" ]
  [ "$(cat "$SRC")" = "omes-created content" ]
}

@test "module_rollback_managed_paths is a no-op when the module has no managed_paths recorded" {
  run module_rollback_managed_paths "no-such-module"
  [ "$status" -eq 0 ]
}

@test "module_rollback_managed_paths restores from the OLDEST backup that captured the path, not the newest" {
  printf 'version-0\n' > "$SRC"
  backup_begin "demo" "pre-apply" >/dev/null
  backup_path "$SRC"
  backup_finish >/dev/null

  printf 'version-1\n' > "$SRC"
  backup_begin "demo" "pre-apply" >/dev/null
  backup_path "$SRC"
  backup_finish >/dev/null

  printf 'version-2 (current)\n' > "$SRC"
  state_set "module.demo.managed_paths" "$SRC"

  run module_rollback_managed_paths "demo"
  [ "$status" -eq 0 ]
  [ "$(cat "$SRC")" = "version-0" ]
}
