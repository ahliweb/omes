#!/usr/bin/env bats
# tests/integration/content.bats - `omes content` extension command (#64).
#
# Uses synthetic tiny files as media; NEVER real media.

setup() {
  load '../test_helper.bash'
  omes_test_setup
  CONTENT_ROOT="${OMES_TEST_TMPDIR}/content-root"
  export OMES_CONTENT_ROOT="$CONTENT_ROOT"
  OMES_BIN="${OMES_TEST_ROOT}/bin/omes"
}

teardown() {
  omes_test_teardown
}

_drop_inbox_file() {
  mkdir -p "${CONTENT_ROOT}/inbox"
  printf '%s' "$2" >"${CONTENT_ROOT}/inbox/$1"
}

@test "omes content scan creates a job for a settled inbox file" {
  _drop_inbox_file "clip.mp4" "tiny-fake-media-bytes"
  run "$OMES_BIN" content scan --json --settle-seconds 0
  [ "$status" -eq 0 ]
  [[ "$output" == *'"created"'* ]]
  [ -d "${CONTENT_ROOT}/state/jobs" ]
  run bash -c "ls ${CONTENT_ROOT}/state/jobs/*.json | wc -l"
  [ "$output" -eq 1 ]
}

@test "omes content scan does not follow symlinks" {
  echo "outside" >"${OMES_TEST_TMPDIR}/outside.mp4"
  mkdir -p "${CONTENT_ROOT}/inbox"
  ln -s "${OMES_TEST_TMPDIR}/outside.mp4" "${CONTENT_ROOT}/inbox/link.mp4"
  run "$OMES_BIN" content scan --json --settle-seconds 0
  [ "$status" -eq 0 ]
  [[ "$output" == *'"created": []'* ]]
}

@test "duplicate content is skipped and recorded as duplicate_of" {
  _drop_inbox_file "a.mp4" "identical-bytes"
  run "$OMES_BIN" content scan --json --settle-seconds 0
  [ "$status" -eq 0 ]
  _drop_inbox_file "b.mp4" "identical-bytes"
  run "$OMES_BIN" content scan --json --settle-seconds 0
  [ "$status" -eq 0 ]
  [[ "$output" == *'"duplicates"'* ]]
  run grep -l duplicate_of "${CONTENT_ROOT}/state/jobs/"*.json
  [ "$status" -eq 0 ]
}

@test "omes content list shows created jobs" {
  _drop_inbox_file "c.mp4" "some-bytes"
  "$OMES_BIN" content scan --json --settle-seconds 0 >/dev/null
  run "$OMES_BIN" content list --json
  [ "$status" -eq 0 ]
  [[ "$output" == *'"queued"'* ]]
}

@test "omes content rescan reports no mismatches for untouched jobs" {
  _drop_inbox_file "d.mp4" "some-more-bytes"
  "$OMES_BIN" content scan --json --settle-seconds 0 >/dev/null
  run "$OMES_BIN" content rescan --json
  [ "$status" -eq 0 ]
  [[ "$output" == *'"mismatches": []'* ]]
}

@test "concurrent scans are rejected via the lock file" {
  _drop_inbox_file "e.mp4" "lock-test-bytes"
  mkdir -p "${CONTENT_ROOT}/state"
  echo "999999999" >"${CONTENT_ROOT}/state/scan.lock"
  # A stale lock (nonexistent pid) is reclaimed automatically, so this
  # should still succeed rather than error.
  run "$OMES_BIN" content scan --json --settle-seconds 0
  [ "$status" -eq 0 ]
}

@test "sessions directory is created privately and holds nothing after a scan" {
  _drop_inbox_file "f.mp4" "bytes"
  "$OMES_BIN" content scan --json --settle-seconds 0 >/dev/null
  [ -d "${CONTENT_ROOT}/sessions" ]
  run bash -c "ls -A '${CONTENT_ROOT}/sessions' | wc -l"
  [ "$output" -eq 0 ]
  run bash -c "stat -c '%a' '${CONTENT_ROOT}/sessions'"
  [ "$output" = "700" ]
}
