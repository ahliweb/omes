#!/usr/bin/env bats
# tests/integration/install.bats - `omes install` integration tests.
#
# Root-scope behavior is exercised via the OMES_TEST=1 / OMES_FAKE_ROOT=1
# test hook (lib/omes/core.sh: omes_is_root), never real privilege
# escalation. os-release fixtures are generated inline via heredocs (see
# check.bats for why tests/fixtures/os-release is not used here).

setup() {
  load '../test_helper.bash'
  omes_test_setup
  OMES_BIN="${OMES_TEST_ROOT}/bin/omes"

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

  # The server profile now includes the user-scope `hermes` and
  # `hermes-gateway` modules (#11, #12). Override $HOME (the bats docker
  # container's real $HOME is "/", not writable by a non-root uid) and
  # simulate an already-installed, healthy Hermes + gateway via the shims
  # so these apt-base-focused tests exercise both as a trivial no-op
  # rather than a real (network-touching) install; their own behavior is
  # covered by tests/*/hermes*.bats and tests/*/hermes-gateway*.bats.
  export HOME="${OMES_TEST_TMPDIR}/home"
  mkdir -p "$HOME"
  export SHIM_HERMES_VERSION="1.2.3"
  export SHIM_USER_ENABLED_FILE="${OMES_TEST_TMPDIR}/user-enabled"
  export SHIM_USER_ACTIVE_FILE="${OMES_TEST_TMPDIR}/user-active"

  # The server profile now also includes the root-scope `security-baseline`
  # module (#7). Redirect its /etc-rooted paths to an isolated tmpdir (the
  # bats docker container's non-root uid cannot write real /etc) and give
  # ufw a fixed statefile so these apt-base-focused tests exercise
  # security-baseline as a lightweight pass-through rather than failing on
  # filesystem permissions; its own behavior is covered by
  # tests/*/security-baseline*.bats and tests/integration/server.bats.
  # ufw/unattended-upgrades are marked already-installed so the existing
  # "apt-get install exactly once" assertions below (about apt-base's own
  # install) remain valid.
  export OMES_ETC_DIR="${OMES_TEST_TMPDIR}/etc"
  export SHIM_UFW_STATE_FILE="${OMES_TEST_TMPDIR}/ufw-state"
  printf 'ufw\nunattended-upgrades\n' >> "$SHIM_INSTALLED_PKGS_FILE"
}

teardown() {
  omes_test_teardown
}

@test "install --dry-run --yes as non-root skips the root-scope profile entirely (nothing to dry-run)" {
  run "$OMES_BIN" install --profile server --dry-run --yes
  [ "$status" -eq 0 ]
  [[ "$output" == *"Run with sudo"* ]]
  run grep -c 'apt-get' "$SHIM_LOG"
  [ "$status" -ne 0 ] || [ "$output" -eq 0 ]
}

@test "install --dry-run --yes as (simulated) root prints would-run lines and touches no apt-get" {
  OMES_TEST=1 OMES_FAKE_ROOT=1 run "$OMES_BIN" install --profile server --dry-run --yes
  [ "$status" -eq 0 ]
  [[ "$output" == *"would run"* ]]
  run grep -c 'apt-get' "$SHIM_LOG"
  [ "$status" -ne 0 ] || [ "$output" -eq 0 ]
}

@test "install --profile server as non-root skips apt-base and exits 0 with the sudo follow-up" {
  run "$OMES_BIN" install --profile server --yes
  [ "$status" -eq 0 ]
  [[ "$output" == *"Run with sudo: sudo omes install --profile server"* ]]
  run grep -c 'apt-get' "$SHIM_LOG"
  [ "$status" -ne 0 ] || [ "$output" -eq 0 ]
}

@test "install --module apt-base as non-root (explicit wrong-scope request) exits 5" {
  run "$OMES_BIN" install --module apt-base --yes
  [ "$status" -eq 5 ]
  run grep -c 'apt-get' "$SHIM_LOG"
  [ "$status" -ne 0 ] || [ "$output" -eq 0 ]
}

@test "install --profile server as (simulated) root installs only missing packages, once" {
  OMES_TEST=1 OMES_FAKE_ROOT=1 run "$OMES_BIN" install --profile server --yes
  [ "$status" -eq 0 ]

  run grep -c 'apt-get install' "$SHIM_LOG"
  [ "$status" -eq 0 ]
  [ "$output" -eq 1 ]

  run grep -q '^module.apt-base.status=applied$' "${OMES_STATE_DIR}/state"
  [ "$status" -eq 0 ]
}

@test "re-running install as (simulated) root is idempotent: no second apt-get install" {
  OMES_TEST=1 OMES_FAKE_ROOT=1 run "$OMES_BIN" install --profile server --yes
  [ "$status" -eq 0 ]

  : >"$SHIM_LOG"

  OMES_TEST=1 OMES_FAKE_ROOT=1 run "$OMES_BIN" install --profile server --yes
  [ "$status" -eq 0 ]

  run grep -c 'apt-get install' "$SHIM_LOG"
  [ "$status" -ne 0 ] || [ "$output" -eq 0 ]

  run grep -q '^module.apt-base.status=applied$' "${OMES_STATE_DIR}/state"
  [ "$status" -eq 0 ]
}

@test "install without --profile or --module is a usage error (exit 2)" {
  run "$OMES_BIN" install --yes
  [ "$status" -eq 2 ]
}
