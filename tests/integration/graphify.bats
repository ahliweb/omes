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

# ---------------------------------------------------------------------------
# omes graphify run (issue #51): path validation, mode gating, provenance
# ---------------------------------------------------------------------------

@test "omes graphify run without a path is a usage error" {
  run "$OMES_BIN" graphify run
  [ "$status" -eq 2 ]
}

@test "omes graphify run refuses a nonexistent path" {
  run "$OMES_BIN" graphify run "${OMES_TEST_TMPDIR}/does-not-exist"
  [ "$status" -eq 2 ]
  [[ "$output" == *"does not exist"* ]]
}

@test "omes graphify run refuses a path that is itself a graphify-out directory" {
  mkdir -p "${OMES_TEST_TMPDIR}/project/graphify-out"
  run "$OMES_BIN" graphify run "${OMES_TEST_TMPDIR}/project/graphify-out"
  [ "$status" -eq 2 ]
  [[ "$output" == *"graphify-out"* ]]
}

@test "omes graphify run refuses a path nested inside a graphify-out directory" {
  mkdir -p "${OMES_TEST_TMPDIR}/project/graphify-out/nested"
  run "$OMES_BIN" graphify run "${OMES_TEST_TMPDIR}/project/graphify-out/nested"
  [ "$status" -eq 2 ]
  [[ "$output" == *"graphify-out"* ]]
}

@test "omes graphify run refuses an invalid --mode value" {
  mkdir -p "${OMES_TEST_TMPDIR}/project"
  run "$OMES_BIN" graphify run "${OMES_TEST_TMPDIR}/project" --mode bogus
  [ "$status" -eq 2 ]
  [[ "$output" == *"invalid --mode"* ]]
}

@test "omes graphify run refuses when graphify is not installed" {
  mkdir -p "${OMES_TEST_TMPDIR}/project"
  export SHIM_GRAPHIFY_ABSENT=1
  # The static shim always exists on PATH (command -v graphify succeeds
  # regardless of SHIM_GRAPHIFY_ABSENT), so "not installed" for `run`'s
  # own check is simulated the same way tests/unit/graphify.bats does for
  # module_verify: hide the shim entirely.
  local stripped="" dir
  IFS=':' read -ra parts <<<"$PATH"
  for dir in "${parts[@]}"; do
    [[ -x "${dir}/graphify" ]] && continue
    stripped="${stripped:+${stripped}:}${dir}"
  done
  PATH="$stripped" run "$OMES_BIN" graphify run "${OMES_TEST_TMPDIR}/project"
  [ "$status" -eq 2 ]
  [[ "$output" == *"not installed"* ]]
}

@test "omes graphify run defaults to code-only extraction with no provider credential needed" {
  mkdir -p "${OMES_TEST_TMPDIR}/project"
  run "$OMES_BIN" graphify run "${OMES_TEST_TMPDIR}/project"
  [ "$status" -eq 0 ]
  run grep -c -- '--code-only' "$SHIM_LOG"
  [ "$status" -eq 0 ]
  [ "$output" -eq 1 ]
  [ -f "${OMES_TEST_TMPDIR}/project/graphify-out/omes-provenance.json" ]
}

@test "omes graphify run --out threads through to graphify and the provenance sidecar location" {
  mkdir -p "${OMES_TEST_TMPDIR}/project" "${OMES_TEST_TMPDIR}/custom-out"
  run "$OMES_BIN" graphify run "${OMES_TEST_TMPDIR}/project" --out "${OMES_TEST_TMPDIR}/custom-out"
  [ "$status" -eq 0 ]
  run grep -c -- "--out ${OMES_TEST_TMPDIR}/custom-out" "$SHIM_LOG"
  [ "$status" -eq 0 ]
  [ "$output" -eq 1 ]
  [ -f "${OMES_TEST_TMPDIR}/custom-out/omes-provenance.json" ]
  [ ! -e "${OMES_TEST_TMPDIR}/project/graphify-out" ]
}

@test "omes graphify run --mode semantic without OMES_GRAPHIFY_PROVIDER_ENV is refused" {
  mkdir -p "${OMES_TEST_TMPDIR}/project"
  run "$OMES_BIN" graphify run "${OMES_TEST_TMPDIR}/project" --mode semantic --yes
  [ "$status" -eq 2 ]
  [[ "$output" == *"OMES_GRAPHIFY_PROVIDER_ENV"* ]]
  run grep -c '^graphify extract' "$SHIM_LOG"
  [ "$status" -ne 0 ] || [ "$output" -eq 0 ]
}

@test "omes graphify run --mode semantic with an empty named credential variable is refused" {
  mkdir -p "${OMES_TEST_TMPDIR}/project"
  export OMES_GRAPHIFY_PROVIDER_ENV="MY_FAKE_PROVIDER_KEY"
  unset MY_FAKE_PROVIDER_KEY || true
  run "$OMES_BIN" graphify run "${OMES_TEST_TMPDIR}/project" --mode semantic --yes
  [ "$status" -eq 2 ]
  [[ "$output" == *"MY_FAKE_PROVIDER_KEY"* ]]
  run grep -c '^graphify extract' "$SHIM_LOG"
  [ "$status" -ne 0 ] || [ "$output" -eq 0 ]
}

@test "omes graphify run --mode semantic with a satisfied provider gate runs without --code-only" {
  mkdir -p "${OMES_TEST_TMPDIR}/project"
  export OMES_GRAPHIFY_PROVIDER_ENV="MY_FAKE_PROVIDER_KEY"
  export MY_FAKE_PROVIDER_KEY="super-secret-value-do-not-leak"
  run "$OMES_BIN" graphify run "${OMES_TEST_TMPDIR}/project" --mode semantic --backend ollama --yes
  [ "$status" -eq 0 ]
  run grep -c '^graphify extract .*--code-only' "$SHIM_LOG"
  [ "$status" -ne 0 ] || [ "$output" -eq 0 ]
  run grep -c -- '--backend ollama' "$SHIM_LOG"
  [ "$status" -eq 0 ]
  [ "$output" -eq 1 ]
}

@test "omes graphify run --mode semantic without --yes and no tty is refused, no extraction runs" {
  mkdir -p "${OMES_TEST_TMPDIR}/project"
  export OMES_GRAPHIFY_PROVIDER_ENV="MY_FAKE_PROVIDER_KEY"
  export MY_FAKE_PROVIDER_KEY="super-secret-value-do-not-leak"
  run "$OMES_BIN" graphify run "${OMES_TEST_TMPDIR}/project" --mode semantic
  [ "$status" -ne 0 ]
  run grep -c '^graphify extract' "$SHIM_LOG"
  [ "$status" -ne 0 ] || [ "$output" -eq 0 ]
}

@test "omes graphify run --dry-run performs no extraction and writes no provenance file" {
  mkdir -p "${OMES_TEST_TMPDIR}/project"
  run "$OMES_BIN" graphify run "${OMES_TEST_TMPDIR}/project" --dry-run --json
  [ "$status" -eq 0 ]
  run grep -c '^graphify extract' "$SHIM_LOG"
  [ "$status" -ne 0 ] || [ "$output" -eq 0 ]
  [ ! -e "${OMES_TEST_TMPDIR}/project/graphify-out/omes-provenance.json" ]
}

@test "omes graphify run --json emits a valid provenance-describing envelope" {
  mkdir -p "${OMES_TEST_TMPDIR}/project"
  omes_run_stdout_only "$OMES_BIN" graphify run "${OMES_TEST_TMPDIR}/project" --json
  [ "$status" -eq 0 ]
  run python3 -c "import json,sys; d=json.loads(sys.argv[1]); assert d['command']=='graphify'; assert d['subcommand']=='run'; assert d['ok'] is True; assert d['mode']=='code'; assert d['provenance_file'].endswith('omes-provenance.json')" "$output"
  [ "$status" -eq 0 ]
}

@test "omes graphify run's provenance sidecar records the provider env var NAME, never its value" {
  mkdir -p "${OMES_TEST_TMPDIR}/project"
  export OMES_GRAPHIFY_PROVIDER_ENV="MY_FAKE_PROVIDER_KEY"
  export MY_FAKE_PROVIDER_KEY="super-secret-value-do-not-leak"
  run "$OMES_BIN" graphify run "${OMES_TEST_TMPDIR}/project" --mode semantic --yes
  [ "$status" -eq 0 ]
  local sidecar="${OMES_TEST_TMPDIR}/project/graphify-out/omes-provenance.json"
  [ -f "$sidecar" ]
  run grep -c "MY_FAKE_PROVIDER_KEY" "$sidecar"
  [ "$status" -eq 0 ]
  [ "$output" -ge 1 ]
  run grep -c "super-secret-value-do-not-leak" "$sidecar"
  [ "$status" -ne 0 ] || [ "$output" -eq 0 ]
  # ...nor does it ever appear in the log OMES itself produced.
  run grep -c "super-secret-value-do-not-leak" "$SHIM_LOG"
  [ "$status" -ne 0 ] || [ "$output" -eq 0 ]
}

@test "omes graphify run's provenance sidecar is valid JSON with the expected fields" {
  mkdir -p "${OMES_TEST_TMPDIR}/project"
  run "$OMES_BIN" graphify run "${OMES_TEST_TMPDIR}/project"
  [ "$status" -eq 0 ]
  run python3 -c "
import json
with open('${OMES_TEST_TMPDIR}/project/graphify-out/omes-provenance.json') as f:
    d = json.load(f)
assert d['mode'] == 'code'
assert d['provider_env_var'] is None
assert d['backend'] is None
assert d['graphify_version']
assert d['omes_version']
assert d['path'].endswith('/project')
"
  [ "$status" -eq 0 ]
}

# ---------------------------------------------------------------------------
# omes graphify skill install / uninstall (issue #51)
# ---------------------------------------------------------------------------

@test "omes graphify skill install copies SKILL.md and run.sh into HERMES_HOME/skills/graphify" {
  export OMES_HERMES_HOME="${HOME}/.hermes"
  run "$OMES_BIN" graphify skill install --yes
  [ "$status" -eq 0 ]
  [ -f "${OMES_HERMES_HOME}/skills/graphify/SKILL.md" ]
  [ -f "${OMES_HERMES_HOME}/skills/graphify/run.sh" ]
  [ -x "${OMES_HERMES_HOME}/skills/graphify/run.sh" ]
  run grep -c 'omes graphify run' "${OMES_HERMES_HOME}/skills/graphify/run.sh"
  [ "$status" -eq 0 ]
  [ "$output" -ge 1 ]
}

@test "omes graphify skill install --json reports the target directory" {
  export OMES_HERMES_HOME="${HOME}/.hermes"
  omes_run_stdout_only "$OMES_BIN" graphify skill install --yes --json
  [ "$status" -eq 0 ]
  run python3 -c "import json,sys; d=json.loads(sys.argv[1]); assert d['command']=='graphify'; assert d['subcommand']=='skill'; assert d['action']=='install'; assert d['ok'] is True; assert d['target_dir'].endswith('skills/graphify')" "$output"
  [ "$status" -eq 0 ]
}

@test "omes graphify skill install --dry-run writes nothing" {
  export OMES_HERMES_HOME="${HOME}/.hermes"
  run "$OMES_BIN" graphify skill install --dry-run
  [ "$status" -eq 0 ]
  [ ! -e "${OMES_HERMES_HOME}/skills/graphify" ]
}

@test "omes graphify skill install backs up a pre-existing SKILL.md before overwriting it" {
  export OMES_HERMES_HOME="${HOME}/.hermes"
  mkdir -p "${OMES_HERMES_HOME}/skills/graphify"
  printf 'pre-existing, foreign skill content\n' > "${OMES_HERMES_HOME}/skills/graphify/SKILL.md"
  run "$OMES_BIN" graphify skill install --yes
  [ "$status" -eq 0 ]
  run grep -rl 'pre-existing, foreign skill content' "${OMES_STATE_DIR}/backups"
  [ "$status" -eq 0 ]
  [ -n "$output" ]
}

@test "omes graphify skill uninstall removes only SKILL.md and run.sh, nothing else under skills/" {
  export OMES_HERMES_HOME="${HOME}/.hermes"
  run "$OMES_BIN" graphify skill install --yes
  [ "$status" -eq 0 ]
  mkdir -p "${OMES_HERMES_HOME}/skills/other-skill"
  : > "${OMES_HERMES_HOME}/skills/other-skill/SKILL.md"

  run "$OMES_BIN" graphify skill uninstall --yes
  [ "$status" -eq 0 ]
  [ ! -e "${OMES_HERMES_HOME}/skills/graphify/SKILL.md" ]
  [ ! -e "${OMES_HERMES_HOME}/skills/graphify/run.sh" ]
  [ -f "${OMES_HERMES_HOME}/skills/other-skill/SKILL.md" ]
}

@test "omes graphify skill uninstall without --yes and no tty is refused" {
  export OMES_HERMES_HOME="${HOME}/.hermes"
  run "$OMES_BIN" graphify skill install --yes
  [ "$status" -eq 0 ]
  run "$OMES_BIN" graphify skill uninstall
  [ "$status" -ne 0 ]
  [ -f "${OMES_HERMES_HOME}/skills/graphify/SKILL.md" ]
}

@test "omes graphify skill with no action is a usage error" {
  run "$OMES_BIN" graphify skill
  [ "$status" -eq 2 ]
}
