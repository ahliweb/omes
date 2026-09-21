#!/usr/bin/env bats
# tests/unit/bootstrap.bats - unit & integration tests for install/bootstrap.sh (issue #169).

setup() {
  load '../test_helper.bash'
  omes_test_setup

  # Mock os-release with supported Ubuntu 24.04 LTS
  OS_RELEASE_FILE="${OMES_TEST_TMPDIR}/os-release"
  cat >"$OS_RELEASE_FILE" <<'EOF'
PRETTY_NAME="Ubuntu 24.04 LTS"
NAME="Ubuntu"
VERSION_ID="24.04"
VERSION_CODENAME=noble
ID=ubuntu
ID_LIKE=debian
UBUNTU_CODENAME=noble
EOF
  export OMES_OS_RELEASE_FILE="$OS_RELEASE_FILE"

  # Build a local bare repository to act as upstream origin
  UPSTREAM_DIR="${OMES_TEST_TMPDIR}/upstream.git"
  git init -q -b main --bare "$UPSTREAM_DIR"

  # Seed upstream with commits and tags
  SEED_DIR="${OMES_TEST_TMPDIR}/seed"
  git init -q -b main "$SEED_DIR"
  git -C "$SEED_DIR" config user.email "test@example.invalid"
  git -C "$SEED_DIR" config user.name "Test Committer"
  mkdir -p "$SEED_DIR/bin"
  cat >"$SEED_DIR/bin/omes" <<'EOF'
#!/usr/bin/env bash
if [[ "${1:-}" == "check" ]]; then
  echo "[omes] check ok"
  exit 0
fi
echo "[omes] mock cli"
exit 0
EOF
  chmod +x "$SEED_DIR/bin/omes"
  git -C "$SEED_DIR" add -A
  git -C "$SEED_DIR" commit -q -m "initial commit"
  git -C "$SEED_DIR" tag -a "v0.3.0" -m "release v0.3.0"
  git -C "$SEED_DIR" commit -q --allow-empty -m "rc commit"
  git -C "$SEED_DIR" tag -a "v0.4.0-rc1" -m "release candidate v0.4.0-rc1"
  git -C "$SEED_DIR" commit -q --allow-empty -m "main edge commit"
  git -C "$SEED_DIR" remote add origin "$UPSTREAM_DIR"
  git -C "$SEED_DIR" push -q --tags origin main

  # Set up isolated install dir and bin dir for bootstrap tests
  INSTALL_DIR="${OMES_TEST_TMPDIR}/install_dir"
  BIN_DIR="${OMES_TEST_TMPDIR}/bin_dir"
  BOOTSTRAP_SCRIPT="${OMES_TEST_ROOT}/install/bootstrap.sh"
}

teardown() {
  omes_test_teardown
}

@test "bootstrap fails on unsupported platform before any mutation" {
  cat >"$OS_RELEASE_FILE" <<'EOF'
NAME="Fedora Linux"
ID=fedora
VERSION_ID="40"
EOF
  run bash "$BOOTSTRAP_SCRIPT" \
    --repo-url "$UPSTREAM_DIR" \
    --install-dir "$INSTALL_DIR" \
    --bin-dir "$BIN_DIR"
  [ "$status" -eq 3 ]
  [[ "$output" == *"unsupported platform"* ]]
  [ ! -d "$INSTALL_DIR" ]
  [ ! -d "$BIN_DIR" ]
}

@test "bootstrap rejects invalid channel name" {
  run bash "$BOOTSTRAP_SCRIPT" \
    --channel "invalid-channel" \
    --repo-url "$UPSTREAM_DIR" \
    --install-dir "$INSTALL_DIR" \
    --bin-dir "$BIN_DIR"
  [ "$status" -eq 2 ]
  [[ "$output" == *"invalid channel"* ]]
}

@test "bootstrap rejects pre-release ref on stable channel" {
  run bash "$BOOTSTRAP_SCRIPT" \
    --channel "stable" \
    --ref "v0.4.0-rc1" \
    --repo-url "$UPSTREAM_DIR" \
    --install-dir "$INSTALL_DIR" \
    --bin-dir "$BIN_DIR"
  [ "$status" -eq 2 ]
  [[ "$output" == *"pre-release candidate ref"* ]]
}

@test "bootstrap rc channel requires explicit candidate ref" {
  run bash "$BOOTSTRAP_SCRIPT" \
    --channel "rc" \
    --repo-url "$UPSTREAM_DIR" \
    --install-dir "$INSTALL_DIR" \
    --bin-dir "$BIN_DIR"
  [ "$status" -eq 2 ]
  [[ "$output" == *"channel 'rc' requires an explicit"* ]]
}

@test "bootstrap rc channel rejects non-rc ref" {
  run bash "$BOOTSTRAP_SCRIPT" \
    --channel "rc" \
    --ref "v0.3.0" \
    --repo-url "$UPSTREAM_DIR" \
    --install-dir "$INSTALL_DIR" \
    --bin-dir "$BIN_DIR"
  [ "$status" -eq 2 ]
  [[ "$output" == *"does not appear to be a pre-release candidate"* ]]
}

@test "fresh stable install defaults to immutable v0.3.0 release tag and records provenance" {
  run bash "$BOOTSTRAP_SCRIPT" \
    --channel "stable" \
    --repo-url "$UPSTREAM_DIR" \
    --install-dir "$INSTALL_DIR" \
    --bin-dir "$BIN_DIR" \
    --skip-check
  [ "$status" -eq 0 ]
  [ -f "${INSTALL_DIR}/.omes-channel.json" ]
  [ -L "${BIN_DIR}/omes" ]

  # Verify git HEAD points to v0.3.0 commit
  expected_sha="$(git --git-dir="$UPSTREAM_DIR" rev-parse "v0.3.0^{commit}")"
  actual_sha="$(git -C "$INSTALL_DIR" rev-parse HEAD)"
  [ "$actual_sha" = "$expected_sha" ]

  # Verify provenance content
  run python3 -c "
import json
with open('${INSTALL_DIR}/.omes-channel.json') as f:
    d = json.load(f)
assert d['channel'] == 'stable'
assert d['requested_ref'] == 'v0.3.0'
assert d['resolved_sha'] == '${expected_sha}'
assert d['is_dirty'] is False
"
  [ "$status" -eq 0 ]
}

@test "RC install checks out candidate ref with pre-release warning" {
  run bash "$BOOTSTRAP_SCRIPT" \
    --channel "rc" \
    --ref "v0.4.0-rc1" \
    --repo-url "$UPSTREAM_DIR" \
    --install-dir "$INSTALL_DIR" \
    --bin-dir "$BIN_DIR" \
    --skip-check
  [ "$status" -eq 0 ]
  [[ "$output" == *"channel 'rc' selected"* ]]

  expected_sha="$(git --git-dir="$UPSTREAM_DIR" rev-parse "v0.4.0-rc1^{commit}")"
  actual_sha="$(git -C "$INSTALL_DIR" rev-parse HEAD)"
  [ "$actual_sha" = "$expected_sha" ]
}

@test "edge channel resolves main to exact commit" {
  run bash "$BOOTSTRAP_SCRIPT" \
    --channel "edge" \
    --repo-url "$UPSTREAM_DIR" \
    --install-dir "$INSTALL_DIR" \
    --bin-dir "$BIN_DIR" \
    --skip-check
  [ "$status" -eq 0 ]
  [[ "$output" == *"channel 'edge' selected"* ]]

  expected_sha="$(git --git-dir="$UPSTREAM_DIR" rev-parse "main^{commit}")"
  actual_sha="$(git -C "$INSTALL_DIR" rev-parse HEAD)"
  [ "$actual_sha" = "$expected_sha" ]
}

@test "dry-run produces no filesystem mutation" {
  run bash "$BOOTSTRAP_SCRIPT" \
    --channel "stable" \
    --repo-url "$UPSTREAM_DIR" \
    --install-dir "$INSTALL_DIR" \
    --bin-dir "$BIN_DIR" \
    --dry-run
  [ "$status" -eq 0 ]
  [[ "$output" == *"[dry-run]"* ]]
  [ ! -d "$INSTALL_DIR" ]
  [ ! -d "$BIN_DIR" ]
}

@test "json output produces valid JSON without leaking secrets" {
  run bash -c 'bash "$1" \
    --channel "stable" \
    --repo-url "$2" \
    --install-dir "$3" \
    --bin-dir "$4" \
    --json \
    --skip-check 2>/dev/null' _ "$BOOTSTRAP_SCRIPT" "$UPSTREAM_DIR" "$INSTALL_DIR" "$BIN_DIR"
  [ "$status" -eq 0 ]

  OMES_TEST_JSON="$output" run python3 -c "
import json, os
d = json.loads(os.environ['OMES_TEST_JSON'])
assert d['channel'] == 'stable'
assert 'resolved_sha' in d
assert 'timestamp_utc' in d
assert d['dry_run'] is False
"
  [ "$status" -eq 0 ]
}

@test "bootstrap detects unexpected origin and aborts unless overridden" {
  # Perform initial install
  run bash "$BOOTSTRAP_SCRIPT" \
    --channel "stable" \
    --repo-url "$UPSTREAM_DIR" \
    --install-dir "$INSTALL_DIR" \
    --bin-dir "$BIN_DIR" \
    --skip-check
  [ "$status" -eq 0 ]

  # Expected url differs from actual upstream
  OTHER_REPO="https://github.com/other-org/omes.git"

  # Subsequent bootstrap expecting OTHER_REPO fails with exit code 4
  run bash "$BOOTSTRAP_SCRIPT" \
    --channel "stable" \
    --repo-url "$OTHER_REPO" \
    --install-dir "$INSTALL_DIR" \
    --bin-dir "$BIN_DIR" \
    --skip-check
  [ "$status" -eq 4 ]
  [[ "$output" == *"does not match expected"* ]]

  # With --allow-unverified-origin, it succeeds because fetch origin uses existing UPSTREAM_DIR
  run bash "$BOOTSTRAP_SCRIPT" \
    --channel "stable" \
    --repo-url "$OTHER_REPO" \
    --install-dir "$INSTALL_DIR" \
    --bin-dir "$BIN_DIR" \
    --allow-unverified-origin \
    --skip-check
  [ "$status" -eq 0 ]
}

@test "bootstrap refuses to overwrite dirty working tree in stable mode" {
  # Perform initial install
  run bash "$BOOTSTRAP_SCRIPT" \
    --channel "stable" \
    --repo-url "$UPSTREAM_DIR" \
    --install-dir "$INSTALL_DIR" \
    --bin-dir "$BIN_DIR" \
    --skip-check
  [ "$status" -eq 0 ]

  # Introduce dirty uncommitted modifications
  echo "dirty modification" >> "${INSTALL_DIR}/bin/omes"

  # Running stable bootstrap again should fail with code 5
  run bash "$BOOTSTRAP_SCRIPT" \
    --channel "stable" \
    --repo-url "$UPSTREAM_DIR" \
    --install-dir "$INSTALL_DIR" \
    --bin-dir "$BIN_DIR" \
    --skip-check
  [ "$status" -eq 5 ]
  [[ "$output" == *"uncommitted modifications"* ]]
}

@test "dev channel preserves dirty working tree without overwriting" {
  # Perform initial install
  run bash "$BOOTSTRAP_SCRIPT" \
    --channel "stable" \
    --repo-url "$UPSTREAM_DIR" \
    --install-dir "$INSTALL_DIR" \
    --bin-dir "$BIN_DIR" \
    --skip-check
  [ "$status" -eq 0 ]

  # Introduce local modifications
  echo "local operator edit" >> "${INSTALL_DIR}/bin/omes"

  # Dev mode preserves it and exits 0
  run bash "$BOOTSTRAP_SCRIPT" \
    --channel "dev" \
    --repo-url "$UPSTREAM_DIR" \
    --install-dir "$INSTALL_DIR" \
    --bin-dir "$BIN_DIR" \
    --skip-check
  [ "$status" -eq 0 ]
  [[ "$output" == *"preserving dev checkout without updating"* ]]
  grep -q "local operator edit" "${INSTALL_DIR}/bin/omes"
}

@test "repeated bootstrap runs are idempotent" {
  run bash "$BOOTSTRAP_SCRIPT" \
    --channel "stable" \
    --repo-url "$UPSTREAM_DIR" \
    --install-dir "$INSTALL_DIR" \
    --bin-dir "$BIN_DIR" \
    --skip-check
  [ "$status" -eq 0 ]

  run bash "$BOOTSTRAP_SCRIPT" \
    --channel "stable" \
    --repo-url "$UPSTREAM_DIR" \
    --install-dir "$INSTALL_DIR" \
    --bin-dir "$BIN_DIR" \
    --skip-check
  [ "$status" -eq 0 ]
  [ -L "${BIN_DIR}/omes" ]
}

@test "bootstrap fails on non-existent tag or broken fetch" {
  run bash "$BOOTSTRAP_SCRIPT" \
    --channel "stable" \
    --ref "v999.999.999" \
    --repo-url "$UPSTREAM_DIR" \
    --install-dir "$INSTALL_DIR" \
    --bin-dir "$BIN_DIR" \
    --skip-check
  [ "$status" -ne 0 ]
}
