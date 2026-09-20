#!/usr/bin/env bats
# tests/integration/dr-failed-install.bats - DR scenario (a), issue #17:
# a failed installation mid-profile leaves the failed module unrecorded
# (see the "known gap" note below), `omes restore --from <timestamp>`
# returns an earlier module's managed file to its pre-install content, and
# re-running `omes install` afterward converges (both modules end up
# applied).
#
# Uses the `hermes` profile (hermes -> hermes-gateway, MODULE_REQUIRES
# ordered) against tests/shims/, exactly like tests/integration/
# hermes-gateway.bats's setup.

setup() {
  load '../test_helper.bash'
  omes_test_setup
  OMES_BIN="${OMES_TEST_ROOT}/bin/omes"

  export HOME="${OMES_TEST_TMPDIR}/home"
  mkdir -p "$HOME"
  unset XDG_DATA_HOME XDG_SESSION_TYPE || true
  export OMES_HERMES_HOME="${HOME}/.hermes"

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

  export SHIM_HERMES_VERSION="1.2.3"
  export SHIM_USER_ENABLED_FILE="${OMES_TEST_TMPDIR}/user-enabled"
  export SHIM_USER_ACTIVE_FILE="${OMES_TEST_TMPDIR}/user-active"

  # A pre-existing user file the `hermes` module manages (appends its PATH
  # marker block to) - this is what gets backed up before hermes writes to
  # it, and what `omes restore` will return to this exact content.
  printf '# my custom prompt\n' >"${HOME}/.bashrc"
}

teardown() {
  omes_test_teardown
}

@test "a mid-profile failure (hermes ok, hermes-gateway fails apply) exits 6, names hermes-gateway, and leaves hermes applied" {
  export SHIM_HERMES_GATEWAY_INSTALL_EXIT=1

  run "$OMES_BIN" install --profile hermes --yes
  [ "$status" -eq 6 ]
  [[ "$output" == *"hermes-gateway"* ]]

  run grep -q '^module.hermes.status=applied$' "${OMES_STATE_DIR}/state"
  [ "$status" -eq 0 ]

  # KNOWN GAP (docs/testing.md / docs/disaster-recovery.md): run_apply
  # (lib/omes/module.sh) never writes a "failed" status - a module whose
  # apply failed simply has no module.<name>.status key at all, rather
  # than an explicit failed marker. Documented, not silently assumed.
  run grep -c '^module.hermes-gateway.status=' "${OMES_STATE_DIR}/state"
  [ "$status" -ne 0 ] || [ "$output" -eq 0 ]

  # hermes's own PATH marker WAS written to .bashrc before hermes-gateway
  # ever ran (module_apply for hermes completed and was verified first).
  run grep -c 'BEGIN OMES hermes PATH' "${HOME}/.bashrc"
  [ "$status" -eq 0 ]
  [ "$output" -eq 1 ]
}

@test "omes restore --from <hermes's pre-apply timestamp> returns .bashrc to its pre-install content" {
  export SHIM_HERMES_GATEWAY_INSTALL_EXIT=1
  run "$OMES_BIN" install --profile hermes --yes
  [ "$status" -eq 6 ]

  # Find hermes's own pre-apply backup session (the earlier of the two
  # sessions this failed run created - hermes's succeeded and actually
  # captured .bashrc; hermes-gateway's own pre-apply session opened and
  # closed around its failed apply, capturing nothing, per
  # lib/omes/module.sh's run_apply).
  omes_run_stdout_only "$OMES_BIN" restore --list --json
  [ "$status" -eq 0 ]
  local hermes_ts
  hermes_ts="$(python3 -c 'import json,sys; d=json.loads(sys.argv[1]); ms=[b["timestamp"] for b in d["backups"] if b["module"]=="hermes"]; print(ms[0] if ms else "")' "$output")"
  [ -n "$hermes_ts" ]

  run "$OMES_BIN" restore --from "$hermes_ts" --yes
  [ "$status" -eq 0 ]

  run cat "${HOME}/.bashrc"
  [ "$output" = "# my custom prompt" ]
}

@test "re-running install after the fix converges: both modules end up applied, marker restored exactly once" {
  export SHIM_HERMES_GATEWAY_INSTALL_EXIT=1
  run "$OMES_BIN" install --profile hermes --yes
  [ "$status" -eq 6 ]

  omes_run_stdout_only "$OMES_BIN" restore --list --json
  local hermes_ts
  hermes_ts="$(python3 -c 'import json,sys; d=json.loads(sys.argv[1]); ms=[b["timestamp"] for b in d["backups"] if b["module"]=="hermes"]; print(ms[0] if ms else "")' "$output")"
  run "$OMES_BIN" restore --from "$hermes_ts" --yes
  [ "$status" -eq 0 ]

  # Simulate the underlying problem being fixed, then re-install.
  unset SHIM_HERMES_GATEWAY_INSTALL_EXIT
  run "$OMES_BIN" install --profile hermes --yes
  [ "$status" -eq 0 ]

  run grep -q '^module.hermes.status=applied$' "${OMES_STATE_DIR}/state"
  [ "$status" -eq 0 ]
  run grep -q '^module.hermes-gateway.status=applied$' "${OMES_STATE_DIR}/state"
  [ "$status" -eq 0 ]

  run grep -c 'BEGIN OMES hermes PATH' "${HOME}/.bashrc"
  [ "$status" -eq 0 ]
  [ "$output" -eq 1 ]
}
