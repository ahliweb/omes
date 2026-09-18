#!/usr/bin/env bats
# tests/integration/hermes.bats - `omes install --profile hermes` (modules/hermes)
# integration tests, executing bin/omes end to end against tests/shims/.
#
# HOME is always overridden to an isolated tmpdir so nothing here touches
# the real developer's home directory. Root-scope behavior elsewhere in the
# suite uses OMES_FAKE_ROOT; the hermes module is user-scope and must NEVER
# run as (simulated) root - several tests below assert exactly that.

setup() {
  load '../test_helper.bash'
  omes_test_setup
  OMES_BIN="${OMES_TEST_ROOT}/bin/omes"

  export HOME="${OMES_TEST_TMPDIR}/home"
  mkdir -p "$HOME"
  unset XDG_DATA_HOME || true

  export OMES_HERMES_HOME="${HOME}/.hermes"

  OMES_OS_RELEASE_FILE="$(omes_fixture_path os-release)"
  cat > "$OMES_OS_RELEASE_FILE" <<'EOF'
PRETTY_NAME="Ubuntu 24.04 LTS"
NAME="Ubuntu"
VERSION_ID="24.04"
VERSION_CODENAME=noble
ID=ubuntu
ID_LIKE=debian
UBUNTU_CODENAME=noble
EOF
  export OMES_OS_RELEASE_FILE

  # tests/shims/curl (already on PATH via test_helper.bash) stands in for
  # curl, which module_check requires unconditionally. module_apply here
  # always takes the "already installed" path via SHIM_HERMES_VERSION, so
  # curl's -o/download behavior is never exercised in this file - see
  # tests/unit/hermes.bats for that.
  export SHIM_HERMES_VERSION="1.2.3"
}

teardown() {
  omes_test_teardown
}

@test "install --profile hermes as (simulated) root skips the user-scope hermes module" {
  OMES_TEST=1 OMES_FAKE_ROOT=1 run "$OMES_BIN" install --profile hermes --yes
  [ "$status" -eq 0 ]
  [[ "$output" == *"Run as your user"* ]]
  run grep -c '^hermes ' "$SHIM_LOG"
  [ "$status" -ne 0 ] || [ "$output" -eq 0 ]
}

@test "install --module hermes as (simulated) root (explicit wrong-scope request) exits 5" {
  OMES_TEST=1 OMES_FAKE_ROOT=1 run "$OMES_BIN" install --module hermes --yes
  [ "$status" -eq 5 ]
}

@test "install --profile hermes as non-root applies hermes and passes verification" {
  run "$OMES_BIN" install --profile hermes --yes
  [ "$status" -eq 0 ]
  run grep -q '^module.hermes.status=applied$' "${OMES_STATE_DIR}/state"
  [ "$status" -eq 0 ]
}

@test "install --profile hermes never backs up \$HERMES_HOME/.env (secret boundary)" {
  run "$OMES_BIN" install --profile hermes --yes
  [ "$status" -eq 0 ]
  [ -f "${OMES_HERMES_HOME}/.env" ]

  run find "${OMES_STATE_DIR}/backups" -type f -name '.env'
  [ "$status" -eq 0 ]
  [ -z "$output" ]
}

@test "install --profile hermes exits 7 when hermes doctor fails verification" {
  export SHIM_HERMES_DOCTOR_EXIT=1
  export SHIM_HERMES_DOCTOR_OUTPUT="FAIL: telegram gateway unreachable"
  run "$OMES_BIN" install --profile hermes --yes
  [ "$status" -eq 7 ]
  [[ "$output" == *"telegram gateway unreachable"* ]]
}

@test "install --profile hermes --dry-run performs no download and writes no state" {
  run "$OMES_BIN" install --profile hermes --dry-run --yes
  [ "$status" -eq 0 ]
  [[ "$output" == *"would"* ]] || [[ "$output" == *"dry-run"* ]]
  [ ! -e "${OMES_HERMES_HOME}/.env" ]
  run grep -c '^module.hermes.status=' "${OMES_STATE_DIR}/state"
  [ "$status" -ne 0 ] || [ "$output" -eq 0 ]
}

@test "re-running install --profile hermes is idempotent (.env preserved, still applied)" {
  run "$OMES_BIN" install --profile hermes --yes
  [ "$status" -eq 0 ]
  [ -f "${OMES_HERMES_HOME}/.env" ]
  local mode
  mode="$(stat -c '%a' "${OMES_HERMES_HOME}/.env")"
  [ "$mode" = "600" ]

  printf 'TELEGRAM_BOT_TOKEN=keepme\n' > "${OMES_HERMES_HOME}/.env"
  chmod 600 "${OMES_HERMES_HOME}/.env"

  run "$OMES_BIN" install --profile hermes --yes
  [ "$status" -eq 0 ]
  run grep -c 'keepme' "${OMES_HERMES_HOME}/.env"
  [ "$status" -eq 0 ]
  [ "$output" -eq 1 ]
}
