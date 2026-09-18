#!/usr/bin/env bats
# tests/integration/update.bats - `omes update` integration tests.
#
# `omes update` operates on its OWN checkout via git (fetch + fast-forward
# only), so it must never be exercised against this repository's real
# working tree. Each test builds a throwaway git checkout (a copy of
# bin/+lib/+modules/+profiles) with a LOCAL bare "remote", entirely
# offline-capable except where a test deliberately wants network.

setup() {
  load '../test_helper.bash'
  omes_test_setup

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

  REMOTE_DIR="${OMES_TEST_TMPDIR}/remote.git"
  WORK_DIR="${OMES_TEST_TMPDIR}/work"

  # -b main pins the branch name on both sides regardless of the host's
  # global init.defaultBranch, so nothing downstream depends on guessing
  # or renaming a default branch name.
  git init -q -b main --bare "$REMOTE_DIR"

  mkdir -p "$WORK_DIR"
  cp -a "${OMES_TEST_ROOT}/bin" "${OMES_TEST_ROOT}/lib" "${OMES_TEST_ROOT}/modules" "${OMES_TEST_ROOT}/profiles" "$WORK_DIR/"
  git -C "$WORK_DIR" init -q -b main
  git -C "$WORK_DIR" config user.email "test@example.invalid"
  git -C "$WORK_DIR" config user.name "Test"
  git -C "$WORK_DIR" add -A
  git -C "$WORK_DIR" commit -q -m "initial checkout"
  git -C "$WORK_DIR" remote add origin "$REMOTE_DIR"
  git -C "$WORK_DIR" push -q -u origin main

  WORK_BIN="${WORK_DIR}/bin/omes"
}

teardown() {
  omes_test_teardown
}

@test "update --dry-run makes no git changes and exits 0" {
  local before
  before="$(git -C "$WORK_DIR" rev-parse HEAD)"

  OMES_DRY_RUN=1 run "$WORK_BIN" update
  [ "$status" -eq 0 ]
  [[ "$output" == *"would run"* ]] || [[ "$output" == *"dry-run"* ]]

  local after
  after="$(git -C "$WORK_DIR" rev-parse HEAD)"
  [ "$before" = "$after" ]
}

@test "update refuses a dirty working tree (exit 1, no mutation)" {
  printf '# local edit\n' >> "${WORK_DIR}/bin/omes"

  run "$WORK_BIN" update
  [ "$status" -eq 1 ]
  [[ "$output" == *"local changes"* ]]
}

@test "update refuses a detached HEAD (exit 1, no mutation)" {
  git -C "$WORK_DIR" checkout -q --detach

  run "$WORK_BIN" update
  [ "$status" -eq 1 ]
  [[ "$output" == *"detached"* ]]
}

@test "update fast-forwards from a local remote, then re-runs check" {
  # Simulate an upstream change landing on origin/main via a second clone.
  local other="${OMES_TEST_TMPDIR}/other-clone"
  git clone -q "$REMOTE_DIR" "$other"
  git -C "$other" config user.email "test@example.invalid"
  git -C "$other" config user.name "Test"
  printf 'upstream change\n' > "${other}/CHANGED"
  git -C "$other" add -A
  git -C "$other" commit -q -m "upstream change"
  git -C "$other" push -q origin main

  local before
  before="$(git -C "$WORK_DIR" rev-parse HEAD)"

  run "$WORK_BIN" update
  [ "$status" -eq 0 ]
  [[ "$output" == *"platform:"* ]] || [[ "$output" == *"checks:"* ]]

  local after
  after="$(git -C "$WORK_DIR" rev-parse HEAD)"
  [ "$before" != "$after" ]
  [ -f "${WORK_DIR}/CHANGED" ]
}

@test "update --json on success is check's JSON envelope (command:check)" {
  local other="${OMES_TEST_TMPDIR}/other-clone2"
  git clone -q "$REMOTE_DIR" "$other"
  git -C "$other" config user.email "test@example.invalid"
  git -C "$other" config user.name "Test"
  printf 'x\n' > "${other}/CHANGED2"
  git -C "$other" add -A
  git -C "$other" commit -q -m "another upstream change"
  git -C "$other" push -q origin main

  omes_run_stdout_only "$WORK_BIN" update --json
  [ "$status" -eq 0 ]
  run python3 -c 'import json,sys; d=json.loads(sys.stdin.read()); assert d["command"]=="check"; assert d["exit_code"]==0' <<< "$output"
  [ "$status" -eq 0 ]
}

@test "update fails with exit 8 when network is unavailable" {
  OMES_ASSUME_ONLINE=0 OMES_ASSUME_OFFLINE=1 run "$WORK_BIN" update
  [ "$status" -eq 8 ]
}

@test "update on a non-git checkout exits 1 with a clear message" {
  local plain="${OMES_TEST_TMPDIR}/plain"
  mkdir -p "$plain"
  cp -a "${OMES_TEST_ROOT}/bin" "${OMES_TEST_ROOT}/lib" "${OMES_TEST_ROOT}/modules" "${OMES_TEST_ROOT}/profiles" "$plain/"

  run "${plain}/bin/omes" update
  [ "$status" -eq 1 ]
  [[ "$output" == *"not a git checkout"* ]]
}
