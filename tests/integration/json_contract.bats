#!/usr/bin/env bats
# tests/integration/json_contract.bats - proves EVERY `omes` command's
# --json output is exactly one JSON object on stdout (docs/cli.md Section 3
# / docs/architecture.md Section 8.2), with all logging kept off stdout,
# by piping ONLY stdout (never bats' merged stdout+stderr $output) through
# `python3 -m json.tool`.
#
# Deliberately exercises commands both on a fresh state dir (never
# applied) and after a real apt-base install (simulated root), so the
# assertion covers the richer, log-heavy code paths too, not just the
# empty-state fast paths.

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

# _assert_json_stdout <command...>
# Runs <command...>, asserts stdout alone parses under `python3 -m
# json.tool` (fails the test right here if not), and records the
# command's own exit status in $CMD_EXIT for the caller to additionally
# assert if it wants to. Always itself returns the json.tool assertion's
# own status (0 on valid JSON) - deliberately decoupled from the tested
# command's exit code, since a plain (non-`run`) nonzero return from a
# bats test-body function fails the test immediately, which would make
# every non-success command under test (usage errors, preflight
# failures, etc.) look like a broken assertion instead of the expected
# outcome being validated.
_assert_json_stdout() {
  omes_run_stdout_only "$@"
  CMD_EXIT="$status"
  run python3 -m json.tool <<< "$output"
  [ "$status" -eq 0 ]
}

@test "version --json parses as JSON on stdout" {
  _assert_json_stdout "$OMES_BIN" version --json
}

@test "help --json parses as JSON on stdout" {
  _assert_json_stdout "$OMES_BIN" help --json
}

@test "check --json parses as JSON on stdout" {
  _assert_json_stdout "$OMES_BIN" check --json
}

@test "check --profile server --json parses as JSON on stdout (non-root: skipped-module log path)" {
  _assert_json_stdout "$OMES_BIN" check --profile server --json
}

@test "modules --json parses as JSON on stdout" {
  _assert_json_stdout "$OMES_BIN" modules --json
}

@test "status --json parses as JSON on stdout (fresh state)" {
  _assert_json_stdout "$OMES_BIN" status --json
}

@test "doctor --json parses as JSON on stdout (fresh state)" {
  _assert_json_stdout "$OMES_BIN" doctor --json
}

@test "backup --json parses as JSON on stdout (nothing to back up)" {
  _assert_json_stdout "$OMES_BIN" backup --json
}

@test "restore --list --json parses as JSON on stdout (no backups yet)" {
  _assert_json_stdout "$OMES_BIN" restore --list --json
}

@test "restore --json parses as JSON on stdout (no backups yet, ok:false)" {
  _assert_json_stdout "$OMES_BIN" restore --yes --json
  [ "$CMD_EXIT" -eq 9 ]
}

@test "uninstall --json parses as JSON on stdout (nothing applied)" {
  _assert_json_stdout "$OMES_BIN" uninstall --yes --json
}

@test "install --json parses as JSON on stdout (usage error path, ok:false)" {
  _assert_json_stdout "$OMES_BIN" install --json
  [ "$CMD_EXIT" -eq 2 ]
}

@test "install --profile server --dry-run --json parses as JSON on stdout as non-root" {
  _assert_json_stdout "$OMES_BIN" install --profile server --dry-run --yes --json
}

@test "install --profile server --json parses as JSON on stdout as (simulated) root - the log-heaviest path" {
  OMES_TEST=1 OMES_FAKE_ROOT=1 _assert_json_stdout "$OMES_BIN" install --profile server --yes --json
}

@test "doctor --json parses as JSON on stdout after a real apt-base install (verified module path)" {
  OMES_TEST=1 OMES_FAKE_ROOT=1 "$OMES_BIN" install --profile server --yes >/dev/null
  OMES_TEST=1 OMES_FAKE_ROOT=1 _assert_json_stdout "$OMES_BIN" doctor --json
}

@test "status --json parses as JSON on stdout after a real apt-base install" {
  OMES_TEST=1 OMES_FAKE_ROOT=1 "$OMES_BIN" install --profile server --yes >/dev/null
  _assert_json_stdout "$OMES_BIN" status --json
}

@test "backup --json parses as JSON on stdout after a real install (files_backed_up > 0 path possible)" {
  OMES_TEST=1 OMES_FAKE_ROOT=1 "$OMES_BIN" install --profile server --yes >/dev/null
  _assert_json_stdout "$OMES_BIN" backup --json
}

@test "uninstall --json parses as JSON on stdout after a real install (log-heavy rollback path)" {
  OMES_TEST=1 OMES_FAKE_ROOT=1 "$OMES_BIN" install --profile server --yes >/dev/null
  OMES_TEST=1 OMES_FAKE_ROOT=1 _assert_json_stdout "$OMES_BIN" uninstall --module apt-base --yes --json
}

@test "uninstall --purge-packages --json parses as JSON on stdout (apt-get remove log path)" {
  OMES_TEST=1 OMES_FAKE_ROOT=1 "$OMES_BIN" install --profile server --yes >/dev/null
  OMES_TEST=1 OMES_FAKE_ROOT=1 _assert_json_stdout "$OMES_BIN" uninstall --module apt-base --purge-packages --yes --json
}

@test "update --dry-run --json parses as JSON on stdout" {
  local work="${OMES_TEST_TMPDIR}/update-json-work"
  mkdir -p "$work"
  cp -a "${OMES_TEST_ROOT}/bin" "${OMES_TEST_ROOT}/lib" "${OMES_TEST_ROOT}/modules" "${OMES_TEST_ROOT}/profiles" "$work/"
  git -C "$work" init -q -b main
  git -C "$work" config user.email "test@example.invalid"
  git -C "$work" config user.name "Test"
  git -C "$work" add -A
  git -C "$work" commit -q -m init

  _assert_json_stdout "${work}/bin/omes" update --dry-run --json
}
