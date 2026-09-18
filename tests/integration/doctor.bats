#!/usr/bin/env bats
# tests/integration/doctor.bats - `omes doctor` integration tests.

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

@test "doctor on a fresh (never-applied) state dir exits 0 with only OK checks" {
  run "$OMES_BIN" doctor
  [ "$status" -eq 0 ]
  [[ "$output" == *"platform"*"OK"* ]]
  [[ "$output" == *"backups"*"OK"* ]]
  [[ "$output" == *"log_writable"*"OK"* ]]
  [[ "$output" != *"FAIL"* ]]
}

@test "doctor --json emits a single valid JSON object with a checks array" {
  omes_run_stdout_only "$OMES_BIN" doctor --json
  [ "$status" -eq 0 ]
  run python3 -c 'import json,sys
d=json.loads(sys.stdin.read())
assert d["command"]=="doctor"
assert d["ok"] is True
assert any(c["name"]=="platform" and c["level"]=="OK" for c in d["checks"])
assert all(c["level"] in ("OK","WARN","FAIL") for c in d["checks"])' <<< "$output"
  [ "$status" -eq 0 ]
}

@test "doctor on an unsupported platform reports platform FAIL and exits non-zero" {
  local debian_osr
  debian_osr="$(omes_fixture_path os-release-debian)"
  cat > "$debian_osr" <<'EOF'
PRETTY_NAME="Debian GNU/Linux 12 (bookworm)"
NAME="Debian GNU/Linux"
VERSION_ID="12"
VERSION_CODENAME=bookworm
ID=debian
EOF
  OMES_OS_RELEASE_FILE="$debian_osr" run "$OMES_BIN" doctor
  [ "$status" -ne 0 ]
  [[ "$output" == *"platform"*"FAIL"* ]]
}

@test "doctor reports a module recorded as applied but missing from disk as WARN, not FAIL" {
  mkdir -p "$OMES_STATE_DIR"
  {
    printf 'module.ghost.status=applied\n'
    printf 'module.ghost.managed_paths=\n'
  } >> "${OMES_STATE_DIR}/state"

  run "$OMES_BIN" doctor
  [ "$status" -eq 0 ]
  [[ "$output" == *"module:ghost"*"WARN"* ]]
}

@test "doctor verifies a real applied module (apt-base) as OK" {
  OMES_TEST=1 OMES_FAKE_ROOT=1 "$OMES_BIN" install --profile server --yes >/dev/null

  OMES_TEST=1 OMES_FAKE_ROOT=1 run "$OMES_BIN" doctor
  [ "$status" -eq 0 ]
  [[ "$output" == *"module:apt-base"*"OK"*"verified"* ]]
}

@test "doctor reports a root-scope module as WARN (not FAIL) when run as non-root" {
  OMES_TEST=1 OMES_FAKE_ROOT=1 "$OMES_BIN" install --profile server --yes >/dev/null

  run "$OMES_BIN" doctor
  [ "$status" -eq 0 ]
  [[ "$output" == *"module:apt-base"*"WARN"*"requires root"* ]]
}
