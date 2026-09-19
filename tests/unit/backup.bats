#!/usr/bin/env bats
# tests/unit/backup.bats - lib/omes/backup.sh unit tests.
#
# Note: backup_begin sets OMES_CURRENT_BACKUP_DIR as a side effect in the
# CALLING shell (so later backup_path/backup_finish calls can find it).
# Capturing its printed return value via `dir="$(backup_begin ...)"` would
# run it in a command-substitution subshell, and that side effect would be
# lost when the subshell exits. Tests therefore call backup_begin directly
# (output redirected, not captured) and read $OMES_CURRENT_BACKUP_DIR.

setup() {
  load '../test_helper.bash'
  omes_test_setup
  # shellcheck source=../../lib/omes/core.sh
  source "${OMES_TEST_ROOT}/lib/omes/core.sh"
  # shellcheck source=../../lib/omes/state.sh
  source "${OMES_TEST_ROOT}/lib/omes/state.sh"
  # shellcheck source=../../lib/omes/backup.sh
  source "${OMES_TEST_ROOT}/lib/omes/backup.sh"
  state_init >/dev/null

  SRC_DIR="${OMES_TEST_TMPDIR}/src"
  mkdir -p "$SRC_DIR"
}

teardown() {
  omes_test_teardown
}

@test "backup_begin creates a 0700 backup dir with MANIFEST and META" {
  backup_begin "apt-base" "pre-apply" >/dev/null
  local dir="$OMES_CURRENT_BACKUP_DIR"
  [ -d "$dir" ]
  [ -f "${dir}/MANIFEST" ]
  [ -f "${dir}/META" ]
  local mode
  mode="$(stat -c '%a' "$dir")"
  [ "$mode" = "700" ]
  grep -q '^module=apt-base$' "${dir}/META"
  grep -q '^reason=pre-apply$' "${dir}/META"
}

@test "backup_path records a correct sha256 line in MANIFEST" {
  local f="${SRC_DIR}/config.yaml"
  printf 'hello world\n' > "$f"
  local expected
  expected="$(sha256sum "$f" | awk '{print $1}')"

  backup_begin "hermes" "pre-apply" >/dev/null
  local dir="$OMES_CURRENT_BACKUP_DIR"
  backup_path "$f"

  local rel="${f#/}"
  run grep -F "$expected  $rel" "${dir}/MANIFEST"
  [ "$status" -eq 0 ]

  # The copied file must exist at the mirrored relative path and have the
  # same content (thus the same hash).
  [ -f "${dir}/${rel}" ]
  local copied_sum
  copied_sum="$(sha256sum "${dir}/${rel}" | awk '{print $1}')"
  [ "$copied_sum" = "$expected" ]
}

@test "backup_path on a directory hashes every regular file inside it" {
  mkdir -p "${SRC_DIR}/tree/sub"
  printf 'one\n' > "${SRC_DIR}/tree/a.txt"
  printf 'two\n' > "${SRC_DIR}/tree/sub/b.txt"

  backup_begin "m" "pre-apply" >/dev/null
  local dir="$OMES_CURRENT_BACKUP_DIR"
  backup_path "${SRC_DIR}/tree"

  local sum_a sum_b
  sum_a="$(sha256sum "${SRC_DIR}/tree/a.txt" | awk '{print $1}')"
  sum_b="$(sha256sum "${SRC_DIR}/tree/sub/b.txt" | awk '{print $1}')"

  [ -s "${dir}/MANIFEST" ]
  grep -qF "$sum_a" "${dir}/MANIFEST"
  grep -qF "$sum_b" "${dir}/MANIFEST"
}

@test "backup_path is a no-op when the source path does not exist yet" {
  backup_begin "m" "pre-apply" >/dev/null
  local dir="$OMES_CURRENT_BACKUP_DIR"
  run backup_path "${SRC_DIR}/does-not-exist"
  [ "$status" -eq 0 ]
  [ ! -s "${dir}/MANIFEST" ]
}

@test "backup_finish records finished_at in META" {
  backup_begin "m" "pre-apply" >/dev/null
  local dir="$OMES_CURRENT_BACKUP_DIR"
  backup_finish >/dev/null
  grep -q '^finished_at=' "${dir}/META"
}

@test "backup_finish unsets OMES_CURRENT_BACKUP_DIR" {
  backup_begin "m" "pre-apply" >/dev/null
  backup_finish >/dev/null
  [ -z "${OMES_CURRENT_BACKUP_DIR:-}" ]
}

@test "backup_finish writes a .finished marker into the session directory" {
  backup_begin "m" "pre-apply" >/dev/null
  local dir="$OMES_CURRENT_BACKUP_DIR"
  backup_finish >/dev/null
  [ -e "${dir}/.finished" ]
}

@test "backup_finish exports OMES_LAST_BACKUP_ID when called as a plain statement (#129)" {
  backup_begin "m" "pre-apply" >/dev/null
  local dir="$OMES_CURRENT_BACKUP_DIR"
  backup_finish >/dev/null
  [ "${OMES_LAST_BACKUP_ID:-}" = "$(basename "$dir")" ]
}

@test "backup_begin clears a stale OMES_CURRENT_BACKUP_DIR left by \"ts=\$(backup_finish)\" (#129)" {
  backup_begin "m" "r1" >/dev/null
  local ts
  ts="$(backup_finish)"
  # The subshell's unset never reached this shell.
  [ "${OMES_CURRENT_BACKUP_DIR:-}" = "$ts" ]

  backup_begin "m" "r2" >/dev/null
  # backup_begin must have replaced the stale reference with a brand-new
  # session, not left it pointed at the already-finished one.
  [ "$OMES_CURRENT_BACKUP_DIR" != "$ts" ]
  [ -e "${OMES_CURRENT_BACKUP_DIR}/META" ]
  grep -q '^reason=r2$' "${OMES_CURRENT_BACKUP_DIR}/META"
}

@test "backup_list lists sessions oldest first" {
  local d1 d2
  backup_begin "m" "r1" >/dev/null
  d1="$OMES_CURRENT_BACKUP_DIR"
  backup_finish >/dev/null
  backup_begin "m" "r2" >/dev/null
  d2="$OMES_CURRENT_BACKUP_DIR"
  backup_finish >/dev/null

  run backup_list
  [ "$status" -eq 0 ]
  [ "${lines[0]}" = "$(basename "$d1")" ]
  [ "${lines[1]}" = "$(basename "$d2")" ]
}

@test "backup_prune keeps only the newest N sessions" {
  local i
  for i in 1 2 3 4 5; do
    backup_begin "m" "r${i}" >/dev/null
    backup_finish >/dev/null
  done

  run backup_list
  [ "${#lines[@]}" -eq 5 ]

  backup_prune 2

  run backup_list
  [ "${#lines[@]}" -eq 2 ]
}
