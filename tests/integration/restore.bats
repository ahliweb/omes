#!/usr/bin/env bats
# tests/integration/restore.bats - `omes restore` integration tests: the
# full backup -> modify -> restore round trip through the real CLI,
# corrupt-manifest refusal, --list, and offline behavior.

setup() {
  load '../test_helper.bash'
  omes_test_setup
  OMES_BIN="${OMES_TEST_ROOT}/bin/omes"

  LIVE_DIR="${OMES_TEST_TMPDIR}/live"
  mkdir -p "$LIVE_DIR"
  FILE_A="${LIVE_DIR}/a.conf"
  printf 'original content\n' > "$FILE_A"

  mkdir -p "$OMES_STATE_DIR"
  {
    printf 'module.demo.status=applied\n'
    printf 'module.demo.managed_paths=%s\n' "$FILE_A"
  } >> "${OMES_STATE_DIR}/state"
}

teardown() {
  omes_test_teardown
}

@test "restore with no backups available fails with exit 9" {
  run "$OMES_BIN" restore --yes
  [ "$status" -eq 9 ]
}

@test "restore --list on a fresh state dir reports no backups" {
  run "$OMES_BIN" restore --list
  [ "$status" -eq 0 ]
  [[ "$output" == *"no backups available"* ]]
}

@test "backup -> modify -> restore round-trips the original content, verified by checksum" {
  run "$OMES_BIN" backup
  [ "$status" -eq 0 ]

  local original_sum
  original_sum="$(sha256sum "$FILE_A" | awk '{print $1}')"

  printf 'modified by something else\n' > "$FILE_A"
  [ "$(cat "$FILE_A")" = "modified by something else" ]

  run "$OMES_BIN" restore --yes
  [ "$status" -eq 0 ]

  [ "$(cat "$FILE_A")" = "original content" ]
  local restored_sum
  restored_sum="$(sha256sum "$FILE_A" | awk '{print $1}')"
  [ "$restored_sum" = "$original_sum" ]
}

@test "restore --list shows the session after a backup" {
  "$OMES_BIN" backup >/dev/null
  run "$OMES_BIN" restore --list
  [ "$status" -eq 0 ]
  [[ "$output" == *"module=demo"* ]]
}

@test "restore --list --json is valid JSON and lists the session" {
  "$OMES_BIN" backup >/dev/null
  run "$OMES_BIN" restore --list --json
  [ "$status" -eq 0 ]
  run python3 -c 'import json,sys; d=json.loads(sys.stdin.read()); assert d["command"]=="restore"; assert len(d["backups"])==1; assert d["backups"][0]["module"]=="demo"' <<< "$output"
  [ "$status" -eq 0 ]
}

@test "restore --dry-run does not modify the file" {
  "$OMES_BIN" backup >/dev/null
  printf 'modified\n' > "$FILE_A"

  OMES_DRY_RUN=1 run "$OMES_BIN" restore
  [ "$status" -eq 0 ]
  [ "$(cat "$FILE_A")" = "modified" ]
}

@test "restore --from <timestamp> restores that specific session" {
  printf 'session one\n' > "$FILE_A"
  "$OMES_BIN" backup >/dev/null
  local first
  first="$(ls -1 "${OMES_STATE_DIR}/backups")"

  printf 'session two\n' > "$FILE_A"
  "$OMES_BIN" backup >/dev/null

  printf 'live edit after both backups\n' > "$FILE_A"

  run "$OMES_BIN" restore --from "$first" --yes
  [ "$status" -eq 0 ]
  [ "$(cat "$FILE_A")" = "session one" ]
}

@test "restore --from a nonexistent timestamp fails with exit 9" {
  "$OMES_BIN" backup >/dev/null
  run "$OMES_BIN" restore --from "20000101T000000Z" --yes
  [ "$status" -eq 9 ]
}

@test "restore refuses a corrupt MANIFEST with exit 9 and leaves the file untouched" {
  "$OMES_BIN" backup >/dev/null
  local session
  session="$(ls -1 "${OMES_STATE_DIR}/backups")"
  printf 'not-a-valid-manifest-line\n' >> "${OMES_STATE_DIR}/backups/${session}/MANIFEST"

  printf 'still modified\n' > "$FILE_A"

  run "$OMES_BIN" restore --yes
  [ "$status" -eq 9 ]
  [ "$(cat "$FILE_A")" = "still modified" ]
}

@test "restore works fully offline (OMES_ASSUME_OFFLINE=1)" {
  "$OMES_BIN" backup >/dev/null
  printf 'modified\n' > "$FILE_A"

  OMES_ASSUME_ONLINE=0 OMES_ASSUME_OFFLINE=1 run "$OMES_BIN" restore --yes
  [ "$status" -eq 0 ]
  [ "$(cat "$FILE_A")" = "original content" ]
}

@test "restore --json emits a single valid JSON object on success" {
  "$OMES_BIN" backup >/dev/null
  run "$OMES_BIN" restore --yes --json
  [ "$status" -eq 0 ]
  run python3 -c 'import json,sys; d=json.loads(sys.stdin.read()); assert d["command"]=="restore"; assert d["ok"] is True; assert d["exit_code"]==0' <<< "$output"
  [ "$status" -eq 0 ]
}

@test "restore without --yes and without a TTY refuses (exit 1, no mutation)" {
  "$OMES_BIN" backup >/dev/null
  printf 'modified\n' > "$FILE_A"
  run "$OMES_BIN" restore < /dev/null
  [ "$status" -eq 1 ]
  [ "$(cat "$FILE_A")" = "modified" ]
}
