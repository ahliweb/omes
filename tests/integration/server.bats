#!/usr/bin/env bats
# tests/integration/server.bats - server profile integration tests for #7
# (modules/security-baseline, modules/containers).
#
# Root-scope behavior is exercised via the OMES_TEST=1 / OMES_FAKE_ROOT=1
# test hook (lib/omes/core.sh: omes_is_root), never real privilege
# escalation. Every /etc-rooted path is redirected via OMES_ETC_DIR /
# OMES_APT_SOURCES_DIR so this suite never touches the real filesystem.

setup() {
  load '../test_helper.bash'
  omes_test_setup
  OMES_BIN="${OMES_TEST_ROOT}/bin/omes"

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

  # hermes/hermes-gateway are also in the server profile (#11, #12);
  # simulate them as already-installed/healthy so this suite stays
  # focused on security-baseline/containers - see tests/integration/
  # install.bats for the same convention.
  export HOME="${OMES_TEST_TMPDIR}/home"
  mkdir -p "$HOME"
  export SHIM_HERMES_VERSION="1.2.3"
  export SHIM_USER_ENABLED_FILE="${OMES_TEST_TMPDIR}/user-enabled"
  export SHIM_USER_ACTIVE_FILE="${OMES_TEST_TMPDIR}/user-active"
  export SHIM_SYSTEM_ENABLED_FILE="${OMES_TEST_TMPDIR}/sys-enabled"
  export SHIM_SYSTEM_ACTIVE_FILE="${OMES_TEST_TMPDIR}/sys-active"

  export OMES_ETC_DIR="${OMES_TEST_TMPDIR}/etc"
  export OMES_APT_SOURCES_DIR="${OMES_TEST_TMPDIR}/sources.list.d"
  export SHIM_UFW_STATE_FILE="${OMES_TEST_TMPDIR}/ufw-state"

  PEM_FIXTURE="${OMES_TEST_TMPDIR}/docker-gpg.asc"
  {
    printf -- '-----BEGIN PGP PUBLIC KEY BLOCK-----\n\n'
    printf 'mQINBFit2ioBEADhWpZ8/wvZ6hUTiXOwQHXMAlaFHcPH9hAtr4F1y2+OQ0OF\n'
    printf -- '-----END PGP PUBLIC KEY BLOCK-----\n'
  } > "$PEM_FIXTURE"
  export SHIM_CURL_OUTPUT_FILE="$PEM_FIXTURE"
}

teardown() {
  omes_test_teardown
}

@test "check --profile server as root includes security-baseline and passes" {
  OMES_TEST=1 OMES_FAKE_ROOT=1 run "$OMES_BIN" check --profile server
  [ "$status" -eq 0 ]
  [[ "$output" == *"security-baseline"* ]]
}

@test "install --profile server as root applies security-baseline: ufw active, default deny incoming" {
  OMES_TEST=1 OMES_FAKE_ROOT=1 run "$OMES_BIN" install --profile server --yes
  [ "$status" -eq 0 ]

  run grep -q '^module.security-baseline.status=applied$' "${OMES_STATE_DIR}/state"
  [ "$status" -eq 0 ]

  OMES_TEST=1 OMES_FAKE_ROOT=1 run "$OMES_BIN" status
  [ "$status" -eq 0 ]
  [[ "$output" == *"security-baseline"* ]]
}

@test "install --profile server as root keeps SSH open when an active session is detected" {
  export SHIM_SYSTEM_ACTIVE_FILE="${OMES_TEST_TMPDIR}/sys-active"
  printf 'ssh\n' > "$SHIM_SYSTEM_ACTIVE_FILE"

  OMES_TEST=1 OMES_FAKE_ROOT=1 run "$OMES_BIN" install --profile server --yes
  [ "$status" -eq 0 ]
  [[ "$output" == *"SSH access kept open"* ]]
  run grep -c '^ufw allow OpenSSH$' "$SHIM_LOG"
  [ "$output" -ge 1 ]
}

@test "re-running install --profile server as root is idempotent for security-baseline" {
  OMES_TEST=1 OMES_FAKE_ROOT=1 run "$OMES_BIN" install --profile server --yes
  [ "$status" -eq 0 ]

  : > "$SHIM_LOG"
  OMES_TEST=1 OMES_FAKE_ROOT=1 run "$OMES_BIN" install --profile server --yes
  [ "$status" -eq 0 ]
  run grep -Ec 'ufw (allow|default|--force enable|^enable|disable)' "$SHIM_LOG"
  [ "$status" -ne 0 ] || [ "$output" -eq 0 ]
}

@test "containers is NOT part of the default server profile" {
  OMES_TEST=1 OMES_FAKE_ROOT=1 run "$OMES_BIN" install --profile server --yes
  [ "$status" -eq 0 ]
  run grep -c 'docker' "$SHIM_LOG"
  [ "$status" -ne 0 ] || [ "$output" -eq 0 ]
  [ ! -e "${OMES_APT_SOURCES_DIR}/docker.sources" ]
}

@test "containers applies only when explicitly requested via --module containers" {
  OMES_TEST=1 OMES_FAKE_ROOT=1 run "$OMES_BIN" install --module containers --yes
  [ "$status" -eq 0 ]
  [ -f "${OMES_APT_SOURCES_DIR}/docker.sources" ]
  run grep -q '^module.containers.status=applied$' "${OMES_STATE_DIR}/state"
  [ "$status" -eq 0 ]
}

@test "uninstall --profile server rolls back security-baseline: config files removed, packages kept" {
  OMES_TEST=1 OMES_FAKE_ROOT=1 run "$OMES_BIN" install --profile server --yes
  [ "$status" -eq 0 ]
  local apt_conf="${OMES_ETC_DIR}/apt/apt.conf.d/52omes-unattended-upgrades"
  [ -f "$apt_conf" ]

  OMES_TEST=1 OMES_FAKE_ROOT=1 run "$OMES_BIN" uninstall --profile server --yes
  [ "$status" -eq 0 ]
  [ ! -f "$apt_conf" ]
  [[ "$output" == *"apt-get remove"* ]]

  run grep -q '^module.security-baseline.status=removed$' "${OMES_STATE_DIR}/state"
  [ "$status" -eq 0 ]
}

@test "install --profile server --dry-run as root makes no ufw or apt-get install calls" {
  OMES_TEST=1 OMES_FAKE_ROOT=1 run "$OMES_BIN" install --profile server --dry-run --yes
  [ "$status" -eq 0 ]
  [[ "$output" == *"would run"* ]]
  run grep -Ec 'ufw (allow|default|--force enable|^enable|disable)' "$SHIM_LOG"
  [ "$status" -ne 0 ] || [ "$output" -eq 0 ]
  run grep -c 'apt-get install' "$SHIM_LOG"
  [ "$status" -ne 0 ] || [ "$output" -eq 0 ]
}
