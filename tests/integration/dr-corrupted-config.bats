#!/usr/bin/env bats
# tests/integration/dr-corrupted-config.bats - DR scenario (d), issue #17:
# a managed file gets truncated/garbled by something other than OMES.
# `omes restore --from <ts>` repairs it, and `omes restore` refuses a
# corrupt MANIFEST outright with exit 9 (mirroring
# tests/unit/restore.bats/tests/integration/restore.bats's existing
# coverage, added here too as an explicit DR-scenario regression test per
# issue #17).
#
# KNOWN GAP, tested and documented (not silently assumed): `omes doctor`
# has NO checksum-based corruption check today - lib/omes/module.sh's
# module_verify hooks only assert FUNCTIONAL behavior (a package present,
# a unit active, a binary on PATH), never "does this managed file's
# content match what OMES last wrote." A managed file silently
# truncated/garbled by an operator or another tool is NOT flagged by
# `omes doctor` unless that corruption happens to also break the owning
# module's functional check. See docs/disaster-recovery.md's scenario (d)
# runbook and docs/testing.md's "Known gaps" for the suggested follow-up
# (a lib/omes/module.sh doctor-level checksum check against the latest
# backup's MANIFEST) - out of this issue's file scope to add.

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

@test "a managed file, truncated/garbled outside OMES, is not flagged by omes doctor (known gap - see header)" {
  # Simulate external corruption: not something OMES itself did.
  printf '\x00\x01GARBLED\x02' > "${HOME}/.bashrc"

  run "$OMES_BIN" doctor --json
  # hermes's own module_verify (hermes --version / hermes doctor via the
  # shim) does not read .bashrc at all, so this corruption is invisible
  # to it - doctor exits 0 (no FAIL), confirming the gap documented above
  # rather than asserting a check that does not exist.
  [ "$status" -eq 0 ]
}

@test "omes restore --from <ts> repairs the corrupted file" {
  printf '\x00\x01GARBLED\x02' > "${HOME}/.bashrc"

  omes_run_stdout_only "$OMES_BIN" restore --list --json
  [ "$status" -eq 0 ]
  local ts
  ts="$(python3 -c 'import json,sys; d=json.loads(sys.argv[1]); ms=[b["timestamp"] for b in d["backups"] if b["module"]=="hermes"]; print(ms[0] if ms else "")' "$output")"
  [ -n "$ts" ]

  run "$OMES_BIN" restore --from "$ts" --yes
  [ "$status" -eq 0 ]

  # hermes's pre-apply backup captured .bashrc's content as it was BEFORE
  # hermes ever touched it (omes_manage_path backs up, then the marker is
  # appended) - so restoring this session returns the ORIGINAL, pre-OMES
  # content, not a "with marker" state. That is still exactly the repair
  # this scenario is about: the corruption is gone, replaced by the most
  # relevant backup this module ever took.
  run cat "${HOME}/.bashrc"
  [ "$output" = "# my custom prompt" ]
  run grep -c 'GARBLED' "${HOME}/.bashrc"
  [ "$status" -ne 0 ] || [ "$output" -eq 0 ]
}

@test "omes restore refuses a corrupt MANIFEST outright, exit 9, leaves the managed file untouched" {
  omes_run_stdout_only "$OMES_BIN" restore --list --json
  local ts
  ts="$(python3 -c 'import json,sys; d=json.loads(sys.argv[1]); ms=[b["timestamp"] for b in d["backups"] if b["module"]=="hermes"]; print(ms[0] if ms else "")' "$output")"
  [ -n "$ts" ]

  printf 'not a valid manifest line\n' > "${OMES_STATE_DIR}/backups/${ts}/MANIFEST"

  local before
  before="$(cat "${HOME}/.bashrc")"

  run "$OMES_BIN" restore --from "$ts" --yes
  [ "$status" -eq 9 ]
  [[ "$output" == *"corrupt"* ]] || [[ "$output" == *"MANIFEST"* ]]

  run cat "${HOME}/.bashrc"
  [ "$output" = "$before" ]
}
