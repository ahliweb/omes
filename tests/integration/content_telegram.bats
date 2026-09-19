#!/usr/bin/env bats
# tests/integration/content_telegram.bats - `omes content notify|status
# --telegram|approve --channel telegram|edit` (#65), against
# tests/shims/curl's `-K` fake-Telegram response (no network, no real
# token). Python-level coverage with a real stdlib HTTP fake server lives
# in tests/py/content/test_telegram.py.

setup() {
  load '../test_helper.bash'
  omes_test_setup
  CONTENT_ROOT="${OMES_TEST_TMPDIR}/content-root"
  export OMES_CONTENT_ROOT="$CONTENT_ROOT"
  OMES_BIN="${OMES_TEST_ROOT}/bin/omes"

  HERMES_HOME="${OMES_TEST_TMPDIR}/hermes-home"
  export HERMES_HOME
  mkdir -p "$HERMES_HOME"
  cat >"${HERMES_HOME}/.env" <<'EOF'
TELEGRAM_BOT_TOKEN=PLANTED_FAKE_TOKEN_FOR_TESTS
TELEGRAM_ALLOWED_USERS=111,222
EOF
  chmod 600 "${HERMES_HOME}/.env"

  mkdir -p "${CONTENT_ROOT}/inbox"
  echo "tiny-media-bytes" > "${CONTENT_ROOT}/inbox/clip.mp4"
  "$OMES_BIN" content scan --json --settle-seconds 0 >/dev/null
  JOB_ID="$(ls "${CONTENT_ROOT}/state/jobs" | head -1 | sed 's/\.json$//')"
  "$OMES_BIN" content plan "$JOB_ID" --actor alice --caption "hello" --target generic_browser >/dev/null
}

teardown() {
  omes_test_teardown
}

@test "notify sends the preview and never leaks the token to stdout or audit" {
  run "$OMES_BIN" content notify "$JOB_ID" --chat-id 111 --json
  [ "$status" -eq 0 ]
  [[ "$output" != *"PLANTED_FAKE_TOKEN_FOR_TESTS"* ]]
  run grep -c "PLANTED_FAKE_TOKEN_FOR_TESTS" "${CONTENT_ROOT}/state/audit.jsonl"
  [ "$output" -eq 0 ]
}

@test "approve --channel telegram rejects a non-approver" {
  run "$OMES_BIN" content approve "$JOB_ID" --actor 999 --channel telegram --json
  [ "$status" -ne 0 ]
  run "$OMES_BIN" content list --state approval-required --json
  [[ "$output" == *"$JOB_ID"* ]]
}

@test "approve --channel telegram accepts an authorized approver (subset of TELEGRAM_ALLOWED_USERS)" {
  export OMES_CONTENT_APPROVERS="111"
  run "$OMES_BIN" content approve "$JOB_ID" --actor 111 --channel telegram --json
  [ "$status" -eq 0 ]
  [[ "$output" == *'"state": "approved"'* ]]
}

@test "approve --channel telegram rejects an id in OMES_CONTENT_APPROVERS but not TELEGRAM_ALLOWED_USERS" {
  export OMES_CONTENT_APPROVERS="333"
  run "$OMES_BIN" content approve "$JOB_ID" --actor 333 --channel telegram --json
  [ "$status" -ne 0 ]
}

@test "approve rejects a mismatched --expected-hash" {
  export OMES_CONTENT_APPROVERS="111"
  run "$OMES_BIN" content approve "$JOB_ID" --actor 111 --channel telegram --expected-hash "deadbeef" --json
  [ "$status" -ne 0 ]
}

@test "edit updates the caption while approval-required without changing state" {
  run "$OMES_BIN" content edit "$JOB_ID" --actor alice --caption "new caption" --json
  [ "$status" -eq 0 ]
  [[ "$output" == *"new caption"* ]]
  [[ "$output" == *'"state": "approval-required"'* ]]
}

@test "status --telegram sends a summary without leaking the token" {
  run "$OMES_BIN" content status "$JOB_ID" --telegram --chat-id 111 --json
  [ "$status" -eq 0 ]
  [[ "$output" != *"PLANTED_FAKE_TOKEN_FOR_TESTS"* ]]
}

@test "the CLI-channel approval path still works without any Telegram config" {
  unset OMES_CONTENT_APPROVERS
  run "$OMES_BIN" content approve "$JOB_ID" --actor alice --json
  [ "$status" -eq 0 ]
}
