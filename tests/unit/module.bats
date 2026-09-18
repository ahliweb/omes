#!/usr/bin/env bats
# tests/unit/module.bats - lib/omes/module.sh unit tests: topo-sort, cycle
# detection, scope filtering, and module_load contract validation.

setup() {
  load '../test_helper.bash'
  omes_test_setup
  # shellcheck source=../../lib/omes/core.sh
  source "${OMES_TEST_ROOT}/lib/omes/core.sh"
  # shellcheck source=../../lib/omes/log.sh
  source "${OMES_TEST_ROOT}/lib/omes/log.sh"
  # shellcheck source=../../lib/omes/state.sh
  source "${OMES_TEST_ROOT}/lib/omes/state.sh"
  # shellcheck source=../../lib/omes/backup.sh
  source "${OMES_TEST_ROOT}/lib/omes/backup.sh"
  # shellcheck source=../../lib/omes/module.sh
  source "${OMES_TEST_ROOT}/lib/omes/module.sh"
}

teardown() {
  omes_test_teardown
}

@test "module_topo_sort keeps file order when there are no dependencies" {
  declare -gA MODULE_REQUIRES_OF=()
  run module_topo_sort a b c
  [ "$status" -eq 0 ]
  [ "${lines[0]}" = "a" ]
  [ "${lines[1]}" = "b" ]
  [ "${lines[2]}" = "c" ]
}

@test "module_topo_sort orders a dependency before its dependent" {
  declare -gA MODULE_REQUIRES_OF=()
  MODULE_REQUIRES_OF["hermes-gateway"]="hermes"
  MODULE_REQUIRES_OF["hermes"]=""
  run module_topo_sort hermes-gateway hermes
  [ "$status" -eq 0 ]
  [ "${lines[0]}" = "hermes" ]
  [ "${lines[1]}" = "hermes-gateway" ]
}

@test "module_topo_sort handles a diamond dependency without duplicating nodes" {
  declare -gA MODULE_REQUIRES_OF=()
  # core.sh sets IFS=$'\n\t'; a multi-value MODULE_REQUIRES_OF entry is
  # joined the same way production code joins it ("${MODULE_REQUIRES[*]}"),
  # i.e. with the first IFS character, not a literal space.
  MODULE_REQUIRES_OF["d"]="b${IFS:0:1}c"
  MODULE_REQUIRES_OF["b"]="a"
  MODULE_REQUIRES_OF["c"]="a"
  MODULE_REQUIRES_OF["a"]=""
  run module_topo_sort d b c a
  [ "$status" -eq 0 ]
  [ "${#lines[@]}" -eq 4 ]
  [ "${lines[0]}" = "a" ]
  [ "${lines[3]}" = "d" ]
}

@test "module_topo_sort detects a direct cycle and exits 2" {
  declare -gA MODULE_REQUIRES_OF=()
  MODULE_REQUIRES_OF["a"]="b"
  MODULE_REQUIRES_OF["b"]="a"
  run module_topo_sort a b
  [ "$status" -eq 2 ]
}

@test "module_topo_sort detects a transitive (3-node) cycle and exits 2" {
  declare -gA MODULE_REQUIRES_OF=()
  MODULE_REQUIRES_OF["a"]="b"
  MODULE_REQUIRES_OF["b"]="c"
  MODULE_REQUIRES_OF["c"]="a"
  run module_topo_sort a b c
  [ "$status" -eq 2 ]
}

@test "module_topo_sort is safe with no MODULE_REQUIRES_OF populated" {
  unset MODULE_REQUIRES_OF
  run module_topo_sort solo
  [ "$status" -eq 0 ]
  [ "$output" = "solo" ]
}

# --- module_load contract validation -------------------------------------

@test "module_load sources a well-formed module and exposes its metadata" {
  mkdir -p "${OMES_ROOT}/modules/apt-base"
  run module_load "apt-base"
  [ "$status" -eq 0 ]
}

@test "module_load dies with a usage error for a missing module" {
  run module_load "does-not-exist"
  [ "$status" -eq 2 ]
}

@test "module_load dies with a usage error when required metadata is missing" {
  local dir="${OMES_TEST_TMPDIR}/modroot/modules/broken-meta"
  mkdir -p "$dir"
  cat > "${dir}/module.sh" <<'EOF'
#!/usr/bin/env bash
MODULE_NAME="broken-meta"
MODULE_SCOPE="root"
MODULE_REQUIRES=()
module_check() { :; }
module_apply() { :; }
module_verify() { :; }
module_rollback() { :; }
EOF
  run env OMES_ROOT="${OMES_TEST_TMPDIR}/modroot" bash -c '
    source "'"${OMES_TEST_ROOT}"'/lib/omes/core.sh"
    source "'"${OMES_TEST_ROOT}"'/lib/omes/log.sh"
    source "'"${OMES_TEST_ROOT}"'/lib/omes/state.sh"
    source "'"${OMES_TEST_ROOT}"'/lib/omes/backup.sh"
    source "'"${OMES_TEST_ROOT}"'/lib/omes/module.sh"
    module_load broken-meta
  '
  [ "$status" -eq 2 ]
}

@test "module_load dies with a usage error when a required function is missing" {
  local dir="${OMES_TEST_TMPDIR}/modroot/modules/broken-func"
  mkdir -p "$dir"
  cat > "${dir}/module.sh" <<'EOF'
#!/usr/bin/env bash
MODULE_NAME="broken-func"
MODULE_DESCRIPTION="missing module_verify"
MODULE_SCOPE="root"
MODULE_REQUIRES=()
module_check() { :; }
module_apply() { :; }
module_rollback() { :; }
EOF
  run env OMES_ROOT="${OMES_TEST_TMPDIR}/modroot" bash -c '
    source "'"${OMES_TEST_ROOT}"'/lib/omes/core.sh"
    source "'"${OMES_TEST_ROOT}"'/lib/omes/log.sh"
    source "'"${OMES_TEST_ROOT}"'/lib/omes/state.sh"
    source "'"${OMES_TEST_ROOT}"'/lib/omes/backup.sh"
    source "'"${OMES_TEST_ROOT}"'/lib/omes/module.sh"
    module_load broken-func
  '
  [ "$status" -eq 2 ]
}

@test "module_load rejects an invalid MODULE_SCOPE" {
  local dir="${OMES_TEST_TMPDIR}/modroot/modules/bad-scope"
  mkdir -p "$dir"
  cat > "${dir}/module.sh" <<'EOF'
#!/usr/bin/env bash
MODULE_NAME="bad-scope"
MODULE_DESCRIPTION="invalid scope"
MODULE_SCOPE="everyone"
MODULE_REQUIRES=()
module_check() { :; }
module_apply() { :; }
module_verify() { :; }
module_rollback() { :; }
EOF
  run env OMES_ROOT="${OMES_TEST_TMPDIR}/modroot" bash -c '
    source "'"${OMES_TEST_ROOT}"'/lib/omes/core.sh"
    source "'"${OMES_TEST_ROOT}"'/lib/omes/log.sh"
    source "'"${OMES_TEST_ROOT}"'/lib/omes/state.sh"
    source "'"${OMES_TEST_ROOT}"'/lib/omes/backup.sh"
    source "'"${OMES_TEST_ROOT}"'/lib/omes/module.sh"
    module_load bad-scope
  '
  [ "$status" -eq 2 ]
}

# --- scope filtering (ADR-0005: filter, not fail) -------------------------

@test "module_filter_by_scope keeps only current-scope modules as non-root" {
  local dir="${OMES_TEST_TMPDIR}/modroot/modules"
  mkdir -p "${dir}/root-mod" "${dir}/user-mod"
  cat > "${dir}/root-mod/module.sh" <<'EOF'
MODULE_NAME="root-mod"
MODULE_DESCRIPTION="root scope"
MODULE_SCOPE="root"
MODULE_REQUIRES=()
module_check() { :; }
module_apply() { :; }
module_verify() { :; }
module_rollback() { :; }
EOF
  cat > "${dir}/user-mod/module.sh" <<'EOF'
MODULE_NAME="user-mod"
MODULE_DESCRIPTION="user scope"
MODULE_SCOPE="user"
MODULE_REQUIRES=()
module_check() { :; }
module_apply() { :; }
module_verify() { :; }
module_rollback() { :; }
EOF
  run env OMES_ROOT="${OMES_TEST_TMPDIR}/modroot" bash -c '
    source "'"${OMES_TEST_ROOT}"'/lib/omes/core.sh"
    source "'"${OMES_TEST_ROOT}"'/lib/omes/log.sh"
    source "'"${OMES_TEST_ROOT}"'/lib/omes/state.sh"
    source "'"${OMES_TEST_ROOT}"'/lib/omes/backup.sh"
    source "'"${OMES_TEST_ROOT}"'/lib/omes/module.sh"
    module_filter_by_scope root-mod user-mod
  '
  [ "$status" -eq 0 ]
  [ "$output" = "user-mod" ]
}

@test "module_other_scope_modules reports the mismatched-scope modules" {
  local dir="${OMES_TEST_TMPDIR}/modroot/modules"
  mkdir -p "${dir}/root-mod" "${dir}/user-mod"
  cat > "${dir}/root-mod/module.sh" <<'EOF'
MODULE_NAME="root-mod"
MODULE_DESCRIPTION="root scope"
MODULE_SCOPE="root"
MODULE_REQUIRES=()
module_check() { :; }
module_apply() { :; }
module_verify() { :; }
module_rollback() { :; }
EOF
  cat > "${dir}/user-mod/module.sh" <<'EOF'
MODULE_NAME="user-mod"
MODULE_DESCRIPTION="user scope"
MODULE_SCOPE="user"
MODULE_REQUIRES=()
module_check() { :; }
module_apply() { :; }
module_verify() { :; }
module_rollback() { :; }
EOF
  run env OMES_ROOT="${OMES_TEST_TMPDIR}/modroot" bash -c '
    source "'"${OMES_TEST_ROOT}"'/lib/omes/core.sh"
    source "'"${OMES_TEST_ROOT}"'/lib/omes/log.sh"
    source "'"${OMES_TEST_ROOT}"'/lib/omes/state.sh"
    source "'"${OMES_TEST_ROOT}"'/lib/omes/backup.sh"
    source "'"${OMES_TEST_ROOT}"'/lib/omes/module.sh"
    module_other_scope_modules root-mod user-mod
  '
  [ "$status" -eq 0 ]
  [ "$output" = "root-mod" ]
}

# --- run_apply exit-code mapping (lib/omes/pkg.sh return-code convention) --

@test "run_apply maps a module_apply return of exactly OMES_EX_NETWORK to process exit 8" {
  local dir="${OMES_TEST_TMPDIR}/modroot/modules/net-mod"
  mkdir -p "$dir"
  cat > "${dir}/module.sh" <<'EOF'
MODULE_NAME="net-mod"
MODULE_DESCRIPTION="returns the network sentinel from module_apply"
MODULE_SCOPE="root"
MODULE_REQUIRES=()
module_check() { :; }
module_apply() { return 8; }
module_verify() { :; }
module_rollback() { :; }
EOF
  run env OMES_ROOT="${OMES_TEST_TMPDIR}/modroot" OMES_TEST=1 OMES_FAKE_ROOT=1 bash -c '
    source "'"${OMES_TEST_ROOT}"'/lib/omes/core.sh"
    source "'"${OMES_TEST_ROOT}"'/lib/omes/log.sh"
    source "'"${OMES_TEST_ROOT}"'/lib/omes/state.sh"
    source "'"${OMES_TEST_ROOT}"'/lib/omes/backup.sh"
    source "'"${OMES_TEST_ROOT}"'/lib/omes/module.sh"
    state_init >/dev/null
    run_apply net-mod
  '
  [ "$status" -eq 8 ]
}

@test "run_apply maps any other module_apply failure to process exit 6" {
  local dir="${OMES_TEST_TMPDIR}/modroot/modules/fail-mod"
  mkdir -p "$dir"
  cat > "${dir}/module.sh" <<'EOF'
MODULE_NAME="fail-mod"
MODULE_DESCRIPTION="returns a generic failure from module_apply"
MODULE_SCOPE="root"
MODULE_REQUIRES=()
module_check() { :; }
module_apply() { return 1; }
module_verify() { :; }
module_rollback() { :; }
EOF
  run env OMES_ROOT="${OMES_TEST_TMPDIR}/modroot" OMES_TEST=1 OMES_FAKE_ROOT=1 bash -c '
    source "'"${OMES_TEST_ROOT}"'/lib/omes/core.sh"
    source "'"${OMES_TEST_ROOT}"'/lib/omes/log.sh"
    source "'"${OMES_TEST_ROOT}"'/lib/omes/state.sh"
    source "'"${OMES_TEST_ROOT}"'/lib/omes/backup.sh"
    source "'"${OMES_TEST_ROOT}"'/lib/omes/module.sh"
    state_init >/dev/null
    run_apply fail-mod
  '
  [ "$status" -eq 6 ]
}
