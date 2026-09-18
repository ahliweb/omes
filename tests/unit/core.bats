#!/usr/bin/env bats
# tests/unit/core.bats - lib/omes/core.sh unit tests: redaction, exit code
# constants, dry-run/confirm helpers.

setup() {
  load '../test_helper.bash'
  omes_test_setup
  # shellcheck source=../../lib/omes/core.sh
  source "${OMES_TEST_ROOT}/lib/omes/core.sh"
}

teardown() {
  omes_test_teardown
}

@test "omes_redact masks a TOKEN= value" {
  run omes_redact "TELEGRAM_BOT_TOKEN=abc123secret"
  [ "$status" -eq 0 ]
  [ "$output" = "TELEGRAM_BOT_TOKEN=[REDACTED]" ]
}

@test "omes_redact masks KEY=, SECRET= and PASSWORD= case-insensitively" {
  run omes_redact "api_key=xyz SECRET=foo Password=bar"
  [ "$status" -eq 0 ]
  [[ "$output" == *"api_key=[REDACTED]"* ]]
  [[ "$output" == *"SECRET=[REDACTED]"* ]]
  [[ "$output" == *"Password=[REDACTED]"* ]]
}

@test "omes_redact leaves unrelated text untouched" {
  run omes_redact "installing package curl for module apt-base"
  [ "$status" -eq 0 ]
  [ "$output" = "installing package curl for module apt-base" ]
}

@test "omes_redact does not touch keys that merely contain a similar word in the value" {
  run omes_redact "message=this KEY is fine"
  [ "$status" -eq 0 ]
  # "message=" is not a *_KEY=/*_TOKEN=/*_SECRET=/*_PASSWORD= key, so it is untouched.
  [ "$output" = "message=this KEY is fine" ]
}

@test "exit code constants match the documented contract" {
  [ "$OMES_EX_OK" -eq 0 ]
  [ "$OMES_EX_ERROR" -eq 1 ]
  [ "$OMES_EX_USAGE" -eq 2 ]
  [ "$OMES_EX_UNSUPPORTED" -eq 3 ]
  [ "$OMES_EX_PREFLIGHT" -eq 4 ]
  [ "$OMES_EX_PRIVILEGE" -eq 5 ]
  [ "$OMES_EX_MODULE_APPLY" -eq 6 ]
  [ "$OMES_EX_VERIFY" -eq 7 ]
  [ "$OMES_EX_NETWORK" -eq 8 ]
  [ "$OMES_EX_BACKUP" -eq 9 ]
  [ "$OMES_EX_ROLLBACK" -eq 10 ]
}

@test "omes_dry_run is false by default" {
  run omes_dry_run
  [ "$status" -eq 1 ]
}

@test "omes_dry_run is true when OMES_DRY_RUN=1" {
  OMES_DRY_RUN=1
  run omes_dry_run
  [ "$status" -eq 0 ]
}

@test "omes_run prints a would-run line and does not execute under dry-run" {
  OMES_DRY_RUN=1
  local marker="${OMES_TEST_TMPDIR}/should-not-exist"
  run omes_run touch "$marker"
  [ "$status" -eq 0 ]
  [[ "$output" == *"would run: touch"* ]]
  [ ! -e "$marker" ]
}

@test "omes_run executes the command when not in dry-run" {
  local marker="${OMES_TEST_TMPDIR}/should-exist"
  run omes_run touch "$marker"
  [ "$status" -eq 0 ]
  [ -e "$marker" ]
}

@test "omes_confirm auto-confirms under OMES_NONINTERACTIVE=1" {
  OMES_NONINTERACTIVE=1
  run omes_confirm "proceed?"
  [ "$status" -eq 0 ]
}

@test "omes_is_root honors OMES_FAKE_ROOT only when OMES_TEST=1" {
  OMES_TEST=1 OMES_FAKE_ROOT=1
  run omes_is_root
  [ "$status" -eq 0 ]
}

@test "omes_is_root ignores OMES_FAKE_ROOT when OMES_TEST is not 1" {
  unset OMES_TEST
  OMES_FAKE_ROOT=1
  run omes_is_root
  # Real EUID in the test container/sandbox is expected to be non-root.
  [ "$status" -eq 1 ]
}
