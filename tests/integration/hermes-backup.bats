#!/usr/bin/env bats
# tests/integration/hermes-backup.bats - `omes agent-backup` integration
# tests (issue #82), executing bin/omes end to end.
#
# Core backup/restore behavior is exercised exhaustively in
# tests/py/hermesbackup/; these tests focus on the lib/omes/cmd/
# agent-backup.sh wrapper wiring: global --json/--yes propagation, exit
# codes, and the modules/hermes-backup doctor hook through `omes doctor`.

setup() {
  load '../test_helper.bash'
  omes_test_setup
  OMES_BIN="${OMES_TEST_ROOT}/bin/omes"

  export HOME="${OMES_TEST_TMPDIR}/home"
  export HERMES_HOME="${HOME}/.hermes"
  mkdir -p "$HERMES_HOME/skills"
  printf 'model: gpt\n' > "${HERMES_HOME}/config.yaml"
  printf 'print(1)\n' > "${HERMES_HOME}/skills/foo.py"
  printf 'TELEGRAM_BOT_TOKEN=canary-secret-value\n' > "${HERMES_HOME}/.env"

  OMES_OS_RELEASE_FILE="$(omes_fixture_path os-release)"
  cat >"$OMES_OS_RELEASE_FILE" <<'EOF'
PRETTY_NAME="Ubuntu 24.04 LTS"
NAME="Ubuntu"
VERSION_ID="24.04"
VERSION_CODENAME=noble
ID=ubuntu
ID_LIKE=debian
UBUNTU_CODENAME=noble
EOF
  export OMES_OS_RELEASE_FILE
}

teardown() {
  omes_test_teardown
}

@test "omes agent-backup with no subcommand prints usage and exits 2" {
  run "$OMES_BIN" agent-backup
  [ "$status" -eq 2 ]
}

@test "omes agent-backup create defaults to config+skills and excludes .env" {
  run "$OMES_BIN" agent-backup create --json
  [ "$status" -eq 0 ]
  [[ "$output" == *'"config"'* ]]
  [[ "$output" == *'"skills"'* ]]
  [[ "$output" != *"canary-secret-value"* ]]
}

@test "omes agent-backup --json create honors the global --json flag placed right after the command word" {
  local out
  out="$("$OMES_BIN" agent-backup --json create 2>/dev/null)"
  run python3 -c "import json,sys; json.loads(sys.argv[1])" "$out"
  [ "$status" -eq 0 ]
}

@test "omes agent-backup create --dry-run creates no session directory" {
  run "$OMES_BIN" agent-backup create --dry-run --json
  [ "$status" -eq 0 ]
  [ ! -d "${OMES_STATE_DIR}/backups/hermes" ] || [ -z "$(ls -A "${OMES_STATE_DIR}/backups/hermes" 2>/dev/null)" ]
}

@test "omes agent-backup create --class secrets without --include-secrets is refused" {
  run "$OMES_BIN" agent-backup create --class secrets --json
  [ "$status" -ne 0 ]
  [[ "$output" == *"include-secrets"* ]] || [[ "$output" == *"secrets"* ]]
}

@test "omes agent-backup list shows a created session" {
  "$OMES_BIN" agent-backup create --yes >/dev/null
  run "$OMES_BIN" agent-backup list --json
  [ "$status" -eq 0 ]
  [[ "$output" == *"config"* ]]
}

@test "omes agent-backup verify reports OK for a freshly created session" {
  run "$OMES_BIN" agent-backup create --json
  [ "$status" -eq 0 ]
  ts="$(printf '%s' "$output" | python3 -c 'import json,sys; print(json.load(sys.stdin)["timestamp"])')"
  run "$OMES_BIN" agent-backup verify "$ts" --json
  [ "$status" -eq 0 ]
  [[ "$output" == *'"ok": true'* ]]
}

@test "omes agent-backup restore requires --yes or a TTY to proceed" {
  run "$OMES_BIN" agent-backup create --json
  ts="$(printf '%s' "$output" | python3 -c 'import json,sys; print(json.load(sys.stdin)["timestamp"])')"
  printf 'changed\n' > "${HERMES_HOME}/config.yaml"

  run "$OMES_BIN" agent-backup restore "$ts"
  [ "$status" -ne 0 ]
  [ "$(cat "${HERMES_HOME}/config.yaml")" = "changed" ]
}

@test "omes agent-backup restore --yes restores config and creates a pre-restore backup" {
  run "$OMES_BIN" agent-backup create --json
  ts="$(printf '%s' "$output" | python3 -c 'import json,sys; print(json.load(sys.stdin)["timestamp"])')"
  printf 'changed\n' > "${HERMES_HOME}/config.yaml"

  run "$OMES_BIN" agent-backup restore "$ts" --class config --yes --json
  [ "$status" -eq 0 ]
  [ "$(cat "${HERMES_HOME}/config.yaml")" = "model: gpt" ]
  [[ "$output" == *"pre_restore_backup"* ]]
}

@test "omes agent-backup restore never restores secrets without --restore-secrets" {
  local create_out
  create_out="$("$OMES_BIN" agent-backup create --class secrets --include-secrets --yes --json 2>/dev/null)"
  ts="$(printf '%s' "$create_out" | python3 -c 'import json,sys; print(json.load(sys.stdin)["timestamp"])')"
  printf 'TELEGRAM_BOT_TOKEN=live-value\n' > "${HERMES_HOME}/.env"

  run "$OMES_BIN" agent-backup restore "$ts" --class secrets --yes --json
  [ "$status" -eq 0 ]
  [[ "$output" == *'"skipped_secrets": true'* ]]
  [ "$(cat "${HERMES_HOME}/.env")" = "TELEGRAM_BOT_TOKEN=live-value" ]
}

@test "omes doctor reports the hermes-backup module_doctor line once applied" {
  "$OMES_BIN" agent-backup create --yes >/dev/null
  run "$OMES_BIN" install --module hermes-backup --yes
  [ "$status" -eq 0 ]

  run "$OMES_BIN" doctor
  [ "$status" -eq 0 ]
  [[ "$output" == *"agent-backup"* ]]
}
