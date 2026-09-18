#!/usr/bin/env bats
# tests/integration/dr-corrupted-state.bats - DR scenario (e), issue #17:
# the state file itself is corrupted (not a backup's MANIFEST - the small
# key=value file at <state-dir>/state that records what OMES has
# applied). `omes status` should fail safe with a clear message, and
# `omes restore` should still work from the backup directory alone (it
# never reads the state file at all - lib/omes/restore.sh's
# backup_target_dir/backup_list only ever list <state-dir>/backups/
# directly).
#
# KNOWN LIMITATION, tested and documented (not silently assumed): `omes
# status` does NOT "fail safe with a clear message" today - state_get/
# state_list_modules (lib/omes/state.sh) do line-by-line best-effort
# parsing of the state file with no corruption detection at all. Garbage
# bytes that don't match the `key=value` shape are silently skipped
# (state_list_modules only matches lines against a `module.*.*` key
# pattern), so `omes status` exits 0 and simply under-reports (fewer or
# no modules listed) rather than surfacing an explicit "state file is
# corrupted" error. See docs/disaster-recovery.md's scenario (e) runbook
# for the operator-facing consequence and a suggested follow-up issue
# (an explicit state-file integrity check, analogous to
# backup_manifest_validate for a MANIFEST) - out of this issue's file
# scope (lib/omes/state.sh) to add.

setup() {
  load '../test_helper.bash'
  omes_test_setup
  OMES_BIN="${OMES_TEST_ROOT}/bin/omes"

  export HOME="${OMES_TEST_TMPDIR}/home"
  mkdir -p "$HOME"
  unset XDG_DATA_HOME XDG_SESSION_TYPE || true
  export OMES_HERMES_HOME="${HOME}/.hermes"

  OMES_OS_RELEASE_FILE="$(omes_fixture_path os-release)"
  cat > "$OMES_OS_RELEASE_FILE" << 'EOF'
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

  printf '# my custom prompt\n' > "${HOME}/.bashrc"
  run "$OMES_BIN" install --module hermes --yes
  [ "$status" -eq 0 ]
}

teardown() {
  omes_test_teardown
}

@test "omes status on a corrupted (binary garbage) state file does not crash, but does not surface a clear error either (known limitation - see header)" {
  printf '\x00\x01\x02garbage-not-key-value-lines\xff\xfe' > "${OMES_STATE_DIR}/state"

  run "$OMES_BIN" status --json
  # Documents the actual behavior: exits 0 (status "never fails on its
  # own" per docs/cli.md) and simply reports no modules, rather than an
  # explicit "state file corrupted" error - this is the gap, not a pass.
  [ "$status" -eq 0 ]
  run python3 -c 'import json,sys; d=json.loads(sys.argv[1]); sys.exit(0 if d["modules"]==[] else 1)' "$output"
  [ "$status" -eq 0 ]
}

@test "omes status on a DELETED state file/state dir does not crash" {
  rm -rf "${OMES_STATE_DIR}/state"

  run "$OMES_BIN" status
  [ "$status" -eq 0 ]
  [[ "$output" == *"modules: none applied yet"* ]] || [[ "$output" == *"modules:"* ]]
}

@test "omes restore still works from the backup directory alone, even with the state file deleted entirely" {
  omes_run_stdout_only "$OMES_BIN" restore --list --json
  [ "$status" -eq 0 ]
  local ts
  ts="$(python3 -c 'import json,sys; d=json.loads(sys.argv[1]); ms=[b["timestamp"] for b in d["backups"] if b["module"]=="hermes"]; print(ms[0] if ms else "")' "$output")"
  [ -n "$ts" ]

  rm -f "${OMES_STATE_DIR}/state"
  printf 'CORRUPTED\n' >> "${HOME}/.bashrc"

  run "$OMES_BIN" restore --from "$ts" --yes
  [ "$status" -eq 0 ]

  # See dr-corrupted-config.bats: this session captured .bashrc's content
  # from BEFORE hermes ever touched it, so restoring it returns the
  # original pre-OMES content, not a "with marker" state.
  run cat "${HOME}/.bashrc"
  [ "$output" = "# my custom prompt" ]
  run grep -c 'CORRUPTED' "${HOME}/.bashrc"
  [ "$status" -ne 0 ] || [ "$output" -eq 0 ]
}

@test "omes restore --list still works with the state file deleted (it reads the backups/ directory only)" {
  rm -f "${OMES_STATE_DIR}/state"

  run "$OMES_BIN" restore --list
  [ "$status" -eq 0 ]
  [[ "$output" == *"hermes"* ]]
}
