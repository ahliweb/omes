#!/usr/bin/env bats
# tests/integration/uninstall.bats - `omes uninstall` integration tests.
#
# apt-base (root-scope, simulated via OMES_TEST=1/OMES_FAKE_ROOT=1) is the
# exercising module for the package-purge policy tests, since it is the
# only real module in this repository so far and has no filesystem
# managed_paths of its own (module_rollback_managed_paths is a no-op for
# it, so these tests isolate the package-purge behavior cleanly). The
# rollback-failure (exit 10) test builds a throwaway copy of bin/+lib/
# alongside a synthetic module, since that scenario needs a module whose
# module_rollback deliberately fails.

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

  # The server profile now also includes the root-scope `security-baseline`
  # module (#7) - see tests/integration/install.bats for the identical
  # rationale/pattern.
  export OMES_ETC_DIR="${OMES_TEST_TMPDIR}/etc"
  export SHIM_UFW_STATE_FILE="${OMES_TEST_TMPDIR}/ufw-state"
  printf 'ufw\nunattended-upgrades\n' >>"$SHIM_INSTALLED_PKGS_FILE"
}

teardown() {
  omes_test_teardown
}

_install_apt_base() {
  OMES_TEST=1 OMES_FAKE_ROOT=1 "$OMES_BIN" install --profile server --yes >/dev/null
}

@test "uninstall with nothing applied is a no-op (exit 0)" {
  run "$OMES_BIN" uninstall --yes
  [ "$status" -eq 0 ]
  [[ "$output" == *"nothing to do"* ]]
}

@test "uninstall --dry-run prints the packages OMES installed without removing them or mutating state" {
  _install_apt_base
  : >"$SHIM_LOG"

  OMES_TEST=1 OMES_FAKE_ROOT=1 run "$OMES_BIN" uninstall --module apt-base --dry-run --yes
  [ "$status" -eq 0 ]
  [[ "$output" == *"installed these packages"* ]]
  [[ "$output" == *"apt-get remove"* ]]

  run grep -c 'apt-get remove' "$SHIM_LOG"
  [ "$status" -ne 0 ] || [ "$output" -eq 0 ]

  run grep -q '^module.apt-base.status=applied$' "${OMES_STATE_DIR}/state"
  [ "$status" -eq 0 ]
}

@test "uninstall without --purge-packages prints the packages but does not remove them; state becomes removed" {
  _install_apt_base
  : >"$SHIM_LOG"

  OMES_TEST=1 OMES_FAKE_ROOT=1 run "$OMES_BIN" uninstall --module apt-base --yes
  [ "$status" -eq 0 ]
  [[ "$output" == *"installed these packages"* ]]

  run grep -c 'apt-get remove' "$SHIM_LOG"
  [ "$status" -ne 0 ] || [ "$output" -eq 0 ]

  run grep -q '^module.apt-base.status=removed$' "${OMES_STATE_DIR}/state"
  [ "$status" -eq 0 ]

  # The installed_packages record is preserved (nothing was actually
  # removed), so an operator/`--purge-packages` re-run later still knows
  # what OMES installed.
  run grep -q '^module.apt-base.installed_packages=' "${OMES_STATE_DIR}/state"
  [ "$status" -eq 0 ]
}

@test "uninstall --purge-packages calls apt-get remove exactly once, only for the recorded packages, and clears the record" {
  _install_apt_base
  local recorded
  recorded="$(grep '^module.apt-base.installed_packages=' "${OMES_STATE_DIR}/state" | cut -d= -f2-)"
  [ -n "$recorded" ]
  : >"$SHIM_LOG"

  OMES_TEST=1 OMES_FAKE_ROOT=1 run "$OMES_BIN" uninstall --module apt-base --purge-packages --yes
  [ "$status" -eq 0 ]

  run grep -c 'apt-get remove' "$SHIM_LOG"
  [ "$output" -eq 1 ]

  run grep 'apt-get remove' "$SHIM_LOG"
  local pkg
  local old_ifs="$IFS"
  IFS=':'
  for pkg in $recorded; do
    [[ "$output" == *"$pkg"* ]]
  done
  IFS="$old_ifs"

  run grep -q '^module.apt-base.installed_packages=' "${OMES_STATE_DIR}/state"
  [ "$status" -ne 0 ]
}

@test "uninstall --module naming a wrong-scope module exits 5 with no mutation" {
  _install_apt_base
  : >"$SHIM_LOG"
  run "$OMES_BIN" uninstall --module apt-base --yes
  [ "$status" -eq 5 ]
  run grep -c 'apt-get' "$SHIM_LOG"
  [ "$status" -ne 0 ] || [ "$output" -eq 0 ]
}

@test "uninstall --json emits a single valid JSON object" {
  _install_apt_base
  OMES_TEST=1 OMES_FAKE_ROOT=1 omes_run_stdout_only "$OMES_BIN" uninstall --module apt-base --yes --json
  [ "$status" -eq 0 ]
  run python3 -c 'import json,sys; d=json.loads(sys.stdin.read()); assert d["command"]=="uninstall"; assert d["ok"] is True; assert d["modules"][0]["name"]=="apt-base"; assert d["modules"][0]["status"]=="removed"' <<<"$output"
  [ "$status" -eq 0 ]
}

@test "uninstall exits 10 naming the module when module_rollback fails" {
  local work="${OMES_TEST_TMPDIR}/rollback-fail-tree"
  mkdir -p "$work"
  cp -a "${OMES_TEST_ROOT}/bin" "${OMES_TEST_ROOT}/lib" "${OMES_TEST_ROOT}/profiles" "$work/"
  mkdir -p "${work}/modules/fail-rollback"
  cat >"${work}/modules/fail-rollback/module.sh" <<'EOF'
MODULE_NAME="fail-rollback"
MODULE_DESCRIPTION="always fails module_rollback"
MODULE_SCOPE="root"
MODULE_REQUIRES=()
module_check() { :; }
module_apply() { :; }
module_verify() { :; }
module_rollback() { return 1; }
EOF

  mkdir -p "$OMES_STATE_DIR"
  {
    printf 'module.fail-rollback.status=applied\n'
    printf 'module.fail-rollback.managed_paths=\n'
  } >>"${OMES_STATE_DIR}/state"

  OMES_TEST=1 OMES_FAKE_ROOT=1 run "${work}/bin/omes" uninstall --module fail-rollback --yes
  [ "$status" -eq 10 ]
  [[ "$output" == *"fail-rollback"* ]]
}

# Regression: module_resolve_order expands MODULE_REQUIRES transitively,
# which is right for install but must never make `uninstall --module X`
# roll back X's dependencies too.
@test "uninstall --module does not cascade into the module's dependencies" {
  local work="${OMES_TEST_TMPDIR}/cascade-tree"
  mkdir -p "$work"
  cp -a "${OMES_TEST_ROOT}/bin" "${OMES_TEST_ROOT}/lib" "${OMES_TEST_ROOT}/profiles" "$work/"
  mkdir -p "${work}/modules/base-dep" "${work}/modules/leaf"
  cat >"${work}/modules/base-dep/module.sh" <<'EOF2'
MODULE_NAME="base-dep"
MODULE_DESCRIPTION="dependency"
MODULE_SCOPE="root"
MODULE_REQUIRES=()
module_check() { :; }
module_apply() { :; }
module_verify() { :; }
module_rollback() { echo "ROLLBACK base-dep"; }
EOF2
  cat >"${work}/modules/leaf/module.sh" <<'EOF2'
MODULE_NAME="leaf"
MODULE_DESCRIPTION="depends on base-dep"
MODULE_SCOPE="root"
MODULE_REQUIRES=(base-dep)
module_check() { :; }
module_apply() { :; }
module_verify() { :; }
module_rollback() { echo "ROLLBACK leaf"; }
EOF2

  mkdir -p "$OMES_STATE_DIR"
  {
    printf 'module.base-dep.status=applied\n'
    printf 'module.base-dep.managed_paths=\n'
    printf 'module.leaf.status=applied\n'
    printf 'module.leaf.managed_paths=\n'
  } >>"${OMES_STATE_DIR}/state"

  OMES_TEST=1 OMES_FAKE_ROOT=1 run "${work}/bin/omes" uninstall --module leaf --yes
  [ "$status" -eq 0 ]
  [[ "$output" == *"ROLLBACK leaf"* ]]
  [[ "$output" != *"ROLLBACK base-dep"* ]]
  grep -q '^module.leaf.status=removed$' "${OMES_STATE_DIR}/state"
  grep -q '^module.base-dep.status=applied$' "${OMES_STATE_DIR}/state"
}
