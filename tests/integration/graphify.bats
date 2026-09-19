#!/usr/bin/env bats
# tests/integration/graphify.bats - `omes install/uninstall --module graphify`
# and `omes graphify update/uninstall`, executing bin/omes end to end
# against tests/shims/{graphify,uv,pipx,curl}.
#
# HOME is always overridden to an isolated tmpdir. The graphify module is
# user-scope and must NEVER run as (simulated) root.

setup() {
  load '../test_helper.bash'
  omes_test_setup
  OMES_BIN="${OMES_TEST_ROOT}/bin/omes"

  export HOME="${OMES_TEST_TMPDIR}/home"
  mkdir -p "$HOME"

  unset OMES_GRAPHIFY_VERSION OMES_GRAPHIFY_INSTALLER \
    SHIM_GRAPHIFY_ABSENT SHIM_GRAPHIFY_VERSION SHIM_GRAPHIFY_HELP_EXIT \
    SHIM_UV_EXIT SHIM_PIPX_EXIT || true

  # A real python3 is not guaranteed in every image tests/run.sh falls
  # back to (the bats/bats:latest Alpine image ships none at all) - give
  # module_check a controlled, always-passing python3 >= 3.10, exactly
  # like tests/unit/graphify.bats.
  FAKE_PYTHON_DIR="${OMES_TEST_TMPDIR}/fake-python"
  mkdir -p "$FAKE_PYTHON_DIR"
  cat > "${FAKE_PYTHON_DIR}/python3" <<'EOF'
#!/usr/bin/env bash
printf 'Python 3.12.0\n'
exit 0
EOF
  chmod +x "${FAKE_PYTHON_DIR}/python3"
  export PATH="${FAKE_PYTHON_DIR}:${PATH}"

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

  export SHIM_GRAPHIFY_VERSION="0.9.64"
}

teardown() {
  omes_test_teardown
}

@test "install --module graphify as non-root installs and verifies successfully" {
  run "$OMES_BIN" install --module graphify --yes
  [ "$status" -eq 0 ]
  run grep -c '^uv tool install' "$SHIM_LOG"
  [ "$status" -ne 0 ] || [ "$output" -eq 0 ]
  run grep -c 'pip install' "$SHIM_LOG"
  [ "$status" -ne 0 ] || [ "$output" -eq 0 ]
}

@test "install --module graphify installs graphifyy via uv when not yet installed" {
  # tests/shims/graphify is a static shim: SHIM_GRAPHIFY_ABSENT=1 makes it
  # behave as "not installed" both before AND after `uv tool install` runs
  # (it does not dynamically flip state), so `module_verify` still fails
  # here - the point of this test is only that `omes install` reaches and
  # runs the correct install command, which tests/unit/graphify.bats's
  # "module_apply installs via uv when not yet installed" already proves
  # in isolation without that limitation. Verify's own pass/fail behavior
  # is covered separately (test 1 above).
  export SHIM_GRAPHIFY_ABSENT=1
  run "$OMES_BIN" install --module graphify --yes
  [ "$status" -eq 7 ] # OMES_EX_VERIFY (docs/cli.md Section 2)
  run grep -c '^uv tool install graphifyy$' "$SHIM_LOG"
  [ "$status" -eq 0 ]
  [ "$output" -eq 1 ]
}

@test "install --module graphify as (simulated) root (explicit wrong-scope request) exits 5" {
  OMES_TEST=1 OMES_FAKE_ROOT=1 run "$OMES_BIN" install --module graphify --yes
  [ "$status" -eq 5 ]
}

@test "install --module graphify is not wired into any default profile" {
  run grep -c '^graphify$' "${OMES_TEST_ROOT}/profiles/server.profile"
  [ "$status" -ne 0 ] || [ "$output" -eq 0 ]
  run grep -c '^graphify$' "${OMES_TEST_ROOT}/profiles/desktop.profile"
  [ "$status" -ne 0 ] || [ "$output" -eq 0 ]
  run grep -c '^graphify$' "${OMES_TEST_ROOT}/profiles/hermes.profile"
  [ "$status" -ne 0 ] || [ "$output" -eq 0 ]
}

@test "omes graphify update upgrades via uv and reports JSON" {
  run "$OMES_BIN" graphify update --yes --json
  [ "$status" -eq 0 ]
  run grep -c '^uv tool upgrade graphifyy$' "$SHIM_LOG"
  [ "$status" -eq 0 ]
  [ "$output" -eq 1 ]
}

@test "omes graphify update --json emits exactly one JSON object on stdout" {
  omes_run_stdout_only "$OMES_BIN" graphify update --yes --json
  [ "$status" -eq 0 ]
  run python3 -c "import json,sys; d=json.loads(sys.argv[1]); assert d['command']=='graphify'; assert d['subcommand']=='update'; assert d['ok'] is True" "$output"
  [ "$status" -eq 0 ]
}

@test "omes graphify update --dry-run performs no upgrade call" {
  run "$OMES_BIN" graphify update --dry-run --json
  [ "$status" -eq 0 ]
  run grep -c '^uv tool upgrade' "$SHIM_LOG"
  [ "$status" -ne 0 ] || [ "$output" -eq 0 ]
}

@test "omes graphify update without --yes and no tty is refused" {
  run "$OMES_BIN" graphify update
  [ "$status" -ne 0 ]
  run grep -c '^uv tool upgrade' "$SHIM_LOG"
  [ "$status" -ne 0 ] || [ "$output" -eq 0 ]
}

@test "omes graphify uninstall removes graphifyy via uv and reports JSON" {
  omes_run_stdout_only "$OMES_BIN" graphify uninstall --yes --json
  [ "$status" -eq 0 ]
  run grep -c '^uv tool uninstall graphifyy$' "$SHIM_LOG"
  [ "$status" -eq 0 ]
  [ "$output" -eq 1 ]
  run python3 -c "import json,sys; d=json.loads(sys.argv[1]); assert d['command']=='graphify'; assert d['subcommand']=='uninstall'; assert d['ok'] is True" "$output"
  [ "$status" -eq 0 ]
}

@test "omes graphify uninstall never touches graphify-out or pip" {
  mkdir -p "${OMES_TEST_TMPDIR}/project/graphify-out"
  : > "${OMES_TEST_TMPDIR}/project/graphify-out/graph.json"
  run "$OMES_BIN" graphify uninstall --yes
  [ "$status" -eq 0 ]
  [ -f "${OMES_TEST_TMPDIR}/project/graphify-out/graph.json" ]
  run grep -c 'pip ' "$SHIM_LOG"
  [ "$status" -ne 0 ] || [ "$output" -eq 0 ]
}

@test "omes graphify run is not implemented yet and exits with a usage error" {
  run "$OMES_BIN" graphify run /some/path
  [ "$status" -eq 2 ]
  [[ "$output" == *"not implemented yet"* ]]
  [[ "$output" == *"#51"* ]]
}

@test "omes graphify with no subcommand is a usage error" {
  run "$OMES_BIN" graphify
  [ "$status" -eq 2 ]
}

@test "uninstall --module graphify removes the tool-env install" {
  run "$OMES_BIN" install --module graphify --yes
  [ "$status" -eq 0 ]
  : > "$SHIM_LOG"
  run "$OMES_BIN" uninstall --module graphify --yes
  [ "$status" -eq 0 ]
  run grep -c '^uv tool uninstall graphifyy$' "$SHIM_LOG"
  [ "$status" -eq 0 ]
  [ "$output" -eq 1 ]
}
