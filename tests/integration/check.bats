#!/usr/bin/env bats
# tests/integration/check.bats - `omes check` integration tests.
#
# os-release fixtures are generated inline via heredocs into the per-test
# temp dir (tests/fixtures/os-release is owned by another part of the test
# suite and is intentionally not used here).

setup() {
  load '../test_helper.bash'
  omes_test_setup
  OMES_BIN="${OMES_TEST_ROOT}/bin/omes"

  # The server profile now includes the user-scope `hermes` module (#11),
  # whose module_check asserts $HOME is writable; override it to an
  # isolated, always-writable tmpdir (the bats docker container's real
  # $HOME is "/", which is not writable by a non-root uid).
  export HOME="${OMES_TEST_TMPDIR}/home"
  mkdir -p "$HOME"
}

teardown() {
  omes_test_teardown
}

_ubuntu_2404_fixture() {
  local path
  path="$(omes_fixture_path os-release)"
  cat > "$path" <<'EOF'
PRETTY_NAME="Ubuntu 24.04 LTS"
NAME="Ubuntu"
VERSION_ID="24.04"
VERSION_CODENAME=noble
ID=ubuntu
ID_LIKE=debian
UBUNTU_CODENAME=noble
EOF
  printf '%s' "$path"
}

_debian_12_fixture() {
  local path
  path="$(omes_fixture_path os-release)"
  cat > "$path" <<'EOF'
PRETTY_NAME="Debian GNU/Linux 12 (bookworm)"
NAME="Debian GNU/Linux"
VERSION_ID="12"
VERSION_CODENAME=bookworm
ID=debian
EOF
  printf '%s' "$path"
}

@test "omes check on Ubuntu 24.04 (tier1) exits 0" {
  local osr
  osr="$(_ubuntu_2404_fixture)"
  OMES_OS_RELEASE_FILE="$osr" run "$OMES_BIN" check
  [ "$status" -eq 0 ]
  [[ "$output" == *"tier=tier1"* ]]
}

@test "omes check --json on Ubuntu 24.04 is valid JSON and exits 0" {
  local osr
  osr="$(_ubuntu_2404_fixture)"
  OMES_OS_RELEASE_FILE="$osr" run "$OMES_BIN" check --json
  [ "$status" -eq 0 ]
  run python3 -c 'import json,sys; d=json.loads(sys.stdin.read()); assert d["exit_code"]==0; assert d["platform"]["tier"]=="tier1"' <<< "$output"
  [ "$status" -eq 0 ]
}

@test "omes check on Debian 12 (unsupported) exits 3 and never touches apt-get" {
  local osr
  osr="$(_debian_12_fixture)"
  OMES_OS_RELEASE_FILE="$osr" run "$OMES_BIN" check --profile server
  [ "$status" -eq 3 ]
  run grep -c 'apt-get' "$SHIM_LOG"
  [ "$status" -ne 0 ] || [ "$output" -eq 0 ]
}

@test "omes check --profile server as non-root reports apt-base as skipped, not failed" {
  local osr
  osr="$(_ubuntu_2404_fixture)"
  OMES_OS_RELEASE_FILE="$osr" run "$OMES_BIN" check --profile server
  [ "$status" -eq 0 ]
  [[ "$output" == *"apt-base"* ]]
  [[ "$output" == *"Run with sudo"* ]]
}

@test "omes check --module naming a wrong-scope module exits 5 with no mutation" {
  local osr
  osr="$(_ubuntu_2404_fixture)"
  OMES_OS_RELEASE_FILE="$osr" run "$OMES_BIN" check --module apt-base
  [ "$status" -eq 5 ]
  run grep -c 'apt-get' "$SHIM_LOG"
  [ "$status" -ne 0 ] || [ "$output" -eq 0 ]
}
