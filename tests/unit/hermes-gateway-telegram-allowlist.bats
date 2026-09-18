#!/usr/bin/env bats
# tests/unit/hermes-gateway-telegram-allowlist.bats - unit tests for
# modules/hermes-gateway/telegram-allowlist.sh (issue #13).
#
# Sources the script directly (its `main` only runs when executed, not
# sourced - see its BASH_SOURCE guard) so cmd_add/cmd_remove/cmd_check/
# cmd_diagnose can be called and asserted on individually. HOME is always
# overridden to an isolated tmpdir. tests/shims/curl is already on PATH
# via test_helper.bash.

setup() {
  load '../test_helper.bash'
  omes_test_setup

  export HOME="${OMES_TEST_TMPDIR}/home"
  export OMES_HERMES_HOME="${HOME}/.hermes"
  mkdir -p "$OMES_HERMES_HOME"

  ENV_FILE="${OMES_HERMES_HOME}/.env"
  export ENV_FILE

  # shellcheck source=../../modules/hermes-gateway/telegram-allowlist.sh
  source "${OMES_TEST_ROOT}/modules/hermes-gateway/telegram-allowlist.sh"
}

teardown() {
  # A read-only-directory test below intentionally leaves
  # OMES_HERMES_HOME non-writable; restore it so cleanup can remove it.
  chmod 700 "$OMES_HERMES_HOME" 2>/dev/null || true
  omes_test_teardown
}

_write_env() {
  printf '%s\n' "$@" >"$ENV_FILE"
  chmod 600 "$ENV_FILE"
}

# --- add: writes both variables, validates input ----------------------------

@test "cmd_add writes the chat id to both allowlist variables" {
  _write_env 'TELEGRAM_BOT_TOKEN=abc123secret' 'TELEGRAM_ALLOWED_USERS=111'
  run cmd_add "-1001"
  [ "$status" -eq 0 ]

  run grep -q '^TELEGRAM_ALLOWED_CHATS=-1001$' "$ENV_FILE"
  [ "$status" -eq 0 ]
  run grep -q '^TELEGRAM_GROUP_ALLOWED_CHATS=-1001$' "$ENV_FILE"
  [ "$status" -eq 0 ]
  # The token line and unrelated keys are untouched.
  run grep -q '^TELEGRAM_BOT_TOKEN=abc123secret$' "$ENV_FILE"
  [ "$status" -eq 0 ]
  run grep -q '^TELEGRAM_ALLOWED_USERS=111$' "$ENV_FILE"
  [ "$status" -eq 0 ]
}

@test "cmd_add is idempotent when the id is already in both lists" {
  _write_env 'TELEGRAM_ALLOWED_CHATS=-1001' 'TELEGRAM_GROUP_ALLOWED_CHATS=-1001'
  run cmd_add "-1001"
  [ "$status" -eq 0 ]
  [[ "$output" == *"nothing to do"* ]]
}

@test "cmd_add heals a half-enabled group (present in only one list)" {
  _write_env 'TELEGRAM_ALLOWED_CHATS=-1001' 'TELEGRAM_GROUP_ALLOWED_CHATS='
  run cmd_add "-1001"
  [ "$status" -eq 0 ]
  run grep -q '^TELEGRAM_GROUP_ALLOWED_CHATS=-1001$' "$ENV_FILE"
  [ "$status" -eq 0 ]
}

@test "cmd_add rejects a wildcard" {
  _write_env 'TELEGRAM_ALLOWED_CHATS=' 'TELEGRAM_GROUP_ALLOWED_CHATS='
  run cmd_add "*"
  [ "$status" -eq 1 ]
  run grep -c '\*' "$ENV_FILE"
  [ "$status" -ne 0 ] || [ "$output" -eq 0 ]
}

@test "cmd_add rejects a non-numeric value" {
  _write_env 'TELEGRAM_ALLOWED_CHATS=' 'TELEGRAM_GROUP_ALLOWED_CHATS='
  run cmd_add "not-a-chat-id"
  [ "$status" -eq 1 ]
}

@test "cmd_add rejects an empty value" {
  _write_env 'TELEGRAM_ALLOWED_CHATS=' 'TELEGRAM_GROUP_ALLOWED_CHATS='
  run cmd_add ""
  [ "$status" -eq 1 ]
}

@test "cmd_add never invokes systemctl (never restarts the gateway itself)" {
  _write_env 'TELEGRAM_ALLOWED_CHATS=' 'TELEGRAM_GROUP_ALLOWED_CHATS='
  run cmd_add "-1001"
  [ "$status" -eq 0 ]
  run grep -c 'systemctl' "$SHIM_LOG"
  [ "$status" -ne 0 ] || [ "$output" -eq 0 ]
}

@test "cmd_add prints the exact restart command and warns the change is inert" {
  _write_env 'TELEGRAM_ALLOWED_CHATS=' 'TELEGRAM_GROUP_ALLOWED_CHATS='
  run cmd_add "-1001"
  [ "$status" -eq 0 ]
  [[ "$output" == *"INERT"* ]]
  [[ "$output" == *"systemctl --user restart hermes-gateway"* ]]
  [[ "$output" == *"sudo systemctl restart hermes-gateway"* ]]
}

# --- remove ------------------------------------------------------------------

@test "cmd_remove removes the chat id from both allowlist variables" {
  _write_env 'TELEGRAM_ALLOWED_CHATS=-1001,-1002' 'TELEGRAM_GROUP_ALLOWED_CHATS=-1001,-1002'
  run cmd_remove "-1001"
  [ "$status" -eq 0 ]
  run grep -q '^TELEGRAM_ALLOWED_CHATS=-1002$' "$ENV_FILE"
  [ "$status" -eq 0 ]
  run grep -q '^TELEGRAM_GROUP_ALLOWED_CHATS=-1002$' "$ENV_FILE"
  [ "$status" -eq 0 ]
}

@test "cmd_remove rejects a non-numeric value" {
  _write_env 'TELEGRAM_ALLOWED_CHATS=' 'TELEGRAM_GROUP_ALLOWED_CHATS='
  run cmd_remove "*"
  [ "$status" -eq 1 ]
}

# --- mode/owner preservation --------------------------------------------------

@test "cmd_add preserves the .env file's 0600 mode" {
  _write_env 'TELEGRAM_ALLOWED_CHATS=' 'TELEGRAM_GROUP_ALLOWED_CHATS='
  run cmd_add "-1001"
  [ "$status" -eq 0 ]
  local mode
  mode="$(stat -c '%a' "$ENV_FILE")"
  [ "$mode" = "600" ]
}

@test "cmd_add preserves the .env file's owner" {
  _write_env 'TELEGRAM_ALLOWED_CHATS=' 'TELEGRAM_GROUP_ALLOWED_CHATS='
  local before after
  before="$(stat -c '%u:%g' "$ENV_FILE")"
  run cmd_add "-1001"
  [ "$status" -eq 0 ]
  after="$(stat -c '%u:%g' "$ENV_FILE")"
  [ "$before" = "$after" ]
}

# --- atomicity: no partial file on failure ------------------------------------

@test "cmd_add leaves .env completely untouched when the write fails (read-only directory)" {
  _write_env 'TELEGRAM_ALLOWED_CHATS=-1001' 'TELEGRAM_GROUP_ALLOWED_CHATS=-1001'
  local before
  before="$(cat "$ENV_FILE")"

  chmod 555 "$OMES_HERMES_HOME"
  run cmd_add "-2002"
  local rc="$status"
  chmod 700 "$OMES_HERMES_HOME"

  [ "$rc" -eq 1 ]
  local after
  after="$(cat "$ENV_FILE")"
  [ "$before" = "$after" ]
}

# --- --check: half-enabled detection + redaction -----------------------------

@test "cmd_check flags a group present in only one allowlist variable" {
  _write_env 'TELEGRAM_ALLOWED_CHATS=-1001,-2002' 'TELEGRAM_GROUP_ALLOWED_CHATS=-1001'
  run cmd_check
  [ "$status" -eq 1 ]
  [[ "$output" == *"-2002"* ]]
  [[ "$output" == *"half-enabled"* ]]
}

@test "cmd_check reports no issue when both allowlist variables match" {
  _write_env 'TELEGRAM_ALLOWED_CHATS=-1001' 'TELEGRAM_GROUP_ALLOWED_CHATS=-1001'
  run cmd_check
  [ "$status" -eq 0 ]
  [[ "$output" == *"no half-enabled"* ]]
}

@test "cmd_check never prints the bot token" {
  _write_env 'TELEGRAM_BOT_TOKEN=super-secret-value-12345' 'TELEGRAM_ALLOWED_CHATS=-1001' 'TELEGRAM_GROUP_ALLOWED_CHATS=-1001'
  run cmd_check
  [[ "$output" != *"super-secret-value-12345"* ]]
  [[ "$output" == *"REDACTED"* ]]
}

@test "cmd_check reports <not set> when there is no token" {
  _write_env 'TELEGRAM_ALLOWED_CHATS=' 'TELEGRAM_GROUP_ALLOWED_CHATS='
  run cmd_check
  [[ "$output" == *"TELEGRAM_BOT_TOKEN=<not set>"* ]]
}

# --- diagnose: never getUpdates, never token in argv/log ---------------------

@test "cmd_diagnose rejects a non-numeric chat id" {
  run cmd_diagnose "not-a-chat-id"
  [ "$status" -eq 1 ]
}

@test "cmd_diagnose fails cleanly when no token is configured" {
  _write_env 'TELEGRAM_ALLOWED_CHATS='
  run cmd_diagnose "-1001"
  [ "$status" -eq 1 ]
  [[ "$output" == *"TELEGRAM_BOT_TOKEN"* ]]
}

@test "cmd_diagnose never places the token in curl's argv or log" {
  _write_env 'TELEGRAM_BOT_TOKEN=verysecrettoken999' 'TELEGRAM_ALLOWED_CHATS='
  run cmd_diagnose "-1001"
  run grep -c 'verysecrettoken999' "$SHIM_LOG"
  [ "$status" -ne 0 ] || [ "$output" -eq 0 ]
}

@test "cmd_diagnose calls getChat and getChatMemberCount, never the prohibited endpoint" {
  _write_env 'TELEGRAM_BOT_TOKEN=abc123' 'TELEGRAM_ALLOWED_CHATS='
  run cmd_diagnose "-1001"
  [[ "$output" == *"getChat"* ]]
  [[ "$output" == *"getChatMemberCount"* ]]
  [[ "$output" != *"getUpdates"* ]]
}

@test "cmd_diagnose additionally calls getChatMember when --member is given" {
  _write_env 'TELEGRAM_BOT_TOKEN=abc123' 'TELEGRAM_ALLOWED_CHATS='
  run cmd_diagnose "-1001" --member "555"
  [[ "$output" == *"getChatMember chat_id=-1001 user_id=555"* ]]
}

# --- guard: the prohibited endpoint's name never appears in modules/ --------

@test "guard: 'getUpdates' never appears in any script under modules/" {
  run grep -rln 'getUpdates' "${OMES_TEST_ROOT}/modules"
  [ "$status" -eq 1 ]
  [ -z "$output" ]
}
