#!/usr/bin/env bats
# tests/integration/pkg.bats - lib/omes/pkg.sh integration tests via `omes`
# (apt-base is the exercising module, since it's the only real module in
# this repository so far).

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

  # The server profile now also includes the root-scope `security-baseline`
  # module (#7) - see tests/integration/install.bats for the identical
  # rationale/pattern.
  export OMES_ETC_DIR="${OMES_TEST_TMPDIR}/etc"
  export SHIM_UFW_STATE_FILE="${OMES_TEST_TMPDIR}/ufw-state"
  printf 'ufw\nunattended-upgrades\n' >> "$SHIM_INSTALLED_PKGS_FILE"
}

teardown() {
  omes_test_teardown
}

@test "check fails (exit 4) when a missing package does not exist in the configured repos, before any apt-get install" {
  OMES_TEST=1 OMES_FAKE_ROOT=1 SHIM_APT_CACHE_UNKNOWN_PKGS="jq" run "$OMES_BIN" check --profile server
  [ "$status" -eq 4 ]
  [[ "$output" == *"jq"* ]]
  run grep -c 'apt-get install' "$SHIM_LOG"
  [ "$status" -ne 0 ] || [ "$output" -eq 0 ]
}

@test "install fails (exit 4) when a missing package does not exist in the configured repos, before any apt-get install" {
  OMES_TEST=1 OMES_FAKE_ROOT=1 SHIM_APT_CACHE_UNKNOWN_PKGS="jq" run "$OMES_BIN" install --profile server --yes
  [ "$status" -eq 4 ]
  run grep -c 'apt-get install' "$SHIM_LOG"
  [ "$status" -ne 0 ] || [ "$output" -eq 0 ]
  run grep -q '^module.apt-base.status=applied$' "${OMES_STATE_DIR}/state"
  [ "$status" -ne 0 ]
}

@test "check fails (exit 4) when offline and packages are missing (never exit 8 at check time)" {
  OMES_TEST=1 OMES_FAKE_ROOT=1 OMES_ASSUME_ONLINE=0 OMES_ASSUME_OFFLINE=1 run "$OMES_BIN" check --profile server
  [ "$status" -eq 4 ]
}

@test "install succeeds (exit 0), calls apt-get install exactly once, and records installed_packages when all packages exist in repos" {
  OMES_TEST=1 OMES_FAKE_ROOT=1 run "$OMES_BIN" install --profile server --yes
  [ "$status" -eq 0 ]
  run grep -c 'apt-get install' "$SHIM_LOG"
  [ "$output" -eq 1 ]
  run grep -q '^module.apt-base.installed_packages=' "${OMES_STATE_DIR}/state"
  [ "$status" -eq 0 ]
}
